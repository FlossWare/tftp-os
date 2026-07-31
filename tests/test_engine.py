"""Unit tests for FirmwareEngine.

Exercises serve(), stage(), resolve_rule(), load_profile_for_rule(),
and error paths using temp dirs and mocks.  Runs under default pytest
without tftpy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tftpos.config import TftpOSConfig
from tftpos.engine import FirmwareEngine
from tftpos.matcher import HostMatcher
from tftpos.models import HostRule, ProvisionProfile
from tftpos.plugins.base import FirmwarePlugin
from tftpos.registry import PluginRegistry


def _rule(**kwargs) -> HostRule:
    kwargs.setdefault("profile", "test")
    kwargs.setdefault("os_family", "openwrt")
    kwargs.setdefault("os_version", "23.05")
    return HostRule(**kwargs)


def _engine(tmp_path, rules=None, plugins=None):
    if rules is None:
        rules = [_rule(mac="aa:bb:cc:dd:ee:ff")]

    matcher = HostMatcher(rules)
    registry = PluginRegistry()

    if plugins:
        for family, plugin in plugins.items():
            registry._plugins[family] = type(plugin)
            registry._instances[family] = plugin

    tftp_root = tmp_path / "tftp"
    tftp_root.mkdir()
    data_dir = tmp_path / "data"
    (data_dir / "profiles").mkdir(parents=True)

    config = TftpOSConfig(
        tftp_root=tftp_root,
        data_dir=data_dir,
    )
    return FirmwareEngine(registry, matcher, config)


class TestResolveRule:

    def test_resolve_known_mac(self, tmp_path):
        engine = _engine(tmp_path)
        rule = engine.resolve_rule("aa:bb:cc:dd:ee:ff")
        assert rule.os_family == "openwrt"
        assert rule.profile == "test"

    def test_resolve_unknown_mac_raises(self, tmp_path):
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="no matching host rule"):
            engine.resolve_rule("00:00:00:00:00:00")

    def test_get_rule_returns_none_for_unknown(self, tmp_path):
        engine = _engine(tmp_path)
        assert engine.get_rule("00:00:00:00:00:00") is None

    def test_get_rule_returns_rule_for_known(self, tmp_path):
        engine = _engine(tmp_path)
        rule = engine.get_rule("aa:bb:cc:dd:ee:ff")
        assert rule is not None
        assert rule.mac == "aa:bb:cc:dd:ee:ff"


class TestLoadProfile:

    def test_loads_toml_profile(self, tmp_path):
        engine = _engine(tmp_path)
        profiles_dir = tmp_path / "data" / "profiles"
        (profiles_dir / "test.toml").write_text(
            '[profile]\nname = "test"\nos_family = "openwrt"\n'
            'os_version = "23.05"\n'
        )
        rule = _rule(mac="aa:bb:cc:dd:ee:ff")
        profile = engine.load_profile_for_rule(rule)
        assert profile.name == "test"
        assert profile.os_family == "openwrt"

    def test_synthesizes_profile_when_toml_missing(self, tmp_path):
        engine = _engine(tmp_path)
        rule = _rule(mac="aa:bb:cc:dd:ee:ff")
        profile = engine.load_profile_for_rule(rule)
        assert profile.name == "test"
        assert profile.os_family == "openwrt"
        assert profile.os_version == "23.05"

    def test_rejects_traversal_in_profile_name(self, tmp_path):
        engine = _engine(tmp_path)
        rule = _rule(mac="aa:bb:cc:dd:ee:ff", profile="../etc/passwd")
        with pytest.raises(ValueError, match="invalid profile name"):
            engine.load_profile_for_rule(rule)

    def test_rejects_slash_in_profile_name(self, tmp_path):
        engine = _engine(tmp_path)
        rule = _rule(mac="aa:bb:cc:dd:ee:ff", profile="foo/bar")
        with pytest.raises(ValueError, match="invalid profile name"):
            engine.load_profile_for_rule(rule)


class TestServe:

    def test_serve_returns_firmware_path(self, tmp_path):
        plugin = MagicMock(spec=FirmwarePlugin)
        plugin.os_family = "openwrt"
        plugin.validate_profile.return_value = []
        plugin.firmware_path.return_value = "/srv/tftp/firmware.bin"

        engine = _engine(tmp_path, plugins={"openwrt": plugin})
        result = engine.serve(mac="aa:bb:cc:dd:ee:ff")
        assert result == "/srv/tftp/firmware.bin"
        plugin.firmware_path.assert_called_once()

    def test_serve_raises_on_validation_errors(self, tmp_path):
        plugin = MagicMock(spec=FirmwarePlugin)
        plugin.os_family = "openwrt"
        plugin.validate_profile.return_value = ["missing install_url"]

        engine = _engine(tmp_path, plugins={"openwrt": plugin})
        with pytest.raises(ValueError, match="invalid profile.*missing install_url"):
            engine.serve(mac="aa:bb:cc:dd:ee:ff")

    def test_serve_raises_on_unknown_os_family(self, tmp_path):
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="unknown os_family"):
            engine.serve(mac="aa:bb:cc:dd:ee:ff")

    def test_serve_raises_on_unknown_mac(self, tmp_path):
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="no matching host rule"):
            engine.serve(mac="00:00:00:00:00:00")


class TestStage:

    def test_stage_creates_file_under_tftp_root(self, tmp_path):
        fw_file = tmp_path / "firmware.bin"
        fw_file.write_bytes(b"\x00" * 64)

        plugin = MagicMock(spec=FirmwarePlugin)
        plugin.os_family = "openwrt"
        plugin.validate_profile.return_value = []
        plugin.firmware_path.return_value = str(fw_file)

        engine = _engine(tmp_path, plugins={"openwrt": plugin})
        staged = engine.stage(mac="aa:bb:cc:dd:ee:ff")

        assert staged.exists()
        assert staged.parent == tmp_path / "tftp"

    def test_stage_with_custom_name(self, tmp_path):
        fw_file = tmp_path / "firmware.bin"
        fw_file.write_bytes(b"\x00" * 64)

        plugin = MagicMock(spec=FirmwarePlugin)
        plugin.os_family = "openwrt"
        plugin.validate_profile.return_value = []
        plugin.firmware_path.return_value = str(fw_file)

        engine = _engine(tmp_path, plugins={"openwrt": plugin})
        staged = engine.stage(mac="aa:bb:cc:dd:ee:ff", name="router.bin")

        assert staged.name == "router.bin"
        assert staged.exists()

    def test_stage_raises_on_unknown_mac(self, tmp_path):
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="no matching host rule"):
            engine.stage(mac="00:00:00:00:00:00")


class TestEnsureTracked:

    def test_registers_new_mac(self, tmp_path):
        engine = _engine(tmp_path)
        rule = _rule(mac="aa:bb:cc:dd:ee:ff")
        engine.ensure_tracked("aa:bb:cc:dd:ee:ff", rule)
        record = engine.tracker.get("aa:bb:cc:dd:ee:ff")
        assert record is not None
        assert record.os_family == "openwrt"

    def test_idempotent_on_existing_mac(self, tmp_path):
        engine = _engine(tmp_path)
        rule = _rule(mac="aa:bb:cc:dd:ee:ff")
        engine.ensure_tracked("aa:bb:cc:dd:ee:ff", rule)
        engine.ensure_tracked("aa:bb:cc:dd:ee:ff", rule)
        record = engine.tracker.get("aa:bb:cc:dd:ee:ff")
        assert record is not None


class TestBaseUrl:

    def test_base_url_http(self, tmp_path):
        engine = _engine(tmp_path)
        url = engine.base_url()
        assert url.startswith("http://")
        assert "127.0.0.1" in url

    def test_base_url_https_when_tls(self, tmp_path):
        engine = _engine(tmp_path)
        engine._config.tls_cert = Path("/etc/tls/cert.pem")
        url = engine.base_url()
        assert url.startswith("https://")


class TestInvalidateCaches:

    def test_invalidate_returns_zero(self, tmp_path):
        engine = _engine(tmp_path)
        assert engine.invalidate_caches() == 0

    def test_invalidate_with_mac_returns_zero(self, tmp_path):
        engine = _engine(tmp_path)
        assert engine.invalidate_caches(mac="aa:bb:cc:dd:ee:ff") == 0
