"""Tests for repository mirroring (issue #42)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tftpos.repo_mirror import (
    RepoManager,
    RepoMirror,
    SyncResult,
    _validate_mirror_name,
    _validate_source_url,
)


# -- RepoMirror dataclass tests --


class TestRepoMirrorModel:

    def test_default_values(self):
        m = RepoMirror(
            name="fedora-40",
            source_url="https://mirror.example.com/fedora/40/",
            local_path="/srv/mirrors/fedora-40",
        )
        assert m.sync_interval == 86400
        assert m.last_sync is None

    def test_custom_interval(self):
        m = RepoMirror(
            name="fedora-40",
            source_url="https://mirror.example.com/fedora/40/",
            local_path="/srv/mirrors/fedora-40",
            sync_interval=3600,
        )
        assert m.sync_interval == 3600

    def test_last_sync_stored(self):
        m = RepoMirror(
            name="test",
            source_url="https://example.com/repo/",
            local_path="/tmp/test",
            last_sync=1000000.0,
        )
        assert m.last_sync == 1000000.0


# -- Name validation tests --


class TestValidateMirrorName:

    def test_valid_name(self):
        _validate_mirror_name("fedora-40")

    def test_valid_name_with_dots(self):
        _validate_mirror_name("fedora.40.x86_64")

    def test_valid_name_with_underscores(self):
        _validate_mirror_name("fedora_40")

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="invalid mirror name"):
            _validate_mirror_name("")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="invalid mirror name|path traversal"):
            _validate_mirror_name("../etc/passwd")

    def test_rejects_slash(self):
        with pytest.raises(ValueError, match="invalid mirror name"):
            _validate_mirror_name("bad/name")

    def test_rejects_starting_with_hyphen(self):
        with pytest.raises(ValueError, match="invalid mirror name"):
            _validate_mirror_name("-bad-name")


# -- URL validation tests --


class TestValidateSourceUrl:

    def test_http_allowed(self):
        _validate_source_url("http://mirror.example.com/repo/")

    def test_https_allowed(self):
        _validate_source_url("https://mirror.example.com/repo/")

    def test_ftp_allowed(self):
        _validate_source_url("ftp://mirror.example.com/repo/")

    def test_rsync_allowed(self):
        _validate_source_url("rsync://mirror.example.com/repo/")

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="unsupported URL scheme"):
            _validate_source_url("file:///tmp/repo/")

    def test_rejects_missing_host(self):
        with pytest.raises(ValueError, match="must include a host"):
            _validate_source_url("http://")


# -- RepoManager CRUD tests --


class TestRepoManagerCrud:

    def test_add_mirror(self, tmp_path):
        mgr = RepoManager(tmp_path)
        mirror = RepoMirror(
            name="fedora-40",
            source_url="https://mirror.example.com/fedora/40/",
            local_path="",
        )
        result = mgr.add_mirror(mirror)
        assert result.name == "fedora-40"
        assert result.source_url == "https://mirror.example.com/fedora/40/"

    def test_add_mirror_assigns_default_path(self, tmp_path):
        mgr = RepoManager(tmp_path)
        mirror = RepoMirror(
            name="fedora-40",
            source_url="https://mirror.example.com/fedora/40/",
            local_path="",
        )
        result = mgr.add_mirror(mirror)
        assert "fedora-40" in result.local_path

    def test_add_duplicate_raises(self, tmp_path):
        mgr = RepoManager(tmp_path)
        mirror = RepoMirror(
            name="fedora-40",
            source_url="https://mirror.example.com/fedora/40/",
            local_path="",
        )
        mgr.add_mirror(mirror)
        with pytest.raises(ValueError, match="already exists"):
            mgr.add_mirror(
                RepoMirror(
                    name="fedora-40",
                    source_url="https://other.example.com/",
                    local_path="",
                )
            )

    def test_remove_mirror(self, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="fedora-40",
                source_url="https://mirror.example.com/fedora/40/",
                local_path="",
            )
        )
        assert mgr.remove_mirror("fedora-40") is True
        assert mgr.get_mirror("fedora-40") is None

    def test_remove_nonexistent_returns_false(self, tmp_path):
        mgr = RepoManager(tmp_path)
        assert mgr.remove_mirror("nonexistent") is False

    def test_get_mirror(self, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="test-mirror",
                source_url="https://mirror.example.com/",
                local_path="",
            )
        )
        m = mgr.get_mirror("test-mirror")
        assert m is not None
        assert m.name == "test-mirror"

    def test_get_mirror_returns_none(self, tmp_path):
        mgr = RepoManager(tmp_path)
        assert mgr.get_mirror("nonexistent") is None

    def test_list_mirrors_sorted(self, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="zzz-mirror",
                source_url="https://mirror.example.com/zzz/",
                local_path="",
            )
        )
        mgr.add_mirror(
            RepoMirror(
                name="aaa-mirror",
                source_url="https://mirror.example.com/aaa/",
                local_path="",
            )
        )
        mirrors = mgr.list_mirrors()
        assert len(mirrors) == 2
        assert mirrors[0].name == "aaa-mirror"
        assert mirrors[1].name == "zzz-mirror"

    def test_list_mirrors_empty(self, tmp_path):
        mgr = RepoManager(tmp_path)
        assert mgr.list_mirrors() == []


# -- Persistence tests --


class TestRepoManagerPersistence:

    def test_config_persisted_to_json(self, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="persisted",
                source_url="https://mirror.example.com/repo/",
                local_path="",
            )
        )
        config_path = tmp_path / "mirrors.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert len(data) == 1
        assert data[0]["name"] == "persisted"

    def test_config_survives_reload(self, tmp_path):
        mgr1 = RepoManager(tmp_path)
        mgr1.add_mirror(
            RepoMirror(
                name="persist-test",
                source_url="https://mirror.example.com/",
                local_path="",
            )
        )
        # Create a new manager instance (simulates restart)
        mgr2 = RepoManager(tmp_path)
        m = mgr2.get_mirror("persist-test")
        assert m is not None
        assert m.source_url == "https://mirror.example.com/"

    def test_removal_persisted(self, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="to-remove",
                source_url="https://mirror.example.com/",
                local_path="",
            )
        )
        mgr.remove_mirror("to-remove")

        mgr2 = RepoManager(tmp_path)
        assert mgr2.get_mirror("to-remove") is None

    def test_corrupt_json_handled_gracefully(self, tmp_path):
        config_path = tmp_path / "mirrors.json"
        config_path.write_text("{invalid json")
        mgr = RepoManager(tmp_path)
        assert mgr.list_mirrors() == []


# -- Sync tests --


class TestRepoManagerSync:

    @patch("tftpos.repo_mirror.subprocess.run")
    def test_sync_rsync_source(self, mock_run, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="rsync-repo",
                source_url="rsync://mirror.example.com/repo/",
                local_path=str(tmp_path / "mirrors" / "rsync-repo"),
            )
        )
        result = mgr.sync_mirror("rsync-repo")

        assert result.success is True
        assert result.mirror_name == "rsync-repo"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "rsync"

    @patch("tftpos.repo_mirror.subprocess.run")
    def test_sync_http_source(self, mock_run, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="http-repo",
                source_url="https://mirror.example.com/repo/",
                local_path=str(tmp_path / "mirrors" / "http-repo"),
            )
        )
        result = mgr.sync_mirror("http-repo")

        assert result.success is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "wget"

    @patch("tftpos.repo_mirror.subprocess.run")
    def test_sync_updates_last_sync(self, mock_run, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="sync-time-test",
                source_url="https://mirror.example.com/repo/",
                local_path=str(tmp_path / "mirrors" / "sync-time-test"),
            )
        )
        assert mgr.get_mirror("sync-time-test").last_sync is None

        mgr.sync_mirror("sync-time-test")

        m = mgr.get_mirror("sync-time-test")
        assert m.last_sync is not None
        assert m.last_sync > 0

    def test_sync_nonexistent_raises(self, tmp_path):
        mgr = RepoManager(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            mgr.sync_mirror("nonexistent")

    @patch(
        "tftpos.repo_mirror.subprocess.run",
        side_effect=OSError("wget not found"),
    )
    def test_sync_failure_returns_error(self, mock_run, tmp_path):
        mgr = RepoManager(tmp_path)
        mgr.add_mirror(
            RepoMirror(
                name="fail-test",
                source_url="https://mirror.example.com/repo/",
                local_path=str(tmp_path / "mirrors" / "fail-test"),
            )
        )
        result = mgr.sync_mirror("fail-test")

        assert result.success is False
        assert "wget not found" in result.error
