from __future__ import annotations

from collections.abc import Hashable
import time


class TTLCache:
    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._store: dict[Hashable, tuple[float, object]] = {}

    def get(self, key: Hashable, default: object = None) -> object:
        item = self._store.get(key)
        if item is None:
            return default
        expires_at, value = item
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return default
        return value

    def set(self, key: Hashable, value: object) -> None:
        self._store[key] = (time.monotonic() + self.ttl_seconds, value)

