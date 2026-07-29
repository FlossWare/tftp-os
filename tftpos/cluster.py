"""Cluster provisioning workflows for multi-host deployments.

Provides a TOML-based cluster definition format and a ClusterManager
that orchestrates ordered provisioning of host groups.  Clusters are
persisted as JSON files under ``<data_dir>/clusters/``.

A cluster definition groups hosts by role, specifies provisioning
order, and carries shared configuration that is injected into each
host's provisioning context.
"""

from __future__ import annotations

import enum
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("tftpos.cluster")

# ------------------------------------------------------------------
# Name validation (same pattern as named_objects)
# ------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_cluster_name(name: str) -> None:
    """Raise ``ValueError`` if *name* is unsafe for use as a filename."""
    if not name:
        raise ValueError("cluster name must not be empty")
    if ".." in name:
        raise ValueError(
            f"cluster name must not contain '..': {name!r}"
        )
    if "/" in name or "\\" in name:
        raise ValueError(
            f"cluster name must not contain path separators: {name!r}"
        )
    if not _SAFE_NAME_RE.match(name):
        raise ValueError(
            f"cluster name contains invalid characters: {name!r}"
        )


# ------------------------------------------------------------------
# Cluster state
# ------------------------------------------------------------------


class ClusterState(enum.Enum):
    """Lifecycle states for a cluster provisioning workflow."""

    DEFINED = "defined"
    PROVISIONING = "provisioning"
    PARTIAL = "partial"
    COMPLETE = "complete"
    FAILED = "failed"


class HostState(enum.Enum):
    """Lifecycle states for an individual host within a cluster."""

    PENDING = "pending"
    PROVISIONING = "provisioning"
    COMPLETE = "complete"
    FAILED = "failed"


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass
class ClusterHost:
    """A single host within a cluster definition."""

    hostname: str
    mac: str
    role: str = "worker"
    profile: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
    state: HostState = HostState.PENDING
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dictionary."""
        return {
            "hostname": self.hostname,
            "mac": self.mac,
            "role": self.role,
            "profile": self.profile,
            "extra": self.extra,
            "state": self.state.value,
            "error_message": self.error_message,
        }


@dataclass
class ClusterDefinition:
    """A named group of hosts to be provisioned together.

    Attributes:
        name:                Unique cluster identifier.
        hosts:               List of hosts with roles.
        shared_config:       Cluster-wide settings injected into all hosts
                             (domain, DNS, NTP, etc.).
        provisioning_order:  Ordered list of role names; hosts with earlier
                             roles are provisioned first.
        state:               Current cluster lifecycle state.
        created_at:          Timestamp when the cluster was defined.
        updated_at:          Timestamp of the last state change.
        callbacks:           Optional list of webhook URLs to POST when
                             provisioning completes.
    """

    name: str
    hosts: List[ClusterHost] = field(default_factory=list)
    shared_config: Dict[str, Any] = field(default_factory=dict)
    provisioning_order: List[str] = field(default_factory=list)
    state: ClusterState = ClusterState.DEFINED
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    callbacks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the full cluster to a JSON-friendly dictionary."""
        return {
            "name": self.name,
            "hosts": [h.to_dict() for h in self.hosts],
            "shared_config": self.shared_config,
            "provisioning_order": self.provisioning_order,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "callbacks": self.callbacks,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> ClusterDefinition:
        """Deserialize from a dictionary (e.g. loaded from JSON)."""
        hosts = [
            ClusterHost(
                hostname=h["hostname"],
                mac=h["mac"],
                role=h.get("role", "worker"),
                profile=h.get("profile", ""),
                extra=h.get("extra", {}),
                state=HostState(h.get("state", "pending")),
                error_message=h.get("error_message", ""),
            )
            for h in data.get("hosts", [])
        ]
        return ClusterDefinition(
            name=data["name"],
            hosts=hosts,
            shared_config=data.get("shared_config", {}),
            provisioning_order=data.get("provisioning_order", []),
            state=ClusterState(data.get("state", "defined")),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            callbacks=data.get("callbacks", []),
        )

    def hosts_by_role(self, role: str) -> List[ClusterHost]:
        """Return all hosts with the given role."""
        return [h for h in self.hosts if h.role == role]

    def ordered_groups(self) -> List[List[ClusterHost]]:
        """Return hosts grouped by provisioning order.

        Returns a list of lists.  Each inner list contains the hosts
        that share a role, ordered according to ``provisioning_order``.
        Hosts whose role is not in the ordering are placed last.
        """
        role_set = {r for r in self.provisioning_order}
        groups: List[List[ClusterHost]] = []

        for role in self.provisioning_order:
            group = self.hosts_by_role(role)
            if group:
                groups.append(group)

        # Collect hosts with roles not in provisioning_order
        remaining = [
            h for h in self.hosts if h.role not in role_set
        ]
        if remaining:
            groups.append(remaining)

        return groups


# ------------------------------------------------------------------
# TOML parser
# ------------------------------------------------------------------


def parse_cluster_toml(text: str) -> ClusterDefinition:
    """Parse a TOML cluster definition string.

    Expected format::

        [cluster]
        name = "my-k8s"
        provisioning_order = ["control-plane", "worker"]
        callbacks = ["http://webhook.example.com/done"]

        [cluster.shared_config]
        domain = "k8s.example.com"
        dns = ["8.8.8.8", "8.8.4.4"]
        ntp = "pool.ntp.org"

        [[cluster.hosts]]
        hostname = "cp-01"
        mac = "aa:bb:cc:dd:ee:01"
        role = "control-plane"
        profile = "k8s-control"

        [[cluster.hosts]]
        hostname = "worker-01"
        mac = "aa:bb:cc:dd:ee:02"
        role = "worker"
        profile = "k8s-worker"

    Returns a :class:`ClusterDefinition`.
    """
    import sys

    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads(text)
    cluster = data.get("cluster", data)

    name = cluster.get("name")
    if not name:
        raise ValueError("cluster definition must have a 'name'")

    hosts_data = cluster.get("hosts", [])
    hosts: List[ClusterHost] = []
    for h in hosts_data:
        if "hostname" not in h or "mac" not in h:
            raise ValueError(
                "each host must have 'hostname' and 'mac'"
            )
        hosts.append(ClusterHost(
            hostname=h["hostname"],
            mac=h["mac"],
            role=h.get("role", "worker"),
            profile=h.get("profile", ""),
            extra=h.get("extra", {}),
        ))

    now = time.time()
    return ClusterDefinition(
        name=name,
        hosts=hosts,
        shared_config=cluster.get("shared_config", {}),
        provisioning_order=cluster.get("provisioning_order", []),
        callbacks=cluster.get("callbacks", []),
        created_at=now,
        updated_at=now,
    )


# ------------------------------------------------------------------
# Cluster store (JSON-backed persistence)
# ------------------------------------------------------------------


class ClusterStore:
    """Persists cluster definitions as JSON files.

    Directory layout::

        <data_dir>/clusters/<name>.json
    """

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "clusters"
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, cluster: ClusterDefinition) -> None:
        """Persist a cluster definition to disk."""
        _validate_cluster_name(cluster.name)
        path = self._dir / f"{cluster.name}.json"
        path.write_text(json.dumps(cluster.to_dict(), indent=2))

    def get(self, name: str) -> Optional[ClusterDefinition]:
        """Load a cluster by name, or return None."""
        _validate_cluster_name(name)
        path = self._dir / f"{name}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ClusterDefinition.from_dict(data)

    def list_all(self) -> List[ClusterDefinition]:
        """Return all stored clusters, sorted by name."""
        results: List[ClusterDefinition] = []
        for p in sorted(self._dir.glob("*.json")):
            data = json.loads(p.read_text())
            results.append(ClusterDefinition.from_dict(data))
        return results

    def delete(self, name: str) -> bool:
        """Delete a cluster definition. Returns True if it existed."""
        _validate_cluster_name(name)
        path = self._dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False


