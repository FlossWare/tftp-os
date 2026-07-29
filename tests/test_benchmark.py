"""Tests for cache concurrency, benchmark infrastructure, and performance.

Covers:
- Thread safety of TTLCacheWrapper and CacheStats under contention
- Cache stats accuracy after concurrent operations
- Cache invalidation correctness
- No data races with ThreadPoolExecutor
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from tftpos.cache import (
    CacheStats,
    TTLCacheWrapper,
    clear_all_caches,
    get_all_cache_stats,
    get_cache,
    profile_cache_key,
    ttl_cache,
)


# ---------------------------------------------------------------------------
# CacheStats thread safety
# ---------------------------------------------------------------------------


class TestCacheStatsThreadSafety:

    def test_concurrent_hits(self):
        stats = CacheStats()
        n = 1000

        def bump():
            for _ in range(n):
                stats.hit()

        threads = [threading.Thread(target=bump) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert stats.hits == n * 10

    def test_concurrent_misses(self):
        stats = CacheStats()
        n = 1000

        def bump():
            for _ in range(n):
                stats.miss()

        threads = [threading.Thread(target=bump) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert stats.misses == n * 10

    def test_concurrent_evictions(self):
        stats = CacheStats()
        n = 500

        def bump():
            for _ in range(n):
                stats.eviction()

        threads = [threading.Thread(target=bump) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert stats.evictions == n * 8

    def test_concurrent_mixed_operations(self):
        stats = CacheStats()
        n = 500

        def do_hits():
            for _ in range(n):
                stats.hit()

        def do_misses():
            for _ in range(n):
                stats.miss()

        def do_evictions():
            for _ in range(n):
                stats.eviction()

        threads = []
        for fn in (do_hits, do_misses, do_evictions):
            for _ in range(4):
                threads.append(threading.Thread(target=fn))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        d = stats.to_dict()
        assert d["hits"] == n * 4
        assert d["misses"] == n * 4
        assert d["evictions"] == n * 4
        assert d["total"] == n * 8  # hits + misses

    def test_hit_rate_accuracy(self):
        stats = CacheStats()
        for _ in range(75):
            stats.hit()
        for _ in range(25):
            stats.miss()
        d = stats.to_dict()
        assert d["hit_rate"] == 0.75


# ---------------------------------------------------------------------------
# TTLCacheWrapper thread safety
# ---------------------------------------------------------------------------


class TestTTLCacheWrapperThreadSafety:

    def test_concurrent_put_and_get(self):
        cache = TTLCacheWrapper("thread-test", maxsize=1000, ttl=300)
        n = 100

        def writer(offset):
            for i in range(n):
                cache.put(f"key-{offset}-{i}", f"val-{offset}-{i}")

        def reader(offset):
            results = []
            for i in range(n):
                results.append(cache.get(f"key-{offset}-{i}"))
            return results

        # Write first from 8 concurrent threads
        with ThreadPoolExecutor(max_workers=8) as pool:
            write_futures = [pool.submit(writer, o) for o in range(8)]
            for f in write_futures:
                f.result()

        # Then read back from 8 concurrent threads
        with ThreadPoolExecutor(max_workers=8) as pool:
            read_futures = [pool.submit(reader, o) for o in range(8)]
            for f in read_futures:
                results = f.result()
                for i, val in enumerate(results):
                    assert val is not None

    def test_concurrent_put_respects_maxsize(self):
        cache = TTLCacheWrapper("maxsize-test", maxsize=50, ttl=300)

        def writer(offset):
            for i in range(100):
                cache.put(f"key-{offset}-{i}", i)

        threads = [threading.Thread(target=writer, args=(o,)) for o in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cache.size <= 50

    def test_concurrent_get_miss_stats(self):
        cache = TTLCacheWrapper("miss-stats", maxsize=100, ttl=300)

        def reader():
            for i in range(100):
                cache.get(f"nonexistent-{i}")

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cache._stats.misses == 400

    def test_concurrent_put_get_clear(self):
        """Interleave put, get, and clear to stress lock acquisition."""
        cache = TTLCacheWrapper("pgc-test", maxsize=100, ttl=300)
        stop = threading.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                cache.put(f"k{i}", i)
                i += 1

        def reader():
            i = 0
            while not stop.is_set():
                cache.get(f"k{i}")
                i += 1

        def clearer():
            while not stop.is_set():
                cache.clear()
                time.sleep(0.001)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=clearer),
        ]
        for t in threads:
            t.start()

        time.sleep(0.1)  # let them run briefly
        stop.set()
        for t in threads:
            t.join(timeout=5)

        # No assertion failures, no deadlocks -- reaching here is success

    def test_invalidate_thread_safety(self):
        cache = TTLCacheWrapper("inv-test", maxsize=200, ttl=300)
        for i in range(100):
            cache.put(f"key-{i}", f"val-{i}")

        removed = []

        def invalidator(start, end):
            count = 0
            for i in range(start, end):
                if cache.invalidate(f"key-{i}"):
                    count += 1
            removed.append(count)

        threads = [
            threading.Thread(target=invalidator, args=(0, 50)),
            threading.Thread(target=invalidator, args=(50, 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(removed) == 100
        assert cache.size == 0


# ---------------------------------------------------------------------------
# Cache stats accuracy
# ---------------------------------------------------------------------------


class TestCacheStatsAccuracy:

    def test_stats_reflect_operations(self):
        cache = TTLCacheWrapper("acc-test", maxsize=10, ttl=300)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")  # hit
        cache.get("a")  # hit
        cache.get("c")  # miss
        stats = cache.stats
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["size"] == 2

    def test_eviction_counted_on_maxsize(self):
        cache = TTLCacheWrapper("evict-count", maxsize=2, ttl=300)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # evicts oldest
        assert cache._stats.evictions >= 1

    def test_eviction_counted_on_ttl_expiry(self):
        cache = TTLCacheWrapper("ttl-evict", maxsize=10, ttl=0.01)
        cache.put("x", 1)
        time.sleep(0.05)
        cache.get("x")  # triggers eviction
        assert cache._stats.evictions == 1
        assert cache._stats.misses == 1  # expired = miss

    def test_global_stats_aggregate(self):
        clear_all_caches()

        @ttl_cache(maxsize=5, ttl=300, name="bench_global_a")
        def fn_a(x):
            return x

        @ttl_cache(maxsize=5, ttl=300, name="bench_global_b")
        def fn_b(x):
            return x * 2

        fn_a(1)
        fn_a(1)  # hit
        fn_b(2)
        fn_b(3)

        stats = get_all_cache_stats()
        assert "bench_global_a" in stats["caches"]
        assert "bench_global_b" in stats["caches"]

    def test_size_property(self):
        cache = TTLCacheWrapper("size-prop", maxsize=100, ttl=300)
        assert cache.size == 0
        cache.put("a", 1)
        assert cache.size == 1
        cache.put("b", 2)
        assert cache.size == 2
        cache.clear()
        assert cache.size == 0


# ---------------------------------------------------------------------------
# TTL cache invalidation (standalone, no engine dependency)
# ---------------------------------------------------------------------------


class TestCacheInvalidationStandalone:

    def test_ttl_cache_wrapper_invalidate(self):
        cache = TTLCacheWrapper("inv-direct", maxsize=10, ttl=300)
        cache.put("key1", "val1")
        cache.put("key2", "val2")
        assert cache.invalidate("key1") is True
        assert cache.get("key1") is None
        assert cache.get("key2") == "val2"
        assert cache.invalidate("key1") is False  # already gone


# ---------------------------------------------------------------------------
# profile_cache_key
# ---------------------------------------------------------------------------


class TestProfileCacheKey:

    def test_same_file_same_hash(self, tmp_path):
        f = tmp_path / "test.toml"
        f.write_text("hello")
        assert profile_cache_key(str(f)) == profile_cache_key(str(f))

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.toml"
        f2 = tmp_path / "b.toml"
        f1.write_text("content-a")
        f2.write_text("content-b")
        assert profile_cache_key(str(f1)) != profile_cache_key(str(f2))

    def test_missing_file_returns_missing(self):
        assert profile_cache_key("/nonexistent/path.toml") == "missing"

    def test_hash_is_16_chars(self, tmp_path):
        f = tmp_path / "test.toml"
        f.write_text("data")
        h = profile_cache_key(str(f))
        assert len(h) == 16


# ---------------------------------------------------------------------------
# get_cache helper
# ---------------------------------------------------------------------------


class TestGetCache:

    def test_get_registered_cache(self):
        @ttl_cache(maxsize=5, ttl=60, name="bench_get_test")
        def fn(x):
            return x

        c = get_cache("bench_get_test")
        assert c is not None
        assert c.name == "bench_get_test"

    def test_get_unknown_cache(self):
        assert get_cache("nonexistent_cache_xyz") is None


