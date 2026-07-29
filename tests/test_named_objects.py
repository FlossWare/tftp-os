"""Tests for tftpos.named_objects -- named distros and hosts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from tftpos.named_objects import (
    NamedDistro,
    NamedHost,
    NamedObjectStore,
    _validate_name,
)


# ---------------------------------------------------------------------------
# NamedDistro dataclass
# ---------------------------------------------------------------------------


class TestNamedDistro:

    def test_required_fields(self):
        d = NamedDistro(
            name="fedora42",
            os_family="fedora",
            vendor="fedora",
            version="42",
        )
        assert d.name == "fedora42"
        assert d.os_family == "fedora"
        assert d.vendor == "fedora"
        assert d.version == "42"

    def test_default_arch(self):
        d = NamedDistro(
            name="test", os_family="f", vendor="v", version="1"
        )
        assert d.arch == "x86_64"

    def test_default_empty_strings(self):
        d = NamedDistro(
            name="test", os_family="f", vendor="v", version="1"
        )
        assert d.kernel_path == ""
        assert d.initrd_path == ""
        assert d.install_url == ""
        assert d.comment == ""

    def test_all_fields_populated(self):
        d = NamedDistro(
            name="rhel9-server",
            os_family="rhel",
            vendor="redhat",
            version="9.4",
            arch="aarch64",
            kernel_path="/srv/tftp/rhel9/vmlinuz",
            initrd_path="/srv/tftp/rhel9/initrd.img",
            install_url="http://mirror/rhel/9",
            comment="production RHEL 9",
        )
        assert d.arch == "aarch64"
        assert d.kernel_path == "/srv/tftp/rhel9/vmlinuz"
        assert d.initrd_path == "/srv/tftp/rhel9/initrd.img"
        assert d.install_url == "http://mirror/rhel/9"
        assert d.comment == "production RHEL 9"

    def test_asdict_roundtrip(self):
        d = NamedDistro(
            name="test", os_family="f", vendor="v", version="1"
        )
        data = asdict(d)
        d2 = NamedDistro(**data)
        assert d == d2


# ---------------------------------------------------------------------------
# NamedHost dataclass
# ---------------------------------------------------------------------------


class TestNamedHost:

    def test_required_fields(self):
        h = NamedHost(name="web01", mac="aa:bb:cc:dd:ee:ff")
        assert h.name == "web01"
        assert h.mac == "aa:bb:cc:dd:ee:ff"

    def test_default_empty_strings(self):
        h = NamedHost(name="web01", mac="aa:bb:cc:dd:ee:ff")
        assert h.profile == ""
        assert h.distro == ""
        assert h.hostname == ""
        assert h.gateway == ""
        assert h.ip_address == ""
        assert h.netmask == ""
        assert h.comment == ""

    def test_default_mutable_fields(self):
        h1 = NamedHost(name="a", mac="00:00:00:00:00:01")
        h2 = NamedHost(name="b", mac="00:00:00:00:00:02")
        h1.nameservers.append("8.8.8.8")
        h1.extra["foo"] = "bar"
        assert h2.nameservers == []
        assert h2.extra == {}

    def test_all_fields_populated(self):
        h = NamedHost(
            name="db-primary",
            mac="aa:bb:cc:dd:ee:01",
            profile="database",
            distro="rhel9-server",
            hostname="db-primary.example.com",
            gateway="10.0.0.1",
            nameservers=["8.8.8.8", "8.8.4.4"],
            ip_address="10.0.0.50",
            netmask="255.255.255.0",
            comment="primary database server",
            extra={"rack": "A3", "slot": 12},
        )
        assert h.profile == "database"
        assert h.distro == "rhel9-server"
        assert h.hostname == "db-primary.example.com"
        assert h.gateway == "10.0.0.1"
        assert h.nameservers == ["8.8.8.8", "8.8.4.4"]
        assert h.ip_address == "10.0.0.50"
        assert h.netmask == "255.255.255.0"
        assert h.comment == "primary database server"
        assert h.extra == {"rack": "A3", "slot": 12}

    def test_asdict_roundtrip(self):
        h = NamedHost(
            name="web01", mac="aa:bb:cc:dd:ee:ff",
            nameservers=["8.8.8.8"], extra={"key": "val"},
        )
        data = asdict(h)
        h2 = NamedHost(**data)
        assert h == h2


# ---------------------------------------------------------------------------
# _validate_name helper
# ---------------------------------------------------------------------------


class TestValidateName:

    def test_valid_simple_name(self):
        _validate_name("fedora42")

    def test_valid_name_with_dots(self):
        _validate_name("rhel-9.4-server")

    def test_valid_name_with_underscores(self):
        _validate_name("my_distro_v2")

    def test_valid_name_with_dashes(self):
        _validate_name("fedora-42-server")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_name("")

    def test_dotdot_rejected(self):
        with pytest.raises(ValueError, match="must not contain"):
            _validate_name("../escape")

    def test_dotdot_in_middle_rejected(self):
        with pytest.raises(ValueError, match="must not contain"):
            _validate_name("foo/../bar")

    def test_forward_slash_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_name("a/b")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_name("a\\b")

    def test_space_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_name("my distro")

    def test_leading_dot_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_name(".hidden")

    def test_leading_dash_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_name("-bad")

    def test_special_chars_rejected(self):
        for char in ["$", "!", "@", "#", "%", "^", "&", "*"]:
            with pytest.raises(ValueError):
                _validate_name(f"bad{char}name")


# ---------------------------------------------------------------------------
# NamedObjectStore -- distro CRUD
# ---------------------------------------------------------------------------


class TestNamedObjectStoreDistro:

    @pytest.fixture
    def store(self, tmp_path: Path) -> NamedObjectStore:
        return NamedObjectStore(tmp_path)

    @pytest.fixture
    def sample_distro(self) -> NamedDistro:
        return NamedDistro(
            name="fedora42-server",
            os_family="fedora",
            vendor="fedora",
            version="42",
            arch="x86_64",
            kernel_path="/srv/tftp/fedora42/vmlinuz",
            initrd_path="/srv/tftp/fedora42/initrd.img",
            install_url="http://mirror/fedora/42",
            comment="Fedora 42 server",
        )

    def test_add_and_get_distro(self, store, sample_distro):
        store.add_distro(sample_distro)
        got = store.get_distro("fedora42-server")
        assert got is not None
        assert got.name == "fedora42-server"
        assert got.os_family == "fedora"
        assert got.vendor == "fedora"
        assert got.version == "42"
        assert got.arch == "x86_64"

    def test_get_nonexistent_distro_returns_none(self, store):
        assert store.get_distro("no-such-distro") is None

    def test_list_distros_empty(self, store):
        assert store.list_distros() == []

    def test_list_distros_sorted(self, store):
        for name in ["zulu", "alpha", "mike"]:
            store.add_distro(NamedDistro(
                name=name, os_family="f", vendor="v", version="1",
            ))
        names = [d.name for d in store.list_distros()]
        assert names == ["alpha", "mike", "zulu"]

    def test_delete_existing_distro(self, store, sample_distro):
        store.add_distro(sample_distro)
        assert store.delete_distro("fedora42-server") is True
        assert store.get_distro("fedora42-server") is None

    def test_delete_nonexistent_distro_returns_false(self, store):
        assert store.delete_distro("no-such-distro") is False

    def test_update_distro(self, store, sample_distro):
        store.add_distro(sample_distro)
        updated = store.update_distro("fedora42-server", {
            "version": "43",
            "comment": "upgraded",
        })
        assert updated is not None
        assert updated.version == "43"
        assert updated.comment == "upgraded"
        # Verify persisted
        got = store.get_distro("fedora42-server")
        assert got is not None
        assert got.version == "43"

    def test_update_nonexistent_distro_returns_none(self, store):
        assert store.update_distro("ghost", {"version": "2"}) is None

    def test_update_ignores_unknown_fields(self, store, sample_distro):
        store.add_distro(sample_distro)
        updated = store.update_distro("fedora42-server", {
            "nonexistent_field": "ignored",
        })
        assert updated is not None
        assert not hasattr(updated, "nonexistent_field")

    def test_add_distro_creates_json_file(self, store, sample_distro, tmp_path):
        store.add_distro(sample_distro)
        json_path = tmp_path / "distros" / "fedora42-server.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["name"] == "fedora42-server"
        assert data["os_family"] == "fedora"

    def test_add_distro_path_traversal_rejected(self, store):
        bad = NamedDistro(
            name="../escape",
            os_family="f", vendor="v", version="1",
        )
        with pytest.raises(ValueError):
            store.add_distro(bad)

    def test_persistence_across_store_instances(self, tmp_path):
        store1 = NamedObjectStore(tmp_path)
        store1.add_distro(NamedDistro(
            name="persist-test",
            os_family="fedora", vendor="fedora", version="40",
        ))

        store2 = NamedObjectStore(tmp_path)
        got = store2.get_distro("persist-test")
        assert got is not None
        assert got.name == "persist-test"
        assert got.os_family == "fedora"

    def test_add_overwrites_existing(self, store):
        store.add_distro(NamedDistro(
            name="overwrite", os_family="f1",
            vendor="v", version="1",
        ))
        store.add_distro(NamedDistro(
            name="overwrite", os_family="f2",
            vendor="v", version="2",
        ))
        got = store.get_distro("overwrite")
        assert got is not None
        assert got.os_family == "f2"
        assert got.version == "2"


# ---------------------------------------------------------------------------
# NamedObjectStore -- host CRUD
# ---------------------------------------------------------------------------


class TestNamedObjectStoreHost:

    @pytest.fixture
    def store(self, tmp_path: Path) -> NamedObjectStore:
        return NamedObjectStore(tmp_path)

    @pytest.fixture
    def sample_host(self) -> NamedHost:
        return NamedHost(
            name="web01",
            mac="aa:bb:cc:dd:ee:ff",
            profile="webserver",
            distro="fedora42-server",
            hostname="web01.example.com",
            gateway="10.0.0.1",
            nameservers=["8.8.8.8", "8.8.4.4"],
            ip_address="10.0.0.10",
            netmask="255.255.255.0",
            comment="primary web server",
            extra={"rack": "B2"},
        )

    def test_add_and_get_host(self, store, sample_host):
        store.add_host(sample_host)
        got = store.get_host("web01")
        assert got is not None
        assert got.name == "web01"
        assert got.mac == "aa:bb:cc:dd:ee:ff"
        assert got.profile == "webserver"
        assert got.distro == "fedora42-server"
        assert got.hostname == "web01.example.com"
        assert got.nameservers == ["8.8.8.8", "8.8.4.4"]
        assert got.extra == {"rack": "B2"}

    def test_get_nonexistent_host_returns_none(self, store):
        assert store.get_host("no-such-host") is None

    def test_list_hosts_empty(self, store):
        assert store.list_hosts() == []

    def test_list_hosts_sorted(self, store):
        for name in ["zebra", "ape", "lion"]:
            store.add_host(NamedHost(
                name=name, mac=f"00:00:00:00:00:{ord(name[0]):02x}",
            ))
        names = [h.name for h in store.list_hosts()]
        assert names == ["ape", "lion", "zebra"]

    def test_delete_existing_host(self, store, sample_host):
        store.add_host(sample_host)
        assert store.delete_host("web01") is True
        assert store.get_host("web01") is None

    def test_delete_nonexistent_host_returns_false(self, store):
        assert store.delete_host("no-such-host") is False

    def test_update_host(self, store, sample_host):
        store.add_host(sample_host)
        updated = store.update_host("web01", {
            "ip_address": "10.0.0.20",
            "comment": "moved to new IP",
        })
        assert updated is not None
        assert updated.ip_address == "10.0.0.20"
        assert updated.comment == "moved to new IP"
        # Verify persisted
        got = store.get_host("web01")
        assert got is not None
        assert got.ip_address == "10.0.0.20"

    def test_update_nonexistent_host_returns_none(self, store):
        assert store.update_host("ghost", {"mac": "11:22:33:44:55:66"}) is None

    def test_update_ignores_unknown_fields(self, store, sample_host):
        store.add_host(sample_host)
        updated = store.update_host("web01", {
            "nonexistent_field": "ignored",
        })
        assert updated is not None
        assert not hasattr(updated, "nonexistent_field")

    def test_add_host_creates_json_file(self, store, sample_host, tmp_path):
        store.add_host(sample_host)
        json_path = tmp_path / "hosts" / "web01.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["name"] == "web01"
        assert data["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_add_host_path_traversal_rejected(self, store):
        bad = NamedHost(name="../escape", mac="aa:bb:cc:dd:ee:ff")
        with pytest.raises(ValueError):
            store.add_host(bad)

    def test_persistence_across_store_instances(self, tmp_path):
        store1 = NamedObjectStore(tmp_path)
        store1.add_host(NamedHost(
            name="persist-host", mac="11:22:33:44:55:66",
        ))

        store2 = NamedObjectStore(tmp_path)
        got = store2.get_host("persist-host")
        assert got is not None
        assert got.name == "persist-host"
        assert got.mac == "11:22:33:44:55:66"


# ---------------------------------------------------------------------------
# NamedObjectStore -- find_host_by_mac
# ---------------------------------------------------------------------------


class TestFindHostByMac:

    @pytest.fixture
    def store(self, tmp_path: Path) -> NamedObjectStore:
        s = NamedObjectStore(tmp_path)
        s.add_host(NamedHost(name="h1", mac="AA:BB:CC:DD:EE:01"))
        s.add_host(NamedHost(name="h2", mac="aa:bb:cc:dd:ee:02"))
        s.add_host(NamedHost(name="h3", mac="11-22-33-44-55-66"))
        return s

    def test_find_exact_match(self, store):
        h = store.find_host_by_mac("AA:BB:CC:DD:EE:01")
        assert h is not None
        assert h.name == "h1"

    def test_find_case_insensitive(self, store):
        h = store.find_host_by_mac("aa:bb:cc:dd:ee:01")
        assert h is not None
        assert h.name == "h1"

    def test_find_dash_to_colon_normalisation(self, store):
        h = store.find_host_by_mac("11:22:33:44:55:66")
        assert h is not None
        assert h.name == "h3"

    def test_find_nonexistent_mac_returns_none(self, store):
        assert store.find_host_by_mac("ff:ff:ff:ff:ff:ff") is None

    def test_find_with_dashes_in_query(self, store):
        h = store.find_host_by_mac("AA-BB-CC-DD-EE-02")
        assert h is not None
        assert h.name == "h2"


# ---------------------------------------------------------------------------
# NamedObjectStore -- directory creation
# ---------------------------------------------------------------------------


class TestStoreDirectoryCreation:

    def test_creates_distro_dir(self, tmp_path):
        data_dir = tmp_path / "new" / "nested"
        store = NamedObjectStore(data_dir)
        assert (data_dir / "distros").is_dir()
        assert (data_dir / "hosts").is_dir()

    def test_idempotent_creation(self, tmp_path):
        """Creating a store twice on same dir does not raise."""
        NamedObjectStore(tmp_path)
        NamedObjectStore(tmp_path)
        assert (tmp_path / "distros").is_dir()


# ---------------------------------------------------------------------------
# Edge cases and integration
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_distro_with_minimal_name(self, tmp_path):
        store = NamedObjectStore(tmp_path)
        store.add_distro(NamedDistro(
            name="a", os_family="f", vendor="v", version="1",
        ))
        assert store.get_distro("a") is not None

    def test_host_with_minimal_name(self, tmp_path):
        store = NamedObjectStore(tmp_path)
        store.add_host(NamedHost(name="x", mac="00:00:00:00:00:01"))
        assert store.get_host("x") is not None

    def test_get_distro_with_invalid_name_raises(self, tmp_path):
        store = NamedObjectStore(tmp_path)
        with pytest.raises(ValueError):
            store.get_distro("../bad")

    def test_delete_distro_with_invalid_name_raises(self, tmp_path):
        store = NamedObjectStore(tmp_path)
        with pytest.raises(ValueError):
            store.delete_distro("../bad")

    def test_get_host_with_invalid_name_raises(self, tmp_path):
        store = NamedObjectStore(tmp_path)
        with pytest.raises(ValueError):
            store.get_host("a/b")

    def test_delete_host_with_invalid_name_raises(self, tmp_path):
        store = NamedObjectStore(tmp_path)
        with pytest.raises(ValueError):
            store.delete_host("a\\b")

    def test_distro_json_structure(self, tmp_path):
        """Verify the on-disk JSON has the expected keys."""
        store = NamedObjectStore(tmp_path)
        store.add_distro(NamedDistro(
            name="json-check", os_family="f",
            vendor="v", version="1",
        ))
        path = tmp_path / "distros" / "json-check.json"
        data = json.loads(path.read_text())
        expected_keys = {
            "name", "os_family", "vendor", "version",
            "arch", "kernel_path", "initrd_path",
            "install_url", "comment",
        }
        assert set(data.keys()) == expected_keys

    def test_host_json_structure(self, tmp_path):
        """Verify the on-disk JSON has the expected keys."""
        store = NamedObjectStore(tmp_path)
        store.add_host(NamedHost(
            name="json-check", mac="00:00:00:00:00:01",
        ))
        path = tmp_path / "hosts" / "json-check.json"
        data = json.loads(path.read_text())
        expected_keys = {
            "name", "mac", "profile", "distro", "hostname",
            "gateway", "nameservers", "ip_address", "netmask",
            "comment", "extra",
        }
        assert set(data.keys()) == expected_keys

    def test_mixed_distro_and_host_operations(self, tmp_path):
        """Distro and host stores are independent."""
        store = NamedObjectStore(tmp_path)
        store.add_distro(NamedDistro(
            name="shared-name", os_family="f",
            vendor="v", version="1",
        ))
        store.add_host(NamedHost(
            name="shared-name", mac="00:00:00:00:00:01",
        ))
        assert store.get_distro("shared-name") is not None
        assert store.get_host("shared-name") is not None
        # Deleting one doesn't affect the other
        store.delete_distro("shared-name")
        assert store.get_distro("shared-name") is None
        assert store.get_host("shared-name") is not None
