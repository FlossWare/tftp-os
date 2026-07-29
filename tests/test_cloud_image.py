"""Tests for cloud image import, management, and related features (issue #7)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tftpos.models import CloudImage, HostRule, ProvisionProfile


# ---------------------------------------------------------------
# CloudImage dataclass tests
# ---------------------------------------------------------------


class TestCloudImageModel:

    def test_defaults(self):
        img = CloudImage(
            name="fedora-40-x86_64",
            os_family="fedora",
            vendor="fedora",
            version="40",
        )
        assert img.name == "fedora-40-x86_64"
        assert img.os_family == "fedora"
        assert img.vendor == "fedora"
        assert img.version == "40"
        assert img.arch == "x86_64"
        assert img.format == "qcow2"
        assert img.path == Path(".")
        assert img.size_bytes == 0
        assert img.cloud_init is True

    def test_custom_fields(self):
        img = CloudImage(
            name="ubuntu-24.04-aarch64",
            os_family="ubuntu",
            vendor="canonical",
            version="24.04",
            arch="aarch64",
            format="vmdk",
            path=Path("/srv/images/ubuntu.vmdk"),
            size_bytes=2_000_000_000,
            cloud_init=False,
        )
        assert img.arch == "aarch64"
        assert img.format == "vmdk"
        assert img.path == Path("/srv/images/ubuntu.vmdk")
        assert img.size_bytes == 2_000_000_000
        assert img.cloud_init is False

    def test_supported_formats(self):
        for fmt in ("qcow2", "raw", "vmdk", "vhd", "vhdx"):
            img = CloudImage(
                name="test",
                os_family="test",
                vendor="test",
                version="1",
                format=fmt,
            )
            assert img.format == fmt

    def test_mutable_default_path_independent(self):
        a = CloudImage(name="a", os_family="x", vendor="x", version="1")
        b = CloudImage(name="b", os_family="x", vendor="x", version="1")
        assert a.path == b.path == Path(".")


# ---------------------------------------------------------------
# HostRule deploy_mode field tests
# ---------------------------------------------------------------


class TestHostRuleDeployMode:

    def test_default_is_pxe(self):
        rule = HostRule(
            profile="test",
            os_family="fedora",
            os_version="40",
        )
        assert rule.deploy_mode == "tftp"

    def test_image_mode(self):
        rule = HostRule(
            profile="test",
            os_family="fedora",
            os_version="40",
            deploy_mode="image",
        )
        assert rule.deploy_mode == "image"

    def test_deploy_mode_preserves_other_defaults(self):
        rule = HostRule(
            profile="test",
            os_family="fedora",
            os_version="40",
            deploy_mode="image",
        )
        assert rule.priority == 100
        assert rule.mac is None


# ---------------------------------------------------------------
# Cloud image manager tests
# ---------------------------------------------------------------


class TestImportCloudImageFromFile:

    @patch("tftpos.cloud_image.shutil.copy2")
    def test_import_local_file(self, mock_copy, tmp_path):
        from tftpos.cloud_image import import_cloud_image

        src = tmp_path / "source.qcow2"
        src.write_bytes(b"\x00" * 1024)

        images_dir = tmp_path / "images"

        def fake_copy(src_str, dest_str):
            Path(dest_str).write_bytes(b"\x00" * 1024)

        mock_copy.side_effect = fake_copy

        image = import_cloud_image(
            source=str(src),
            os_family="fedora",
            vendor="fedora",
            version="40",
            arch="x86_64",
            fmt="qcow2",
            images_dir=images_dir,
        )

        assert image.name == "fedora-40-x86_64"
        assert image.os_family == "fedora"
        assert image.vendor == "fedora"
        assert image.version == "40"
        assert image.format == "qcow2"
        mock_copy.assert_called_once()

    def test_import_local_file_real_copy(self, tmp_path):
        from tftpos.cloud_image import import_cloud_image

        src = tmp_path / "source.qcow2"
        src.write_bytes(b"\x00" * 512)

        images_dir = tmp_path / "images"

        image = import_cloud_image(
            source=str(src),
            os_family="ubuntu",
            vendor="canonical",
            version="24.04",
            images_dir=images_dir,
        )

        assert image.path.exists()
        assert image.size_bytes == 512
        assert image.name == "canonical-24.04-x86_64"

    def test_import_nonexistent_file_raises(self, tmp_path):
        from tftpos.cloud_image import import_cloud_image

        with pytest.raises(FileNotFoundError, match="source image not found"):
            import_cloud_image(
                source="/nonexistent/image.qcow2",
                os_family="fedora",
                vendor="fedora",
                version="40",
                images_dir=tmp_path / "images",
            )

    def test_import_unsupported_format_raises(self, tmp_path):
        from tftpos.cloud_image import import_cloud_image

        src = tmp_path / "image.iso"
        src.write_bytes(b"\x00")

        with pytest.raises(ValueError, match="unsupported image format"):
            import_cloud_image(
                source=str(src),
                os_family="fedora",
                vendor="fedora",
                version="40",
                fmt="iso",
                images_dir=tmp_path / "images",
            )

    def test_import_requires_dir_argument(self):
        from tftpos.cloud_image import import_cloud_image

        with pytest.raises(ValueError, match="images_dir or data_dir"):
            import_cloud_image(
                source="/some/file.qcow2",
                os_family="fedora",
                vendor="fedora",
                version="40",
            )


class TestImportCloudImageFromURL:

    @patch("tftpos.cloud_image._download_file")
    def test_import_from_url(self, mock_download, tmp_path):
        from tftpos.cloud_image import import_cloud_image

        def fake_download(url, dest):
            dest.write_bytes(b"\x00" * 2048)

        mock_download.side_effect = fake_download

        image = import_cloud_image(
            source="https://cloud.example.com/fedora-40.qcow2",
            os_family="fedora",
            vendor="fedora",
            version="40",
            images_dir=tmp_path / "images",
        )

        assert image.name == "fedora-40-x86_64"
        assert image.size_bytes == 2048
        mock_download.assert_called_once()
        call_args = mock_download.call_args[0]
        assert call_args[0] == "https://cloud.example.com/fedora-40.qcow2"

    @patch("tftpos.cloud_image._download_file")
    def test_import_url_uses_data_dir(self, mock_download, tmp_path):
        from tftpos.cloud_image import import_cloud_image

        def fake_download(url, dest):
            dest.write_bytes(b"\x00" * 100)

        mock_download.side_effect = fake_download

        image = import_cloud_image(
            source="http://mirror.example.com/ubuntu-24.04.raw",
            os_family="ubuntu",
            vendor="canonical",
            version="24.04",
            fmt="raw",
            data_dir=tmp_path,
        )

        assert "images" in str(image.path)
        assert image.format == "raw"


# ---------------------------------------------------------------
# Convert / Resize tests
# ---------------------------------------------------------------


class TestConvertImage:

    @patch("tftpos.cloud_image.subprocess.run")
    def test_convert_calls_qemu_img(self, mock_run, tmp_path):
        from tftpos.cloud_image import convert_image

        mock_run.return_value = MagicMock(returncode=0)

        src = tmp_path / "source.qcow2"
        src.write_bytes(b"\x00")
        dest = tmp_path / "output.raw"

        result = convert_image(src, "raw", dest)

        assert result == dest
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "qemu-img"
        assert cmd[1] == "convert"
        assert "-O" in cmd
        assert "raw" in cmd

    def test_convert_nonexistent_source_raises(self, tmp_path):
        from tftpos.cloud_image import convert_image

        with pytest.raises(FileNotFoundError, match="source image not found"):
            convert_image(
                Path("/nonexistent/image.qcow2"),
                "raw",
                tmp_path / "output.raw",
            )

    def test_convert_unsupported_format_raises(self, tmp_path):
        from tftpos.cloud_image import convert_image

        src = tmp_path / "image.qcow2"
        src.write_bytes(b"\x00")

        with pytest.raises(ValueError, match="unsupported target format"):
            convert_image(src, "badformat", tmp_path / "out.bad")

    @patch("tftpos.cloud_image.subprocess.run")
    def test_convert_creates_parent_dirs(self, mock_run, tmp_path):
        from tftpos.cloud_image import convert_image

        mock_run.return_value = MagicMock(returncode=0)
        src = tmp_path / "source.raw"
        src.write_bytes(b"\x00")
        dest = tmp_path / "nested" / "dir" / "output.qcow2"

        convert_image(src, "qcow2", dest)

        assert dest.parent.exists()


class TestResizeImage:

    @patch("tftpos.cloud_image.subprocess.run")
    def test_resize_calls_qemu_img(self, mock_run, tmp_path):
        from tftpos.cloud_image import resize_image

        mock_run.return_value = MagicMock(returncode=0)

        img = tmp_path / "disk.qcow2"
        img.write_bytes(b"\x00")

        resize_image(img, "20G")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["qemu-img", "resize", str(img), "20G"]

    def test_resize_nonexistent_raises(self):
        from tftpos.cloud_image import resize_image

        with pytest.raises(FileNotFoundError, match="image not found"):
            resize_image(Path("/nonexistent/disk.qcow2"), "10G")


# ---------------------------------------------------------------
# List / Delete tests
# ---------------------------------------------------------------


class TestListImages:

    def test_list_empty_dir(self, tmp_path):
        from tftpos.cloud_image import list_images

        images = list_images(images_dir=tmp_path / "images")
        assert images == []

    def test_list_finds_images(self, tmp_path):
        from tftpos.cloud_image import import_cloud_image, list_images

        src = tmp_path / "source.qcow2"
        src.write_bytes(b"\x00" * 100)

        import_cloud_image(
            source=str(src),
            os_family="fedora",
            vendor="fedora",
            version="40",
            images_dir=tmp_path / "images",
        )

        images = list_images(images_dir=tmp_path / "images")
        assert len(images) == 1
        assert images[0].name == "fedora-40-x86_64"

    def test_list_with_data_dir(self, tmp_path):
        from tftpos.cloud_image import import_cloud_image, list_images

        src = tmp_path / "source.raw"
        src.write_bytes(b"\x00" * 50)

        import_cloud_image(
            source=str(src),
            os_family="ubuntu",
            vendor="canonical",
            version="24.04",
            fmt="raw",
            data_dir=tmp_path,
        )

        images = list_images(data_dir=tmp_path)
        assert len(images) == 1
        assert images[0].vendor == "canonical"

    def test_list_requires_dir_argument(self):
        from tftpos.cloud_image import list_images

        with pytest.raises(ValueError, match="images_dir or data_dir"):
            list_images()


class TestDeleteImage:

    def test_delete_existing_image(self, tmp_path):
        from tftpos.cloud_image import (
            delete_image,
            import_cloud_image,
            list_images,
        )

        src = tmp_path / "source.qcow2"
        src.write_bytes(b"\x00" * 100)

        import_cloud_image(
            source=str(src),
            os_family="fedora",
            vendor="fedora",
            version="40",
            images_dir=tmp_path / "images",
        )

        result = delete_image(
            "fedora-40-x86_64",
            images_dir=tmp_path / "images",
        )
        assert result is True

        images = list_images(images_dir=tmp_path / "images")
        assert len(images) == 0

    def test_delete_nonexistent_image(self, tmp_path):
        from tftpos.cloud_image import delete_image

        result = delete_image(
            "nonexistent",
            images_dir=tmp_path / "images",
        )
        assert result is False

    def test_delete_requires_dir_argument(self):
        from tftpos.cloud_image import delete_image

        with pytest.raises(ValueError, match="images_dir or data_dir"):
            delete_image("test")


# ---------------------------------------------------------------
# Cloud-init generation convenience functions
# ---------------------------------------------------------------


class TestGenerateUserData:

    def test_returns_cloud_config(self):
        from tftpos.cloud_init import generate_user_data

        profile = ProvisionProfile(
            name="test-node",
            os_family="ubuntu",
            os_version="24.04",
            network={"hostname": "test-node", "method": "dhcp"},
            packages=["vim"],
            extra={"user": "admin", "timezone": "UTC", "locale": "en_US.UTF-8"},
        )
        result = generate_user_data(profile)
        assert result.startswith("#cloud-config")
        assert "hostname: test-node" in result

    def test_includes_packages(self):
        from tftpos.cloud_init import generate_user_data

        profile = ProvisionProfile(
            name="pkg-test",
            os_family="fedora",
            os_version="40",
            packages=["curl", "wget"],
            extra={"user": "admin", "timezone": "UTC", "locale": "en_US.UTF-8"},
        )
        result = generate_user_data(profile)
        assert "- curl" in result
        assert "- wget" in result


class TestGenerateMetaData:

    def test_returns_meta_data(self):
        from tftpos.cloud_init import generate_meta_data

        result = generate_meta_data("web-server-01")
        assert "instance-id: web-server-01" in result
        assert "local-hostname: web-server-01" in result

    def test_custom_instance_id(self):
        from tftpos.cloud_init import generate_meta_data

        result = generate_meta_data("myhost", instance_id="custom-42")
        assert "instance-id: custom-42" in result
        assert "local-hostname: myhost" in result


class TestGenerateNetworkConfig:

    def test_dhcp_config(self):
        from tftpos.cloud_init import generate_network_config

        profile = ProvisionProfile(
            name="test",
            os_family="ubuntu",
            os_version="24.04",
            network={"method": "dhcp", "device": "eth0"},
        )
        result = generate_network_config(profile)
        assert "dhcp4: true" in result


# ---------------------------------------------------------------
# Config drive ISO creation (mock subprocess)
# ---------------------------------------------------------------


class TestConfigDriveISO:

    @patch("tftpos.cloud_init.subprocess.run")
    @patch("tftpos.cloud_init._find_iso_tool")
    def test_create_config_drive_iso(
        self, mock_find_tool, mock_run, tmp_path,
    ):
        from tftpos.cloud_init import create_config_drive

        mock_find_tool.return_value = "genisoimage"
        mock_run.return_value = MagicMock(returncode=0)

        profile = ProvisionProfile(
            name="iso-test",
            os_family="ubuntu",
            os_version="24.04",
            network={"hostname": "iso-test", "method": "dhcp"},
            extra={"user": "admin", "timezone": "UTC", "locale": "en_US.UTF-8"},
        )
        output = tmp_path / "cidata.iso"

        result = create_config_drive(profile, output)
        assert result == output
        mock_run.assert_called_once()


# ---------------------------------------------------------------
# Metadata persistence tests
# ---------------------------------------------------------------


class TestMetadataPersistence:

    def test_metadata_roundtrip(self, tmp_path):
        from tftpos.cloud_image import _load_metadata, _save_metadata

        img = CloudImage(
            name="test-img",
            os_family="fedora",
            vendor="fedora",
            version="40",
            arch="x86_64",
            format="qcow2",
            path=tmp_path / "image.qcow2",
            size_bytes=999,
            cloud_init=True,
        )
        (tmp_path / "image.qcow2").write_bytes(b"\x00")

        _save_metadata(img)

        meta_path = tmp_path / "image.json"
        assert meta_path.exists()

        loaded = _load_metadata(meta_path)
        assert loaded is not None
        assert loaded.name == "test-img"
        assert loaded.size_bytes == 999
        assert loaded.format == "qcow2"

    def test_load_corrupted_metadata(self, tmp_path):
        from tftpos.cloud_image import _load_metadata

        meta_path = tmp_path / "image.json"
        meta_path.write_text("not valid json{{{")

        result = _load_metadata(meta_path)
        assert result is None


# ---------------------------------------------------------------
# Format detection helper
# ---------------------------------------------------------------


class TestDetectFormat:

    def test_qcow2(self):
        from tftpos.cloud_image import _detect_format

        assert _detect_format(Path("image.qcow2")) == "qcow2"

    def test_raw(self):
        from tftpos.cloud_image import _detect_format

        assert _detect_format(Path("image.raw")) == "raw"

    def test_img_treated_as_raw(self):
        from tftpos.cloud_image import _detect_format

        assert _detect_format(Path("image.img")) == "raw"

    def test_vmdk(self):
        from tftpos.cloud_image import _detect_format

        assert _detect_format(Path("image.vmdk")) == "vmdk"

    def test_vhd(self):
        from tftpos.cloud_image import _detect_format

        assert _detect_format(Path("image.vhd")) == "vpc"

    def test_unknown_defaults_to_qcow2(self):
        from tftpos.cloud_image import _detect_format

        assert _detect_format(Path("image.xyz")) == "qcow2"
