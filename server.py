from __future__ import annotations

import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from logger import logger
except Exception:
    import logging

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)s | bootstrap | %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("local-json-repo-mcp-bootstrap")
    logger.exception("Failed to initialize logger.py; using stderr fallback")


logger.info("=" * 72)
logger.info("Starting local-json-repo-mcp bootstrap")
logger.info("Python executable: %s", sys.executable)
logger.info("Python version: %s", sys.version.replace("\n", " "))
logger.info("Platform: %s", sys.platform)
logger.info("PID: %s", os.getpid())
logger.info("Current working directory: %s", Path.cwd())
logger.info("server.py location: %s", Path(__file__).resolve())
logger.info("GRAVITEE_REPOSITORY_PATH=%r", os.getenv("GRAVITEE_REPOSITORY_PATH"))
logger.info("API_REPOSITORY_PATH=%r", os.getenv("API_REPOSITORY_PATH"))
logger.info("MCP_CONFIG_PATH=%r", os.getenv("MCP_CONFIG_PATH"))

try:
    logger.info("Installed MCP package version: %s", version("mcp"))
except PackageNotFoundError:
    logger.warning("Unable to determine installed MCP package version")

try:
    from mcp.server.mcpserver import MCPServer
    logger.info("MCPServer import successful")
except Exception:
    logger.exception(
        "Failed to import MCPServer. MCP Python SDK 2.x is required for %s",
        sys.executable,
    )
    raise

try:
    from cache import build_cache_manager
    from environment_manager import EnvironmentManager, SearchTarget
    from json_repository import (
        find_json_documents,
        find_string_anywhere_in_json,
        get_json,
        get_json_value,
        select_json_fields,
        string_contains,
    )
    logger.info("Repository, cache, and environment modules imported successfully")
except Exception:
    logger.exception("Failed to import local server modules")
    raise


SERVER_DIR = Path(__file__).resolve().parent


def _load_config() -> dict[str, Any]:
    default_path = SERVER_DIR / "config.json"
    raw_path = os.getenv("MCP_CONFIG_PATH") or str(default_path)
    config_path = Path(raw_path).expanduser().resolve()
    logger.info("Loading MCP config: %s", config_path)

    try:
        with config_path.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except Exception:
        logger.exception("Failed to load MCP config: %s", config_path)
        raise

    if not isinstance(config, dict):
        raise ValueError("MCP config root must be a JSON object")

    logger.info("MCP config loaded successfully")
    return config


def _require_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Config field '{key}' must be an object")
    return value


def _require_string_list(parent: dict[str, Any], key: str) -> list[str]:
    value = parent.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"Config field '{key}' must be a non-empty array")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"Config field '{key}' must contain only non-empty strings")
    return value


CONFIG = _load_config()
FILE_PATTERNS = _require_dict(CONFIG, "file_patterns")
API_PATTERNS = _require_string_list(FILE_PATTERNS, "apis")
APPLICATION_PATTERNS = _require_string_list(FILE_PATTERNS, "applications")
ALL_JSON_PATTERNS = _require_string_list(FILE_PATTERNS, "all_json")
EXCLUDE_PATTERNS = _require_string_list(FILE_PATTERNS, "exclude")

FIELD_PATHS = _require_dict(CONFIG, "field_paths")
API_FIELD_PATHS = _require_dict(FIELD_PATHS, "api")
APPLICATION_FIELD_PATHS = _require_dict(FIELD_PATHS, "application")

API_ID_PATHS = _require_string_list(API_FIELD_PATHS, "id")
API_NAME_PATHS = _require_string_list(API_FIELD_PATHS, "name")
API_VERSION_PATHS = _require_string_list(API_FIELD_PATHS, "version")
API_DESCRIPTION_PATHS = _require_string_list(API_FIELD_PATHS, "description")
API_STATE_PATHS = _require_string_list(API_FIELD_PATHS, "state")

