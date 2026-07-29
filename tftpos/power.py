"""Power management drivers for IPMI and Redfish BMC control."""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PowerError(Exception):
    """Raised when a power management operation fails."""


class PowerDriver(ABC):
    """Abstract base for BMC power management drivers."""

    @abstractmethod
    def power_on(self) -> str:
        """Power on the host. Returns status message."""
        ...

    @abstractmethod
    def power_off(self) -> str:
        """Power off the host. Returns status message."""
        ...

    @abstractmethod
    def power_status(self) -> str:
        """Query current power state. Returns 'on', 'off', or 'unknown'."""
        ...

    @abstractmethod
    def set_boot_device(self, device: str = "pxe") -> str:
        """Set next boot device. device is 'pxe' or 'disk'. Returns status message."""
        ...


class IPMIDriver(PowerDriver):
    """IPMI power driver using the ipmitool CLI."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        interface: str = "lanplus",
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._interface = interface

    def _run(self, *args: str) -> str:
        """Run an ipmitool command and return its stdout."""
        cmd = [
            "ipmitool",
            "-I", self._interface,
            "-H", self._host,
            "-U", self._username,
            "-P", self._password,
            *args,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            raise PowerError("ipmitool not found; install ipmitool")
        except subprocess.TimeoutExpired:
            raise PowerError(f"ipmitool timed out connecting to {self._host}")

        if result.returncode != 0:
            raise PowerError(
                f"ipmitool failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout.strip()

    def power_on(self) -> str:
        return self._run("power", "on")

    def power_off(self) -> str:
        return self._run("power", "off")

    def power_status(self) -> str:
        output = self._run("power", "status")
        lower = output.lower()
        if "on" in lower:
            return "on"
        if "off" in lower:
            return "off"
        return "unknown"

    def set_boot_device(self, device: str = "pxe") -> str:
        if device not in ("pxe", "disk"):
            raise PowerError(f"unsupported boot device: {device!r}")
        return self._run("chassis", "bootdev", device)


class RedfishDriver(PowerDriver):
    """Redfish REST API power driver using urllib."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        system_id: str = "1",
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._system_id = system_id
        self._base_url = f"https://{host}/redfish/v1"

    def _request(
        self,
        path: str,
        method: str = "GET",
        data: Optional[dict] = None,
    ) -> dict:
        """Make an authenticated Redfish API request."""
        url = f"{self._base_url}{path}"
        body = json.dumps(data).encode() if data else None

        req = Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")

        # Basic auth
        import base64
        credentials = base64.b64encode(
            f"{self._username}:{self._password}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {credentials}")

        try:
            with urlopen(req, timeout=30) as resp:
                if resp.status == 204:
                    return {}
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode()
            except Exception:
                pass
            raise PowerError(
                f"Redfish request failed ({exc.code}): {body_text}"
            )
        except URLError as exc:
            raise PowerError(
                f"Cannot reach Redfish at {self._host}: {exc.reason}"
            )

    def power_on(self) -> str:
        self._request(
            f"/Systems/{self._system_id}/Actions/ComputerSystem.Reset",
            method="POST",
            data={"ResetType": "On"},
        )
        return "power on requested"

    def power_off(self) -> str:
        self._request(
            f"/Systems/{self._system_id}/Actions/ComputerSystem.Reset",
            method="POST",
            data={"ResetType": "ForceOff"},
        )
        return "power off requested"

    def power_status(self) -> str:
        data = self._request(f"/Systems/{self._system_id}")
        state = data.get("PowerState", "Unknown").lower()
        if state == "on":
            return "on"
        if state == "off":
            return "off"
        return "unknown"

    def set_boot_device(self, device: str = "pxe") -> str:
        if device not in ("pxe", "disk"):
            raise PowerError(f"unsupported boot device: {device!r}")

        boot_target = "Pxe" if device == "pxe" else "Hdd"
        self._request(
            f"/Systems/{self._system_id}",
            method="PATCH",
            data={
                "Boot": {
                    "BootSourceOverrideTarget": boot_target,
                    "BootSourceOverrideEnabled": "Once",
                }
            },
        )
        return f"boot device set to {device}"


@dataclass
class PowerMapping:
    """Maps a MAC address to its BMC power driver configuration."""
    mac: str
    driver: PowerDriver


class PowerManager:
    """Manages power drivers for hosts, keyed by MAC address."""

    def __init__(self) -> None:
        self._drivers: Dict[str, PowerDriver] = {}

    def register(self, mac: str, driver: PowerDriver) -> None:
        """Register a power driver for a MAC address."""
        self._drivers[mac.lower().replace("-", ":")] = driver

    def get_driver(self, mac: str) -> PowerDriver:
        """Get the power driver for a MAC address."""
        key = mac.lower().replace("-", ":")
        driver = self._drivers.get(key)
        if driver is None:
            raise PowerError(f"no power driver configured for MAC {mac!r}")
        return driver

    def has_driver(self, mac: str) -> bool:
        """Check if a power driver is registered for a MAC."""
        key = mac.lower().replace("-", ":")
        return key in self._drivers

    def power_on(self, mac: str) -> str:
        return self.get_driver(mac).power_on()

    def power_off(self, mac: str) -> str:
        return self.get_driver(mac).power_off()

    def power_status(self, mac: str) -> str:
        return self.get_driver(mac).power_status()

    def set_boot_device(self, mac: str, device: str = "pxe") -> str:
        return self.get_driver(mac).set_boot_device(device)

    @staticmethod
    def create_driver(
        bmc_driver: str,
        bmc_host: str,
        bmc_user: str,
        bmc_password: str,
    ) -> PowerDriver:
        """Factory method to create a PowerDriver from config strings."""
        driver_type = bmc_driver.lower()
        if driver_type == "ipmi":
            return IPMIDriver(bmc_host, bmc_user, bmc_password)
        elif driver_type == "redfish":
            return RedfishDriver(bmc_host, bmc_user, bmc_password)
        else:
            raise PowerError(f"unknown BMC driver type: {bmc_driver!r}")
