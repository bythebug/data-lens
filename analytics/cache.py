"""
In-process query result cache: TTL + LRU eviction, per-dataset invalidation,
and a cache-warming utility.

Thread-safe via threading.Lock. For multi-worker deployments, swap the backend
for Redis (redis-py) — the public interface stays identical.
"""

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class QueryCache:
    """
    TTL + LRU cache keyed by (dataset_id, query_hash).

    Each entry stores: (value, expires_at_monotonic, dataset_id).
    The dataset_id is kept so invalidate_dataset() can sweep in O(n) without
    a secondary index.
    """

    def __init__(self, maxsize: int = 256, default_ttl: float = 300.0) -> None:
        self._store: OrderedDict[str, tuple[Any, float, int]] = OrderedDict()
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    # ── internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _key(dataset_id: int, query_hash: str) -> str:
        return f"{dataset_id}:{query_hash}"

    def _evict_expired(self) -> None:
        """Remove all expired entries (called under lock)."""
        now = time.monotonic()
        stale = [k for k, (_, exp, _) in self._store.items() if exp <= now]
        for k in stale:
            del self._store[k]

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, dataset_id: int, query_hash: str) -> tuple[bool, Any]:
        """Return (hit, value). hit=False means cache miss or expired entry."""
        key = self._key(dataset_id, query_hash)
        with self._lock:
            if key not in self._store:
                self.misses += 1
                return False, None
            value, expires_at, _ = self._store[key]
            if time.monotonic() > expires_at:
                del self._store[key]
                self.misses += 1
                return False, None
            self._store.move_to_end(key)
            self.hits += 1
            return True, value

    def set(
        self,
        dataset_id: int,
        query_hash: str,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        key = self._key(dataset_id, query_hash)
        expires_at = time.monotonic() + (ttl if ttl is not None else self.default_ttl)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expires_at, dataset_id)
            while len(self._store) > self.maxsize:
                evicted_key, _ = self._store.popitem(last=False)
                logger.debug("Cache LRU eviction: %s", evicted_key)

    def invalidate_dataset(self, dataset_id: int) -> int:
        """
        Remove all entries belonging to dataset_id.
        Call after any write that changes the dataset (new rows, schema update).
        Returns the number of entries removed.
        """
        with self._lock:
            keys = [k for k, (_, _, ds) in self._store.items() if ds == dataset_id]
            for k in keys:
                del self._store[k]
        if keys:
            logger.debug("Cache invalidated %d entries for dataset %d", len(keys), dataset_id)
        return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "size": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ── Module-level singleton ────────────────────────────────────────────────────

query_cache = QueryCache()


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_query_hash(params: dict[str, Any]) -> str:
    """
    Deterministic 16-character hex hash of a params dict.
    Suitable as a cache key suffix alongside dataset_id.
    """
    serialized = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def cache_query_result(
    dataset_id: int,
    query_hash: str,
    fn: Callable[[], Any],
    ttl: float = 300.0,
    _cache: QueryCache | None = None,
) -> Any:
    """
    Return a cached result if available; otherwise call fn(), store, and return.

    _cache: injectable for testing (defaults to the module singleton).
    """
    cache = _cache if _cache is not None else query_cache
    hit, value = cache.get(dataset_id, query_hash)
    if hit:
        logger.debug("Cache hit  ds=%d key=%s", dataset_id, query_hash)
        return value
    logger.debug("Cache miss ds=%d key=%s", dataset_id, query_hash)
    result = fn()
    cache.set(dataset_id, query_hash, result, ttl=ttl)
    return result


def warm_cache(
    dataset_id: int,
    warm_fns: dict[str, Callable[[], Any]],
    ttl: float = 300.0,
    _cache: QueryCache | None = None,
) -> int:
    """
    Pre-populate cache with results of common queries.

    warm_fns: mapping of query_hash → zero-arg callable that returns the result.
    Silently skips any callable that raises an exception (warming is non-fatal).
    Returns the number of new entries stored.
    """
    cache = _cache if _cache is not None else query_cache
    warmed = 0
    for query_hash, fn in warm_fns.items():
        hit, _ = cache.get(dataset_id, query_hash)
        if hit:
            continue
        try:
            result = fn()
            cache.set(dataset_id, query_hash, result, ttl=ttl)
            warmed += 1
        except Exception as exc:
            logger.warning("Cache warm failed ds=%d key=%s: %s", dataset_id, query_hash, exc)
    logger.info("Warmed %d cache entries for dataset %d", warmed, dataset_id)
    return warmed
