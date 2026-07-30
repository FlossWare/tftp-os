"""API key authentication and role-based access control."""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import logging
import os
import secrets as stdlib_secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastapi import Depends, HTTPException, Request
    from fastapi.security import (
        HTTPAuthorizationCredentials,
        HTTPBearer,
    )
except ImportError:
    Depends = HTTPException = Request = None
    HTTPAuthorizationCredentials = HTTPBearer = None

logger = logging.getLogger("tftpos.auth")

_FILE_PERMS = 0o600
_DIR_PERMS = 0o700


class Role(enum.Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


_ROLE_LEVEL: Dict[Role, int] = {
    Role.VIEWER: 1,
    Role.OPERATOR: 2,
    Role.ADMIN: 3,
}


def role_has_access(user_role: Role, required_role: Role) -> bool:
    return _ROLE_LEVEL[user_role] >= _ROLE_LEVEL[required_role]


@dataclass
class ApiKey:
    key_hash: str
    name: str
    role: Role
    created_at: float = field(default_factory=time.time)
    last_used_at: Optional[float] = None
    enabled: bool = True


class ApiKeyStore:
    """File-based API key storage with restricted permissions."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._keys_path = self._data_dir / "auth_keys.json"

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def create_key(
        self, name: str, role: Role
    ) -> tuple[str, ApiKey]:
        raw_key = f"tftpos_{stdlib_secrets.token_urlsafe(32)}"
        key_hash = self.hash_key(raw_key)
        api_key = ApiKey(
            key_hash=key_hash, name=name, role=role
        )
        keys = self._read()
        keys[key_hash] = api_key
        self._write(keys)
        return raw_key, api_key

    def validate(self, raw_key: str) -> Optional[ApiKey]:
        key_hash = self.hash_key(raw_key)
        keys = self._read()
        # Use timing-safe comparison to prevent timing attacks.
        # We iterate all stored hashes so the time is constant
        # regardless of whether a match is found.
        matched_key: Optional[ApiKey] = None
        for stored_hash, api_key in keys.items():
            if hmac.compare_digest(key_hash, stored_hash):
                matched_key = api_key
        if matched_key is None or not matched_key.enabled:
            return None
        matched_key.last_used_at = time.time()
        self._write(keys)
        return matched_key

    def list_keys(self) -> List[ApiKey]:
        return list(self._read().values())

    def revoke(self, name: str) -> bool:
        keys = self._read()
        for api_key in keys.values():
            if api_key.name == name:
                api_key.enabled = False
                self._write(keys)
                return True
        return False

    def delete(self, name: str) -> bool:
        keys = self._read()
        to_remove = [
            h for h, k in keys.items() if k.name == name
        ]
        if not to_remove:
            return False
        for h in to_remove:
            del keys[h]
        self._write(keys)
        return True

    def is_empty(self) -> bool:
        return len(self._read()) == 0

    def _read(self) -> Dict[str, ApiKey]:
        if not self._keys_path.exists():
            return {}
        with open(self._keys_path, "r") as fh:
            data = json.load(fh)
        result: Dict[str, ApiKey] = {}
        for key_hash, entry in data.items():
            result[key_hash] = ApiKey(
                key_hash=key_hash,
                name=entry["name"],
                role=Role(entry["role"]),
                created_at=entry.get("created_at", 0),
                last_used_at=entry.get("last_used_at"),
                enabled=entry.get("enabled", True),
            )
        return result

    def _write(self, keys: Dict[str, ApiKey]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(str(self._data_dir), _DIR_PERMS)
        data: Dict[str, Any] = {}
        for key_hash, api_key in keys.items():
            data[key_hash] = {
                "name": api_key.name,
                "role": api_key.role.value,
                "created_at": api_key.created_at,
                "last_used_at": api_key.last_used_at,
                "enabled": api_key.enabled,
            }
        with open(self._keys_path, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.chmod(str(self._keys_path), _FILE_PERMS)


_auth_enabled: bool = False
_key_store: Optional[ApiKeyStore] = None

_bearer_scheme = HTTPBearer(auto_error=False) if HTTPBearer is not None else None


def init_auth(
    enabled: bool, key_store: ApiKeyStore
) -> None:
    global _auth_enabled, _key_store
    _auth_enabled = enabled
    _key_store = key_store


def get_key_store() -> Optional[ApiKeyStore]:
    return _key_store


def is_auth_enabled() -> bool:
    return _auth_enabled


def require_role(min_role: Role):
    async def _dependency(
        credentials: Optional[
            HTTPAuthorizationCredentials
        ] = Depends(_bearer_scheme),
    ) -> Optional[ApiKey]:
        from tftpos.metrics import auth_attempts_total

        if not _auth_enabled or _key_store is None:
            return None

        if credentials is None:
            logger.warning(
                "Auth failure: no credentials provided "
                "for role=%s",
                min_role.value,
            )
            auth_attempts_total.inc(result="failure")
            raise HTTPException(
                status_code=401,
                detail="API key required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        api_key = _key_store.validate(credentials.credentials)
        if api_key is None:
            logger.warning(
                "Auth failure: invalid or disabled key "
                "for role=%s",
                min_role.value,
            )
            auth_attempts_total.inc(result="failure")
            raise HTTPException(
                status_code=401,
                detail="Invalid or disabled API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not role_has_access(api_key.role, min_role):
            logger.warning(
                "Auth failure: insufficient role "
                "name=%s role=%s required=%s",
                api_key.name, api_key.role.value,
                min_role.value,
            )
            auth_attempts_total.inc(result="failure")
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Requires {min_role.value} role "
                    f"or higher"
                ),
            )

        logger.debug(
            "Auth success name=%s role=%s",
            api_key.name, api_key.role.value,
        )
        auth_attempts_total.inc(result="success")
        return api_key

    return _dependency
