"""Secrets management for TftpOS autoinstall configurations.

Provides pluggable secrets backends and a manager that resolves
``{{secret:KEY_NAME}}`` references in ProvisionProfile fields.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

from tftpos.models import BootFirmware, ProvisionProfile

# Regex that matches  {{secret:SOME_KEY}}
_SECRET_REF_RE = re.compile(r"\{\{secret:([A-Za-z0-9_]+)\}\}")

# File permissions
_FILE_PERMS = 0o600  # owner read/write only
_DIR_PERMS = 0o700   # owner rwx only


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------


class SecretsProvider(ABC):
    """Abstract base for secrets backends."""

    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        """Return the secret value for *key*, or ``None`` if not found."""

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        """Store *value* under *key*."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the secret identified by *key*.

        Silently succeeds if *key* does not exist.
        """

    @abstractmethod
    def list_keys(self) -> List[str]:
        """Return a sorted list of all stored secret key names."""


# ---------------------------------------------------------------------------
# File-based provider
# ---------------------------------------------------------------------------


class FileSecretsProvider(SecretsProvider):
    """File-based secrets storage with restricted permissions.

    Secrets are persisted in ``<data_dir>/secrets.json`` as a flat
    JSON object mapping key names to string values.  The directory is
    created with mode 0o700 and the file with mode 0o600 so that only
    the owning user can access the data.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._secrets_path = self._data_dir / "secrets.json"

    # -- public interface ---------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        data = self._read()
        return data.get(key)

    def set(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def delete(self, key: str) -> None:
        data = self._read()
        if key in data:
            del data[key]
            self._write(data)

    def list_keys(self) -> List[str]:
        return sorted(self._read().keys())

    # -- internal helpers ---------------------------------------------------

    def _read(self) -> Dict[str, str]:
        if not self._secrets_path.exists():
            return {}
        with open(self._secrets_path, "r") as fh:
            return json.load(fh)

    def _write(self, data: Dict[str, str]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(str(self._data_dir), _DIR_PERMS)

        with open(self._secrets_path, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)

        os.chmod(str(self._secrets_path), _FILE_PERMS)


# ---------------------------------------------------------------------------
# Environment-variable provider
# ---------------------------------------------------------------------------

_ENV_PREFIX = "TFTPOS_SECRET_"


class EnvironmentSecretsProvider(SecretsProvider):
    """Environment variable secrets with ``TFTPOS_SECRET_`` prefix.

    Secrets are stored as environment variables whose names are formed
    by prepending ``TFTPOS_SECRET_`` to the upper-cased key.  For
    example, key ``root_password`` maps to the environment variable
    ``TFTPOS_SECRET_ROOT_PASSWORD``.
    """

    _lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        env_key = _ENV_PREFIX + key.upper()
        with self._lock:
            return os.environ.get(env_key)

    def set(self, key: str, value: str) -> None:
        env_key = _ENV_PREFIX + key.upper()
        with self._lock:
            os.environ[env_key] = value

    def delete(self, key: str) -> None:
        env_key = _ENV_PREFIX + key.upper()
        with self._lock:
            os.environ.pop(env_key, None)

    def list_keys(self) -> List[str]:
        with self._lock:
            keys: List[str] = []
            for name in os.environ:
                if name.startswith(_ENV_PREFIX):
                    keys.append(name[len(_ENV_PREFIX):])
            return sorted(keys)


# ---------------------------------------------------------------------------
# Secrets manager (resolves references inside profiles)
# ---------------------------------------------------------------------------


class SecretsManager:
    """Resolves ``{{secret:KEY}}`` references in :class:`ProvisionProfile` fields.

    The manager walks every string-valued field of the profile (including
    nested values inside the ``extra`` dict) and replaces any occurrence
    of ``{{secret:KEY_NAME}}`` with the corresponding value from the
    configured :class:`SecretsProvider`.

    Raises :class:`ValueError` if a referenced secret cannot be found.
    """

    def __init__(self, provider: SecretsProvider) -> None:
        self._provider = provider

    @property
    def provider(self) -> SecretsProvider:
        return self._provider

    def resolve_profile(
        self, profile: ProvisionProfile
    ) -> ProvisionProfile:
        """Return a *new* profile with all secret references resolved."""
        data = asdict(profile)

        # asdict() preserves the BootFirmware enum object; pop it
        # so we can pass it directly to the constructor.
        firmware_raw = data.pop("firmware")

        resolved: Dict[str, Any] = {}
        for key, value in data.items():
            resolved[key] = self._resolve_value(value, key)

        # Reconstruct BootFirmware enum -- handle both the enum
        # instance (Python dataclasses.asdict behaviour) and a
        # plain string (defensive).
        if isinstance(firmware_raw, BootFirmware):
            firmware = firmware_raw
        else:
            firmware_str = str(firmware_raw).lower()
            firmware = (
                BootFirmware.UEFI
                if firmware_str == "uefi"
                else BootFirmware.BIOS
            )

        return ProvisionProfile(firmware=firmware, **resolved)

    # -- recursive resolver -------------------------------------------------

    def _resolve_value(self, value: Any, path: str = "") -> Any:
        """Recursively resolve secret references in *value*."""
        if isinstance(value, str):
            return self._resolve_string(value, path)
        if isinstance(value, dict):
            return {
                k: self._resolve_value(v, f"{path}.{k}")
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [
                self._resolve_value(item, f"{path}[{i}]")
                for i, item in enumerate(value)
            ]
        return value

    def _resolve_string(self, text: str, path: str = "") -> str:
        """Replace all ``{{secret:KEY}}`` tokens in *text*."""

        def _replacer(match: re.Match) -> str:
            secret_key = match.group(1)
            secret_val = self._provider.get(secret_key)
            if secret_val is None:
                raise ValueError(
                    f"secret {secret_key!r} referenced in "
                    f"{path!r} not found"
                )
            return secret_val

        return _SECRET_REF_RE.sub(_replacer, text)