# ------------------------------------------------------------------
# Cluster manager (orchestration)
# ------------------------------------------------------------------


class ClusterManager:
    """Orchestrates ordered provisioning of cluster hosts.

    The manager coordinates provisioning by iterating through the
    ``provisioning_order`` roles and triggering provisioning for
    each group of hosts in sequence.

    It does **not** call the provisioning engine directly -- instead
    it tracks state and delegates actual provisioning to an optional
    callback function.
    """

    def __init__(self, store: ClusterStore) -> None:
        self._store = store
        self._provision_callbacks: List[
            Callable[[ClusterDefinition, ClusterHost], None]
        ] = []

    def on_provision(
        self,
        callback: Callable[[ClusterDefinition, ClusterHost], None],
    ) -> None:
        """Register a callback invoked for each host when provisioning."""
        self._provision_callbacks.append(callback)

    def create_cluster(
        self, definition: ClusterDefinition
    ) -> ClusterDefinition:
        """Validate and persist a new cluster definition."""
        _validate_cluster_name(definition.name)

        if not definition.hosts:
            raise ValueError("cluster must have at least one host")

        # Validate MACs are unique within the cluster
        macs = [h.mac.lower() for h in definition.hosts]
        if len(macs) != len(set(macs)):
            raise ValueError(
                "duplicate MAC addresses in cluster definition"
            )

        # Validate hostnames are unique within the cluster
        hostnames = [h.hostname for h in definition.hosts]
        if len(hostnames) != len(set(hostnames)):
            raise ValueError(
                "duplicate hostnames in cluster definition"
            )

        existing = self._store.get(definition.name)
        if existing is not None:
            raise ValueError(
                f"cluster {definition.name!r} already exists"
            )

        now = time.time()
        if definition.created_at is None:
            definition.created_at = now
        definition.updated_at = now

        self._store.save(definition)
        logger.info(
            "Cluster %r created with %d hosts",
            definition.name, len(definition.hosts),
        )
        return definition

    def start_provisioning(
        self, name: str
    ) -> ClusterDefinition:
        """Begin the provisioning workflow for a cluster.

        Walks through the provisioning order, marking hosts as
        provisioning and invoking any registered callbacks.
        """
        cluster = self._store.get(name)
        if cluster is None:
            raise ValueError(f"cluster {name!r} not found")

        if cluster.state == ClusterState.PROVISIONING:
            raise ValueError(
                f"cluster {name!r} is already being provisioned"
            )

        cluster.state = ClusterState.PROVISIONING
        cluster.updated_at = time.time()

        groups = cluster.ordered_groups()
        all_succeeded = True

        for group in groups:
            for host in group:
                host.state = HostState.PROVISIONING
                cluster.updated_at = time.time()
                self._store.save(cluster)

                try:
                    for cb in self._provision_callbacks:
                        cb(cluster, host)
                    host.state = HostState.COMPLETE
                except Exception as exc:
                    host.state = HostState.FAILED
                    host.error_message = str(exc)
                    all_succeeded = False
                    logger.error(
                        "Provisioning failed for host %s in "
                        "cluster %s: %s",
                        host.hostname, name, exc,
                    )

                cluster.updated_at = time.time()
                self._store.save(cluster)

        if all_succeeded:
            cluster.state = ClusterState.COMPLETE
        else:
            # Some hosts succeeded, some failed
            has_complete = any(
                h.state == HostState.COMPLETE
                for h in cluster.hosts
            )
            if has_complete:
                cluster.state = ClusterState.PARTIAL
            else:
                cluster.state = ClusterState.FAILED

        cluster.updated_at = time.time()
        self._store.save(cluster)
        logger.info(
            "Cluster %r provisioning finished: %s",
            name, cluster.state.value,
        )
        return cluster

    def get_cluster(self, name: str) -> Optional[ClusterDefinition]:
        """Retrieve a cluster definition by name."""
        return self._store.get(name)

    def list_clusters(self) -> List[ClusterDefinition]:
        """List all cluster definitions."""
        return self._store.list_all()

    def delete_cluster(self, name: str) -> bool:
        """Delete a cluster definition."""
        return self._store.delete(name)

    def get_cluster_status(
        self, name: str
    ) -> Optional[Dict[str, Any]]:
        """Return a summary of the cluster's provisioning status."""
        cluster = self._store.get(name)
        if cluster is None:
            return None

        total = len(cluster.hosts)
        by_state: Dict[str, int] = {}
        for h in cluster.hosts:
            by_state[h.state.value] = (
                by_state.get(h.state.value, 0) + 1
            )

        return {
            "name": cluster.name,
            "state": cluster.state.value,
            "total_hosts": total,
            "hosts_by_state": by_state,
            "created_at": cluster.created_at,
            "updated_at": cluster.updated_at,
        }

    def mark_host_complete(
        self, cluster_name: str, mac: str
    ) -> ClusterDefinition:
        """Mark a single host as complete and update cluster state."""
        cluster = self._store.get(cluster_name)
        if cluster is None:
            raise ValueError(
                f"cluster {cluster_name!r} not found"
            )

        mac_lower = mac.lower()
        host = None
        for h in cluster.hosts:
            if h.mac.lower() == mac_lower:
                host = h
                break

        if host is None:
            raise ValueError(
                f"host with MAC {mac!r} not in cluster "
                f"{cluster_name!r}"
            )

        host.state = HostState.COMPLETE
        cluster.updated_at = time.time()

        # Check if all hosts are now complete
        if all(
            h.state == HostState.COMPLETE
            for h in cluster.hosts
        ):
            cluster.state = ClusterState.COMPLETE
        elif any(
            h.state == HostState.FAILED
            for h in cluster.hosts
        ):
            cluster.state = ClusterState.PARTIAL

        self._store.save(cluster)
        return cluster

    def mark_host_failed(
        self, cluster_name: str, mac: str, error: str = ""
    ) -> ClusterDefinition:
        """Mark a single host as failed and update cluster state."""
        cluster = self._store.get(cluster_name)
        if cluster is None:
            raise ValueError(
                f"cluster {cluster_name!r} not found"
            )

        mac_lower = mac.lower()
        host = None
        for h in cluster.hosts:
            if h.mac.lower() == mac_lower:
                host = h
                break

        if host is None:
            raise ValueError(
                f"host with MAC {mac!r} not in cluster "
                f"{cluster_name!r}"
            )

        host.state = HostState.FAILED
        host.error_message = error
        cluster.updated_at = time.time()

        # Update cluster state
        has_complete = any(
            h.state == HostState.COMPLETE
            for h in cluster.hosts
        )
        if has_complete:
            cluster.state = ClusterState.PARTIAL
        else:
            all_failed = all(
                h.state == HostState.FAILED
                for h in cluster.hosts
            )
            if all_failed:
                cluster.state = ClusterState.FAILED

        self._store.save(cluster)
        return cluster
