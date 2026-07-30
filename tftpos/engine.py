"""Core firmware provisioning engine."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from tftpos.cache import (
    TTLCacheWrapper,
    profile_cache_key,
    ttl_cache,
)
from tftpos.config import TftpOSConfig, load_profile
from tftpos.matcher import HostMatcher
from tftpos.models import HostRule, ProvisionProfile
from tftpos.registry import PluginRegistry
from tftpos.staging import stage as stage_firmware
from tftpos.state import ProvisionState, ProvisionTracker

logger = logging.getLogger("tftpos.engine")


@ttl_cache(maxsize=64, ttl=300, name="profile_loader")
def _cached_load_profile(profile_path: str) -> ProvisionProfile:
    return load_profile(Path(profile_path))


class FirmwareEngine:

    def __init__(
        self,
        registry: PluginRegistry,
        matcher: HostMatcher,
        config: TftpOSConfig,
        tracker: Optional[ProvisionTracker] = None,
    ) -> None:
        self._registry = registry
        self._matcher = matcher
        self._config = config
        self.tracker = tracker or ProvisionTracker()

    def resolve_rule(
        self,
        mac: str,
        hostname: Optional[str] = None,
        subnet: Optional[str] = None,
        serial: Optional[str] = None,
        groups: Optional[list[str]] = None,
        arch: Optional[str] = None,
    ) -> HostRule:
        rule = self._matcher.match(
            mac=mac,
            hostname=hostname,
            subnet=subnet,
            serial=serial,
            groups=groups,
            arch=arch,
        )
        if rule is None:
            raise ValueError(
                f"no matching host rule for MAC {mac!r}"
            )
        return rule

    def get_rule(
        self,
        mac: str,
        hostname: Optional[str] = None,
        subnet: Optional[str] = None,
        serial: Optional[str] = None,
        groups: Optional[list[str]] = None,
        arch: Optional[str] = None,
    ) -> Optional[HostRule]:
        return self._matcher.match(
            mac=mac,
            hostname=hostname,
            subnet=subnet,
            serial=serial,
            groups=groups,
            arch=arch,
        )

    def load_profile_for_rule(
        self, rule: HostRule
    ) -> ProvisionProfile:
        if ".." in rule.profile or "/" in rule.profile:
            raise ValueError(
                f"invalid profile name: {rule.profile!r}"
            )
        profiles_dir = (
            self._config.data_dir / "profiles"
        )
        profile_path = profiles_dir / f"{rule.profile}.toml"
        if (
            profile_path.resolve().parent
            != profiles_dir.resolve()
        ):
            raise ValueError(
                f"invalid profile name: {rule.profile!r}"
            )
        if profile_path.exists():
            return _cached_load_profile(str(profile_path))

        return ProvisionProfile(
            name=rule.profile,
            os_family=rule.os_family,
            os_version=rule.os_version,
            vendor=rule.vendor,
        )

    def profile_path_for_rule(
        self, rule: HostRule
    ) -> Optional[str]:
        if ".." in rule.profile or "/" in rule.profile:
            return None
        profiles_dir = self._config.data_dir / "profiles"
        profile_path = profiles_dir / f"{rule.profile}.toml"
        if profile_path.exists():
            return str(profile_path)
        return None

    def serve(self, mac: str, **kwargs) -> str:
        """Resolve a MAC to its firmware path.

        Returns the firmware file path from the matched plugin.
        """
        rule = self.resolve_rule(mac, **kwargs)
        profile = self.load_profile_for_rule(rule)
        plugin = self._registry.get(rule.os_family)

        errors = plugin.validate_profile(profile)
        if errors:
            raise ValueError(
                f"invalid profile {profile.name!r}: "
                + "; ".join(errors)
            )

        return plugin.firmware_path(profile)

    def ensure_tracked(self, mac: str, rule: HostRule) -> None:
        if self.tracker.get(mac) is None:
            self.tracker.register(
                mac=mac,
                profile=rule.profile,
                os_family=rule.os_family,
                os_version=rule.os_version,
            )

    def invalidate_caches(self, mac: Optional[str] = None) -> int:
        _cached_load_profile.cache_clear()
        return 0

    def stage(self, mac: str, **kwargs) -> Path:
        """Resolve a MAC to firmware and stage it under tftp_root.

        Calls :meth:`serve` to resolve the firmware path, then
        :func:`tftpos.staging.stage` to place it under
        ``config.tftp_root``.  Extra *kwargs* are forwarded to
        both ``serve`` and ``stage_firmware`` (the staging
        keyword arguments ``name`` and ``symlink`` are extracted
        first).
        """
        # Separate staging kwargs from serve kwargs
        stage_name = kwargs.pop("name", None)
        stage_symlink = kwargs.pop("symlink", True)

        firmware_path = self.serve(mac, **kwargs)
        return stage_firmware(
            firmware_path=firmware_path,
            tftp_root=self._config.tftp_root,
            name=stage_name,
            symlink=stage_symlink,
        )

    def base_url(self) -> str:
        scheme = (
            "https" if self._config.tls_cert else "http"
        )
        host = self._config.server_host
        if host == "0.0.0.0":
            host = "127.0.0.1"
        return f"{scheme}://{host}:{self._config.server_port}"
