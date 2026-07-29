"""Tests for tftpos.cluster -- cluster provisioning workflows."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from tftpos.cluster import (
    ClusterDefinition,
    ClusterHost,
    ClusterManager,
    ClusterState,
    ClusterStore,
    HostState,
    _validate_cluster_name,
    parse_cluster_toml,
)


# ---------------------------------------------------------------------------
# _validate_cluster_name
# ---------------------------------------------------------------------------


class TestValidateClusterName:

    def test_valid_simple_name(self):
        _validate_cluster_name("my-cluster")

    def test_valid_name_with_dots(self):
        _validate_cluster_name("k8s-prod.v2")

    def test_valid_name_with_underscores(self):
        _validate_cluster_name("ceph_cluster_01")

    def test_valid_alphanumeric(self):
        _validate_cluster_name("cluster1")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_cluster_name("")

    def test_dotdot_rejected(self):
        with pytest.raises(ValueError, match="must not contain"):
            _validate_cluster_name("../escape")

    def test_forward_slash_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_cluster_name("a/b")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            _validate_cluster_name("a\\b")

    def test_space_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_cluster_name("my cluster")

    def test_leading_dot_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_cluster_name(".hidden")

    def test_leading_dash_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_cluster_name("-bad")

    def test_special_chars_rejected(self):
        for char in ["$", "!", "@", "#", "%", "^", "&", "*"]:
            with pytest.raises(ValueError):
                _validate_cluster_name(f"bad{char}name")


# ---------------------------------------------------------------------------
# ClusterHost dataclass
# ---------------------------------------------------------------------------


class TestClusterHost:

    def test_required_fields(self):
        h = ClusterHost(hostname="node-01", mac="aa:bb:cc:dd:ee:01")
        assert h.hostname == "node-01"
        assert h.mac == "aa:bb:cc:dd:ee:01"

    def test_defaults(self):
        h = ClusterHost(hostname="node-01", mac="aa:bb:cc:dd:ee:01")
        assert h.role == "worker"
        assert h.profile == ""
        assert h.extra == {}
        assert h.state == HostState.PENDING
        assert h.error_message == ""

    def test_custom_role(self):
        h = ClusterHost(
            hostname="cp-01", mac="aa:bb:cc:dd:ee:01",
            role="control-plane",
        )
        assert h.role == "control-plane"

    def test_to_dict(self):
        h = ClusterHost(
            hostname="node-01", mac="aa:bb:cc:dd:ee:01",
            role="worker", profile="k8s-worker",
            extra={"rack": "A1"},
        )
        d = h.to_dict()
        assert d["hostname"] == "node-01"
        assert d["mac"] == "aa:bb:cc:dd:ee:01"
        assert d["role"] == "worker"
        assert d["state"] == "pending"
        assert d["extra"] == {"rack": "A1"}

    def test_mutable_extra_isolation(self):
        h1 = ClusterHost(hostname="a", mac="00:00:00:00:00:01")
        h2 = ClusterHost(hostname="b", mac="00:00:00:00:00:02")
        h1.extra["key"] = "val"
        assert h2.extra == {}


# ---------------------------------------------------------------------------
# ClusterDefinition dataclass
# ---------------------------------------------------------------------------


class TestClusterDefinition:

    def test_minimal_cluster(self):
        c = ClusterDefinition(name="test")
        assert c.name == "test"
        assert c.hosts == []
        assert c.shared_config == {}
        assert c.provisioning_order == []
        assert c.state == ClusterState.DEFINED
        assert c.callbacks == []

    def test_with_hosts(self):
        hosts = [
            ClusterHost(hostname="cp-01", mac="aa:bb:cc:dd:ee:01",
                        role="control-plane"),
            ClusterHost(hostname="w-01", mac="aa:bb:cc:dd:ee:02",
                        role="worker"),
        ]
        c = ClusterDefinition(name="k8s", hosts=hosts)
        assert len(c.hosts) == 2
        assert c.hosts[0].role == "control-plane"
        assert c.hosts[1].role == "worker"

    def test_to_dict_and_from_dict_roundtrip(self):
        hosts = [
            ClusterHost(
                hostname="cp-01", mac="aa:bb:cc:dd:ee:01",
                role="control-plane", profile="k8s-cp",
            ),
            ClusterHost(
                hostname="w-01", mac="aa:bb:cc:dd:ee:02",
                role="worker", profile="k8s-worker",
                extra={"gpu": True},
            ),
        ]
        original = ClusterDefinition(
            name="test-cluster",
            hosts=hosts,
            shared_config={"domain": "test.local", "dns": ["8.8.8.8"]},
            provisioning_order=["control-plane", "worker"],
            state=ClusterState.DEFINED,
            created_at=1000.0,
            updated_at=1001.0,
            callbacks=["http://hook.example.com/done"],
        )
        d = original.to_dict()
        restored = ClusterDefinition.from_dict(d)
        assert restored.name == original.name
        assert len(restored.hosts) == 2
        assert restored.hosts[0].hostname == "cp-01"
        assert restored.hosts[0].role == "control-plane"
        assert restored.hosts[1].extra == {"gpu": True}
        assert restored.shared_config == original.shared_config
        assert restored.provisioning_order == original.provisioning_order
        assert restored.state == original.state
        assert restored.callbacks == original.callbacks

    def test_hosts_by_role(self):
        hosts = [
            ClusterHost(hostname="cp-01", mac="00:00:00:00:00:01",
                        role="control-plane"),
            ClusterHost(hostname="w-01", mac="00:00:00:00:00:02",
                        role="worker"),
            ClusterHost(hostname="w-02", mac="00:00:00:00:00:03",
                        role="worker"),
        ]
        c = ClusterDefinition(name="test", hosts=hosts)
        cps = c.hosts_by_role("control-plane")
        workers = c.hosts_by_role("worker")
        assert len(cps) == 1
        assert len(workers) == 2
        assert c.hosts_by_role("nonexistent") == []

    def test_ordered_groups(self):
        hosts = [
            ClusterHost(hostname="w-01", mac="00:00:00:00:00:01",
                        role="worker"),
            ClusterHost(hostname="cp-01", mac="00:00:00:00:00:02",
                        role="control-plane"),
            ClusterHost(hostname="w-02", mac="00:00:00:00:00:03",
                        role="worker"),
            ClusterHost(hostname="mon-01", mac="00:00:00:00:00:04",
                        role="monitor"),
        ]
        c = ClusterDefinition(
            name="test", hosts=hosts,
            provisioning_order=["control-plane", "worker"],
        )
        groups = c.ordered_groups()
        # First group: control-plane
        assert len(groups) == 3
        assert len(groups[0]) == 1
        assert groups[0][0].role == "control-plane"
        # Second group: workers
        assert len(groups[1]) == 2
        assert all(h.role == "worker" for h in groups[1])
        # Third group: monitor (not in provisioning_order)
        assert len(groups[2]) == 1
        assert groups[2][0].role == "monitor"

    def test_ordered_groups_empty_order(self):
        hosts = [
            ClusterHost(hostname="a", mac="00:00:00:00:00:01",
                        role="worker"),
            ClusterHost(hostname="b", mac="00:00:00:00:00:02",
                        role="db"),
        ]
        c = ClusterDefinition(name="test", hosts=hosts)
        groups = c.ordered_groups()
        # All hosts in one group since no ordering
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_from_dict_defaults(self):
        c = ClusterDefinition.from_dict({"name": "minimal"})
        assert c.name == "minimal"
        assert c.hosts == []
        assert c.state == ClusterState.DEFINED


# ---------------------------------------------------------------------------
# parse_cluster_toml
# ---------------------------------------------------------------------------


class TestParseClusterToml:

    def test_basic_cluster(self):
        toml_text = textwrap.dedent("""\
            [cluster]
            name = "my-k8s"
            provisioning_order = ["control-plane", "worker"]

            [cluster.shared_config]
            domain = "k8s.example.com"
            dns = ["8.8.8.8"]

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
        """)
        cluster = parse_cluster_toml(toml_text)
        assert cluster.name == "my-k8s"
        assert len(cluster.hosts) == 2
        assert cluster.hosts[0].hostname == "cp-01"
        assert cluster.hosts[0].role == "control-plane"
        assert cluster.hosts[1].hostname == "worker-01"
        assert cluster.provisioning_order == ["control-plane", "worker"]
        assert cluster.shared_config["domain"] == "k8s.example.com"
        assert cluster.created_at is not None
        assert cluster.updated_at is not None

    def test_missing_name_raises(self):
        toml_text = textwrap.dedent("""\
            [cluster]
            provisioning_order = ["worker"]

            [[cluster.hosts]]
            hostname = "h1"
            mac = "aa:bb:cc:dd:ee:01"
        """)
        with pytest.raises(ValueError, match="must have a 'name'"):
            parse_cluster_toml(toml_text)

    def test_host_missing_hostname_raises(self):
        toml_text = textwrap.dedent("""\
            [cluster]
            name = "bad-cluster"

            [[cluster.hosts]]
            mac = "aa:bb:cc:dd:ee:01"
        """)
        with pytest.raises(ValueError, match="must have 'hostname' and 'mac'"):
            parse_cluster_toml(toml_text)

    def test_host_missing_mac_raises(self):
        toml_text = textwrap.dedent("""\
            [cluster]
            name = "bad-cluster"

            [[cluster.hosts]]
            hostname = "node-01"
        """)
        with pytest.raises(ValueError, match="must have 'hostname' and 'mac'"):
            parse_cluster_toml(toml_text)

    def test_no_hosts(self):
        toml_text = textwrap.dedent("""\
            [cluster]
            name = "empty-cluster"
        """)
        cluster = parse_cluster_toml(toml_text)
        assert cluster.name == "empty-cluster"
        assert cluster.hosts == []

    def test_host_defaults(self):
        toml_text = textwrap.dedent("""\
            [cluster]
            name = "defaults-test"

            [[cluster.hosts]]
            hostname = "node-01"
            mac = "aa:bb:cc:dd:ee:01"
        """)
        cluster = parse_cluster_toml(toml_text)
        host = cluster.hosts[0]
        assert host.role == "worker"
        assert host.profile == ""
        assert host.extra == {}

    def test_callbacks(self):
        toml_text = textwrap.dedent("""\
            [cluster]
            name = "with-callbacks"
            callbacks = ["http://hook1.example.com", "http://hook2.example.com"]

            [[cluster.hosts]]
            hostname = "h1"
            mac = "aa:bb:cc:dd:ee:01"
        """)
        cluster = parse_cluster_toml(toml_text)
        assert cluster.callbacks == [
            "http://hook1.example.com",
            "http://hook2.example.com",
        ]


# ---------------------------------------------------------------------------
# ClusterStore
# ---------------------------------------------------------------------------


class TestClusterStore:

    @pytest.fixture
    def store(self, tmp_path: Path) -> ClusterStore:
        return ClusterStore(tmp_path)

    @pytest.fixture
    def sample_cluster(self) -> ClusterDefinition:
        return ClusterDefinition(
            name="test-cluster",
            hosts=[
                ClusterHost(hostname="cp-01", mac="aa:bb:cc:dd:ee:01",
                            role="control-plane"),
                ClusterHost(hostname="w-01", mac="aa:bb:cc:dd:ee:02",
                            role="worker"),
            ],
            shared_config={"domain": "test.local"},
            provisioning_order=["control-plane", "worker"],
            created_at=1000.0,
            updated_at=1000.0,
        )

    def test_save_and_get(self, store, sample_cluster):
        store.save(sample_cluster)
        got = store.get("test-cluster")
        assert got is not None
        assert got.name == "test-cluster"
        assert len(got.hosts) == 2
        assert got.hosts[0].hostname == "cp-01"
        assert got.shared_config == {"domain": "test.local"}

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("no-such-cluster") is None

    def test_list_all_empty(self, store):
        assert store.list_all() == []

    def test_list_all_sorted(self, store):
        for name in ["zulu", "alpha", "mike"]:
            store.save(ClusterDefinition(
                name=name,
                hosts=[
                    ClusterHost(hostname="h", mac="00:00:00:00:00:01"),
                ],
            ))
        names = [c.name for c in store.list_all()]
        assert names == ["alpha", "mike", "zulu"]

    def test_delete_existing(self, store, sample_cluster):
        store.save(sample_cluster)
        assert store.delete("test-cluster") is True
        assert store.get("test-cluster") is None

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("no-such-cluster") is False

    def test_save_creates_json_file(self, store, sample_cluster, tmp_path):
        store.save(sample_cluster)
        json_path = tmp_path / "clusters" / "test-cluster.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["name"] == "test-cluster"

    def test_path_traversal_rejected_on_save(self, store):
        cluster = ClusterDefinition(name="../escape")
        with pytest.raises(ValueError):
            store.save(cluster)

    def test_path_traversal_rejected_on_get(self, store):
        with pytest.raises(ValueError):
            store.get("../escape")

    def test_path_traversal_rejected_on_delete(self, store):
        with pytest.raises(ValueError):
            store.delete("../escape")

    def test_persistence_across_instances(self, tmp_path):
        store1 = ClusterStore(tmp_path)
        store1.save(ClusterDefinition(
            name="persist-test",
            hosts=[
                ClusterHost(hostname="h1", mac="00:00:00:00:00:01"),
            ],
        ))

        store2 = ClusterStore(tmp_path)
        got = store2.get("persist-test")
        assert got is not None
        assert got.name == "persist-test"

    def test_directory_creation(self, tmp_path):
        data_dir = tmp_path / "new" / "nested"
        ClusterStore(data_dir)
        assert (data_dir / "clusters").is_dir()


# ---------------------------------------------------------------------------
# ClusterManager
# ---------------------------------------------------------------------------


class TestClusterManager:

    @pytest.fixture
    def manager(self, tmp_path: Path) -> ClusterManager:
        store = ClusterStore(tmp_path)
        return ClusterManager(store)

    @pytest.fixture
    def sample_definition(self) -> ClusterDefinition:
        return ClusterDefinition(
            name="test-cluster",
            hosts=[
                ClusterHost(hostname="cp-01", mac="aa:bb:cc:dd:ee:01",
                            role="control-plane", profile="k8s-cp"),
                ClusterHost(hostname="w-01", mac="aa:bb:cc:dd:ee:02",
                            role="worker", profile="k8s-worker"),
                ClusterHost(hostname="w-02", mac="aa:bb:cc:dd:ee:03",
                            role="worker", profile="k8s-worker"),
            ],
            shared_config={"domain": "k8s.local", "dns": ["8.8.8.8"]},
            provisioning_order=["control-plane", "worker"],
        )

    def test_create_cluster(self, manager, sample_definition):
        cluster = manager.create_cluster(sample_definition)
        assert cluster.name == "test-cluster"
        assert cluster.state == ClusterState.DEFINED
        assert cluster.created_at is not None
        assert cluster.updated_at is not None
        assert len(cluster.hosts) == 3

    def test_create_duplicate_raises(self, manager, sample_definition):
        manager.create_cluster(sample_definition)
        with pytest.raises(ValueError, match="already exists"):
            manager.create_cluster(sample_definition)

    def test_create_empty_hosts_raises(self, manager):
        with pytest.raises(ValueError, match="at least one host"):
            manager.create_cluster(ClusterDefinition(name="empty"))

    def test_create_duplicate_macs_raises(self, manager):
        d = ClusterDefinition(
            name="dup-macs",
            hosts=[
                ClusterHost(hostname="a", mac="aa:bb:cc:dd:ee:01"),
                ClusterHost(hostname="b", mac="aa:bb:cc:dd:ee:01"),
            ],
        )
        with pytest.raises(ValueError, match="duplicate MAC"):
            manager.create_cluster(d)

    def test_create_duplicate_hostnames_raises(self, manager):
        d = ClusterDefinition(
            name="dup-hosts",
            hosts=[
                ClusterHost(hostname="same-name", mac="aa:bb:cc:dd:ee:01"),
                ClusterHost(hostname="same-name", mac="aa:bb:cc:dd:ee:02"),
            ],
        )
        with pytest.raises(ValueError, match="duplicate hostnames"):
            manager.create_cluster(d)

    def test_create_invalid_name_raises(self, manager):
        d = ClusterDefinition(
            name="../bad",
            hosts=[
                ClusterHost(hostname="h1", mac="aa:bb:cc:dd:ee:01"),
            ],
        )
        with pytest.raises(ValueError):
            manager.create_cluster(d)

    def test_get_cluster(self, manager, sample_definition):
        manager.create_cluster(sample_definition)
        got = manager.get_cluster("test-cluster")
        assert got is not None
        assert got.name == "test-cluster"

    def test_get_nonexistent_returns_none(self, manager):
        assert manager.get_cluster("nope") is None

    def test_list_clusters(self, manager):
        for name in ["alpha", "beta"]:
            manager.create_cluster(ClusterDefinition(
                name=name,
                hosts=[
                    ClusterHost(hostname="h1", mac=f"00:00:00:00:00:{ord(name[0]):02x}"),
                ],
            ))
        clusters = manager.list_clusters()
        assert len(clusters) == 2
        names = [c.name for c in clusters]
        assert "alpha" in names
        assert "beta" in names

    def test_delete_cluster(self, manager, sample_definition):
        manager.create_cluster(sample_definition)
        assert manager.delete_cluster("test-cluster") is True
        assert manager.get_cluster("test-cluster") is None

    def test_delete_nonexistent_returns_false(self, manager):
        assert manager.delete_cluster("nope") is False

    def test_get_cluster_status(self, manager, sample_definition):
        manager.create_cluster(sample_definition)
        status = manager.get_cluster_status("test-cluster")
        assert status is not None
        assert status["name"] == "test-cluster"
        assert status["state"] == "defined"
        assert status["total_hosts"] == 3
        assert status["hosts_by_state"]["pending"] == 3

    def test_get_cluster_status_nonexistent(self, manager):
        assert manager.get_cluster_status("nope") is None

    def test_start_provisioning_success(self, manager, sample_definition):
        manager.create_cluster(sample_definition)
        cluster = manager.start_provisioning("test-cluster")
        assert cluster.state == ClusterState.COMPLETE
        assert all(
            h.state == HostState.COMPLETE for h in cluster.hosts
        )

    def test_start_provisioning_with_callback(self, manager, sample_definition):
        provisioned_hosts = []

        def track_provision(cluster, host):
            provisioned_hosts.append(host.hostname)

        manager.on_provision(track_provision)
        manager.create_cluster(sample_definition)
        manager.start_provisioning("test-cluster")

        # Should have provisioned in order: cp first, then workers
        assert provisioned_hosts[0] == "cp-01"
        assert set(provisioned_hosts[1:]) == {"w-01", "w-02"}

    def test_start_provisioning_callback_failure(self, manager, sample_definition):
        call_count = 0

        def failing_callback(cluster, host):
            nonlocal call_count
            call_count += 1
            if host.role == "worker":
                raise RuntimeError("worker provision failed")

        manager.on_provision(failing_callback)
        manager.create_cluster(sample_definition)
        cluster = manager.start_provisioning("test-cluster")

        # Control plane succeeded, workers failed
        assert cluster.state == ClusterState.PARTIAL
        cp = [h for h in cluster.hosts if h.role == "control-plane"]
        workers = [h for h in cluster.hosts if h.role == "worker"]
        assert cp[0].state == HostState.COMPLETE
        assert all(w.state == HostState.FAILED for w in workers)
        assert "worker provision failed" in workers[0].error_message

    def test_start_provisioning_all_fail(self, manager):
        d = ClusterDefinition(
            name="fail-all",
            hosts=[
                ClusterHost(hostname="h1", mac="00:00:00:00:00:01"),
                ClusterHost(hostname="h2", mac="00:00:00:00:00:02"),
            ],
        )

        def always_fail(cluster, host):
            raise RuntimeError("boom")

        manager.on_provision(always_fail)
        manager.create_cluster(d)
        cluster = manager.start_provisioning("fail-all")
        assert cluster.state == ClusterState.FAILED

    def test_start_provisioning_nonexistent_raises(self, manager):
        with pytest.raises(ValueError, match="not found"):
            manager.start_provisioning("nope")

    def test_start_provisioning_already_running_raises(
        self, manager, sample_definition
    ):
        manager.create_cluster(sample_definition)
        # Manually set to provisioning
        cluster = manager.get_cluster("test-cluster")
        cluster.state = ClusterState.PROVISIONING
        manager._store.save(cluster)

        with pytest.raises(ValueError, match="already being provisioned"):
            manager.start_provisioning("test-cluster")

    def test_mark_host_complete(self, manager, sample_definition):
        manager.create_cluster(sample_definition)
        cluster = manager.mark_host_complete(
            "test-cluster", "aa:bb:cc:dd:ee:01"
        )
        cp = [h for h in cluster.hosts if h.role == "control-plane"]
        assert cp[0].state == HostState.COMPLETE

    def test_mark_host_complete_all_done(self, manager):
        d = ClusterDefinition(
            name="tiny",
            hosts=[
                ClusterHost(hostname="h1", mac="00:00:00:00:00:01"),
            ],
        )
        manager.create_cluster(d)
        cluster = manager.mark_host_complete("tiny", "00:00:00:00:00:01")
        assert cluster.state == ClusterState.COMPLETE

    def test_mark_host_complete_nonexistent_cluster_raises(self, manager):
        with pytest.raises(ValueError, match="not found"):
            manager.mark_host_complete("nope", "00:00:00:00:00:01")

    def test_mark_host_complete_nonexistent_mac_raises(
        self, manager, sample_definition
    ):
        manager.create_cluster(sample_definition)
        with pytest.raises(ValueError, match="not in cluster"):
            manager.mark_host_complete(
                "test-cluster", "ff:ff:ff:ff:ff:ff"
            )

    def test_mark_host_complete_case_insensitive_mac(
        self, manager, sample_definition
    ):
        manager.create_cluster(sample_definition)
        cluster = manager.mark_host_complete(
            "test-cluster", "AA:BB:CC:DD:EE:01"
        )
        cp = [h for h in cluster.hosts if h.role == "control-plane"]
        assert cp[0].state == HostState.COMPLETE

    def test_mark_host_failed(self, manager, sample_definition):
        manager.create_cluster(sample_definition)
        cluster = manager.mark_host_failed(
            "test-cluster", "aa:bb:cc:dd:ee:01", "disk error"
        )
        cp = [h for h in cluster.hosts if h.role == "control-plane"]
        assert cp[0].state == HostState.FAILED
        assert cp[0].error_message == "disk error"

    def test_mark_host_failed_all_fail(self, manager):
        d = ClusterDefinition(
            name="tiny",
            hosts=[
                ClusterHost(hostname="h1", mac="00:00:00:00:00:01"),
            ],
        )
        manager.create_cluster(d)
        cluster = manager.mark_host_failed(
            "tiny", "00:00:00:00:00:01", "boom"
        )
        assert cluster.state == ClusterState.FAILED

    def test_mark_host_failed_partial(self, manager, sample_definition):
        manager.create_cluster(sample_definition)
        # Mark one complete, one failed
        manager.mark_host_complete("test-cluster", "aa:bb:cc:dd:ee:01")
        cluster = manager.mark_host_failed(
            "test-cluster", "aa:bb:cc:dd:ee:02", "error"
        )
        assert cluster.state == ClusterState.PARTIAL

    def test_mark_host_failed_nonexistent_cluster_raises(self, manager):
        with pytest.raises(ValueError, match="not found"):
            manager.mark_host_failed("nope", "00:00:00:00:00:01")

    def test_mark_host_failed_nonexistent_mac_raises(
        self, manager, sample_definition
    ):
        manager.create_cluster(sample_definition)
        with pytest.raises(ValueError, match="not in cluster"):
            manager.mark_host_failed(
                "test-cluster", "ff:ff:ff:ff:ff:ff"
            )
