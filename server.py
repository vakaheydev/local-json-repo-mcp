from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from mcp.server.fastmcp import FastMCP

from json_repository import (
    find_json_documents,
    find_string_anywhere_in_json,
    get_json,
    get_json_value,
    select_json_fields,
    string_contains,
)
from logger import logger


mcp = FastMCP("gravitee-local-repository")

REPO_PATH = Path(
    os.getenv("GRAVITEE_REPOSITORY_PATH")
    or os.getenv("API_REPOSITORY_PATH")
    or "."
).expanduser().resolve()

API_PATTERNS = ["**/*.Api.json", "**/*api*.json"]
APPLICATION_PATTERNS = [
    "**/*.Application.json",
    "**/*application*.json",
    "**/*app*.json",
]
ALL_JSON_PATTERNS = "**/*.json"
EXCLUDE_PATTERNS = [
    "**/.git/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
]

API_ID_PATHS = ["id", "api", "api_id", "apiId", "metadata.id"]
API_NAME_PATHS = ["name", "metadata.name"]
API_VERSION_PATHS = ["version", "apiVersion", "metadata.version"]
API_DESCRIPTION_PATHS = ["description", "metadata.description"]
API_STATE_PATHS = ["lifecycle_state", "lifecycleState", "state"]
APP_ID_PATHS = ["id", "application", "application_id", "applicationId", "metadata.id"]
APP_NAME_PATHS = ["name", "metadata.name"]
APP_DESCRIPTION_PATHS = ["description", "metadata.description"]
APP_CLIENT_ID_PATHS = [
    "client_id",
    "clientId",
    "settings.client_id",
    "settings.clientId",
    "settings.oauth.client_id",
    "settings.oauth.clientId",
    "settings.app.client_id",
    "settings.app.clientId",
]


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


def _compact(values: Iterable[Any], limit: int = 10) -> list[Any]:
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
        string_contains(get_json_value(data, path, None), query, case_sensitive=False)
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
            query_cf in str(key).casefold() or _recursive_string_match(value, query)
            for key, value in data.items()
        )
    if isinstance(data, list):
        return any(_recursive_string_match(value, query) for value in data)
    return False


def _api_summary(document: dict[str, Any]) -> dict[str, Any]:
    data = document["data"]
    return {
        "path": document["path"],
        "id": _first(data, API_ID_PATHS),
        "name": _first(data, API_NAME_PATHS),
        "version": _first(data, API_VERSION_PATHS),
        "description": _first(data, API_DESCRIPTION_PATHS),
        "state": _first(data, API_STATE_PATHS),
        "virtual_hosts": _compact(
            _values_at_key(data, "virtual_hosts") + _values_at_key(data, "virtualHosts")
        ),
        "paths": _compact(_values_at_key(data, "path")),
        "endpoints": _compact(
            _values_at_key(data, "target") + _values_at_key(data, "url")
        ),
    }


def _application_summary(document: dict[str, Any]) -> dict[str, Any]:
    data = document["data"]
    return {
        "path": document["path"],
        "id": _first(data, APP_ID_PATHS),
        "name": _first(data, APP_NAME_PATHS),
        "description": _first(data, APP_DESCRIPTION_PATHS),
        "client_id": _first(data, APP_CLIENT_ID_PATHS),
        "type": _first(data, ["type", "settings.type"]),
    }


def _search_api(predicate) -> list[dict[str, Any]]:
    docs = find_json_documents(
        REPO_PATH,
        API_PATTERNS,
        predicate=predicate,
        exclude_patterns=EXCLUDE_PATTERNS,
    )
    return [_api_summary(doc) for doc in docs]


def _search_application(predicate) -> list[dict[str, Any]]:
    docs = find_json_documents(
        REPO_PATH,
        APPLICATION_PATTERNS,
        predicate=predicate,
        exclude_patterns=EXCLUDE_PATTERNS,
    )
    return [_application_summary(doc) for doc in docs]


@mcp.tool()
def search_api_by_id(api_id: str) -> list[dict[str, Any]]:
    """Find APIs by exact Gravitee API id. Returns compact summaries only."""
    logger.info("search_api_by_id api_id=%s", api_id)
    return _search_api(lambda data: _equals_any_path(data, API_ID_PATHS, api_id))


@mcp.tool()
def search_api_by_name(name: str) -> list[dict[str, Any]]:
    """Find APIs by partial, case-insensitive name match."""
    logger.info("search_api_by_name name=%s", name)
    return _search_api(lambda data: _contains_any_path(data, API_NAME_PATHS, name))


@mcp.tool()
def search_api_by_path(path: str) -> list[dict[str, Any]]:
    """Find APIs containing a context path or virtual-host path."""
    logger.info("search_api_by_path path=%s", path)
    return _search_api(lambda data: _recursive_string_match(data, path))


@mcp.tool()
def search_api_by_backend_url(url: str) -> list[dict[str, Any]]:
    """Find APIs referencing a backend endpoint URL or target."""
    logger.info("search_api_by_backend_url url=%s", url)
    return _search_api(lambda data: _recursive_string_match(data, url))


@mcp.tool()
def search_api_by_policy(policy: str) -> list[dict[str, Any]]:
    """Find APIs containing a policy name/id anywhere in their definition."""
    logger.info("search_api_by_policy policy=%s", policy)
    return _search_api(lambda data: _recursive_string_match(data, policy))


@mcp.tool()
def search_api_by_tag(tag: str) -> list[dict[str, Any]]:
    """Find APIs containing a Gravitee tag/label."""
    logger.info("search_api_by_tag tag=%s", tag)
    return _search_api(lambda data: _recursive_string_match(data, tag))


