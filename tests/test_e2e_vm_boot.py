"""End-to-end integration test: TftpOS manages boot files, VM PXE boots via TFTP.

Two test tiers:
  - TestTftpOSStack / TestProvisionTracking: no root required, tests the full
    TftpOS pipeline (config -> matching -> plugin -> firmware file)
  - TestVMPxeBoot: requires root (sudo), creates libvirt network + VMs

Run stack tests:  pytest tests/test_e2e_vm_boot.py -m integration -v --no-cov
Run VM tests:     sudo pytest tests/test_e2e_vm_boot.py -m integration -v --no-cov
"""

from __future__ import annotations

import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from tftpos.client.libvirt_backend import LibvirtBackend
from tftpos.config import TftpOSConfig, load_hosts
from tftpos.db import MemoryBackend
from tftpos.engine import FirmwareEngine
from tftpos.matcher import HostMatcher
from tests.plugins.test_firmware import E2EFirmwarePlugin
from tftpos.registry import PluginRegistry
from tftpos.state import ProvisionState, ProvisionTracker

pytestmark = pytest.mark.integration

IPXE_BINARY = Path("/usr/share/ipxe/undionly.kpxe")
NETWORK_NAME = "tftpos-test"
BRIDGE_NAME = "virbr-tftest"
SUBNET = "192.168.201"
VM_NAME = "tftpos-e2e-01"
VM_MAC = "52:54:00:e2:e0:01"
VM_NAME_2 = "tftpos-e2e-02"
VM_MAC_2 = "52:54:00:e2:e0:02"


def _virsh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["virsh", *args],
        capture_output=True, text=True, timeout=30,
    )


def _vm_exists(name: str) -> bool:
    return _virsh("domstate", name).returncode == 0


def _net_exists(name: str) -> bool:
    return _virsh("net-info", name).returncode == 0


def _net_active(name: str) -> bool:
    r = _virsh("net-info", name)
    return r.returncode == 0 and "Active:         yes" in r.stdout


def _cleanup_vm(name: str) -> None:
    if not _vm_exists(name):
        return
    state = _virsh("domstate", name).stdout.strip()
    if state == "running":
        _virsh("destroy", name)
    _virsh("undefine", name, "--remove-all-storage")


def _cleanup_network() -> None:
    if not _net_exists(NETWORK_NAME):
        return
    if _net_active(NETWORK_NAME):
        _virsh("net-destroy", NETWORK_NAME)
    _virsh("net-undefine", NETWORK_NAME)


