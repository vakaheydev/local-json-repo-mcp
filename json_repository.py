from __future__ import annotations

import fnmatch
import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

_MISSING = object()


def resolve_repository_path(repository: str | Path) -> Path:
    repository_path = Path(repository).expanduser().resolve()

    if not repository_path.exists():
        raise FileNotFoundError(f"Repository does not exist: {repository_path}")

    if not repository_path.is_dir():
        raise NotADirectoryError(
            f"Repository path is not a directory: {repository_path}"
        )

    return repository_path


def resolve_safe_path(repository: str | Path, path: str | Path) -> Path:
    repository_path = resolve_repository_path(repository)
    candidate = Path(path)

    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (repository_path / candidate).resolve()

    if not resolved.is_relative_to(repository_path):
        raise ValueError(f"Path is outside repository: {path}")

    return resolved


def relative_path(repository: str | Path, path: str | Path) -> str:
    repository_path = resolve_repository_path(repository)
    file_path = resolve_safe_path(repository_path, path)
    return file_path.relative_to(repository_path).as_posix()


def normalize_patterns(
    patterns: str | Iterable[str] | None,
    default: str = "**/*",
) -> list[str]:
    if patterns is None:
        return [default]

    if isinstance(patterns, str):
        return [patterns]

    return list(patterns)


def find_files(
    repository: str | Path,
    patterns: str | Iterable[str] | None = None,
    *,
    exclude_patterns: str | Iterable[str] | None = None,
) -> list[Path]:
    repository_path = resolve_repository_path(repository)
    include_patterns = normalize_patterns(patterns)
    excludes = (
        normalize_patterns(exclude_patterns, default="")
        if exclude_patterns
        else []
    )

    files: set[Path] = set()

    for pattern in include_patterns:
        for path in repository_path.glob(pattern):
            if not path.is_file():
                continue

            relative = path.relative_to(repository_path).as_posix()

            if any(fnmatch.fnmatch(relative, exclude) for exclude in excludes):
                continue

            files.add(path.resolve())

    return sorted(files)


def find_json_files(
    repository: str | Path,
    patterns: str | Iterable[str] | None = None,
    *,
    exclude_patterns: str | Iterable[str] | None = None,
) -> list[Path]:
    patterns = patterns or "**/*.json"
    return [
        path
        for path in find_files(
            repository,
            patterns,
            exclude_patterns=exclude_patterns,
        )
        if path.suffix.lower() == ".json"
    ]


def find_files_by_name(
    repository: str | Path,
    name_pattern: str,
    *,
    patterns: str | Iterable[str] | None = None,
) -> list[Path]:
    files = find_files(repository, patterns)
    return [path for path in files if fnmatch.fnmatch(path.name, name_pattern)]


def read_text_file(
    repository: str | Path,
    path: str | Path,
    *,
    encoding: str = "utf-8",
) -> str:
    file_path = resolve_safe_path(repository, path)

    if not file_path.exists():
        raise FileNotFoundError(file_path)

    if not file_path.is_file():
        raise IsADirectoryError(file_path)

    return file_path.read_text(encoding=encoding)


def try_read_text_file(
    repository: str | Path,
    path: str | Path,
    *,
    encoding: str = "utf-8",
) -> str | None:
    try:
        return read_text_file(repository, path, encoding=encoding)
    except (OSError, UnicodeDecodeError):
        logger.exception("Failed to read file: %s", path)
        return None


def read_lines(
    repository: str | Path,
    path: str | Path,
    *,
    encoding: str = "utf-8",
) -> list[str]:
    return read_text_file(repository, path, encoding=encoding).splitlines()


def load_json_file(repository: str | Path, path: str | Path) -> Any:
    text = read_text_file(repository, path)
    return json.loads(text)


