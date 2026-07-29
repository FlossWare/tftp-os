"""Plugin registry with auto-discovery."""

from __future__ import annotations

import importlib
import inspect
import sys
from typing import Dict, Type

from tftpos.plugins.base import FirmwarePlugin


class PluginRegistry:

    def __init__(
        self,
        entry_point_group: str = "tftpos.plugins",
    ) -> None:
        self._entry_point_group = entry_point_group
        self._plugins: Dict[str, Type[FirmwarePlugin]] = {}
        self._instances: Dict[str, FirmwarePlugin] = {}

    def register(self, plugin_cls: Type[FirmwarePlugin]) -> None:
        instance = plugin_cls()
        family = instance.os_family.lower()
        self._plugins[family] = plugin_cls
        self._instances[family] = instance

    def get(self, os_family: str) -> FirmwarePlugin:
        family = os_family.lower()
        if family not in self._instances:
            raise ValueError(
                f"unknown os_family {os_family!r}; "
                f"available: {self.available}"
            )
        return self._instances[family]

    @property
    def available(self) -> list[str]:
        return sorted(self._plugins.keys())

    def discover(self) -> None:
        if sys.version_info >= (3, 10):
            from importlib.metadata import entry_points

            eps = entry_points(group=self._entry_point_group)
        else:
            from importlib.metadata import entry_points

            all_eps = entry_points()
            eps = all_eps.get(self._entry_point_group, [])

        for ep in eps:
            try:
                plugin_cls = ep.load()
                if (
                    isinstance(plugin_cls, type)
                    and issubclass(plugin_cls, FirmwarePlugin)
                    and plugin_cls is not FirmwarePlugin
                ):
                    self.register(plugin_cls)
            except Exception:
                pass

    def load_builtins(self, module_names: list[str] | None = None) -> None:
        if module_names is None:
            return
        for mod_name in module_names:
            try:
                mod = importlib.import_module(mod_name)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, FirmwarePlugin)
                        and attr is not FirmwarePlugin
                        and not inspect.isabstract(attr)
                    ):
                        self.register(attr)
            except ImportError:
                pass
