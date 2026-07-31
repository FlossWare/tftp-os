"""Tests for tftpos.registry.PluginRegistry."""

from __future__ import annotations

import logging
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


class _ConfiguredPlugin(FirmwarePlugin):
    """Plugin that requires constructor arguments."""

    def __init__(self, root: str, family: str):
        self._root = root
        self._family = family

    @property
    def os_family(self) -> str:
        return self._family

    @property
    def supported_versions(self) -> list[str]:
        return ["1.0"]

    def firmware_path(self, profile) -> str:
        return f"{self._root}/{profile.os_version}/firmware.bin"


# ---------------------------------------------------------------------------
# register / get
# ---------------------------------------------------------------------------

class TestRegisterAndGet:

    def test_register_and_get(self):
        registry = PluginRegistry()
        registry.register(_FakePlugin)
        plugin = registry.get("fakeos")
        assert isinstance(plugin, _FakePlugin)

    def test_register_instance(self):
        registry = PluginRegistry()
        instance = _FakePlugin()
        registry.register(instance)
        assert registry.get("fakeos") is instance

    def test_register_class_with_kwargs(self):
        registry = PluginRegistry()
        registry.register(_ConfiguredPlugin, root="/srv", family="myos")
        plugin = registry.get("myos")
        assert isinstance(plugin, _ConfiguredPlugin)
        assert plugin.os_family == "myos"

    def test_register_rejects_non_plugin(self):
        registry = PluginRegistry()
        with pytest.raises(TypeError, match="expected FirmwarePlugin"):
            registry.register("not a plugin")

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
        """load_builtins() with no args registers nothing."""
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
        """Failed entry point loads are logged, not silently swallowed."""
        mock_ep = MagicMock()
        mock_ep.name = "broken_plugin"
        mock_ep.load.side_effect = ImportError("broken")

        registry = PluginRegistry()

        import importlib.metadata
        with patch.object(importlib.metadata, "entry_points", return_value=[mock_ep]):
            with patch("tftpos.registry.logger") as mock_logger:
                registry.discover()

        assert registry.available == []
        mock_logger.warning.assert_called_once()
        assert "broken_plugin" in str(mock_logger.warning.call_args)

    def test_discover_stores_class_when_instantiation_fails(self):
        """Plugins requiring config appear in discovered but not available."""
        mock_ep = MagicMock()
        mock_ep.name = "configured"
        mock_ep.load.return_value = _ConfiguredPlugin

        registry = PluginRegistry()

        import importlib.metadata
        with patch.object(importlib.metadata, "entry_points", return_value=[mock_ep]):
            registry.discover()

        assert registry.available == []
        assert "configured" in registry.discovered
        assert registry.discovered["configured"] is _ConfiguredPlugin

    def test_discover_logs_info_for_config_required(self):
        """Plugins requiring config log an info message."""
        mock_ep = MagicMock()
        mock_ep.name = "configured"
        mock_ep.load.return_value = _ConfiguredPlugin

        registry = PluginRegistry()

        import importlib.metadata
        with patch.object(importlib.metadata, "entry_points", return_value=[mock_ep]):
            with patch("tftpos.registry.logger") as mock_logger:
                registry.discover()

        mock_logger.info.assert_called_once()
        assert "configured" in str(mock_logger.info.call_args)

    def test_discover_and_manual_register_flow(self):
        """Full flow: discover() finds class, user registers with config."""
        mock_ep = MagicMock()
        mock_ep.name = "configured"
        mock_ep.load.return_value = _ConfiguredPlugin

        registry = PluginRegistry()

        import importlib.metadata
        with patch.object(importlib.metadata, "entry_points", return_value=[mock_ep]):
            registry.discover()

        assert registry.available == []
        cls = registry.discovered["configured"]
        registry.register(cls, root="/srv/tftpboot", family="myos")
        assert "myos" in registry.available
        plugin = registry.get("myos")
        assert isinstance(plugin, _ConfiguredPlugin)
        assert plugin.os_family == "myos"