def try_load_json_file(repository: str | Path, path: str | Path) -> Any | None:
    try:
        return load_json_file(repository, path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("Failed to load JSON file: %s", path)
        return None


def load_json_path(path: str | Path) -> Any:
    path = Path(path)
    with path.open(mode="r", encoding="utf-8") as file:
        return json.load(file)


def try_load_json_path(path: str | Path) -> Any | None:
    try:
        return load_json_path(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("Failed to load JSON: %s", path)
        return None


def get_json_value(data: Any, path: str, default: Any = None) -> Any:
    if not path:
        return data

    current = data

    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
            continue

        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return default

            if index < 0 or index >= len(current):
                return default

            current = current[index]
            continue

        return default

    return current


def json_path_exists(data: Any, path: str) -> bool:
    return get_json_value(data, path, _MISSING) is not _MISSING


def walk_json(data: Any, path: str = ""):
    yield path, data

    if isinstance(data, dict):
        for key, value in data.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from walk_json(value, child_path)

    elif isinstance(data, list):
        for index, value in enumerate(data):
            child_path = f"{path}.{index}" if path else str(index)
            yield from walk_json(value, child_path)


def find_json_values_recursive(
    data: Any,
    expected_value: Any,
) -> list[dict[str, Any]]:
    results = []

    for path, value in walk_json(data):
        if value == expected_value:
            results.append({"json_path": path, "value": value})

    return results


def find_json_keys_recursive(
    data: Any,
    key: str,
    *,
    case_sensitive: bool = True,
) -> list[dict[str, Any]]:
    results = []
    expected = key if case_sensitive else key.lower()

    for path, value in walk_json(data):
        if not isinstance(value, dict):
            continue

        for current_key, current_value in value.items():
            candidate = current_key if case_sensitive else current_key.lower()

            if candidate != expected:
                continue

            result_path = f"{path}.{current_key}" if path else current_key
            results.append(
                {
                    "json_path": result_path,
                    "value": current_value,
                }
            )

    return results


def string_equals(
    value: Any,
    query: str,
    *,
    case_sensitive: bool = False,
) -> bool:
    if not isinstance(value, str):
        return False

    if case_sensitive:
        return value == query

    return value.casefold() == query.casefold()


def string_contains(
    value: Any,
    query: str,
    *,
    case_sensitive: bool = False,
) -> bool:
    if not isinstance(value, str):
        return False

    if case_sensitive:
        return query in value

    return query.casefold() in value.casefold()


def string_starts_with(
    value: Any,
    query: str,
    *,
    case_sensitive: bool = False,
) -> bool:
    if not isinstance(value, str):
        return False

    if case_sensitive:
        return value.startswith(query)

    return value.casefold().startswith(query.casefold())


def string_ends_with(
    value: Any,
    query: str,
    *,
    case_sensitive: bool = False,
) -> bool:
    if not isinstance(value, str):
        return False

    if case_sensitive:
        return value.endswith(query)

    return value.casefold().endswith(query.casefold())


def find_json_documents(
    repository: str | Path,
    patterns: str | Iterable[str] | None = None,
    *,
    predicate: Callable[[Any], bool] | None = None,
    exclude_patterns: str | Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    repository_path = resolve_repository_path(repository)
    results = []

    files = find_json_files(
        repository_path,
        patterns,
        exclude_patterns=exclude_patterns,
    )

    for file_path in files:
        data = try_load_json_path(file_path)

        if data is None:
            continue

        if predicate is not None and not predicate(data):
            continue

        results.append(
            {
                "path": file_path.relative_to(repository_path).as_posix(),
                "data": data,
            }
        )

    return results


def find_by_json_value(
    repository: str | Path,
    json_path: str,
    expected_value: Any,
    *,
    patterns: str | Iterable[str] | None = None,
    exclude_patterns: str | Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    return find_json_documents(
        repository,
        patterns,
        exclude_patterns=exclude_patterns,
        predicate=lambda data: (
            get_json_value(data, json_path, _MISSING) == expected_value
        ),
    )


def find_by_any_json_value(
    repository: str | Path,
    expected_value: Any,
    *,
    patterns: str | Iterable[str] | None = None,
    exclude_patterns: str | Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    repository_path = resolve_repository_path(repository)
    results = []

    for file_path in find_json_files(
        repository_path,
        patterns,
        exclude_patterns=exclude_patterns,
    ):
        data = try_load_json_path(file_path)

        if data is None:
            continue

        matches = find_json_values_recursive(data, expected_value)

        if matches:
            results.append(
                {
                    "path": file_path.relative_to(repository_path).as_posix(),
                    "matches": matches,
                }
            )

    return results


def find_by_json_string(
    repository: str | Path,
    json_path: str,
    query: str,
    *,
    patterns: str | Iterable[str] | None = None,
    case_sensitive: bool = False,
    exclude_patterns: str | Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    def predicate(data: Any) -> bool:
        value = get_json_value(data, json_path, _MISSING)
        return string_contains(value, query, case_sensitive=case_sensitive)

    return find_json_documents(
        repository,
        patterns,
        predicate=predicate,
        exclude_patterns=exclude_patterns,
    )


def find_by_json_string_prefix(
    repository: str | Path,
    json_path: str,
    prefix: str,
    *,
    patterns: str | Iterable[str] | None = None,
    case_sensitive: bool = False,
) -> list[dict[str, Any]]:
    return find_json_documents(
        repository,
        patterns,
        predicate=lambda data: string_starts_with(
            get_json_value(data, json_path, _MISSING),
            prefix,
            case_sensitive=case_sensitive,
        ),
    )


def find_by_json_string_suffix(
    repository: str | Path,
    json_path: str,
    suffix: str,
    *,
    patterns: str | Iterable[str] | None = None,
    case_sensitive: bool = False,
) -> list[dict[str, Any]]:
    return find_json_documents(
        repository,
        patterns,
        predicate=lambda data: string_ends_with(
            get_json_value(data, json_path, _MISSING),
            suffix,
            case_sensitive=case_sensitive,
        ),
    )


def find_string_anywhere_in_json(
    repository: str | Path,
    query: str,
    *,
    patterns: str | Iterable[str] | None = None,
    case_sensitive: bool = False,
    include_keys: bool = False,
    exclude_patterns: str | Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    repository_path = resolve_repository_path(repository)
    results = []

    for file_path in find_json_files(
        repository_path,
        patterns,
        exclude_patterns=exclude_patterns,
    ):
        data = try_load_json_path(file_path)

        if data is None:
            continue

        matches = []

        for json_path, value in walk_json(data):
            if string_contains(value, query, case_sensitive=case_sensitive):
                matches.append(
                    {
                        "json_path": json_path,
                        "value": value,
                    }
                )

            if include_keys and isinstance(value, dict):
                for key in value:
                    if string_contains(key, query, case_sensitive=case_sensitive):
                        key_path = f"{json_path}.{key}" if json_path else key
                        matches.append(
                            {
                                "json_path": key_path,
                                "key": key,
                            }
                        )

        if matches:
            results.append(
                {
                    "path": file_path.relative_to(repository_path).as_posix(),
                    "matches": matches,
                }
            )

    return results


def find_text_in_files(
    repository: str | Path,
    query: str,
    *,
    patterns: str | Iterable[str] | None = None,
    case_sensitive: bool = False,
    exclude_patterns: str | Iterable[str] | None = None,
    max_matches_per_file: int | None = None,
) -> list[dict[str, Any]]:
    repository_path = resolve_repository_path(repository)
    results = []

    files = find_files(
        repository_path,
        patterns,
        exclude_patterns=exclude_patterns,
    )

    search_query = query if case_sensitive else query.casefold()

    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue

        matches = []

        for line_number, line in enumerate(lines, start=1):
            candidate = line if case_sensitive else line.casefold()

            if search_query not in candidate:
                continue

            matches.append(
                {
                    "line": line_number,
                    "text": line,
                }
            )

            if (
                max_matches_per_file is not None
                and len(matches) >= max_matches_per_file
            ):
                break

        if matches:
            results.append(
                {
                    "path": file_path.relative_to(repository_path).as_posix(),
                    "matches": matches,
                }
            )

    return results


def json_array_contains(data: Any, json_path: str, expected_value: Any) -> bool:
    value = get_json_value(data, json_path, _MISSING)
    return isinstance(value, list) and expected_value in value


def find_by_json_array_value(
    repository: str | Path,
    json_path: str,
    expected_value: Any,
    *,
    patterns: str | Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    return find_json_documents(
        repository,
        patterns,
        predicate=lambda data: json_array_contains(
            data,
            json_path,
            expected_value,
        ),
    )


def find_by_json_array_string(
    repository: str | Path,
    json_path: str,
    query: str,
    *,
    patterns: str | Iterable[str] | None = None,
    case_sensitive: bool = False,
) -> list[dict[str, Any]]:
    def predicate(data: Any) -> bool:
        value = get_json_value(data, json_path, _MISSING)

        if not isinstance(value, list):
            return False

        return any(
            string_contains(item, query, case_sensitive=case_sensitive)
            for item in value
        )

    return find_json_documents(repository, patterns, predicate=predicate)


def select_json_fields(data: Any, fields: Iterable[str]) -> dict[str, Any]:
    result = {}

    for field in fields:
        value = get_json_value(data, field, _MISSING)

        if value is not _MISSING:
            result[field] = value

    return result


def summarize_json_documents(
    documents: Iterable[dict[str, Any]],
    fields: Iterable[str],
) -> list[dict[str, Any]]:
    results = []

    for document in documents:
        result = {"path": document["path"]}
        result.update(select_json_fields(document["data"], fields))
        results.append(result)

    return results


def get_json(repository: str | Path, path: str | Path) -> Any:
    return load_json_file(repository, path)


def get_file(repository: str | Path, path: str | Path) -> str:
    return read_text_file(repository, path)
