"""Named distros and hosts as first-class objects, cobbler-style.

Provides persistent, JSON-backed CRUD for named distros (imported OS
references) and named hosts (specific machine registrations).  Each
object is stored as an individual ``<name>.json`` file under a
dedicated subdirectory so that the store is human-readable and
version-controllable.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ------------------------------------------------------------------
# Name-validation helper
# ------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_name(name: str) -> None:
    """Raise ``ValueError`` if *name* could escape the data directory.

    Rejects empty strings, names containing path separators (``/``,
    ``\\``), parent-directory references (``..``), and names that do
    not match a conservative whitelist pattern.
    """
    if not name:
        raise ValueError("name must not be empty")
    if ".." in name:
        raise ValueError(
            f"name must not contain '..': {name!r}"
        )
    if "/" in name or "\\" in name:
        raise ValueError(
            f"name must not contain path separators: {name!r}"
        )
    if not _SAFE_NAME_RE.match(name):
        raise ValueError(
            f"name contains invalid characters: {name!r}"
        )


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass
class NamedDistro:
    """A named distro is a saved reference to an imported OS."""

    name: str
    os_family: str
    vendor: str
    version: str
    arch: str = "x86_64"
    kernel_path: str = ""
    initrd_path: str = ""
    install_url: str = ""
    comment: str = ""


@dataclass
class NamedHost:
    """A named host is a saved reference to a specific machine."""

    name: str
    mac: str
    profile: str = ""
    distro: str = ""
    hostname: str = ""
    gateway: str = ""
    nameservers: List[str] = field(default_factory=list)
    ip_address: str = ""
    netmask: str = ""
    comment: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------
# Persistent store
# ------------------------------------------------------------------


class NamedObjectStore:
    """Persists named distros and hosts as JSON files.

    Directory layout::

        <data_dir>/
            distros/
                <name>.json
            hosts/
                <name>.json
    """

    def __init__(self, data_dir: Path) -> None:
        self._distro_dir = data_dir / "distros"
        self._host_dir = data_dir / "hosts"
        self._distro_dir.mkdir(parents=True, exist_ok=True)
        self._host_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------
    # Distro CRUD
    # ---------------------------------------------------------------

    def add_distro(self, distro: NamedDistro) -> None:
        """Persist a :class:`NamedDistro` to disk."""
        _validate_name(distro.name)
        path = self._distro_dir / f"{distro.name}.json"
        path.write_text(json.dumps(asdict(distro), indent=2))

    def get_distro(self, name: str) -> Optional[NamedDistro]:
        """Return a :class:`NamedDistro` by *name*, or ``None``."""
        _validate_name(name)
        path = self._distro_dir / f"{name}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return NamedDistro(**data)

    def list_distros(self) -> List[NamedDistro]:
        """Return all persisted distros, sorted by name."""
        results: List[NamedDistro] = []
        for p in sorted(self._distro_dir.glob("*.json")):
            data = json.loads(p.read_text())
            results.append(NamedDistro(**data))
        return results

    def delete_distro(self, name: str) -> bool:
        """Delete a named distro.  Returns ``True`` if it existed."""
        _validate_name(name)
        path = self._distro_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def update_distro(
        self, name: str, updates: Dict[str, Any]
    ) -> Optional[NamedDistro]:
        """Apply *updates* to an existing distro and persist.

        Returns the updated :class:`NamedDistro`, or ``None`` if it
        did not exist.
        """
        distro = self.get_distro(name)
        if distro is None:
            return None
        for k, v in updates.items():
            if hasattr(distro, k):
                setattr(distro, k, v)
        self.add_distro(distro)
        return distro

    # ---------------------------------------------------------------
    # Host CRUD
    # ---------------------------------------------------------------

    def add_host(self, host: NamedHost) -> None:
        """Persist a :class:`NamedHost` to disk."""
        _validate_name(host.name)
        path = self._host_dir / f"{host.name}.json"
        path.write_text(json.dumps(asdict(host), indent=2))

    def get_host(self, name: str) -> Optional[NamedHost]:
        """Return a :class:`NamedHost` by *name*, or ``None``."""
        _validate_name(name)
        path = self._host_dir / f"{name}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return NamedHost(**data)

    def list_hosts(self) -> List[NamedHost]:
        """Return all persisted hosts, sorted by name."""
        results: List[NamedHost] = []
        for p in sorted(self._host_dir.glob("*.json")):
            data = json.loads(p.read_text())
            results.append(NamedHost(**data))
        return results

    def delete_host(self, name: str) -> bool:
        """Delete a named host.  Returns ``True`` if it existed."""
        _validate_name(name)
        path = self._host_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def update_host(
        self, name: str, updates: Dict[str, Any]
    ) -> Optional[NamedHost]:
        """Apply *updates* to an existing host and persist.

        Returns the updated :class:`NamedHost`, or ``None`` if it
        did not exist.
        """
        host = self.get_host(name)
        if host is None:
            return None
        for k, v in updates.items():
            if hasattr(host, k):
                setattr(host, k, v)
        self.add_host(host)
        return host

    # ---------------------------------------------------------------
    # Search helpers
    # ---------------------------------------------------------------

    def find_host_by_mac(self, mac: str) -> Optional[NamedHost]:
        """Find a host whose MAC matches *mac* (case-insensitive).

        Normalises both sides to lower-case colon-separated form
        before comparison.
        """
        normalised = mac.lower().replace("-", ":")
        for host in self.list_hosts():
            if host.mac.lower().replace("-", ":") == normalised:
                return host
        return None
