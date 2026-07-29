"""Host matching engine for provisioning requests."""

from __future__ import annotations

import fnmatch
import ipaddress
from typing import List, Optional

from tftpos.models import HostRule


class HostMatcher:

    def __init__(self, rules: List[HostRule]) -> None:
        self._rules = sorted(rules, key=lambda r: r.priority)

    def match(
        self,
        mac: Optional[str] = None,
        hostname: Optional[str] = None,
        subnet: Optional[str] = None,
        serial: Optional[str] = None,
        groups: Optional[List[str]] = None,
        arch: Optional[str] = None,
    ) -> Optional[HostRule]:
        mac_norm = mac.lower().replace("-", ":") if mac else None

        candidates: list[tuple[int, int, HostRule]] = []

        for rule in self._rules:
            tier = self._match_tier(
                rule, mac_norm, hostname, subnet, serial,
                groups, arch,
            )
            if tier is not None:
                candidates.append((tier, rule.priority, rule))

        if not candidates:
            return None

        candidates.sort(key=lambda t: (t[0], t[1]))
        return candidates[0][2]

    def _match_tier(
        self,
        rule: HostRule,
        mac: Optional[str],
        hostname: Optional[str],
        subnet: Optional[str],
        serial: Optional[str],
        groups: Optional[List[str]],
        arch: Optional[str],
    ) -> Optional[int]:
        if rule.mac and mac:
            rule_mac = rule.mac.lower().replace("-", ":")
            if rule_mac == mac:
                return 0
            return None

        if rule.mac_prefix and mac:
            prefix = rule.mac_prefix.lower().replace("-", ":")
            if mac.startswith(prefix):
                return 1
            return None

        if rule.hostname_pattern and hostname:
            if fnmatch.fnmatch(hostname.lower(), rule.hostname_pattern.lower()):
                return 2
            return None

        if rule.subnet and subnet:
            if self._subnet_match(subnet, rule.subnet):
                return 3
            return None

        if rule.serial and serial:
            if rule.serial == serial:
                return 4
            return None

        if rule.group and groups:
            if rule.group in groups:
                return 5
            return None

        if rule.arch and arch:
            if rule.arch.lower() == arch.lower():
                return 6
            return None

        has_criteria = any([
            rule.mac, rule.mac_prefix, rule.hostname_pattern,
            rule.subnet, rule.serial, rule.group, rule.arch,
        ])
        if not has_criteria:
            return 7

        return None

    @staticmethod
    def _subnet_match(client_ip: str, cidr: str) -> bool:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
            address = ipaddress.ip_address(client_ip)
            return address in network
        except ValueError:
            return False