@mcp.tool()
def search_api_by_host(host: str) -> list[dict[str, Any]]:
    """Find APIs referencing a host in virtual hosts, endpoints, or URLs."""
    logger.info("search_api_by_host host=%s", host)
    return _search_api(lambda data: _recursive_string_match(data, host))


@mcp.tool()
def search_api_by_plan_name(plan_name: str) -> list[dict[str, Any]]:
    """Find APIs whose exported definition references a plan name."""
    logger.info("search_api_by_plan_name plan_name=%s", plan_name)
    return _search_api(lambda data: _recursive_string_match(data, plan_name))


@mcp.tool()
def search_api_by_plan_id(plan_id: str) -> list[dict[str, Any]]:
    """Find APIs whose exported definition references a plan id."""
    logger.info("search_api_by_plan_id plan_id=%s", plan_id)
    return _search_api(lambda data: _recursive_string_match(data, plan_id))


@mcp.tool()
def search_application_by_id(application_id: str) -> list[dict[str, Any]]:
    """Find applications by exact application id. Returns compact summaries."""
    logger.info("search_application_by_id application_id=%s", application_id)
    return _search_application(
        lambda data: _equals_any_path(data, APP_ID_PATHS, application_id)
    )


@mcp.tool()
def search_application_by_name(name: str) -> list[dict[str, Any]]:
    """Find applications by partial, case-insensitive name match."""
    logger.info("search_application_by_name name=%s", name)
    return _search_application(lambda data: _contains_any_path(data, APP_NAME_PATHS, name))


@mcp.tool()
def search_application_by_client_id(client_id: str) -> list[dict[str, Any]]:
    """Find applications by OAuth/client id across common Gravitee field layouts."""
    logger.info("search_application_by_client_id client_id=%s", client_id)
    return _search_application(
        lambda data: _equals_any_path(data, APP_CLIENT_ID_PATHS, client_id)
        or _recursive_string_match(data, client_id)
    )


@mcp.tool()
def search_application_by_api_id(api_id: str) -> list[dict[str, Any]]:
    """Find application definitions that reference a given API id."""
    logger.info("search_application_by_api_id api_id=%s", api_id)
    return _search_application(lambda data: _recursive_string_match(data, api_id))


@mcp.tool()
def search_application_by_subscription_id(subscription_id: str) -> list[dict[str, Any]]:
    """Find applications referencing a subscription id."""
    logger.info("search_application_by_subscription_id subscription_id=%s", subscription_id)
    return _search_application(lambda data: _recursive_string_match(data, subscription_id))


@mcp.tool()
def search_repository(query: str, max_results: int = 25) -> list[dict[str, Any]]:
    """Generic fallback search across all JSON files.

    Returns matching JSON paths/values, not full definitions. Prefer the
    domain-specific search tools whenever possible.
    """
    logger.info("search_repository query=%s max_results=%s", query, max_results)
    results = find_string_anywhere_in_json(
        REPO_PATH,
        query,
        patterns=ALL_JSON_PATTERNS,
        case_sensitive=False,
        include_keys=True,
        exclude_patterns=EXCLUDE_PATTERNS,
    )
    return results[:max_results]


@mcp.tool()
def list_apis(limit: int = 100) -> list[dict[str, Any]]:
    """List API definitions as compact summaries without returning full JSON."""
    logger.info("list_apis limit=%s", limit)
    docs = find_json_documents(
        REPO_PATH,
        API_PATTERNS,
        exclude_patterns=EXCLUDE_PATTERNS,
    )
    return [_api_summary(doc) for doc in docs[:limit]]


@mcp.tool()
def list_applications(limit: int = 100) -> list[dict[str, Any]]:
    """List application definitions as compact summaries without full JSON."""
    logger.info("list_applications limit=%s", limit)
    docs = find_json_documents(
        REPO_PATH,
        APPLICATION_PATTERNS,
        exclude_patterns=EXCLUDE_PATTERNS,
    )
    return [_application_summary(doc) for doc in docs[:limit]]


@mcp.tool()
def get_api_definition(file_path: str) -> Any:
    """Return the complete API JSON definition by repository-relative file path.

    Use after a search tool has identified the relevant API and full detail is
    actually required.
    """
    logger.info("get_api_definition file_path=%s", file_path)
    return get_json(REPO_PATH, file_path)


@mcp.tool()
def get_application_definition(file_path: str) -> Any:
    """Return the complete application JSON definition by relative file path."""
    logger.info("get_application_definition file_path=%s", file_path)
    return get_json(REPO_PATH, file_path)


@mcp.tool()
def get_json_definition(file_path: str) -> Any:
    """Return any complete JSON document from the repository by relative path."""
    logger.info("get_json_definition file_path=%s", file_path)
    return get_json(REPO_PATH, file_path)


@mcp.tool()
def get_json_fields(file_path: str, fields: list[str]) -> dict[str, Any]:
    """Read selected dot-path fields from a JSON definition.

    Prefer this over get_json_definition when only a few fields are required.
    Example fields: ['id', 'name', 'proxy.virtual_hosts', 'plans'].
    """
    logger.info("get_json_fields file_path=%s fields=%s", file_path, fields)
    data = get_json(REPO_PATH, file_path)
    return select_json_fields(data, fields)


@mcp.tool()
def get_json_value_by_path(file_path: str, json_path: str) -> Any:
    """Return one nested JSON value using dot notation, e.g. proxy.virtual_hosts.0.path."""
    logger.info(
        "get_json_value_by_path file_path=%s json_path=%s",
        file_path,
        json_path,
    )
    data = get_json(REPO_PATH, file_path)
    return get_json_value(data, json_path)


if __name__ == "__main__":
    logger.info("Starting MCP server repository=%s", REPO_PATH)
    mcp.run()
