"""Tests for tftpos.secrets -- providers, manager, CLI, and API endpoints."""

from __future__ import annotations

import json
import os
import stat
from unittest.mock import MagicMock

import pytest
from tftpos.models import BootFirmware, ProvisionProfile
from tftpos.secrets import (
    EnvironmentSecretsProvider,
    FileSecretsProvider,
    SecretsManager,
    SecretsProvider,
    _SECRET_REF_RE,
)


# ====================================================================
# FileSecretsProvider
# ====================================================================


class TestFileSecretsProviderSetGet:
    """Tests for FileSecretsProvider set() and get()."""

    def test_set_and_get(self, tmp_path):
        """set() stores a value, get() retrieves it."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("ROOT_PW", "hunter2")
        assert provider.get("ROOT_PW") == "hunter2"

    def test_get_missing_returns_none(self, tmp_path):
        """get() returns None for a key that does not exist."""
        provider = FileSecretsProvider(tmp_path)
        assert provider.get("NONEXISTENT") is None

    def test_get_missing_no_file_returns_none(self, tmp_path):
        """get() returns None when the secrets file does not exist at all."""
        provider = FileSecretsProvider(tmp_path / "subdir")
        assert provider.get("ANYTHING") is None

    def test_set_overwrites_existing(self, tmp_path):
        """set() with an existing key overwrites the previous value."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("KEY", "old")
        provider.set("KEY", "new")
        assert provider.get("KEY") == "new"

    def test_set_creates_directory(self, tmp_path):
        """set() creates the data directory if it doesn't exist."""
        data_dir = tmp_path / "deep" / "nested" / "dir"
        provider = FileSecretsProvider(data_dir)
        provider.set("K", "V")
        assert provider.get("K") == "V"
        assert data_dir.exists()

    def test_multiple_keys(self, tmp_path):
        """Multiple keys coexist in the same file."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("A", "1")
        provider.set("B", "2")
        provider.set("C", "3")
        assert provider.get("A") == "1"
        assert provider.get("B") == "2"
        assert provider.get("C") == "3"


class TestFileSecretsProviderDelete:
    """Tests for FileSecretsProvider delete()."""

    def test_delete_existing(self, tmp_path):
        """delete() removes a stored key."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("KEY", "val")
        provider.delete("KEY")
        assert provider.get("KEY") is None

    def test_delete_nonexistent_is_silent(self, tmp_path):
        """delete() does not raise when key does not exist."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("OTHER", "val")
        provider.delete("MISSING")  # should not raise
        assert provider.get("OTHER") == "val"

    def test_delete_preserves_other_keys(self, tmp_path):
        """delete() only removes the targeted key."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("KEEP", "yes")
        provider.set("DROP", "no")
        provider.delete("DROP")
        assert provider.get("KEEP") == "yes"
        assert provider.get("DROP") is None


class TestFileSecretsProviderListKeys:
    """Tests for FileSecretsProvider list_keys()."""

    def test_list_empty(self, tmp_path):
        """list_keys() returns empty list when no secrets exist."""
        provider = FileSecretsProvider(tmp_path)
        assert provider.list_keys() == []

    def test_list_returns_sorted(self, tmp_path):
        """list_keys() returns keys in sorted order."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("ZEBRA", "z")
        provider.set("APPLE", "a")
        provider.set("MANGO", "m")
        assert provider.list_keys() == ["APPLE", "MANGO", "ZEBRA"]


class TestFileSecretsProviderPermissions:
    """Tests for file and directory permission enforcement."""

    def test_file_permissions_owner_only(self, tmp_path):
        """The secrets file is created with 0o600 permissions."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("TEST", "value")

        secrets_path = tmp_path / "secrets.json"
        file_mode = secrets_path.stat().st_mode
        assert stat.S_IMODE(file_mode) == 0o600

    def test_directory_permissions(self, tmp_path):
        """The data directory is set to 0o700 permissions."""
        data_dir = tmp_path / "secure"
        provider = FileSecretsProvider(data_dir)
        provider.set("TEST", "value")

        dir_mode = data_dir.stat().st_mode
        assert stat.S_IMODE(dir_mode) == 0o700

    def test_file_is_valid_json(self, tmp_path):
        """The secrets file is valid JSON."""
        provider = FileSecretsProvider(tmp_path)
        provider.set("K1", "V1")
        provider.set("K2", "V2")

        secrets_path = tmp_path / "secrets.json"
        with open(secrets_path) as fh:
            data = json.load(fh)
        assert data == {"K1": "V1", "K2": "V2"}


# ====================================================================
# EnvironmentSecretsProvider
# ====================================================================


