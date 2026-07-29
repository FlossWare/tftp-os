"""Mnemonic aliases for distro identification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class DistroAlias:
    os_family: str
    vendor: str
    version: str


BUILTIN_ALIASES: Dict[str, DistroAlias] = {
    # Fedora
    "fedora40": DistroAlias("fedora", "fedora", "40"),
    "fedora41": DistroAlias("fedora", "fedora", "41"),
    "fedora42": DistroAlias("fedora", "fedora", "42"),
    # RHEL
    "rhel8": DistroAlias("fedora", "rhel", "8"),
    "rhel9": DistroAlias("fedora", "rhel", "9"),
    "rhel10": DistroAlias("fedora", "rhel", "10"),
    # Rocky
    "rocky8": DistroAlias("fedora", "rocky", "8"),
    "rocky9": DistroAlias("fedora", "rocky", "9"),
    # AlmaLinux
    "alma8": DistroAlias("fedora", "alma", "8"),
    "alma9": DistroAlias("fedora", "alma", "9"),
    # CentOS Stream
    "centos8": DistroAlias("fedora", "centos", "8"),
    "centos9": DistroAlias("fedora", "centos", "9"),
    # Debian
    "deb11": DistroAlias("debian", "debian", "11"),
    "deb12": DistroAlias("debian", "debian", "12"),
    "deb13": DistroAlias("debian", "debian", "13"),
    "bullseye": DistroAlias("debian", "debian", "11"),
    "bookworm": DistroAlias("debian", "debian", "12"),
    "trixie": DistroAlias("debian", "debian", "13"),
    # Ubuntu
    "ubuntu2204": DistroAlias("ubuntu", "ubuntu", "22.04"),
    "ubuntu2404": DistroAlias("ubuntu", "ubuntu", "24.04"),
    "ubuntu2410": DistroAlias("ubuntu", "ubuntu", "24.10"),
    "jammy": DistroAlias("ubuntu", "ubuntu", "22.04"),
    "noble": DistroAlias("ubuntu", "ubuntu", "24.04"),
    # SUSE
    "sles15": DistroAlias("suse", "sles", "15"),
    "leap15": DistroAlias("suse", "opensuse", "15"),
    "tumbleweed": DistroAlias("suse", "opensuse", "tumbleweed"),
    # FreeBSD
    "fbsd13": DistroAlias("freebsd", "freebsd", "13"),
    "fbsd14": DistroAlias("freebsd", "freebsd", "14"),
    "freebsd13": DistroAlias("freebsd", "freebsd", "13"),
    "freebsd14": DistroAlias("freebsd", "freebsd", "14"),
    # OpenBSD
    "obsd75": DistroAlias("openbsd", "openbsd", "7.5"),
    "obsd76": DistroAlias("openbsd", "openbsd", "7.6"),
    "openbsd76": DistroAlias("openbsd", "openbsd", "7.6"),
    # NetBSD
    "nbsd10": DistroAlias("netbsd", "netbsd", "10"),
    "netbsd10": DistroAlias("netbsd", "netbsd", "10"),
    # Arch
    "arch": DistroAlias("arch", "arch", "latest"),
    # Windows
    "win10": DistroAlias("windows", "windows", "10"),
    "win11": DistroAlias("windows", "windows", "11"),
    "win2022": DistroAlias("windows", "windows", "2022"),
    "win2025": DistroAlias("windows", "windows", "2025"),
}

_PATTERN = re.compile(
    r"^([a-z]+?)(\d[\d.]*)$", re.IGNORECASE
)

_FAMILY_PREFIXES: Dict[str, Tuple[str, str]] = {
    "fedora": ("fedora", "fedora"),
    "rhel": ("fedora", "rhel"),
    "rocky": ("fedora", "rocky"),
    "alma": ("fedora", "alma"),
    "centos": ("fedora", "centos"),
    "deb": ("debian", "debian"),
    "debian": ("debian", "debian"),
    "ubuntu": ("ubuntu", "ubuntu"),
    "sles": ("suse", "sles"),
    "leap": ("suse", "opensuse"),
    "suse": ("suse", "suse"),
    "fbsd": ("freebsd", "freebsd"),
    "freebsd": ("freebsd", "freebsd"),
    "obsd": ("openbsd", "openbsd"),
    "openbsd": ("openbsd", "openbsd"),
    "nbsd": ("netbsd", "netbsd"),
    "netbsd": ("netbsd", "netbsd"),
    "arch": ("arch", "arch"),
    "win": ("windows", "windows"),
    "windows": ("windows", "windows"),
}


class MnemonicRegistry:

    def __init__(self) -> None:
        self._aliases: Dict[str, DistroAlias] = dict(BUILTIN_ALIASES)

    def register(self, name: str, alias: DistroAlias) -> None:
        self._aliases[name.lower()] = alias

    def resolve(self, mnemonic: str) -> Optional[DistroAlias]:
        if not mnemonic or not mnemonic.strip():
            return None
        key = mnemonic.lower().replace("-", "").replace("_", "")
        if key in self._aliases:
            return self._aliases[key]
        return self._try_parse(key)

    def list_aliases(self) -> List[Tuple[str, DistroAlias]]:
        return sorted(self._aliases.items())

    def load_from_config(self, mnemonics_dict: dict) -> None:
        for name, entry in mnemonics_dict.items():
            if isinstance(entry, dict):
                self.register(
                    name,
                    DistroAlias(
                        os_family=entry.get("os_family", ""),
                        vendor=entry.get("vendor", ""),
                        version=entry.get("version", ""),
                    ),
                )

    @staticmethod
    def _try_parse(key: str) -> Optional[DistroAlias]:
        m = _PATTERN.match(key)
        if not m:
            return None
        prefix = m.group(1).lower()
        version = m.group(2)
        if prefix in _FAMILY_PREFIXES:
            os_family, vendor = _FAMILY_PREFIXES[prefix]
            return DistroAlias(os_family, vendor, version)
        return None


_default_registry = MnemonicRegistry()


def resolve_mnemonic(mnemonic: str) -> Optional[DistroAlias]:
    return _default_registry.resolve(mnemonic)


def list_mnemonics() -> List[Tuple[str, DistroAlias]]:
    return _default_registry.list_aliases()


def get_registry() -> MnemonicRegistry:
    return _default_registry