def _can_manage_libvirt_networks() -> bool:
    """Check if we can create/start libvirt networks (needs root or polkit)."""
    r = _virsh("net-list")
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Fixture: TftpOS stack only (no root needed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tftpos_env(tmp_path_factory):
    """Build the full TftpOS stack with test plugin — no root required."""
    if not IPXE_BINARY.exists():
        pytest.skip(f"iPXE binary not found at {IPXE_BINARY}; install ipxe-bootimgs-x86")

    base = tmp_path_factory.mktemp("tftpos_e2e")
    tftp_root = base / "tftp"
    data_dir = base / "data"
    profiles_dir = data_dir / "profiles"
    tftp_root.mkdir()
    data_dir.mkdir()
    profiles_dir.mkdir()

    hosts_file = data_dir / "hosts.toml"
    hosts_file.write_text(textwrap.dedent(f"""\
        [[host]]
        profile = "test-bios"
        os_family = "test"
        os_version = "1.0"
        mac = "{VM_MAC}"

        [[host]]
        profile = "test-bios"
        os_family = "test"
        os_version = "1.0"
        mac = "{VM_MAC_2}"
    """))

    profile_file = profiles_dir / "test-bios.toml"
    profile_file.write_text(textwrap.dedent("""\
        [profile]
        name = "test-bios"
        os_family = "test"
        os_version = "1.0"
        firmware = "bios"
    """))

    config = TftpOSConfig(
        server_host=f"{SUBNET}.1",
        server_port=8443,
        tftp_root=tftp_root,
        data_dir=data_dir,
    )
    rules = load_hosts(hosts_file)
    matcher = HostMatcher(rules)
    registry = PluginRegistry()
    registry.register(E2EFirmwarePlugin)
    plugin = registry.get("test")
    plugin.configure(tftp_root)
    tracker = ProvisionTracker(backend=MemoryBackend())
    engine = FirmwareEngine(registry, matcher, config, tracker)

    path1 = engine.serve(mac=VM_MAC)
    path2 = engine.serve(mac=VM_MAC_2)

    return {
        "base": base,
        "tftp_root": tftp_root,
        "data_dir": data_dir,
        "config": config,
        "engine": engine,
        "tracker": tracker,
        "firmware_path_1": path1,
        "firmware_path_2": path2,
    }


# ---------------------------------------------------------------------------
# Fixture: Full VM environment (needs root for network/VM creation)
# ---------------------------------------------------------------------------


def _make_path_traversable(path: Path) -> None:
    """Ensure dnsmasq (non-root) can traverse every directory up to path."""
    dirs = []
    p = path
    while p != p.parent:
        dirs.append(p)
        p = p.parent
    for d in reversed(dirs):
        if d.is_dir():
            d.chmod(d.stat().st_mode | 0o005)


@pytest.fixture(scope="module")
def vm_env(tftpos_env):
    """Create libvirt network and VMs — requires root privileges."""
    if not Path("/dev/kvm").exists():
        pytest.skip("KVM not available (/dev/kvm missing)")
    if not LibvirtBackend().is_available():
        pytest.skip("virsh not found in PATH")

    tftp_root = tftpos_env["tftp_root"]
    base = tftpos_env["base"]

    _make_path_traversable(tftp_root)
    for f in tftp_root.rglob("*"):
        if f.is_file():
            f.chmod(f.stat().st_mode | 0o004)

    _cleanup_vm(VM_NAME)
    _cleanup_vm(VM_NAME_2)
    _cleanup_network()

    net_xml = textwrap.dedent(f"""\
        <network>
          <name>{NETWORK_NAME}</name>
          <forward mode='nat'/>
          <bridge name='{BRIDGE_NAME}' stp='on' delay='0'/>
          <ip address='{SUBNET}.1' netmask='255.255.255.0'>
            <tftp root='{tftp_root}'/>
            <dhcp>
              <range start='{SUBNET}.100' end='{SUBNET}.200'/>
              <bootp file='undionly.kpxe'/>
            </dhcp>
          </ip>
        </network>
    """)
    net_xml_path = base / "network.xml"
    net_xml_path.write_text(net_xml)

    r = _virsh("net-define", str(net_xml_path))
    if r.returncode != 0:
        pytest.skip(f"Cannot define libvirt network (need root?): {r.stderr.strip()}")

    r = _virsh("net-start", NETWORK_NAME)
    if r.returncode != 0:
        _virsh("net-undefine", NETWORK_NAME)
        pytest.skip(f"Cannot start libvirt network (need root?): {r.stderr.strip()}")

    yield tftpos_env

    _cleanup_vm(VM_NAME)
    _cleanup_vm(VM_NAME_2)
    _cleanup_network()


# ---------------------------------------------------------------------------
# Tests: TftpOS stack (no root)
# ---------------------------------------------------------------------------


class TestTftpOSStack:
    """Verify TftpOS resolves MACs to firmware paths and populates tftp_root."""

    def test_engine_resolves_mac_to_firmware(self, tftpos_env):
        path = tftpos_env["firmware_path_1"]
        assert path.endswith("undionly.kpxe")
        assert Path(path).exists()

    def test_firmware_file_in_tftp_root(self, tftpos_env):
        ipxe = tftpos_env["tftp_root"] / "undionly.kpxe"
        assert ipxe.exists()
        assert ipxe.stat().st_size > 0

    def test_marker_file_created(self, tftpos_env):
        marker = tftpos_env["tftp_root"] / "test" / "marker.txt"
        assert marker.exists()
        assert marker.read_text().strip() == "TFTPOS_E2E_OK"

    def test_second_mac_resolves_same_firmware(self, tftpos_env):
        path = tftpos_env["firmware_path_2"]
        assert path.endswith("undionly.kpxe")
        assert Path(path).exists()

    def test_host_matching_by_mac(self, tftpos_env):
        engine = tftpos_env["engine"]
        rule = engine.resolve_rule(mac=VM_MAC)
        assert rule.profile == "test-bios"
        assert rule.os_family == "test"
        assert rule.mac == VM_MAC

    def test_host_matching_second_mac(self, tftpos_env):
        engine = tftpos_env["engine"]
        rule = engine.resolve_rule(mac=VM_MAC_2)
        assert rule.mac == VM_MAC_2

    def test_plugin_registered_correctly(self, tftpos_env):
        engine = tftpos_env["engine"]
        rule = engine.resolve_rule(mac=VM_MAC)
        profile = engine.load_profile_for_rule(rule)
        assert profile.name == "test-bios"
        assert profile.os_family == "test"
        assert profile.firmware.value == "bios"


# ---------------------------------------------------------------------------
# Tests: Provision tracking (no root)
# ---------------------------------------------------------------------------


class TestProvisionTracking:
    """Verify the state machine tracks provisioning correctly."""

    def test_tracker_registers_host(self, tftpos_env):
        tracker = tftpos_env["tracker"]
        tracker.register(VM_MAC, "test-bios", "test", "1.0")
        record = tracker.get(VM_MAC)
        assert record is not None
        assert record.state == ProvisionState.REGISTERED

    def test_tracker_transitions_to_booting(self, tftpos_env):
        tracker = tftpos_env["tracker"]
        tracker.transition(VM_MAC, ProvisionState.BOOTING)
        record = tracker.get(VM_MAC)
        assert record.state == ProvisionState.BOOTING

    def test_tracker_transitions_to_complete(self, tftpos_env):
        tracker = tftpos_env["tracker"]
        tracker.transition(VM_MAC, ProvisionState.INSTALLING)
        tracker.transition(VM_MAC, ProvisionState.COMPLETE)
        record = tracker.get(VM_MAC)
        assert record.state == ProvisionState.COMPLETE
        assert record.completed_at is not None

    def test_tracker_history(self, tftpos_env):
        tracker = tftpos_env["tracker"]
        record = tracker.get(VM_MAC)
        states = [s for s, _ in record.history]
        assert ProvisionState.REGISTERED in states
        assert ProvisionState.BOOTING in states
        assert ProvisionState.COMPLETE in states

    def test_netboot_disable(self, tftpos_env):
        tracker = tftpos_env["tracker"]
        tracker.disable_netboot(VM_MAC)
        assert not tracker.is_netboot_enabled(VM_MAC)


# ---------------------------------------------------------------------------
# Tests: VM PXE boot (needs root)
# ---------------------------------------------------------------------------


class TestVMPxeBoot:
    """Create and boot VMs, verify firmware is transferred via TFTP."""

    def test_create_and_boot_vm(self, vm_env):
        backend = LibvirtBackend()

        result = backend.create_vm(
            name=VM_NAME,
            mac=VM_MAC,
            memory_mb=512,
            vcpus=1,
            disk_gb=1,
            bridge=BRIDGE_NAME,
        )
        assert result["name"] == VM_NAME
        assert result["mac"] == VM_MAC

        status = backend.get_vm_status(VM_NAME)
        if status != "running":
            backend.start_vm(VM_NAME)
        assert backend.get_vm_status(VM_NAME) == "running"

        tftp_seen = self._wait_for_tftp_sent("undionly.kpxe")
        backend.stop_vm(VM_NAME)
        assert tftp_seen, "TFTP transfer of undionly.kpxe not seen within 30s"

    def test_second_vm_boots_independently(self, vm_env):
        backend = LibvirtBackend()

        result = backend.create_vm(
            name=VM_NAME_2,
            mac=VM_MAC_2,
            memory_mb=512,
            vcpus=1,
            disk_gb=1,
            bridge=BRIDGE_NAME,
        )
        assert result["mac"] == VM_MAC_2

        status = backend.get_vm_status(VM_NAME_2)
        if status != "running":
            backend.start_vm(VM_NAME_2)
        assert backend.get_vm_status(VM_NAME_2) == "running"

        tftp_seen = self._wait_for_tftp_sent("undionly.kpxe")
        backend.stop_vm(VM_NAME_2)
        assert tftp_seen, "TFTP transfer of undionly.kpxe not seen within 30s"

    def test_vm_cleanup(self, vm_env):
        _cleanup_vm(VM_NAME)
        _cleanup_vm(VM_NAME_2)
        assert not _vm_exists(VM_NAME)
        assert not _vm_exists(VM_NAME_2)

    @staticmethod
    def _wait_for_tftp_sent(filename: str, timeout_seconds: int = 30) -> bool:
        """Wait for dnsmasq-tftp 'sent' log proving VM fetched firmware via TFTP."""
        for _ in range(timeout_seconds):
            time.sleep(1)
            r = subprocess.run(
                ["journalctl", "--no-pager", "--since", "2 minutes ago",
                 "-q", "-t", "dnsmasq-tftp"],
                capture_output=True, text=True, timeout=10,
            )
            if "sent" in r.stdout and filename in r.stdout:
                return True
        return False
