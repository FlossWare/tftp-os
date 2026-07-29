"""Tests for tftpos.cache -- TTL cache decorator and statistics."""

from __future__ import annotations

import time

import pytest

from tftpos.cache import (
    CacheStats,
    TTLCacheWrapper,
    clear_all_caches,
    get_all_cache_stats,
    ttl_cache,
)


# ---------------------------------------------------------------------------
# CacheStats
# ---------------------------------------------------------------------------


class TestCacheStats:

    def test_initial_state(self):
        stats = CacheStats()
        d = stats.to_dict()
        assert d["hits"] == 0
        assert d["misses"] == 0
        assert d["evictions"] == 0
        assert d["total"] == 0
        assert d["hit_rate"] == 0.0

    def test_hit_tracking(self):
        stats = CacheStats()
        stats.hit()
        stats.hit()
        stats.miss()
        d = stats.to_dict()
        assert d["hits"] == 2
        assert d["misses"] == 1
        assert d["total"] == 3
        assert abs(d["hit_rate"] - 2 / 3) < 0.001

    def test_reset(self):
        stats = CacheStats()
        stats.hit()
        stats.miss()
        stats.eviction()
        stats.reset()
        d = stats.to_dict()
        assert d["hits"] == 0
        assert d["misses"] == 0
        assert d["evictions"] == 0


# ---------------------------------------------------------------------------
# TTLCacheWrapper
# ---------------------------------------------------------------------------


class TestTTLCacheWrapper:

    def test_put_and_get(self):
        cache = TTLCacheWrapper("test", maxsize=10, ttl=300)
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_returns_none(self):
        cache = TTLCacheWrapper("test", maxsize=10, ttl=300)
        assert cache.get("nonexistent") is None

    def test_ttl_expiration(self):
        cache = TTLCacheWrapper("test", maxsize=10, ttl=0.05)
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"
        time.sleep(0.1)
        assert cache.get("key1") is None

    def test_maxsize_eviction(self):
        cache = TTLCacheWrapper("test", maxsize=2, ttl=300)
        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.put("key3", "value3")  # should evict key1
        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"
        assert cache.get("key3") == "value3"

    def test_clear(self):
        cache = TTLCacheWrapper("test", maxsize=10, ttl=300)
        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_stats(self):
        cache = TTLCacheWrapper("test-stats", maxsize=10, ttl=300)
        cache.put("key1", "value1")
        cache.get("key1")  # hit
        cache.get("key1")  # hit
        cache.get("missing")  # miss

        stats = cache.stats
        assert stats["name"] == "test-stats"
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["size"] == 1
        assert stats["maxsize"] == 10
        assert stats["ttl"] == 300

    def test_update_existing_key(self):
        cache = TTLCacheWrapper("test", maxsize=10, ttl=300)
        cache.put("key1", "value1")
        cache.put("key1", "value2")
        assert cache.get("key1") == "value2"


# ---------------------------------------------------------------------------
# ttl_cache decorator
# ---------------------------------------------------------------------------


class TestTTLCacheDecorator:

    def test_caches_function_result(self):
        call_count = 0

        @ttl_cache(maxsize=10, ttl=300, name="test_caches")
        def expensive_fn(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        assert expensive_fn(5) == 10
        assert expensive_fn(5) == 10  # cached
        assert call_count == 1  # only called once

    def test_different_args_not_cached(self):
        call_count = 0

        @ttl_cache(maxsize=10, ttl=300, name="test_diff_args")
        def fn(x):
            nonlocal call_count
            call_count += 1
            return x + 1

        fn(1)
        fn(2)
        assert call_count == 2

    def test_cache_clear(self):
        call_count = 0

        @ttl_cache(maxsize=10, ttl=300, name="test_clear")
        def fn(x):
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        assert call_count == 1
        fn.cache_clear()
        fn(1)
        assert call_count == 2

    def test_cache_attribute(self):
        @ttl_cache(maxsize=10, ttl=300, name="test_attr")
        def fn(x):
            return x

        assert hasattr(fn, "cache")
        assert isinstance(fn.cache, TTLCacheWrapper)

    def test_ttl_expiration(self):
        call_count = 0

        @ttl_cache(maxsize=10, ttl=0.05, name="test_ttl_exp")
        def fn(x):
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        assert call_count == 1
        time.sleep(0.1)
        fn(1)
        assert call_count == 2  # re-called after TTL expired

    def test_kwargs_in_cache_key(self):
        call_count = 0

        @ttl_cache(maxsize=10, ttl=300, name="test_kwargs")
        def fn(x, y=10):
            nonlocal call_count
            call_count += 1
            return x + y

        fn(1, y=10)
        fn(1, y=10)  # same kwargs, cached
        assert call_count == 1

        fn(1, y=20)  # different kwargs
        assert call_count == 2


# ---------------------------------------------------------------------------
# Global cache management
# ---------------------------------------------------------------------------


class TestGlobalCacheManagement:

    def test_get_all_cache_stats(self):
        @ttl_cache(maxsize=5, ttl=300, name="global_test")
        def fn(x):
            return x

        fn(1)
        fn(1)

        stats = get_all_cache_stats()
        assert "global" in stats
        assert "caches" in stats

    def test_clear_all_caches(self):
        @ttl_cache(maxsize=5, ttl=300, name="clear_all_test")
        def fn(x):
            return x

        fn(1)
        count = clear_all_caches()
        assert count >= 1


# ---------------------------------------------------------------------------
# Cached profile loading in engine
# ---------------------------------------------------------------------------


class TestCachedProfileLoading:

    def test_engine_uses_cached_profile(self, tmp_path):
        """Verify that the engine caches profile loading."""
        from tftpos.config import TftpOSConfig
        from tftpos.engine import FirmwareEngine, _cached_load_profile
        from tftpos.matcher import HostMatcher
        from tftpos.models import HostRule
        from tftpos.registry import PluginRegistry

        # Clear the profile cache to start fresh
        _cached_load_profile.cache_clear()

        # Set up config
        data_dir = tmp_path
        profiles_dir = data_dir / "profiles"
        profiles_dir.mkdir()

        profile_toml = profiles_dir / "test.toml"
        profile_toml.write_text(
            '[profile]\n'
            'name = "test"\n'
            'os_family = "fedora"\n'
            'os_version = "40"\n'
        )

        config = TftpOSConfig(data_dir=data_dir)
        registry = PluginRegistry()
        registry.load_builtins()
        rule = HostRule(
            profile="test",
            os_family="fedora",
            os_version="40",
            mac="aa:bb:cc:dd:ee:ff",
        )
        matcher = HostMatcher([rule])

        engine = FirmwareEngine(registry, matcher, config)

        # Load profile twice -- second should be cached
        profile1 = engine.load_profile_for_rule(rule)
        profile2 = engine.load_profile_for_rule(rule)

        assert profile1.name == "test"
        assert profile2.name == "test"

        # Check cache has entries
        stats = _cached_load_profile.cache.stats
        assert stats["size"] >= 1