class TestEnvironmentSecretsProviderSetGet:
    """Tests for EnvironmentSecretsProvider set() and get()."""

    def test_set_and_get(self, monkeypatch):
        """set() stores value in os.environ, get() retrieves it."""
        provider = EnvironmentSecretsProvider()
        monkeypatch.delenv("TFTPOS_SECRET_MY_KEY", raising=False)
        provider.set("MY_KEY", "myval")
        assert provider.get("MY_KEY") == "myval"
        # cleanup
        provider.delete("MY_KEY")

    def test_get_missing_returns_none(self, monkeypatch):
        """get() returns None for a key not in environment."""
        monkeypatch.delenv(
            "TFTPOS_SECRET_NO_SUCH_KEY", raising=False
        )
        provider = EnvironmentSecretsProvider()
        assert provider.get("NO_SUCH_KEY") is None

    def test_uppercases_key(self, monkeypatch):
        """Keys are uppercased to form the env var name."""
        provider = EnvironmentSecretsProvider()
        monkeypatch.delenv(
            "TFTPOS_SECRET_LOWER_CASE", raising=False
        )
        provider.set("lower_case", "works")
        assert provider.get("lower_case") == "works"
        assert os.environ.get("TFTPOS_SECRET_LOWER_CASE") == "works"
        provider.delete("lower_case")

    def test_set_overwrites(self, monkeypatch):
        """set() overwrites an existing env var."""
        provider = EnvironmentSecretsProvider()
        monkeypatch.delenv("TFTPOS_SECRET_OVER", raising=False)
        provider.set("OVER", "old")
        provider.set("OVER", "new")
        assert provider.get("OVER") == "new"
        provider.delete("OVER")


class TestEnvironmentSecretsProviderDelete:
    """Tests for EnvironmentSecretsProvider delete()."""

    def test_delete_removes_env_var(self, monkeypatch):
        """delete() removes the environment variable."""
        provider = EnvironmentSecretsProvider()
        monkeypatch.delenv("TFTPOS_SECRET_DEL_ME", raising=False)
        provider.set("DEL_ME", "gone")
        provider.delete("DEL_ME")
        assert provider.get("DEL_ME") is None
        assert "TFTPOS_SECRET_DEL_ME" not in os.environ

    def test_delete_nonexistent_is_silent(self, monkeypatch):
        """delete() does not raise for a missing key."""
        monkeypatch.delenv(
            "TFTPOS_SECRET_NOPE", raising=False
        )
        provider = EnvironmentSecretsProvider()
        provider.delete("NOPE")  # should not raise


class TestEnvironmentSecretsProviderListKeys:
    """Tests for EnvironmentSecretsProvider list_keys()."""

    def test_list_filters_prefix(self, monkeypatch):
        """list_keys() returns only keys with the TFTPOS_SECRET_ prefix."""
        provider = EnvironmentSecretsProvider()
        # Clean slate -- remove any stale test env vars
        for k in list(os.environ):
            if k.startswith("TFTPOS_SECRET_"):
                monkeypatch.delenv(k, raising=False)

        provider.set("ALPHA", "a")
        provider.set("BETA", "b")

        keys = provider.list_keys()
        assert "ALPHA" in keys
        assert "BETA" in keys
        assert sorted(keys) == keys  # verify sorted

        provider.delete("ALPHA")
        provider.delete("BETA")

    def test_list_empty_when_none_set(self, monkeypatch):
        """list_keys() returns empty list when no TFTPOS_SECRET_ vars exist."""
        for k in list(os.environ):
            if k.startswith("TFTPOS_SECRET_"):
                monkeypatch.delenv(k, raising=False)

        provider = EnvironmentSecretsProvider()
        assert provider.list_keys() == []


# ====================================================================
# SecretsManager
# ====================================================================


