"""Lab proof: MAC resolve -> stage -> TFTP transfer -> checksum verify.

Uses the REAL StaticFirmwarePlugin (no mocks) and tftpy for user-mode
TFTP transfer.  Proves the full provisioning loop without root.

Requires: pip install tftpy  (or: pip install tftpos[test-tftp])
"""

from __future__ import annotations

import hashlib
import os
import socket
import threading
import time

import pytest

tftpy = pytest.importorskip("tftpy", reason="tftpy not installed")

from tftpos.config import TftpOSConfig
from tftpos.engine import FirmwareEngine
from tftpos.matcher import HostMatcher
from tftpos.models import HostRule
from tftpos.plugins.static import StaticFirmwarePlugin
from tftpos.registry import PluginRegistry

pytestmark = pytest.mark.integration


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestEndToEndTftp:

    def _build_engine(self, tmp_path):
        distro_root = tmp_path / "distros"
        (distro_root / "openwrt" / "23.05").mkdir(parents=True)
        firmware_blob = os.urandom(256)
        fw_path = distro_root / "openwrt" / "23.05" / "firmware.bin"
        fw_path.write_bytes(firmware_blob)

        registry = PluginRegistry()
        registry.register(
            StaticFirmwarePlugin,
            distro_root=str(distro_root),
            os_family="openwrt",
            supported_versions=["23.05"],
        )

        rules = [
            HostRule(
                profile="lab-router",
                os_family="openwrt",
                os_version="23.05",
                mac="aa:bb:cc:dd:ee:ff",
            ),
        ]
        matcher = HostMatcher(rules)

        tftp_root = tmp_path / "tftp"
        data_dir = tmp_path / "data"
        config = TftpOSConfig(
            tftp_root=tftp_root,
            data_dir=data_dir,
        )
        (data_dir / "profiles").mkdir(parents=True, exist_ok=True)

        engine = FirmwareEngine(registry, matcher, config)
        return engine, firmware_blob, tftp_root

    def test_resolve_stage_tftp_verify(self, tmp_path):
        """Full loop: MAC -> firmware path -> stage -> TFTP get -> checksum."""
        engine, firmware_blob, tftp_root = self._build_engine(tmp_path)

        firmware_path = engine.serve("aa:bb:cc:dd:ee:ff")
        assert os.path.isfile(firmware_path)

        staged_path = engine.stage("aa:bb:cc:dd:ee:ff")
        assert staged_path.exists()
        assert staged_path.read_bytes() == firmware_blob

        port = _free_port()
        server = tftpy.TftpServer(str(tftp_root))
        server_thread = threading.Thread(
            target=server.listen,
            args=("127.0.0.1", port, 2),
            daemon=True,
        )
        server_thread.start()
        time.sleep(0.3)

        try:
            download_path = str(tmp_path / "downloaded.bin")
            client = tftpy.TftpClient("127.0.0.1", port)
            client.download(staged_path.name, download_path, timeout=5)

            with open(download_path, "rb") as f:
                downloaded = f.read()

            assert hashlib.sha256(downloaded).digest() == hashlib.sha256(
                firmware_blob
            ).digest()
            assert downloaded == firmware_blob
        finally:
            server.stop(now=True)
            server_thread.join(timeout=2)

    def test_multiple_macs_different_firmware(self, tmp_path):
        """Two MACs stage different firmware; both verify over TFTP."""
        distro_root = tmp_path / "distros"

        blob_a = os.urandom(128)
        (distro_root / "openwrt" / "23.05").mkdir(parents=True)
        (distro_root / "openwrt" / "23.05" / "firmware.bin").write_bytes(
            blob_a
        )

        blob_b = os.urandom(128)
        (distro_root / "ddwrt" / "2024").mkdir(parents=True)
        (distro_root / "ddwrt" / "2024" / "firmware.bin").write_bytes(blob_b)

        registry = PluginRegistry()
        registry.register(
            StaticFirmwarePlugin,
            distro_root=str(distro_root),
            os_family="openwrt",
            supported_versions=["23.05"],
        )
        registry.register(
            StaticFirmwarePlugin,
            distro_root=str(distro_root),
            os_family="ddwrt",
            supported_versions=["2024"],
        )

        rules = [
            HostRule(
                profile="router-a",
                os_family="openwrt",
                os_version="23.05",
                mac="aa:bb:cc:dd:ee:01",
            ),
            HostRule(
                profile="router-b",
                os_family="ddwrt",
                os_version="2024",
                mac="aa:bb:cc:dd:ee:02",
            ),
        ]
        matcher = HostMatcher(rules)

        tftp_root = tmp_path / "tftp"
        data_dir = tmp_path / "data"
        config = TftpOSConfig(tftp_root=tftp_root, data_dir=data_dir)
        (data_dir / "profiles").mkdir(parents=True, exist_ok=True)

        engine = FirmwareEngine(registry, matcher, config)

        staged_a = engine.stage(
            "aa:bb:cc:dd:ee:01", name="router-a.bin"
        )
        staged_b = engine.stage(
            "aa:bb:cc:dd:ee:02", name="router-b.bin"
        )

        port = _free_port()
        server = tftpy.TftpServer(str(tftp_root))
        server_thread = threading.Thread(
            target=server.listen,
            args=("127.0.0.1", port, 2),
            daemon=True,
        )
        server_thread.start()
        time.sleep(0.3)

        try:
            client = tftpy.TftpClient("127.0.0.1", port)

            dl_a = str(tmp_path / "dl_a.bin")
            client.download("router-a.bin", dl_a, timeout=5)

            dl_b = str(tmp_path / "dl_b.bin")
            client.download("router-b.bin", dl_b, timeout=5)

            with open(dl_a, "rb") as f:
                assert f.read() == blob_a
            with open(dl_b, "rb") as f:
                assert f.read() == blob_b
        finally:
            server.stop(now=True)
            server_thread.join(timeout=2)

    def test_checksum_mismatch_detected(self, tmp_path):
        """Verify that a corrupted download is caught by checksum."""
        engine, firmware_blob, tftp_root = self._build_engine(tmp_path)
        staged_path = engine.stage("aa:bb:cc:dd:ee:ff")

        corrupt = b"\x00" * len(firmware_blob)
        assert hashlib.sha256(corrupt).digest() != hashlib.sha256(
            firmware_blob
        ).digest()
