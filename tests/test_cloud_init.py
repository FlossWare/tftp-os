"""Tests for cloud-init config generation and config drive creation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tftpos.cloud_init import (
    CloudInitConfig,
    _find_iso_tool,
    _hash_password,
    _render_meta_data,
    _render_network_config,
    _render_user_data,
    create_config_drive,
    generate,
    write_config_drive,
)
from tftpos.models import ProvisionProfile


# ---- helpers ----


def _profile(**overrides) -> ProvisionProfile:
    """Build a ProvisionProfile with sensible defaults for cloud-init."""
    defaults = dict(
        name="test-node",
        os_family="ubuntu",
        os_version="24.04",
        network={
            "hostname": "test-node",
            "method": "dhcp",
            "device": "eth0",
        },
        packages=["vim", "curl"],
        post_scripts=["systemctl enable sshd"],
        extra={
            "user": "admin",
            "timezone": "UTC",
            "locale": "en_US.UTF-8",
        },
    )
    defaults.update(overrides)
    return ProvisionProfile(**defaults)


# ---- generate() ----


class TestGenerate:

    def test_returns_cloud_init_config(self):
        profile = _profile()
        config = generate(profile)

        assert isinstance(config, CloudInitConfig)
        assert isinstance(config.user_data, str)
        assert isinstance(config.meta_data, str)
        assert isinstance(config.network_config, str)

    def test_all_three_sections_non_empty(self):
        profile = _profile()
        config = generate(profile)

        assert len(config.user_data) > 0
        assert len(config.meta_data) > 0
        assert len(config.network_config) > 0


# ---- user_data ----


class TestUserData:

    def test_starts_with_cloud_config_header(self):
        profile = _profile()
        user_data = _render_user_data(profile)

        assert user_data.startswith("#cloud-config")

    def test_contains_hostname(self):
        profile = _profile(
            network={"hostname": "web-01", "method": "dhcp"},
        )
        user_data = _render_user_data(profile)

        assert "hostname: web-01" in user_data

    def test_contains_fqdn(self):
        profile = _profile(
            network={
                "hostname": "web-01",
                "fqdn": "web-01.example.com",
                "method": "dhcp",
            },
        )
        user_data = _render_user_data(profile)

        assert "fqdn: web-01.example.com" in user_data

    def test_default_fqdn_uses_dot_local(self):
        profile = _profile(
            network={"hostname": "myhost", "method": "dhcp"},
        )
        user_data = _render_user_data(profile)

        assert "fqdn: myhost.local" in user_data

    def test_contains_users_section(self):
        profile = _profile()
        user_data = _render_user_data(profile)

        assert "users:" in user_data
        assert "name: admin" in user_data

    def test_default_user_has_sudo_group(self):
        profile = _profile()
        user_data = _render_user_data(profile)

        assert "sudo" in user_data

    def test_packages_section(self):
        profile = _profile(packages=["vim", "curl", "htop"])
        user_data = _render_user_data(profile)

        assert "packages:" in user_data
        assert "- vim" in user_data
        assert "- curl" in user_data
        assert "- htop" in user_data

    def test_empty_packages_omits_section(self):
        profile = _profile(packages=[])
        user_data = _render_user_data(profile)

        assert "packages:" not in user_data

    def test_runcmd_from_post_scripts(self):
        profile = _profile(
            post_scripts=[
                "systemctl enable sshd",
                "echo done",
            ],
        )
        user_data = _render_user_data(profile)

        assert "runcmd:" in user_data
        assert "systemctl enable sshd" in user_data
        assert "echo done" in user_data

    def test_empty_post_scripts_omits_runcmd(self):
        profile = _profile(post_scripts=[])
        user_data = _render_user_data(profile)

        assert "runcmd:" not in user_data

    def test_ssh_authorized_keys(self):
        profile = _profile(
            extra={
                "user": "deploy",
                "ssh_authorized_keys": [
                    "ssh-rsa AAAA... user@host",
                ],
                "timezone": "UTC",
                "locale": "en_US.UTF-8",
            },
        )
        user_data = _render_user_data(profile)

        assert "ssh_authorized_keys:" in user_data
        assert "ssh-rsa AAAA... user@host" in user_data

    def test_password_hashes_and_unlocks_passwd(self):
        profile = _profile(
            extra={
                "user": "admin",
                "password": "s3cret",
                "timezone": "UTC",
                "locale": "en_US.UTF-8",
            },
        )
        user_data = _render_user_data(profile)

        # The password should be hashed (SHA-512), not plaintext
        assert "s3cret" not in user_data
        assert "passwd:" in user_data
        assert "$6$" in user_data
        # lock_passwd should be false when password is set
        assert "lock_passwd: false" in user_data

    def test_write_files_section(self):
        profile = _profile(
            extra={
                "user": "admin",
                "timezone": "UTC",
                "locale": "en_US.UTF-8",
                "write_files": [
                    {
                        "path": "/etc/motd",
                        "content": "Welcome!",
                        "permissions": "0644",
                        "owner": "root:root",
                    },
                ],
            },
        )
        user_data = _render_user_data(profile)

        assert "write_files:" in user_data
        assert "path: /etc/motd" in user_data
        assert "Welcome!" in user_data
        assert "0644" in user_data
        assert "root:root" in user_data

    def test_timezone_included(self):
        profile = _profile(
            extra={
                "user": "admin",
                "timezone": "America/New_York",
                "locale": "en_US.UTF-8",
            },
        )
        user_data = _render_user_data(profile)

        assert "timezone: America/New_York" in user_data

    def test_locale_included(self):
        profile = _profile(
            extra={
                "user": "admin",
                "timezone": "UTC",
                "locale": "de_DE.UTF-8",
            },
        )
        user_data = _render_user_data(profile)

        assert "locale: de_DE.UTF-8" in user_data

    def test_ssh_pwauth_default_false(self):
        profile = _profile()
        user_data = _render_user_data(profile)

        assert "ssh_pwauth: false" in user_data

    def test_disable_root_default_true(self):
        profile = _profile()
        user_data = _render_user_data(profile)

        assert "disable_root: true" in user_data

    def test_final_message(self):
        profile = _profile()
        user_data = _render_user_data(profile)

        assert "final_message:" in user_data

    def test_hostname_falls_back_to_profile_name(self):
        profile = _profile(network={"method": "dhcp"})
        user_data = _render_user_data(profile)

        assert "hostname: test-node" in user_data

    def test_custom_users_list(self):
        profile = _profile(
            extra={
                "users": [
                    {
                        "name": "alice",
                        "groups": ["wheel", "docker"],
                        "shell": "/bin/zsh",
                        "sudo": "ALL=(ALL) NOPASSWD:ALL",
                        "lock_passwd": True,
                    },
                ],
                "timezone": "UTC",
                "locale": "en_US.UTF-8",
            },
        )
        user_data = _render_user_data(profile)

        assert "name: alice" in user_data
        assert "zsh" in user_data


# ---- meta_data ----


class TestMetaData:

    def test_contains_instance_id(self):
        profile = _profile()
        meta_data = _render_meta_data(profile)

        assert "instance-id:" in meta_data

    def test_instance_id_defaults_to_hostname_version(self):
        profile = _profile(
            network={"hostname": "web-01"},
        )
        meta_data = _render_meta_data(profile)

        assert "instance-id: web-01-24.04" in meta_data

    def test_custom_instance_id(self):
        profile = _profile(
            extra={
                "instance_id": "custom-id-42",
                "user": "admin",
                "timezone": "UTC",
                "locale": "en_US.UTF-8",
            },
        )
        meta_data = _render_meta_data(profile)

        assert "instance-id: custom-id-42" in meta_data

    def test_contains_local_hostname(self):
        profile = _profile(
            network={"hostname": "db-node"},
        )
        meta_data = _render_meta_data(profile)

        assert "local-hostname: db-node" in meta_data

    def test_local_hostname_falls_back_to_name(self):
        profile = _profile(network={})
        meta_data = _render_meta_data(profile)

        assert "local-hostname: test-node" in meta_data

    def test_extra_meta_data(self):
        profile = _profile(
            extra={
                "meta_data": {"cloud-name": "openstack"},
                "user": "admin",
                "timezone": "UTC",
                "locale": "en_US.UTF-8",
            },
        )
        meta_data = _render_meta_data(profile)

        assert "cloud-name: openstack" in meta_data


# ---- network_config ----


class TestNetworkConfig:

    def test_dhcp_mode(self):
        profile = _profile(
            network={"method": "dhcp", "device": "eth0"},
        )
        network_config = _render_network_config(profile)

        assert "version: 2" in network_config
        assert "eth0:" in network_config
        assert "dhcp4: true" in network_config

    def test_dhcp_is_default_when_no_method(self):
        profile = _profile(network={"device": "ens3"})
        network_config = _render_network_config(profile)

        assert "dhcp4: true" in network_config
        assert "ens3:" in network_config

    def test_dhcp_when_network_empty(self):
        profile = _profile(network={})
        network_config = _render_network_config(profile)

        assert "dhcp4: true" in network_config

    def test_static_mode(self):
        profile = _profile(
            network={
                "method": "static",
                "device": "ens3",
                "address": "192.168.1.100/24",
                "gateway": "192.168.1.1",
                "nameservers": ["8.8.8.8", "1.1.1.1"],
            },
        )
        network_config = _render_network_config(profile)

        assert "version: 2" in network_config
        assert "ens3:" in network_config
        assert "dhcp4: false" in network_config
        assert "192.168.1.100/24" in network_config
        assert "192.168.1.1" in network_config
        assert "8.8.8.8" in network_config
        assert "1.1.1.1" in network_config

    def test_static_with_default_nameservers(self):
        profile = _profile(
            network={
                "method": "static",
                "device": "eth0",
                "address": "10.0.0.5/24",
                "gateway": "10.0.0.1",
            },
        )
        network_config = _render_network_config(profile)

        assert "8.8.8.8" in network_config
        assert "8.8.4.4" in network_config

    def test_static_default_device_is_eth0(self):
        profile = _profile(
            network={
                "method": "static",
                "address": "10.0.0.5/24",
                "gateway": "10.0.0.1",
            },
        )
        network_config = _render_network_config(profile)

        assert "eth0:" in network_config

    def test_static_has_routes_section(self):
        profile = _profile(
            network={
                "method": "static",
                "device": "eth0",
                "address": "10.0.0.5/24",
                "gateway": "10.0.0.1",
            },
        )
        network_config = _render_network_config(profile)

        assert "routes:" in network_config
        assert "to: default" in network_config
        assert "via: 10.0.0.1" in network_config


# ---- _hash_password ----


class TestHashPassword:

    def test_returns_sha512_hash(self):
        hashed = _hash_password("mypassword")

        assert hashed.startswith("$6$")

    def test_different_calls_produce_different_salts(self):
        h1 = _hash_password("password")
        h2 = _hash_password("password")

        # Same password but different salts should produce different hashes
        assert h1 != h2

    def test_hash_is_not_plaintext(self):
        hashed = _hash_password("s3cret")

        assert "s3cret" != hashed
        assert len(hashed) > 20


# ---- _find_iso_tool ----


class TestFindIsoTool:

    @patch("tftpos.cloud_init.shutil.which")
    def test_returns_genisoimage_first(self, mock_which):
        mock_which.side_effect = lambda t: (
            "/usr/bin/genisoimage" if t == "genisoimage" else None
        )

        result = _find_iso_tool()

        assert result == "genisoimage"

    @patch("tftpos.cloud_init.shutil.which")
    def test_returns_mkisofs_if_genisoimage_missing(self, mock_which):
        mock_which.side_effect = lambda t: (
            "/usr/bin/mkisofs" if t == "mkisofs" else None
        )

        result = _find_iso_tool()

        assert result == "mkisofs"

    @patch("tftpos.cloud_init.shutil.which")
    def test_returns_xorriso_as_last_option(self, mock_which):
        mock_which.side_effect = lambda t: (
            "/usr/bin/xorriso" if t == "xorriso" else None
        )

        result = _find_iso_tool()

        assert result == "xorriso"

    @patch("tftpos.cloud_init.shutil.which")
    def test_returns_none_when_no_tool_found(self, mock_which):
        mock_which.return_value = None

        result = _find_iso_tool()

        assert result is None


# ---- write_config_drive ----


class TestWriteConfigDrive:

    @patch("tftpos.cloud_init.subprocess.run")
    @patch("tftpos.cloud_init._find_iso_tool")
    def test_creates_iso_with_genisoimage(
        self, mock_find_tool, mock_run, tmp_path,
    ):
        mock_find_tool.return_value = "genisoimage"
        mock_run.return_value = MagicMock(returncode=0)

        config = CloudInitConfig(
            user_data="#cloud-config\nhostname: test",
            meta_data="instance-id: test-1\nlocal-hostname: test",
            network_config="version: 2\nethernets:\n  eth0:\n    dhcp4: true",
        )
        output = tmp_path / "cidata.iso"

        result = write_config_drive(config, output)

        assert result == output
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "genisoimage"
        assert "-volid" in cmd
        assert "cidata" in cmd
        assert str(output) in cmd

    @patch("tftpos.cloud_init.subprocess.run")
    @patch("tftpos.cloud_init._find_iso_tool")
    def test_xorriso_uses_genisoimage_compat_mode(
        self, mock_find_tool, mock_run, tmp_path,
    ):
        mock_find_tool.return_value = "/usr/bin/xorriso"
        mock_run.return_value = MagicMock(returncode=0)

        config = CloudInitConfig(
            user_data="#cloud-config\nhostname: test",
            meta_data="instance-id: test-1",
        )
        output = tmp_path / "cidata.iso"

        write_config_drive(config, output)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/xorriso"
        assert "-as" in cmd
        assert "genisoimage" in cmd

    @patch("tftpos.cloud_init._find_iso_tool")
    def test_raises_when_no_iso_tool(self, mock_find_tool, tmp_path):
        mock_find_tool.return_value = None

        config = CloudInitConfig(
            user_data="#cloud-config\nhostname: test",
            meta_data="instance-id: test-1",
        )
        output = tmp_path / "cidata.iso"

        with pytest.raises(RuntimeError, match="no ISO creation tool"):
            write_config_drive(config, output)

    @patch("tftpos.cloud_init.subprocess.run")
    @patch("tftpos.cloud_init._find_iso_tool")
    def test_writes_user_data_to_temp_dir(
        self, mock_find_tool, mock_run, tmp_path,
    ):
        mock_find_tool.return_value = "genisoimage"
        mock_run.return_value = MagicMock(returncode=0)

        written_files = {}

        original_run = mock_run.side_effect

        def capture_cmd(cmd, **kwargs):
            # The work dir is the last argument to genisoimage
            work_dir = Path(cmd[-1])
            if work_dir.exists():
                for f in work_dir.iterdir():
                    written_files[f.name] = f.read_text()
            return MagicMock(returncode=0)

        mock_run.side_effect = capture_cmd

        config = CloudInitConfig(
            user_data="#cloud-config\nhostname: test",
            meta_data="instance-id: test-1\nlocal-hostname: test",
            network_config="version: 2",
        )
        output = tmp_path / "cidata.iso"

        write_config_drive(config, output)

        assert "user-data" in written_files
        assert written_files["user-data"] == "#cloud-config\nhostname: test"
        assert "meta-data" in written_files
        assert "network-config" in written_files

    @patch("tftpos.cloud_init.subprocess.run")
    @patch("tftpos.cloud_init._find_iso_tool")
    def test_skips_network_config_when_empty(
        self, mock_find_tool, mock_run, tmp_path,
    ):
        mock_find_tool.return_value = "genisoimage"

        written_files = {}

        def capture_cmd(cmd, **kwargs):
            work_dir = Path(cmd[-1])
            if work_dir.exists():
                for f in work_dir.iterdir():
                    written_files[f.name] = f.read_text()
            return MagicMock(returncode=0)

        mock_run.side_effect = capture_cmd

        config = CloudInitConfig(
            user_data="#cloud-config\nhostname: test",
            meta_data="instance-id: test-1",
            network_config="",
        )
        output = tmp_path / "cidata.iso"

        write_config_drive(config, output)

        assert "network-config" not in written_files

    @patch("tftpos.cloud_init.subprocess.run")
    @patch("tftpos.cloud_init._find_iso_tool")
    def test_creates_parent_directories(
        self, mock_find_tool, mock_run, tmp_path,
    ):
        mock_find_tool.return_value = "genisoimage"
        mock_run.return_value = MagicMock(returncode=0)

        config = CloudInitConfig(
            user_data="#cloud-config\nhostname: test",
            meta_data="instance-id: test-1",
        )
        output = tmp_path / "nested" / "dir" / "cidata.iso"

        write_config_drive(config, output)

        assert output.parent.exists()


# ---- create_config_drive (end-to-end) ----


class TestCreateConfigDrive:

    @patch("tftpos.cloud_init.subprocess.run")
    @patch("tftpos.cloud_init._find_iso_tool")
    def test_end_to_end(
        self, mock_find_tool, mock_run, tmp_path,
    ):
        mock_find_tool.return_value = "mkisofs"
        mock_run.return_value = MagicMock(returncode=0)

        profile = _profile()
        output = tmp_path / "cloud-init.iso"

        result = create_config_drive(profile, output)

        assert result == output
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "mkisofs"

    @patch("tftpos.cloud_init._find_iso_tool")
    def test_end_to_end_raises_without_iso_tool(
        self, mock_find_tool, tmp_path,
    ):
        mock_find_tool.return_value = None

        profile = _profile()
        output = tmp_path / "cloud-init.iso"

        with pytest.raises(RuntimeError, match="no ISO creation tool"):
            create_config_drive(profile, output)