class TestSecretsManagerResolve:
    """Tests for SecretsManager.resolve_profile()."""

    def _make_provider(self, secrets: dict) -> SecretsProvider:
        """Create a FileSecretsProvider with pre-loaded secrets."""
        mock = MagicMock(spec=SecretsProvider)
        mock.get.side_effect = lambda k: secrets.get(k)
        return mock

    def test_resolves_install_url(self):
        """Replaces {{secret:X}} in install_url."""
        provider = self._make_provider({"TOKEN": "abc123"})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            install_url="http://mirror.example.com/{{secret:TOKEN}}/repo",
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.install_url == "http://mirror.example.com/abc123/repo"

    def test_resolves_extra_dict(self):
        """Replaces {{secret:X}} in the extra dict values."""
        provider = self._make_provider(
            {"ROOT_PW": "$6$hashed_password"}
        )
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            extra={"rootpw_hash": "{{secret:ROOT_PW}}"},
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.extra["rootpw_hash"] == "$6$hashed_password"

    def test_resolves_nested_extra(self):
        """Replaces {{secret:X}} in nested dict within extra."""
        provider = self._make_provider({"DB_PASS": "s3cret"})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            extra={
                "database": {
                    "host": "db.example.com",
                    "password": "{{secret:DB_PASS}}",
                }
            },
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.extra["database"]["password"] == "s3cret"
        assert resolved.extra["database"]["host"] == "db.example.com"

    def test_resolves_list_in_extra(self):
        """Replaces {{secret:X}} in a list inside extra."""
        provider = self._make_provider({"SSH_KEY": "ssh-ed25519 AAAA..."})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            extra={
                "ssh_authorized_keys": ["{{secret:SSH_KEY}}"]
            },
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.extra["ssh_authorized_keys"] == [
            "ssh-ed25519 AAAA..."
        ]

    def test_leaves_non_secret_values_unchanged(self):
        """Values without {{secret:X}} are left as-is."""
        provider = self._make_provider({})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="my-server",
            os_family="fedora",
            os_version="40",
            arch="x86_64",
            install_url="http://mirror.example.com/fedora/40",
            packages=["vim", "tmux"],
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.name == "my-server"
        assert resolved.os_family == "fedora"
        assert resolved.os_version == "40"
        assert resolved.arch == "x86_64"
        assert resolved.install_url == "http://mirror.example.com/fedora/40"
        assert resolved.packages == ["vim", "tmux"]

    def test_raises_on_missing_secret(self):
        """Raises ValueError when a referenced secret is not found."""
        provider = self._make_provider({})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            extra={"rootpw_hash": "{{secret:MISSING_KEY}}"},
        )
        with pytest.raises(ValueError, match="MISSING_KEY"):
            mgr.resolve_profile(profile)

    def test_multiple_secrets_in_one_field(self):
        """Replaces multiple {{secret:X}} references in a single string."""
        provider = self._make_provider(
            {"USER": "admin", "PASS": "s3cret"}
        )
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            install_url="http://{{secret:USER}}:{{secret:PASS}}@mirror.example.com",
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.install_url == "http://admin:s3cret@mirror.example.com"

    def test_preserves_firmware_enum(self):
        """Firmware enum value is preserved through resolution."""
        provider = self._make_provider({})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            firmware=BootFirmware.UEFI,
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.firmware == BootFirmware.UEFI

    def test_preserves_bios_firmware(self):
        """BIOS firmware enum is preserved through resolution."""
        provider = self._make_provider({})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            firmware=BootFirmware.BIOS,
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.firmware == BootFirmware.BIOS

    def test_resolves_post_scripts(self):
        """Replaces {{secret:X}} in post_scripts list."""
        provider = self._make_provider({"API_TOKEN": "tok_abc"})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            post_scripts=[
                "curl -H 'Auth: {{secret:API_TOKEN}}' http://api.example.com/register",
            ],
        )
        resolved = mgr.resolve_profile(profile)
        assert "tok_abc" in resolved.post_scripts[0]
        assert "{{secret:" not in resolved.post_scripts[0]

    def test_resolves_packages_unchanged(self):
        """Package names without secrets pass through unchanged."""
        provider = self._make_provider({})
        mgr = SecretsManager(provider)
        profile = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            packages=["vim", "tmux", "htop"],
        )
        resolved = mgr.resolve_profile(profile)
        assert resolved.packages == ["vim", "tmux", "htop"]

    def test_provider_property(self):
        """The provider property returns the configured provider."""
        provider = self._make_provider({})
        mgr = SecretsManager(provider)
        assert mgr.provider is provider

    def test_returns_new_profile_object(self):
        """resolve_profile returns a new ProvisionProfile, not mutated original."""
        provider = self._make_provider({"PW": "x"})
        mgr = SecretsManager(provider)
        original = ProvisionProfile(
            name="test",
            os_family="fedora",
            os_version="40",
            extra={"pw": "{{secret:PW}}"},
        )
        resolved = mgr.resolve_profile(original)
        assert resolved is not original
        assert original.extra["pw"] == "{{secret:PW}}"
        assert resolved.extra["pw"] == "x"


# ====================================================================
# Secret reference regex
# ====================================================================


class TestSecretRefRegex:
    """Tests for the {{secret:KEY}} regex pattern."""

    def test_matches_simple_key(self):
        assert _SECRET_REF_RE.search("{{secret:MY_KEY}}")

    def test_matches_alphanumeric(self):
        assert _SECRET_REF_RE.search("{{secret:Key123}}")

    def test_extracts_key_name(self):
        m = _SECRET_REF_RE.search("prefix{{secret:ROOT_PW}}suffix")
        assert m is not None
        assert m.group(1) == "ROOT_PW"

    def test_no_match_without_braces(self):
        assert _SECRET_REF_RE.search("secret:MY_KEY") is None

    def test_no_match_with_spaces(self):
        assert _SECRET_REF_RE.search("{{ secret:MY_KEY }}") is None