APP_ID_PATHS = _require_string_list(APPLICATION_FIELD_PATHS, "id")
APP_NAME_PATHS = _require_string_list(APPLICATION_FIELD_PATHS, "name")
APP_DESCRIPTION_PATHS = _require_string_list(APPLICATION_FIELD_PATHS, "description")
APP_CLIENT_ID_PATHS = _require_string_list(APPLICATION_FIELD_PATHS, "client_id")
APP_TYPE_PATHS = _require_string_list(APPLICATION_FIELD_PATHS, "type")

SUMMARY_CONFIG = CONFIG.get("summary", {})
if not isinstance(SUMMARY_CONFIG, dict):
    raise ValueError("Config field 'summary' must be an object")
SUMMARY_MAX_VALUES = SUMMARY_CONFIG.get("max_values_per_field", 10)
if not isinstance(SUMMARY_MAX_VALUES, int) or SUMMARY_MAX_VALUES <= 0:
    raise ValueError("summary.max_values_per_field must be a positive integer")

_raw_repo_path = (
    os.getenv("GRAVITEE_REPOSITORY_PATH")
    or os.getenv("API_REPOSITORY_PATH")
    or "."
)
REPO_PATH = Path(_raw_repo_path).expanduser().resolve()
logger.info("Source Gravitee repository: %s", REPO_PATH)

ENVIRONMENT_CONFIG = _require_dict(CONFIG, "environments")
try:
    ENVIRONMENTS = EnvironmentManager(
        REPO_PATH,
        ENVIRONMENT_CONFIG,
        base_dir=SERVER_DIR,
    )
except Exception:
    logger.exception("Failed to initialize environment worktrees")
    raise

DEFAULT_SCOPE = ENVIRONMENTS.default_scope
logger.info("Default search scope: %s", DEFAULT_SCOPE)

cache_namespace = json.dumps(
    {
        "repository": str(REPO_PATH),
        "file_patterns": FILE_PATTERNS,
        "field_paths": FIELD_PATHS,
        "environments": ENVIRONMENT_CONFIG,
    },
    ensure_ascii=False,
    sort_keys=True,
)
CACHE = build_cache_manager(
    CONFIG.get("cache"),
    base_dir=SERVER_DIR,
    namespace=cache_namespace,
)
logger.info("Cache manager initialized")

mcp = MCPServer("gravitee-local-repository")
logger.info("MCPServer instance created successfully")


def _first(data: Any, paths: Iterable[str], default: Any = None) -> Any:
    for path in paths:
        value = get_json_value(data, path, None)
        if value is not None:
            return value
    return default


def _values_at_key(data: Any, key: str) -> list[Any]:
    results: list[Any] = []
    if isinstance(data, dict):
        for current_key, value in data.items():
            if current_key == key:
                results.append(value)
            results.extend(_values_at_key(value, key))
    elif isinstance(data, list):
        for item in data:
            results.extend(_values_at_key(item, key))
    return results


def _compact(values: Iterable[Any], limit: int | None = None) -> list[Any]:
    limit = limit or SUMMARY_MAX_VALUES
    result: list[Any] = []
    seen: set[str] = set()

    for value in values:
        items = value if isinstance(value, list) else [value]
        for item in items:
            marker = repr(item)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(item)
            if len(result) >= limit:
                return result
    return result


def _contains_any_path(data: Any, paths: Iterable[str], query: str) -> bool:
    return any(
        string_contains(
            get_json_value(data, path, None),
            query,
            case_sensitive=False,
        )
        for path in paths
    )


def _equals_any_path(data: Any, paths: Iterable[str], expected: str) -> bool:
    expected_cf = expected.casefold()
    for path in paths:
        value = get_json_value(data, path, None)
        if isinstance(value, str) and value.casefold() == expected_cf:
            return True
    return False


def _recursive_string_match(data: Any, query: str) -> bool:
    query_cf = query.casefold()
    if isinstance(data, str):
        return query_cf in data.casefold()
    if isinstance(data, dict):
        return any(
            query_cf in str(key).casefold()
            or _recursive_string_match(value, query)
            for key, value in data.items()
        )
    if isinstance(data, list):
        return any(_recursive_string_match(value, query) for value in data)
    return False


