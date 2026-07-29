"""Tests for tftpos.client -- VM hypervisor backends and provisioning workflow."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tftpos.client.base import VirtBackend, detect_hypervisor
from tftpos.client.libvirt_backend import LibvirtBackend
from tftpos.client.bhyve_backend import BhyveBackend
from tftpos.client.vmm_backend import VmmBackend
from tftpos.client.hyperv_backend import HyperVBackend


# ---------------------------------------------------------------------------
# VirtBackend ABC tests
# ---------------------------------------------------------------------------


class TestVirtBackendABC:
    """Tests for the VirtBackend abstract base class."""

    def test_cannot_instantiate_directly(self):
        """VirtBackend cannot be instantiated without implementing all methods."""
        with pytest.raises(TypeError):
            VirtBackend()

    def test_concrete_subclass_must_implement_all(self):
        """A subclass missing any abstract method cannot be instantiated."""
        class IncompleteBackend(VirtBackend):
            def create_vm(self, name, mac, **kw):
                pass
            # Missing other methods

        with pytest.raises(TypeError):
            IncompleteBackend()

    def test_concrete_subclass_works(self):
        """A fully implemented subclass can be instantiated."""
        class DummyBackend(VirtBackend):
            def create_vm(self, name, mac, memory_mb=2048, vcpus=2,
                          disk_gb=20, bridge=None):
                return {"name": name, "mac": mac}
            def start_vm(self, name): pass
            def stop_vm(self, name): pass
            def delete_vm(self, name): pass
            def get_vm_status(self, name): return "stopped"
            def is_available(self): return True
            @property
            def hypervisor_name(self): return "dummy"

        backend = DummyBackend()
        assert backend.hypervisor_name == "dummy"
        assert backend.is_available() is True
        result = backend.create_vm("test", "00:11:22:33:44:55")
        assert result["name"] == "test"


# ---------------------------------------------------------------------------
# detect_hypervisor tests
# ---------------------------------------------------------------------------


class TestDetectHypervisor:
    """Tests for the detect_hypervisor function."""

    @patch("tftpos.client.libvirt_backend.shutil.which", return_value="/usr/bin/virsh")
    def test_detects_libvirt(self, mock_which):
        """detect_hypervisor returns LibvirtBackend when virsh is present."""
        result = detect_hypervisor()
        assert result is not None
        assert result.hypervisor_name == "libvirt"

    @patch("shutil.which")
    def test_detects_bhyve_when_no_libvirt(self, mock_which):
        """detect_hypervisor returns BhyveBackend when only bhyvectl is present."""
        def which_side_effect(cmd):
            if cmd == "bhyvectl":
                return "/usr/sbin/bhyvectl"
            return None
        mock_which.side_effect = which_side_effect

        result = detect_hypervisor()
        assert result is not None
        assert result.hypervisor_name == "bhyve"

    @patch("shutil.which")
    def test_detects_vmm_when_no_libvirt_or_bhyve(self, mock_which):
        """detect_hypervisor returns VmmBackend when only vmctl is present."""
        def which_side_effect(cmd):
            if cmd == "vmctl":
                return "/usr/sbin/vmctl"
            return None
        mock_which.side_effect = which_side_effect

        result = detect_hypervisor()
        assert result is not None
        assert result.hypervisor_name == "vmm"

    @patch("shutil.which", return_value=None)
    def test_returns_none_when_nothing_available(self, mock_which):
        """detect_hypervisor returns None when no hypervisor tools are found."""
        result = detect_hypervisor()
        assert result is None


# ---------------------------------------------------------------------------
# LibvirtBackend tests
# ---------------------------------------------------------------------------


class TestLibvirtBackend:
    """Tests for LibvirtBackend (mocked subprocess for virt-install/virsh)."""

    def test_hypervisor_name(self):
        backend = LibvirtBackend()
        assert backend.hypervisor_name == "libvirt"

    @patch("tftpos.client.libvirt_backend.shutil.which", return_value="/usr/bin/virsh")
    def test_is_available_true(self, mock_which):
        assert LibvirtBackend().is_available() is True

    @patch("tftpos.client.libvirt_backend.shutil.which", return_value=None)
    def test_is_available_false(self, mock_which):
        assert LibvirtBackend().is_available() is False

    @patch("tftpos.client.libvirt_backend.subprocess.run")
    def test_create_vm(self, mock_run):
        """create_vm calls virt-install with correct arguments."""
        mock_run.return_value = MagicMock(returncode=0)
        backend = LibvirtBackend()
        result = backend.create_vm(
            "test-vm", "aa:bb:cc:dd:ee:ff",
            memory_mb=4096, vcpus=4, disk_gb=40, bridge="br0",
        )
        assert result["name"] == "test-vm"
        assert result["mac"] == "aa:bb:cc:dd:ee:ff"
        assert result["bridge"] == "br0"

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "virt-install"
        assert "--pxe" in cmd
        assert "--name" in cmd
        idx = cmd.index("--name")
        assert cmd[idx + 1] == "test-vm"

    @patch("tftpos.client.libvirt_backend.subprocess.run")
    def test_create_vm_default_bridge(self, mock_run):
        """create_vm uses virbr0 as default bridge."""
        mock_run.return_value = MagicMock(returncode=0)
        result = LibvirtBackend().create_vm("vm1", "aa:bb:cc:dd:ee:ff")
        assert result["bridge"] == "virbr0"

    @patch("tftpos.client.libvirt_backend.subprocess.run")
    def test_start_vm(self, mock_run):
        """start_vm calls virsh start."""
        LibvirtBackend().start_vm("test-vm")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["virsh", "start", "test-vm"]

    @patch("tftpos.client.libvirt_backend.subprocess.run")
    def test_stop_vm(self, mock_run):
        """stop_vm calls virsh destroy."""
        LibvirtBackend().stop_vm("test-vm")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["virsh", "destroy", "test-vm"]

    @patch("tftpos.client.libvirt_backend.subprocess.run")
    def test_delete_vm(self, mock_run):
        """delete_vm calls virsh undefine with --remove-all-storage."""
        LibvirtBackend().delete_vm("test-vm")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["virsh", "undefine", "test-vm", "--remove-all-storage"]

    @patch("tftpos.client.libvirt_backend.subprocess.run")
    def test_get_vm_status(self, mock_run):
        """get_vm_status calls virsh domstate and returns stripped output."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="running\n"
        )
        status = LibvirtBackend().get_vm_status("test-vm")
        assert status == "running"
        cmd = mock_run.call_args[0][0]
        assert cmd == ["virsh", "domstate", "test-vm"]

    @patch("tftpos.client.libvirt_backend.subprocess.run")
    def test_create_vm_subprocess_error(self, mock_run):
        """create_vm raises CalledProcessError on failure."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "virt-install", stderr="error"
        )
        with pytest.raises(subprocess.CalledProcessError):
            LibvirtBackend().create_vm("fail-vm", "aa:bb:cc:dd:ee:ff")


# ---------------------------------------------------------------------------
# BhyveBackend tests
# ---------------------------------------------------------------------------


class TestBhyveBackend:
    """Tests for BhyveBackend (mocked subprocess)."""

    def test_hypervisor_name(self):
        assert BhyveBackend().hypervisor_name == "bhyve"

    @patch("tftpos.client.bhyve_backend.shutil.which", return_value="/usr/sbin/bhyvectl")
    def test_is_available_true(self, mock_which):
        assert BhyveBackend().is_available() is True

    @patch("tftpos.client.bhyve_backend.shutil.which", return_value=None)
    def test_is_available_false(self, mock_which):
        assert BhyveBackend().is_available() is False

    @patch("tftpos.client.bhyve_backend.shutil.which")
    @patch("tftpos.client.bhyve_backend.subprocess.run")
    def test_create_vm_with_vm_bhyve(self, mock_run, mock_which):
        """create_vm uses vm-bhyve when available."""
        def which_side_effect(cmd):
            if cmd == "bhyvectl":
                return "/usr/sbin/bhyvectl"
            if cmd == "vm":
                return "/usr/local/sbin/vm"
            return None
        mock_which.side_effect = which_side_effect
        mock_run.return_value = MagicMock(returncode=0)

        backend = BhyveBackend()
        result = backend.create_vm("test-vm", "aa:bb:cc:dd:ee:ff")
        assert result["name"] == "test-vm"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "vm"
        assert cmd[1] == "create"

    @patch("tftpos.client.bhyve_backend.shutil.which")
    @patch("tftpos.client.bhyve_backend.subprocess.run")
    def test_create_vm_raw_bhyve(self, mock_run, mock_which):
        """create_vm falls back to raw bhyve when vm-bhyve not available."""
        def which_side_effect(cmd):
            if cmd == "bhyvectl":
                return "/usr/sbin/bhyvectl"
            return None
        mock_which.side_effect = which_side_effect
        mock_run.return_value = MagicMock(
            returncode=0, stdout="tap0\n"
        )

        backend = BhyveBackend()
        result = backend.create_vm("test-vm", "aa:bb:cc:dd:ee:ff")
        assert result["name"] == "test-vm"
        assert "disk" in result

    @patch("tftpos.client.bhyve_backend.shutil.which")
    @patch("tftpos.client.bhyve_backend.subprocess.run")
    def test_stop_vm_with_vm_bhyve(self, mock_run, mock_which):
        """stop_vm uses 'vm stop' when vm-bhyve available."""
        def which_side_effect(cmd):
            if cmd == "bhyvectl":
                return "/usr/sbin/bhyvectl"
            if cmd == "vm":
                return "/usr/local/sbin/vm"
            return None
        mock_which.side_effect = which_side_effect

        BhyveBackend().stop_vm("test-vm")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["vm", "stop", "test-vm"]

    @patch("tftpos.client.bhyve_backend.shutil.which")
    @patch("tftpos.client.bhyve_backend.subprocess.run")
    def test_stop_vm_raw_bhyve(self, mock_run, mock_which):
        """stop_vm uses bhyvectl --destroy when no vm-bhyve."""
        def which_side_effect(cmd):
            if cmd == "bhyvectl":
                return "/usr/sbin/bhyvectl"
            return None
        mock_which.side_effect = which_side_effect

        BhyveBackend().stop_vm("test-vm")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["bhyvectl", "--destroy", "--vm=test-vm"]

    @patch("tftpos.client.bhyve_backend.shutil.which")
    @patch("tftpos.client.bhyve_backend.subprocess.run")
    def test_get_vm_status_with_vm_bhyve(self, mock_run, mock_which):
        """get_vm_status parses vm list output."""
        def which_side_effect(cmd):
            if cmd == "bhyvectl":
                return "/usr/sbin/bhyvectl"
            if cmd == "vm":
                return "/usr/local/sbin/vm"
            return None
        mock_which.side_effect = which_side_effect
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="test-vm  2  2048M  -  Running\n",
        )

        status = BhyveBackend().get_vm_status("test-vm")
        assert status == "Running"


# ---------------------------------------------------------------------------
# VmmBackend tests
# ---------------------------------------------------------------------------


class TestVmmBackend:
    """Tests for VmmBackend (mocked subprocess for vmctl)."""

    def test_hypervisor_name(self):
        assert VmmBackend().hypervisor_name == "vmm"

    @patch("tftpos.client.vmm_backend.shutil.which", return_value="/usr/sbin/vmctl")
    def test_is_available_true(self, mock_which):
        assert VmmBackend().is_available() is True

    @patch("tftpos.client.vmm_backend.shutil.which", return_value=None)
    def test_is_available_false(self, mock_which):
        assert VmmBackend().is_available() is False

    @patch("tftpos.client.vmm_backend.subprocess.run")
    def test_create_vm(self, mock_run):
        """create_vm calls vmctl create with correct size."""
        mock_run.return_value = MagicMock(returncode=0)
        result = VmmBackend().create_vm(
            "test-vm", "aa:bb:cc:dd:ee:ff", disk_gb=30,
        )
        assert result["name"] == "test-vm"
        assert result["mac"] == "aa:bb:cc:dd:ee:ff"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "vmctl"
        assert cmd[1] == "create"
        assert "30G" in cmd

    @patch("tftpos.client.vmm_backend.subprocess.run")
    def test_start_vm(self, mock_run):
        """start_vm calls vmctl start with PXE boot flag."""
        VmmBackend().start_vm("test-vm")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "vmctl"
        assert cmd[1] == "start"
        assert "-B" in cmd
        assert "net" in cmd

    @patch("tftpos.client.vmm_backend.subprocess.run")
    def test_stop_vm(self, mock_run):
        """stop_vm calls vmctl stop -f."""
        VmmBackend().stop_vm("test-vm")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["vmctl", "stop", "test-vm", "-f"]

    @patch("tftpos.client.vmm_backend.subprocess.run")
    @patch("os.unlink")
    def test_delete_vm(self, mock_unlink, mock_run):
        """delete_vm stops the VM and removes the disk."""
        VmmBackend().delete_vm("test-vm")
        # Should call vmctl stop first
        stop_cmd = mock_run.call_args_list[0][0][0]
        assert stop_cmd == ["vmctl", "stop", "test-vm", "-f"]
        # Should remove disk
        mock_unlink.assert_called_once_with("/var/vm/test-vm.qcow2")

    @patch("tftpos.client.vmm_backend.subprocess.run")
    def test_get_vm_status_running(self, mock_run):
        """get_vm_status detects running VM from vmctl output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER NAME\n"
                   "    1  1234     2    2.0G   512M   ttyp0         root test-vm\n",
        )
        status = VmmBackend().get_vm_status("test-vm")
        assert status == "running"

    @patch("tftpos.client.vmm_backend.subprocess.run")
    def test_get_vm_status_stopped(self, mock_run):
        """get_vm_status detects stopped VM from vmctl output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER NAME\n"
                   "    1     -     2    2.0G       -       -         root test-vm\n",
        )
        status = VmmBackend().get_vm_status("test-vm")
        assert status == "stopped"

    @patch("tftpos.client.vmm_backend.subprocess.run")
    def test_get_vm_status_not_found(self, mock_run):
        """get_vm_status returns 'not found' when VM not in list."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER NAME\n",
        )
        status = VmmBackend().get_vm_status("nonexistent")
        assert status == "not found"


