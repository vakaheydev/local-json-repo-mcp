from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from copy import deepcopy
from functools import wraps
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Callable


logger = logging.getLogger(__name__)


class CacheStorage(ABC):
    """Storage-independent cache backend contract."""

    @abstractmethod
    def get(self, key: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def set(self, key: str, entry: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        raise NotImplementedError


class MemoryCacheStorage(CacheStorage):
    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
            return deepcopy(entry) if entry is not None else None

    def set(self, key: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = deepcopy(entry)

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class FileCacheStorage(CacheStorage):
    """Persistent JSON-file cache storage.

    The whole cache is kept in memory while the MCP process is alive and is
    persisted atomically after mutations. Cached MCP results must therefore be
    JSON-serializable, which is already true for this server's tool results.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._entries = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read cache file %s; starting with empty cache", self.path)
            return {}

        if not isinstance(data, dict):
            logger.warning("Cache file root is not an object: %s", self.path)
            return {}

        return data

    def _persist(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._entries, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
            return deepcopy(entry) if entry is not None else None

    def set(self, key: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = deepcopy(entry)
            self._persist()

    def delete(self, key: str) -> None:
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                self._persist()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._persist()


class CacheManager:
    def __init__(
        self,
        storage: CacheStorage,
        *,
        enabled: bool,
        default_ttl_seconds: int,
        ttl_by_operation: dict[str, int] | None = None,
        disabled_operations: list[str] | None = None,
        namespace: str = "default",
    ) -> None:
        self.storage = storage
        self.enabled = enabled
        self.default_ttl_seconds = default_ttl_seconds
        self.ttl_by_operation = ttl_by_operation or {}
        self.disabled_operations = set(disabled_operations or [])
        self.namespace = namespace

    def ttl_for(self, operation: str) -> int:
        return self.ttl_by_operation.get(operation, self.default_ttl_seconds)

    def should_cache(self, operation: str) -> bool:
        return (
            self.enabled
            and operation not in self.disabled_operations
            and self.ttl_for(operation) > 0
        )

    def _key(self, operation: str, params: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "namespace": self.namespace,
                "operation": operation,
                "params": params,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        digest = sha256(payload.encode("utf-8")).hexdigest()
        return f"{operation}:{digest}"

    def get_or_compute(
        self,
        operation: str,
        params: dict[str, Any],
        producer: Callable[[], Any],
    ) -> Any:
        if not self.should_cache(operation):
            logger.debug("Cache BYPASS operation=%s", operation)
            return producer()

        key = self._key(operation, params)
        now = time.time()
        entry = self.storage.get(key)

        if entry is not None:
            expires_at = entry.get("expires_at")
            if isinstance(expires_at, (int, float)) and expires_at > now:
                logger.info("Cache HIT operation=%s", operation)
                return deepcopy(entry.get("value"))

            self.storage.delete(key)
            logger.info("Cache EXPIRED operation=%s", operation)
        else:
            logger.info("Cache MISS operation=%s", operation)

        value = producer()
        ttl = self.ttl_for(operation)
        self.storage.set(
            key,
            {
                "operation": operation,
                "created_at": now,
                "expires_at": now + ttl,
                "value": value,
            },
        )
        return deepcopy(value)

    def clear(self) -> None:
        self.storage.clear()


def build_cache_manager(
    config: dict[str, Any] | None,
    *,
    base_dir: str | Path,
    namespace: str,
) -> CacheManager:
    config = config or {}

    enabled = config.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("cache.enabled must be boolean")

    default_ttl = config.get("default_ttl_seconds", 300)
    if not isinstance(default_ttl, int) or default_ttl < 0:
        raise ValueError("cache.default_ttl_seconds must be a non-negative integer")

    ttl_by_operation = config.get("ttl_by_operation", {})
    if not isinstance(ttl_by_operation, dict) or not all(
        isinstance(name, str)
        and isinstance(ttl, int)
        and ttl >= 0
        for name, ttl in ttl_by_operation.items()
    ):
        raise ValueError("cache.ttl_by_operation must map operation names to non-negative integers")

    disabled_operations = config.get("disabled_operations", [])
    if not isinstance(disabled_operations, list) or not all(
        isinstance(name, str) and name for name in disabled_operations
    ):
        raise ValueError("cache.disabled_operations must be an array of strings")

    storage_config = config.get("storage", {"type": "ram"})
    if not isinstance(storage_config, dict):
        raise ValueError("cache.storage must be an object")

    storage_type = storage_config.get("type", "ram")

    if storage_type == "ram":
        storage: CacheStorage = MemoryCacheStorage()
    elif storage_type == "file":
        file_config = storage_config.get("file", {})
        if not isinstance(file_config, dict):
            raise ValueError("cache.storage.file must be an object")

        raw_path = file_config.get("path", "./cache/cache.json")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("cache.storage.file.path must be a non-empty string")

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path(base_dir) / path

        storage = FileCacheStorage(path)
    else:
        raise ValueError("cache.storage.type must be either 'ram' or 'file'")

    return CacheManager(
        storage,
        enabled=enabled,
        default_ttl_seconds=default_ttl,
        ttl_by_operation=ttl_by_operation,
        disabled_operations=disabled_operations,
        namespace=namespace,
    )


def cached(cache_manager: CacheManager):
    """Decorator that caches function results by function name + arguments."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return cache_manager.get_or_compute(
                func.__name__,
                {"args": args, "kwargs": kwargs},
                lambda: func(*args, **kwargs),
            )

        return wrapper

    return decorator
