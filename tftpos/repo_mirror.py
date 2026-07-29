"""Repository mirroring for offline provisioning.

Provides a RepoMirror data model and RepoManager class that can
mirror or proxy package repositories for reliable offline
provisioning -- a common Cobbler use case.

Mirror configurations are persisted in ``data_dir/mirrors.json``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("tftpos.repo_mirror")

# Characters allowed in mirror names -- prevent path traversal.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")

# Allowed URL schemes for mirror source.
_ALLOWED_SCHEMES = {"http", "https", "ftp", "rsync"}


def _validate_mirror_name(name: str) -> None:
    """Raise ``ValueError`` if *name* is not a safe mirror name."""
    if not name or not _NAME_RE.match(name):
        raise ValueError(
            f"invalid mirror name: {name!r} "
            "(must start with alphanumeric, 1-128 chars, "
            "only alphanumeric/dot/hyphen/underscore)"
        )
    if ".." in name:
        raise ValueError(
            f"path traversal detected in mirror name: {name!r}"
        )


def _validate_source_url(url: str) -> None:
    """Raise ``ValueError`` if *url* has an unsupported scheme."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValueError(f"invalid source URL: {url!r}") from exc
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"unsupported URL scheme {parsed.scheme!r} "
            f"in {url!r}; allowed: {', '.join(sorted(_ALLOWED_SCHEMES))}"
        )
    if not parsed.netloc:
        raise ValueError(
            f"source URL must include a host: {url!r}"
        )


@dataclass
class RepoMirror:
    """A single mirrored repository."""

    name: str
    source_url: str
    local_path: str
    sync_interval: int = 86400  # seconds (default: 24 hours)
    last_sync: Optional[float] = None


@dataclass
class SyncResult:
    """Result of a mirror sync operation."""

    mirror_name: str
    success: bool
    started_at: float
    finished_at: float
    error: str = ""


class RepoManager:
    """Manage mirrored package repositories.

    Mirror metadata is persisted in ``data_dir/mirrors.json``.
    Actual repository content lives under ``data_dir/mirrors/<name>/``.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._config_path = data_dir / "mirrors.json"
        self._mirrors_root = data_dir / "mirrors"
        self._mirrors: Dict[str, RepoMirror] = {}
        self._load()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_mirror(self, mirror: RepoMirror) -> RepoMirror:
        """Register a new mirror.  Raises ``ValueError`` on duplicates."""
        _validate_mirror_name(mirror.name)
        _validate_source_url(mirror.source_url)
        if mirror.name in self._mirrors:
            raise ValueError(
                f"mirror {mirror.name!r} already exists"
            )
        # Resolve local_path relative to mirrors root when not
        # absolute, and ensure it stays under mirrors_root.
        local = Path(mirror.local_path)
        if not local.is_absolute():
            local = self._mirrors_root / mirror.name
        resolved = local.resolve()
        mirrors_root_resolved = self._mirrors_root.resolve()
        # Use trailing separator to prevent prefix-matching
        # attacks (e.g. /srv/mirrorsevil vs /srv/mirrors).
        if not (
            str(resolved).startswith(
                str(mirrors_root_resolved) + "/"
            )
            or resolved == mirrors_root_resolved
        ):
            # Allow absolute paths that don't escape the mirror
            # root only when explicitly set; otherwise default.
            local = self._mirrors_root / mirror.name
        mirror.local_path = str(local)
        self._mirrors[mirror.name] = mirror
        self._save()
        return mirror

    def remove_mirror(self, name: str) -> bool:
        """Remove a mirror by name.  Returns True if found."""
        _validate_mirror_name(name)
        if name not in self._mirrors:
            return False
        mirror = self._mirrors.pop(name)
        self._save()
        # Remove mirror data directory if it exists.
        local = Path(mirror.local_path)
        if local.exists() and local.is_dir():
            shutil.rmtree(local, ignore_errors=True)
        return True

    def get_mirror(self, name: str) -> Optional[RepoMirror]:
        """Return a mirror by name, or ``None``."""
        return self._mirrors.get(name)

    def list_mirrors(self) -> List[RepoMirror]:
        """Return all mirrors sorted by name."""
        return sorted(
            self._mirrors.values(), key=lambda m: m.name
        )

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync_mirror(self, name: str) -> SyncResult:
        """Synchronise a mirror from its source URL.

        Uses ``rsync`` when available and the source uses the rsync://
        scheme; falls back to ``wget --mirror`` for HTTP(S)/FTP sources.
        """
        _validate_mirror_name(name)
        mirror = self._mirrors.get(name)
        if mirror is None:
            raise ValueError(f"mirror {name!r} not found")

        started = time.time()
        dest = Path(mirror.local_path)
        dest.mkdir(parents=True, exist_ok=True)

        try:
            if mirror.source_url.startswith("rsync://"):
                self._sync_rsync(mirror.source_url, dest)
            else:
                self._sync_wget(mirror.source_url, dest)
            mirror.last_sync = time.time()
            self._save()
            return SyncResult(
                mirror_name=name,
                success=True,
                started_at=started,
                finished_at=time.time(),
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.error(
                "Mirror sync failed for %s: %s", name, exc
            )
            return SyncResult(
                mirror_name=name,
                success=False,
                started_at=started,
                finished_at=time.time(),
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_rsync(self, url: str, dest: Path) -> None:
        """Sync via rsync."""
        cmd = [
            "rsync", "-avz", "--delete",
            url.rstrip("/") + "/",
            str(dest) + "/",
        ]
        logger.info("rsync: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True)

    def _sync_wget(self, url: str, dest: Path) -> None:
        """Sync via wget --mirror."""
        cmd = [
            "wget", "--mirror", "--no-parent",
            "--no-host-directories",
            "--directory-prefix", str(dest),
            "--cut-dirs=99",
            url.rstrip("/") + "/",
        ]
        logger.info("wget: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True)

    def _load(self) -> None:
        """Load mirror configs from JSON."""
        if not self._config_path.exists():
            self._mirrors = {}
            return
        try:
            data = json.loads(self._config_path.read_text())
            self._mirrors = {
                m["name"]: RepoMirror(**m) for m in data
            }
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                "Failed to load mirrors config: %s", exc
            )
            self._mirrors = {}

    def _save(self) -> None:
        """Persist mirror configs to JSON."""
        self._config_path.parent.mkdir(
            parents=True, exist_ok=True
        )
        data = [asdict(m) for m in self._mirrors.values()]
        self._config_path.write_text(
            json.dumps(data, indent=2) + "\n"
        )