def _base_summary(document: dict[str, Any], target: SearchTarget) -> dict[str, Any]:
    return {
        "scope": target.scope,
        "stage": target.stage,
        "zone": target.zone,
        "x-filepath": document["path"],
    }


def _api_summary(
    document: dict[str, Any],
    target: SearchTarget,
) -> dict[str, Any]:
    data = document["data"]
    result = _base_summary(document, target)
    result.update(
        {
            "id": _first(data, API_ID_PATHS),
            "name": _first(data, API_NAME_PATHS),
            "version": _first(data, API_VERSION_PATHS),
            "description": _first(data, API_DESCRIPTION_PATHS),
            "state": _first(data, API_STATE_PATHS),
            "virtual_hosts": _compact(
                _values_at_key(data, "virtual_hosts")
                + _values_at_key(data, "virtualHosts")
            ),
            "paths": _compact(_values_at_key(data, "path")),
            "endpoints": _compact(
                _values_at_key(data, "target") + _values_at_key(data, "url")
            ),
        }
    )
    return result


def _application_summary(
    document: dict[str, Any],
    target: SearchTarget,
) -> dict[str, Any]:
    data = document["data"]
    result = _base_summary(document, target)
    result.update(
        {
            "id": _first(data, APP_ID_PATHS),
            "name": _first(data, APP_NAME_PATHS),
            "description": _first(data, APP_DESCRIPTION_PATHS),
            "client_id": _first(data, APP_CLIENT_ID_PATHS),
            "type": _first(data, APP_TYPE_PATHS),
        }
    )
    return result


