"""Tests for firmware staging under tftp_root."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tftpos.staging import (
    list_staged,
    stage,
    unstage,
)


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _create_firmware(tmp_path: Path, name: str = "firmware.bin") -> Path:
    """Create a dummy firmware file and return its path."""
    firmware = tmp_path / "distros" / name
    firmware.parent.mkdir(parents=True, exist_ok=True)
    firmware.write_bytes(b"\x00" * 64)
    return firmware


# ---------------------------------------------------------------
# stage() — symlink creation
# ---------------------------------------------------------------


class TestStageSymlink:

    def test_creates_symlink_by_default(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp)
        assert result.is_symlink()
        assert result.name == "firmware.bin"
        assert result.resolve() == fw.resolve()

    def test_symlink_content_matches(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp)
        assert result.read_bytes() == fw.read_bytes()

    def test_returns_path_under_tftp_root(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp)
        assert result.parent.resolve() == tftp.resolve()


# ---------------------------------------------------------------
# stage() — copy fallback
# ---------------------------------------------------------------


class TestStageCopy:

    def test_copy_when_symlink_false(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp, symlink=False)
        assert not result.is_symlink()
        assert result.is_file()
        assert result.read_bytes() == fw.read_bytes()

    def test_copy_preserves_content(self, tmp_path):
        fw = _create_firmware(tmp_path)
        fw.write_bytes(b"FIRMWARE_DATA_12345")
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp, symlink=False)
        assert result.read_bytes() == b"FIRMWARE_DATA_12345"


# ---------------------------------------------------------------
# stage() — custom name
# ---------------------------------------------------------------


class TestStageCustomName:

    def test_custom_name(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp, name="router-01.bin")
        assert result.name == "router-01.bin"

    def test_custom_name_with_symlink(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp, name="custom.img")
        assert result.is_symlink()
        assert result.resolve() == fw.resolve()

    def test_custom_name_with_copy(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp, name="custom.img", symlink=False)
        assert not result.is_symlink()
        assert result.name == "custom.img"


# ---------------------------------------------------------------
# stage() — path traversal prevention
# ---------------------------------------------------------------


class TestStagePathTraversal:

    def test_rejects_dot_dot_slash(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        with pytest.raises(ValueError, match="unsafe"):
            stage(fw, tftp, name="../../etc/passwd")

    def test_rejects_absolute_path_name(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        with pytest.raises(ValueError, match="unsafe"):
            stage(fw, tftp, name="/etc/passwd")

    def test_rejects_dot_dot_only(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        with pytest.raises(ValueError, match="unsafe"):
            stage(fw, tftp, name="..")

    def test_rejects_dot_only(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        with pytest.raises(ValueError, match="unsafe"):
            stage(fw, tftp, name=".")

    def test_rejects_empty_name(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        with pytest.raises(ValueError, match="unsafe"):
            stage(fw, tftp, name="")

    def test_rejects_name_with_directory_separator(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        with pytest.raises(ValueError, match="unsafe"):
            stage(fw, tftp, name="subdir/firmware.bin")


# ---------------------------------------------------------------
# stage() — FileNotFoundError
# ---------------------------------------------------------------


class TestStageMissingFirmware:

    def test_raises_file_not_found(self, tmp_path):
        tftp = tmp_path / "tftp"
        missing = tmp_path / "does_not_exist.bin"
        with pytest.raises(
            FileNotFoundError, match="firmware file not found"
        ):
            stage(missing, tftp)


# ---------------------------------------------------------------
# stage() — tftp_root creation
# ---------------------------------------------------------------


class TestStageTftpRootCreation:

    def test_creates_tftp_root_directory(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "new" / "tftp" / "dir"
        assert not tftp.exists()
        stage(fw, tftp)
        assert tftp.is_dir()


# ---------------------------------------------------------------
# stage() — overwrites existing
# ---------------------------------------------------------------


class TestStageOverwrite:

    def test_overwrites_existing_file(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        tftp.mkdir()
        existing = tftp / "firmware.bin"
        existing.write_bytes(b"old")
        result = stage(fw, tftp)
        assert result.read_bytes() == fw.read_bytes()

    def test_overwrites_existing_symlink(self, tmp_path):
        fw1 = _create_firmware(tmp_path, "fw1.bin")
        fw2 = _create_firmware(tmp_path, "fw2.bin")
        fw2.write_bytes(b"NEW_FW")
        tftp = tmp_path / "tftp"
        stage(fw1, tftp, name="target.bin")
        result = stage(fw2, tftp, name="target.bin")
        assert result.read_bytes() == b"NEW_FW"


# ---------------------------------------------------------------
# unstage()
# ---------------------------------------------------------------


class TestUnstage:

    def test_removes_file(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp, symlink=False)
        assert result.exists()
        removed = unstage(result)
        assert removed is True
        assert not result.exists()

    def test_removes_symlink(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp)
        assert result.is_symlink()
        removed = unstage(result)
        assert removed is True
        assert not result.exists()

    def test_returns_false_for_missing(self, tmp_path):
        missing = tmp_path / "nonexistent"
        assert unstage(missing) is False

    def test_original_firmware_untouched(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        result = stage(fw, tftp)
        unstage(result)
        # Original firmware should still exist
        assert fw.exists()


# ---------------------------------------------------------------
# list_staged()
# ---------------------------------------------------------------


class TestListStaged:

    def test_lists_staged_files(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        stage(fw, tftp, name="a.bin")
        stage(fw, tftp, name="b.bin")
        stage(fw, tftp, name="c.bin")
        result = list_staged(tftp)
        names = [p.name for p in result]
        assert names == ["a.bin", "b.bin", "c.bin"]

    def test_empty_directory(self, tmp_path):
        tftp = tmp_path / "tftp"
        tftp.mkdir()
        assert list_staged(tftp) == []

    def test_nonexistent_directory(self, tmp_path):
        tftp = tmp_path / "does_not_exist"
        assert list_staged(tftp) == []

    def test_includes_symlinks_and_files(self, tmp_path):
        fw = _create_firmware(tmp_path)
        tftp = tmp_path / "tftp"
        stage(fw, tftp, name="link.bin", symlink=True)
        stage(fw, tftp, name="copy.bin", symlink=False)
        result = list_staged(tftp)
        assert len(result) == 2


# ---------------------------------------------------------------
# FirmwareEngine.stage() integration
# ---------------------------------------------------------------


class TestEngineStage:

    def _make_engine(self, tmp_path):
        """Build a FirmwareEngine with a mock plugin."""
        from tftpos.config import TftpOSConfig
        from tftpos.engine import FirmwareEngine
        from tftpos.matcher import HostMatcher
        from tftpos.models import HostRule
        from tftpos.registry import PluginRegistry

        # Create a real firmware file
        fw = _create_firmware(tmp_path, "openwrt-23.05.bin")

        # Mock plugin that returns the firmware path
        plugin = MagicMock()
        plugin.os_family = "openwrt"
        plugin.validate_profile.return_value = []
        plugin.firmware_path.return_value = str(fw)

        registry = PluginRegistry()
        # Directly insert the mock into both internal dicts
        registry._plugins["openwrt"] = MagicMock
        registry._instances["openwrt"] = plugin

        rules = [
            HostRule(
                profile="test-router",
                os_family="openwrt",
                os_version="23.05",
                mac="aa:bb:cc:dd:ee:ff",
            ),
        ]
        matcher = HostMatcher(rules)

        tftp_root = tmp_path / "tftp"
        config = TftpOSConfig(
            tftp_root=tftp_root,
            data_dir=tmp_path / "data",
        )
        (config.data_dir / "profiles").mkdir(
            parents=True, exist_ok=True
        )

        engine = FirmwareEngine(registry, matcher, config)
        return engine, fw, tftp_root

    def test_engine_stage_creates_symlink(self, tmp_path):
        engine, fw, tftp_root = self._make_engine(tmp_path)
        result = engine.stage("aa:bb:cc:dd:ee:ff")
        assert result.exists()
        assert result.is_symlink()
        assert result.parent.resolve() == tftp_root.resolve()

    def test_engine_stage_with_custom_name(self, tmp_path):
        engine, fw, tftp_root = self._make_engine(tmp_path)
        result = engine.stage(
            "aa:bb:cc:dd:ee:ff", name="router.bin"
        )
        assert result.name == "router.bin"

    def test_engine_stage_with_copy(self, tmp_path):
        engine, fw, tftp_root = self._make_engine(tmp_path)
        result = engine.stage(
            "aa:bb:cc:dd:ee:ff", symlink=False
        )
        assert result.is_file()
        assert not result.is_symlink()

    def test_engine_stage_raises_for_unknown_mac(self, tmp_path):
        engine, fw, tftp_root = self._make_engine(tmp_path)
        with pytest.raises(ValueError, match="no matching"):
            engine.stage("00:00:00:00:00:00")
