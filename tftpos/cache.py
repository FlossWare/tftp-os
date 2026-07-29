"""TTL cache decorator and cache statistics for TftpOS."""

from __future__ import annotations

import functools
import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("tftpos.cache")


class CacheStats:
    """Thread-safe cache hit/miss statistics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def hit(self) -> None:
        with self._lock:
            self._hits += 1

    def miss(self) -> None:
        with self._lock:
            self._misses += 1

    def eviction(self) -> None:
        with self._lock:
            self._evictions += 1

    @property
    def hits(self) -> int:
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        with self._lock:
            return self._misses

    @property
    def evictions(self) -> int:
        with self._lock:
            return self._evictions

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "total": total,
                "hit_rate": (
                    round(self._hits / total, 4) if total > 0 else 0.0
                ),
            }

    def reset(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0


# Global registry of all TTL caches for centralized stats/clearing
_cache_registry: Dict[str, "TTLCacheWrapper"] = {}
_registry_lock = threading.Lock()

# Global stats
_global_stats = CacheStats()


class TTLCacheWrapper:
    """A cache wrapper with TTL expiration and size limits."""

    def __init__(
        self,
        name: str,
        maxsize: int = 128,
        ttl: float = 300.0,
    ) -> None:
        self.name = name
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: Dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._stats = CacheStats()

    def get(self, key: Any) -> Optional[Any]:
        """Get a value from cache. Returns None if not found or expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats.miss()
                _global_stats.miss()
                return None

            timestamp, value = entry
            if time.monotonic() - timestamp > self.ttl:
                del self._cache[key]
                self._stats.eviction()
                self._stats.miss()
                _global_stats.eviction()
                _global_stats.miss()
                return None

            self._stats.hit()
            _global_stats.hit()
            return value

    def put(self, key: Any, value: Any) -> None:
        """Store a value in cache."""
        with self._lock:
            # Evict oldest if at capacity
            if len(self._cache) >= self.maxsize and key not in self._cache:
                oldest_key = min(
                    self._cache, key=lambda k: self._cache[k][0]
                )
                del self._cache[oldest_key]
                self._stats.eviction()
                _global_stats.eviction()

            self._cache[key] = (time.monotonic(), value)

    def invalidate(self, key: Any) -> bool:
        """Remove a specific key from cache. Returns True if removed."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._stats.eviction()
                _global_stats.eviction()
                return True
            return False

    def invalidate_matching(self, predicate: Callable[[Any], bool]) -> int:
        """Remove all entries whose key satisfies *predicate*.

        The scan and removal are performed atomically under the
        cache lock so no concurrent modification can intervene.

        Returns the number of entries removed.
        """
        removed = 0
        with self._lock:
            to_remove = [k for k in self._cache if predicate(k)]
            for key in to_remove:
                del self._cache[key]
                self._stats.eviction()
                _global_stats.eviction()
                removed += 1
        return removed

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        """Current number of entries in cache."""
        with self._lock:
            return len(self._cache)

    @property
    def stats(self) -> Dict[str, Any]:
        result = self._stats.to_dict()
        result["name"] = self.name
        with self._lock:
            result["size"] = len(self._cache)
        result["maxsize"] = self.maxsize
        result["ttl"] = self.ttl
        return result


_SENTINEL = object()


def ttl_cache(
    maxsize: int = 128,
    ttl: float = 300.0,
    name: Optional[str] = None,
) -> Callable:
    """Decorator for TTL-based caching.

    Usage::

        @ttl_cache(maxsize=128, ttl=300)
        def load_profile(path: str) -> Profile:
            ...

    Arguments must be hashable. The cache automatically expires
    entries after ``ttl`` seconds.
    """

    def decorator(func: Callable) -> Callable:
        cache_name = name or func.__qualname__
        wrapper = TTLCacheWrapper(cache_name, maxsize, ttl)

        with _registry_lock:
            _cache_registry[cache_name] = wrapper

        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            # Build hashable key from args + sorted kwargs
            key = (args, tuple(sorted(kwargs.items())))
            result = wrapper.get(key)
            if result is not _SENTINEL and result is not None:
                return result
            # Re-check with sentinel for None values
            with wrapper._lock:
                entry = wrapper._cache.get(key)
                if entry is not None:
                    timestamp, value = entry
                    if time.monotonic() - timestamp <= wrapper.ttl:
                        return value

            result = func(*args, **kwargs)
            wrapper.put(key, result)
            return result

        wrapped.cache = wrapper
        wrapped.cache_clear = wrapper.clear
        return wrapped

    return decorator


def profile_cache_key(profile_path: str) -> str:
    """Compute a content-based hash for a profile file.

    Returns a hex digest of the file contents so cache entries
    can be keyed on (mac, profile_hash) and automatically
    invalidated when the underlying profile changes on disk.
    """
    try:
        data = Path(profile_path).read_bytes()
        return hashlib.sha256(data).hexdigest()[:16]
    except (OSError, IOError):
        return "missing"


def get_all_cache_stats() -> Dict[str, Any]:
    """Return statistics for all registered TTL caches."""
    with _registry_lock:
        caches = {
            name: wrapper.stats
            for name, wrapper in _cache_registry.items()
        }
    return {
        "global": _global_stats.to_dict(),
        "caches": caches,
    }


def clear_all_caches() -> int:
    """Clear all registered caches. Returns number of caches cleared."""
    with _registry_lock:
        count = len(_cache_registry)
        for wrapper in _cache_registry.values():
            wrapper.clear()
    _global_stats.reset()
    return count


def get_cache(name: str) -> Optional[TTLCacheWrapper]:
    """Look up a registered cache by name."""
    with _registry_lock:
        return _cache_registry.get(name)