def _search_api(
    scope: str,
    predicate: Callable[[Any], bool] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for target in ENVIRONMENTS.resolve_scope(scope):
        docs = find_json_documents(
            target.worktree_path,
            target.patterns(API_PATTERNS),
            predicate=predicate,
            exclude_patterns=EXCLUDE_PATTERNS,
        )
        results.extend(_api_summary(doc, target) for doc in docs)
    return results


def _search_application(
    scope: str,
    predicate: Callable[[Any], bool] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for target in ENVIRONMENTS.resolve_scope(scope):
        docs = find_json_documents(
            target.worktree_path,
            target.patterns(APPLICATION_PATTERNS),
            predicate=predicate,
            exclude_patterns=EXCLUDE_PATTERNS,
        )
        results.extend(_application_summary(doc, target) for doc in docs)
    return results


def _cached_search(
    operation: str,
    scope: str,
    params: dict[str, Any],
    producer: Callable[[], Any],
) -> Any:
    cache_params = {
        **params,
        "environment": ENVIRONMENTS.cache_context(scope),
    }
    return CACHE.get_or_compute(operation, cache_params, producer)


def _generic_search(scope: str, query: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for target in ENVIRONMENTS.resolve_scope(scope):
        found = find_string_anywhere_in_json(
            target.worktree_path,
            query,
            patterns=target.patterns(ALL_JSON_PATTERNS),
            case_sensitive=False,
            include_keys=True,
            exclude_patterns=EXCLUDE_PATTERNS,
        )
        for item in found:
            item = dict(item)
            filepath = item.pop("path")
            results.append(
                {
                    "scope": target.scope,
                    "stage": target.stage,
                    "zone": target.zone,
                    "x-filepath": filepath,
                    **item,
                }
            )
    return results


def _read_by_scope(
    file_path: str,
    scope: str,
    transform: Callable[[Any], Any],
) -> Any:
    targets = ENVIRONMENTS.targets_for_file(file_path, scope)
    values = []
    for target in targets:
        data = get_json(target.worktree_path, file_path)
        values.append(
            {
                "scope": target.scope,
                "stage": target.stage,
                "zone": target.zone,
                "x-filepath": file_path,
                "value": transform(data),
            }
        )

    if len(values) == 1:
        return values[0]["value"]
    return values


@mcp.tool()
def git_pull(scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Pull managed Git environment(s). scope: stage_zone, stage, or all.

    Worktrees are detached, so this performs the safe equivalent of git pull:
    fetch remote refs and reset the selected stage worktree(s) to their
    configured remote branches. Default scope: test_int.
    """
    logger.info("git_pull scope=%s", scope)
    return ENVIRONMENTS.pull(scope)


@mcp.tool()
def search_api_by_id(api_id: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs by exact id. scope: stage_zone, stage, or all. Default: test_int."""
    logger.info("search_api_by_id api_id=%s scope=%s", api_id, scope)
    return _cached_search(
        "search_api_by_id",
        scope,
        {"api_id": api_id},
        lambda: _search_api(scope, lambda data: _equals_any_path(data, API_ID_PATHS, api_id)),
    )


@mcp.tool()
def search_api_by_name(name: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs by partial case-insensitive name. Default scope: test_int."""
    logger.info("search_api_by_name name=%s scope=%s", name, scope)
    return _cached_search(
        "search_api_by_name",
        scope,
        {"name": name},
        lambda: _search_api(scope, lambda data: _contains_any_path(data, API_NAME_PATHS, name)),
    )


@mcp.tool()
def search_api_by_path(path: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs containing a context/virtual-host path. Default scope: test_int."""
    logger.info("search_api_by_path path=%s scope=%s", path, scope)
    return _cached_search(
        "search_api_by_path",
        scope,
        {"path": path},
        lambda: _search_api(scope, lambda data: _recursive_string_match(data, path)),
    )


@mcp.tool()
def search_api_by_backend_url(url: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs referencing a backend URL/target. Default scope: test_int."""
    return _cached_search(
        "search_api_by_backend_url",
        scope,
        {"url": url},
        lambda: _search_api(scope, lambda data: _recursive_string_match(data, url)),
    )


@mcp.tool()
def search_api_by_policy(policy: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs containing a policy name/id. Default scope: test_int."""
    return _cached_search(
        "search_api_by_policy",
        scope,
        {"policy": policy},
        lambda: _search_api(scope, lambda data: _recursive_string_match(data, policy)),
    )


@mcp.tool()
def search_api_by_tag(tag: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs containing a Gravitee tag/label. Default scope: test_int."""
    return _cached_search(
        "search_api_by_tag",
        scope,
        {"tag": tag},
        lambda: _search_api(scope, lambda data: _recursive_string_match(data, tag)),
    )


@mcp.tool()
def search_api_by_host(host: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs referencing a host. Default scope: test_int."""
    return _cached_search(
        "search_api_by_host",
        scope,
        {"host": host},
        lambda: _search_api(scope, lambda data: _recursive_string_match(data, host)),
    )


@mcp.tool()
def search_api_by_plan_name(plan_name: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs referencing a plan name. Default scope: test_int."""
    return _cached_search(
        "search_api_by_plan_name",
        scope,
        {"plan_name": plan_name},
        lambda: _search_api(scope, lambda data: _recursive_string_match(data, plan_name)),
    )


@mcp.tool()
def search_api_by_plan_id(plan_id: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find APIs referencing a plan id. Default scope: test_int."""
    return _cached_search(
        "search_api_by_plan_id",
        scope,
        {"plan_id": plan_id},
        lambda: _search_api(scope, lambda data: _recursive_string_match(data, plan_id)),
    )


@mcp.tool()
def search_application_by_id(
    application_id: str,
    scope: str = DEFAULT_SCOPE,
) -> list[dict[str, Any]]:
    """Find applications by exact id. Default scope: test_int."""
    return _cached_search(
        "search_application_by_id",
        scope,
        {"application_id": application_id},
        lambda: _search_application(
            scope,
            lambda data: _equals_any_path(data, APP_ID_PATHS, application_id),
        ),
    )


@mcp.tool()
def search_application_by_name(name: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find applications by partial name. Default scope: test_int."""
    return _cached_search(
        "search_application_by_name",
        scope,
        {"name": name},
        lambda: _search_application(
            scope,
            lambda data: _contains_any_path(data, APP_NAME_PATHS, name),
        ),
    )


@mcp.tool()
def search_application_by_client_id(
    client_id: str,
    scope: str = DEFAULT_SCOPE,
) -> list[dict[str, Any]]:
    """Find applications by OAuth/client id. Default scope: test_int."""
    return _cached_search(
        "search_application_by_client_id",
        scope,
        {"client_id": client_id},
        lambda: _search_application(
            scope,
            lambda data: _equals_any_path(data, APP_CLIENT_ID_PATHS, client_id)
            or _recursive_string_match(data, client_id),
        ),
    )


@mcp.tool()
def search_application_by_api_id(api_id: str, scope: str = DEFAULT_SCOPE) -> list[dict[str, Any]]:
    """Find application definitions referencing an API id. Default: test_int."""
    return _cached_search(
        "search_application_by_api_id",
        scope,
        {"api_id": api_id},
        lambda: _search_application(scope, lambda data: _recursive_string_match(data, api_id)),
    )


@mcp.tool()
def search_application_by_subscription_id(
    subscription_id: str,
    scope: str = DEFAULT_SCOPE,
) -> list[dict[str, Any]]:
    """Find applications referencing a subscription id. Default: test_int."""
    return _cached_search(
        "search_application_by_subscription_id",
        scope,
        {"subscription_id": subscription_id},
        lambda: _search_application(
            scope,
            lambda data: _recursive_string_match(data, subscription_id),
        ),
    )


@mcp.tool()
def search_repository(
    query: str,
    scope: str = DEFAULT_SCOPE,
    max_results: int = 25,
) -> list[dict[str, Any]]:
    """Generic substring search across JSON. scope: stage_zone, stage, or all."""
    return _cached_search(
        "search_repository",
        scope,
        {"query": query, "max_results": max_results},
        lambda: _generic_search(scope, query)[:max_results],
    )


@mcp.tool()
def list_apis(scope: str = DEFAULT_SCOPE, limit: int = 100) -> list[dict[str, Any]]:
    """List API summaries in stage_zone, stage, or all. Default: test_int."""
    return _cached_search(
        "list_apis",
        scope,
        {"limit": limit},
        lambda: _search_api(scope)[:limit],
    )


@mcp.tool()
def list_applications(scope: str = DEFAULT_SCOPE, limit: int = 100) -> list[dict[str, Any]]:
    """List application summaries. Default scope: test_int."""
    return _cached_search(
        "list_applications",
        scope,
        {"limit": limit},
        lambda: _search_application(scope)[:limit],
    )


@mcp.tool()
def get_api_definition(file_path: str, scope: str = DEFAULT_SCOPE) -> Any:
    """Return complete API JSON by x-filepath. Not cached."""
    return _read_by_scope(file_path, scope, lambda data: data)


@mcp.tool()
def get_application_definition(file_path: str, scope: str = DEFAULT_SCOPE) -> Any:
    """Return complete application JSON by x-filepath. Not cached."""
    return _read_by_scope(file_path, scope, lambda data: data)


@mcp.tool()
def get_json_definition(file_path: str, scope: str = DEFAULT_SCOPE) -> Any:
    """Return any complete JSON document by x-filepath. Not cached."""
    return _read_by_scope(file_path, scope, lambda data: data)


@mcp.tool()
def get_json_fields(
    file_path: str,
    fields: list[str],
    scope: str = DEFAULT_SCOPE,
) -> Any:
    """Return selected dot-path fields from JSON by x-filepath. Not cached."""
    return _read_by_scope(file_path, scope, lambda data: select_json_fields(data, fields))


@mcp.tool()
def get_json_value_by_path(
    file_path: str,
    json_path: str,
    scope: str = DEFAULT_SCOPE,
) -> Any:
    """Return one dot-path value from JSON by x-filepath. Not cached."""
    return _read_by_scope(file_path, scope, lambda data: get_json_value(data, json_path))


logger.info("Tool registration completed; server module loaded successfully")

if __name__ == "__main__":
    logger.info("Entering __main__")
    logger.info("Starting MCP 2.x transport: stdio")
    logger.info("Default scope: %s", DEFAULT_SCOPE)
    try:
        mcp.run()
        logger.info("mcp.run() returned normally")
    except KeyboardInterrupt:
        logger.info("MCP server stopped by KeyboardInterrupt")
        raise
    except BaseException:
        logger.exception("MCP server crashed during mcp.run()")
        raise