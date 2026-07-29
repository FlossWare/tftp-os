"""Tests for tftpos.registry.PluginRegistry."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from tftpos.plugins.base import FirmwarePlugin
from tftpos.registry import PluginRegistry


class _FakePlugin(FirmwarePlugin):
    """Concrete FirmwarePlugin subclass for testing registry operations."""

    @property
    def os_family(self) -> str:
        return "fakeos"

    @property
    def supported_versions(self) -> list[str]:
        return ["1.0"]

    def firmware_path(self, profile) -> str:
        return "/fake/firmware"


# ---------------------------------------------------------------------------
# register / get
# ---------------------------------------------------------------------------

class TestRegisterAndGet:

    def test_register_and_get(self):
        registry = PluginRegistry()
        registry.register(_FakePlugin)
        plugin = registry.get("fakeos")
        assert isinstance(plugin, _FakePlugin)

    def test_get_unknown_raises_value_error(self):
        registry = PluginRegistry()
        with pytest.raises(ValueError, match="unknown os_family"):
            registry.get("nonexistent")

    def test_get_unknown_shows_available(self):
        registry = PluginRegistry()
        registry.register(_FakePlugin)
        with pytest.raises(ValueError, match="fakeos"):
            registry.get("nonexistent")

    def test_case_insensitivity(self):
        """register() uses instance.os_family.lower(), get() lowercases input."""
        registry = PluginRegistry()
        registry.register(_FakePlugin)
        # All case variants should resolve to the same instance
        lower = registry.get("fakeos")
        upper = registry.get("FAKEOS")
        mixed = registry.get("FakeOS")
        assert lower is upper is mixed
        assert isinstance(upper, _FakePlugin)


# ---------------------------------------------------------------------------
# load_builtins
# ---------------------------------------------------------------------------

class TestLoadBuiltins:

    def test_load_builtins_returns_empty(self):
        """TftpOS has no builtin plugins; load_builtins should be a no-op."""
        registry = PluginRegistry()
        registry.load_builtins()
        assert registry.available == []

    def test_load_builtins_idempotent(self):
        """Calling load_builtins twice should not duplicate entries."""
        registry = PluginRegistry()
        registry.load_builtins()
        registry.load_builtins()
        assert len(registry.available) == 0


# ---------------------------------------------------------------------------
# available property
# ---------------------------------------------------------------------------

class TestAvailable:

    def test_available_returns_sorted_list(self):
        registry = PluginRegistry()
        registry.register(_FakePlugin)
        avail = registry.available
        assert avail == sorted(avail)

    def test_available_empty_initially(self):
        registry = PluginRegistry()
        assert registry.available == []

    def test_available_grows_on_register(self):
        registry = PluginRegistry()
        assert len(registry.available) == 0
        registry.register(_FakePlugin)
        assert "fakeos" in registry.available
        assert len(registry.available) == 1


# ---------------------------------------------------------------------------
# discover (entry_points)
# ---------------------------------------------------------------------------

class TestDiscover:

    def test_discover_loads_entry_point_plugin(self):
        """Mock importlib.metadata.entry_points to return a fake plugin."""

        # Create a concrete mock plugin class that extends FirmwarePlugin
        class FakePlugin(FirmwarePlugin):
            @property
            def os_family(self) -> str:
                return "fakeos"

            @property
            def supported_versions(self) -> list[str]:
                return ["1.0"]

            def firmware_path(self, profile) -> str:
                return "/fake/firmware"

        # Build a mock entry point whose load() returns FakePlugin
        mock_ep = MagicMock()
        mock_ep.load.return_value = FakePlugin

        registry = PluginRegistry()

        with patch("tftpos.registry.importlib.metadata.entry_points", return_value=[mock_ep]) as mock_eps:
            # Patch sys.version_info to take the >= 3.12 branch
            with patch.object(sys, "version_info", (3, 12, 0)):
                # We need to patch at the point of import inside discover()
                # Since discover() does `from importlib.metadata import entry_points`,
                # we patch the module-level importlib.metadata
                import importlib.metadata
                with patch.object(importlib.metadata, "entry_points", return_value=[mock_ep]):
                    registry.discover()

        assert "fakeos" in registry.available
        plugin = registry.get("fakeos")
        assert isinstance(plugin, FakePlugin)

    def test_discover_skips_non_os_plugin_classes(self):
        """Entry points that do not subclass FirmwarePlugin should be skipped."""

        class NotAPlugin:
            pass

        mock_ep = MagicMock()
        mock_ep.load.return_value = NotAPlugin

        registry = PluginRegistry()

        import importlib.metadata
        with patch.object(importlib.metadata, "entry_points", return_value=[mock_ep]):
            registry.discover()

        assert registry.available == []

    def test_discover_skips_os_plugin_base_class(self):
        """The FirmwarePlugin ABC itself should not be registered."""
        mock_ep = MagicMock()
        mock_ep.load.return_value = FirmwarePlugin

        registry = PluginRegistry()

        import importlib.metadata
        with patch.object(importlib.metadata, "entry_points", return_value=[mock_ep]):
            registry.discover()

        assert registry.available == []

    def test_discover_handles_load_exception(self):
        """If an entry point fails to load, it should be silently skipped."""
        mock_ep = MagicMock()
        mock_ep.load.side_effect = ImportError("broken")

        registry = PluginRegistry()

        import importlib.metadata
        with patch.object(importlib.metadata, "entry_points", return_value=[mock_ep]):
            registry.discover()

        assert registry.available == []
