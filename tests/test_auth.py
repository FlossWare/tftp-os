"""Tests for API key authentication and RBAC."""

from __future__ import annotations

import json
import stat

import pytest

from tftpos.auth import (
    ApiKey,
    ApiKeyStore,
    Role,
    role_has_access,
)
from tftpos.config import TftpOSConfig


# ---------------------------------------------------------------
# Role ordering
# ---------------------------------------------------------------


class TestRoleOrdering:

    def test_viewer_has_viewer_access(self):
        assert role_has_access(Role.VIEWER, Role.VIEWER)

    def test_operator_has_viewer_access(self):
        assert role_has_access(Role.OPERATOR, Role.VIEWER)

    def test_operator_has_operator_access(self):
        assert role_has_access(Role.OPERATOR, Role.OPERATOR)

    def test_admin_has_viewer_access(self):
        assert role_has_access(Role.ADMIN, Role.VIEWER)

    def test_admin_has_operator_access(self):
        assert role_has_access(Role.ADMIN, Role.OPERATOR)

    def test_admin_has_admin_access(self):
        assert role_has_access(Role.ADMIN, Role.ADMIN)

    def test_viewer_lacks_operator_access(self):
        assert not role_has_access(Role.VIEWER, Role.OPERATOR)

    def test_viewer_lacks_admin_access(self):
        assert not role_has_access(Role.VIEWER, Role.ADMIN)

    def test_operator_lacks_admin_access(self):
        assert not role_has_access(Role.OPERATOR, Role.ADMIN)


# ---------------------------------------------------------------
# ApiKeyStore
# ---------------------------------------------------------------


class TestApiKeyStoreCreate:

    def test_returns_raw_key_and_api_key(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        raw_key, api_key = store.create_key("test", Role.VIEWER)
        assert isinstance(raw_key, str)
        assert isinstance(api_key, ApiKey)

    def test_raw_key_starts_with_prefix(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        raw_key, _ = store.create_key("test", Role.VIEWER)
        assert raw_key.startswith("tftpos_")

    def test_raw_key_is_unique(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        raw1, _ = store.create_key("key1", Role.VIEWER)
        raw2, _ = store.create_key("key2", Role.VIEWER)
        assert raw1 != raw2

    def test_created_key_has_correct_role(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        _, api_key = store.create_key("test", Role.ADMIN)
        assert api_key.role == Role.ADMIN

    def test_created_key_is_enabled(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        _, api_key = store.create_key("test", Role.VIEWER)
        assert api_key.enabled is True

    def test_created_key_has_name(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        _, api_key = store.create_key("my-key", Role.OPERATOR)
        assert api_key.name == "my-key"

    def test_created_key_has_timestamp(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        _, api_key = store.create_key("test", Role.VIEWER)
        assert api_key.created_at > 0


class TestApiKeyStoreValidate:

    def test_validate_correct_key(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        raw_key, _ = store.create_key("test", Role.VIEWER)
        result = store.validate(raw_key)
        assert result is not None
        assert result.name == "test"

    def test_validate_wrong_key(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        store.create_key("test", Role.VIEWER)
        assert store.validate("tftpos_bogus") is None

    def test_validate_disabled_key(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        raw_key, _ = store.create_key("test", Role.VIEWER)
        store.revoke("test")
        assert store.validate(raw_key) is None

    def test_validate_updates_last_used(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        raw_key, api_key = store.create_key("test", Role.VIEWER)
        assert api_key.last_used_at is None
        result = store.validate(raw_key)
        assert result.last_used_at is not None
        assert result.last_used_at > 0


class TestApiKeyStoreList:

    def test_list_empty(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        assert store.list_keys() == []

    def test_list_returns_all(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        store.create_key("key1", Role.VIEWER)
        store.create_key("key2", Role.ADMIN)
        keys = store.list_keys()
        assert len(keys) == 2
        names = {k.name for k in keys}
        assert names == {"key1", "key2"}


class TestApiKeyStoreRevoke:

    def test_revoke_disables_key(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        raw_key, _ = store.create_key("test", Role.VIEWER)
        assert store.revoke("test") is True
        assert store.validate(raw_key) is None

    def test_revoke_nonexistent(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        assert store.revoke("nope") is False


class TestApiKeyStoreDelete:

    def test_delete_removes_key(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        store.create_key("test", Role.VIEWER)
        assert store.delete("test") is True
        assert store.list_keys() == []

    def test_delete_nonexistent(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        assert store.delete("nope") is False


class TestApiKeyStoreEmpty:

    def test_is_empty_initially(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        assert store.is_empty() is True

    def test_is_not_empty_after_create(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        store.create_key("test", Role.VIEWER)
        assert store.is_empty() is False


class TestApiKeyStorePermissions:

    def test_file_permissions(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        store.create_key("test", Role.VIEWER)
        keys_path = tmp_path / "auth_keys.json"
        mode = stat.S_IMODE(keys_path.stat().st_mode)
        assert mode == 0o600

    def test_dir_permissions(self, tmp_path):
        sub = tmp_path / "subdir"
        store = ApiKeyStore(sub)
        store.create_key("test", Role.VIEWER)
        mode = stat.S_IMODE(sub.stat().st_mode)
        assert mode == 0o700

    def test_file_is_valid_json(self, tmp_path):
        store = ApiKeyStore(tmp_path)
        store.create_key("test", Role.VIEWER)
        keys_path = tmp_path / "auth_keys.json"
        data = json.loads(keys_path.read_text())
        assert isinstance(data, dict)
        assert len(data) == 1


# ---------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------


class TestConfigAuth:

    def test_default_auth_disabled(self):
        config = TftpOSConfig()
        assert config.auth_enabled is False

    def test_config_load_auth_section(self, tmp_path):
        from tftpos.config import load_config

        config_path = tmp_path / "tftpos.toml"
        config_path.write_text(
            "[server]\n"
            'host = "0.0.0.0"\n'
            "port = 8443\n"
            "\n"
            "[auth]\n"
            "enabled = true\n"
        )
        config = load_config(config_path)
        assert config.auth_enabled is True

    def test_config_load_auth_missing(self, tmp_path):
        from tftpos.config import load_config

        config_path = tmp_path / "tftpos.toml"
        config_path.write_text(
            "[server]\n"
            'host = "0.0.0.0"\n'
            "port = 8443\n"
        )
        config = load_config(config_path)
        assert config.auth_enabled is False


