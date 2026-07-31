"""Tests for configuration loading and evolution guarantees.

Covers:
 - Minimal/empty config produces a valid TftpOSConfig with defaults
 - Unknown keys in TOML are silently ignored (forward-compatible)
 - Malformed TOML raises ValueError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tftpos.config import TftpOSConfig, load_config


class TestLoadConfigDefaults:
    """load_config() with minimal TOML produces valid defaults."""

    def test_empty_config(self, tmp_path):
        """A completely empty TOML file yields a TftpOSConfig with
        all default values."""
        cfg_file = tmp_path / "tftpos.toml"
        cfg_file.write_text("")

        config = load_config(cfg_file)

        assert isinstance(config, TftpOSConfig)
        assert config.server_host == "0.0.0.0"
        assert config.server_port == 8443
        assert config.tftp_root == Path("/srv/tftp")
        assert config.data_dir == Path("/etc/tftpos")
        assert config.auth_enabled is False

    def test_minimal_server_section(self, tmp_path):
        """A config with only [server] still produces valid defaults
        for all other sections."""
        cfg_file = tmp_path / "tftpos.toml"
        cfg_file.write_text(
            '[server]\nhost = "127.0.0.1"\nport = 9999\n'
        )

        config = load_config(cfg_file)

        assert config.server_host == "127.0.0.1"
        assert config.server_port == 9999
        # All other fields should still have defaults
        assert config.tftp_root == Path("/srv/tftp")
        assert config.rate_limit.enabled is False
        assert config.database.backend == "sqlite"
        assert config.webhooks == []


class TestUnknownKeysIgnored:
    """Unknown keys in TOML must be silently ignored
    (forward-compatible)."""

    def test_unknown_top_level_key(self, tmp_path):
        """A top-level key that load_config() does not recognise is
        silently skipped."""
        cfg_file = tmp_path / "tftpos.toml"
        cfg_file.write_text(
            'future_feature = true\n'
            '\n'
            '[server]\n'
            'host = "10.0.0.1"\n'
        )

        config = load_config(cfg_file)

        assert config.server_host == "10.0.0.1"

    def test_unknown_key_inside_section(self, tmp_path):
        """An unknown key inside an existing section is silently
        skipped."""
        cfg_file = tmp_path / "tftpos.toml"
        cfg_file.write_text(
            '[server]\n'
            'host = "10.0.0.1"\n'
            'not_a_real_setting = 42\n'
        )

        config = load_config(cfg_file)

        assert config.server_host == "10.0.0.1"

    def test_unknown_section(self, tmp_path):
        """An entirely unknown TOML section is silently skipped."""
        cfg_file = tmp_path / "tftpos.toml"
        cfg_file.write_text(
            '[spaceship]\n'
            'warp_drive = true\n'
            '\n'
            '[server]\n'
            'port = 7777\n'
        )

        config = load_config(cfg_file)

        assert config.server_port == 7777


class TestMalformedConfig:
    """Malformed TOML raises ValueError."""

    def test_invalid_toml_raises(self, tmp_path):
        cfg_file = tmp_path / "tftpos.toml"
        cfg_file.write_text("[server\nbroken")

        with pytest.raises(ValueError, match="malformed"):
            load_config(cfg_file)

    def test_missing_file_raises(self, tmp_path):
        cfg_file = tmp_path / "does_not_exist.toml"

        with pytest.raises(ValueError, match="cannot read"):
            load_config(cfg_file)