# ---------------------------------------------------------------------------
# HyperVBackend tests
# ---------------------------------------------------------------------------


class TestHyperVBackend:
    """Tests for HyperVBackend (mocked subprocess for PowerShell)."""

    def test_hypervisor_name(self):
        assert HyperVBackend().hypervisor_name == "hyperv"

    @patch("tftpos.client.hyperv_backend.shutil.which", return_value=None)
    def test_is_available_no_powershell(self, mock_which):
        """is_available returns False when no PowerShell found."""
        assert HyperVBackend().is_available() is False

    @patch("tftpos.client.hyperv_backend.subprocess.run")
    @patch("tftpos.client.hyperv_backend.shutil.which", return_value="/usr/bin/pwsh")
    def test_is_available_with_hyperv_module(self, mock_which, mock_run):
        """is_available returns True when Hyper-V module is found."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Hyper-V   2.0.0   Hyper-V\n"
        )
        assert HyperVBackend().is_available() is True

    @patch("tftpos.client.hyperv_backend.subprocess.run")
    @patch("tftpos.client.hyperv_backend.shutil.which", return_value="/usr/bin/pwsh")
    def test_is_available_without_hyperv_module(self, mock_which, mock_run):
        """is_available returns False when Hyper-V module not found."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout=""
        )
        assert HyperVBackend().is_available() is False

    @patch("tftpos.client.hyperv_backend.shutil.which", return_value="/usr/bin/pwsh")
    @patch("tftpos.client.hyperv_backend.subprocess.run")
    def test_create_vm(self, mock_run, mock_which):
        """create_vm calls New-VM, Set-VMProcessor, Set-VMNetworkAdapter."""
        mock_run.return_value = MagicMock(returncode=0)
        result = HyperVBackend().create_vm(
            "test-vm", "aa:bb:cc:dd:ee:ff", vcpus=4,
        )
        assert result["name"] == "test-vm"
        assert result["mac"] == "aa:bb:cc:dd:ee:ff"
        # Should have 3 subprocess calls: New-VM, Set-VMProcessor, Set-VMNetworkAdapter
        assert mock_run.call_count == 3

    @patch("tftpos.client.hyperv_backend.shutil.which", return_value="/usr/bin/pwsh")
    @patch("tftpos.client.hyperv_backend.subprocess.run")
    def test_start_vm(self, mock_run, mock_which):
        """start_vm calls Start-VM."""
        HyperVBackend().start_vm("test-vm")
        cmd = mock_run.call_args[0][0]
        # cmd is [ps, "-NoProfile", "-Command", "Start-VM ..."]
        cmd_str = " ".join(cmd)
        assert "Start-VM" in cmd_str

    @patch("tftpos.client.hyperv_backend.shutil.which", return_value="/usr/bin/pwsh")
    @patch("tftpos.client.hyperv_backend.subprocess.run")
    def test_stop_vm(self, mock_run, mock_which):
        """stop_vm calls Stop-VM -Force."""
        HyperVBackend().stop_vm("test-vm")
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "Stop-VM" in cmd_str
        assert "-Force" in cmd_str

    @patch("tftpos.client.hyperv_backend.shutil.which", return_value="/usr/bin/pwsh")
    @patch("tftpos.client.hyperv_backend.subprocess.run")
    def test_delete_vm(self, mock_run, mock_which):
        """delete_vm calls Stop-VM then Remove-VM."""
        HyperVBackend().delete_vm("test-vm")
        assert mock_run.call_count == 2
        # Second call should be Remove-VM
        remove_cmd = " ".join(mock_run.call_args_list[1][0][0])
        assert "Remove-VM" in remove_cmd

    @patch("tftpos.client.hyperv_backend.shutil.which", return_value="/usr/bin/pwsh")
    @patch("tftpos.client.hyperv_backend.subprocess.run")
    def test_get_vm_status(self, mock_run, mock_which):
        """get_vm_status returns lowercased state from Get-VM."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Running\n"
        )
        status = HyperVBackend().get_vm_status("test-vm")
        assert status == "running"


# ---------------------------------------------------------------------------
# __init__ imports test
# ---------------------------------------------------------------------------


class TestClientInit:
    """Tests for tftpos.client package imports."""

    def test_imports_virt_backend(self):
        from tftpos.client import VirtBackend
        assert VirtBackend is not None

    def test_imports_detect_hypervisor(self):
        from tftpos.client import detect_hypervisor
        assert callable(detect_hypervisor)
