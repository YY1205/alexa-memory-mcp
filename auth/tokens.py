"""Access and refresh tokens: opaque random strings, stored only as SHA-256 hashes.

The store is a small JSON file (default .oauth/tokens.json, gitignored, mode 0600),
so tokens survive a server restart. Refresh tokens rotate on every use; presenting a
refresh token that was already used revokes its whole grant family (theft signal).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ACCESS_PREFIX, REFRESH_PREFIX = "pm_at_", "pm_rt_"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Grant:
    kind: str            # "access" | "refresh"
    client_id: str
    scopes: list[str]
    resource: str        # audience: the canonical MCP server URI
    expires_at: float
    family: str          # all tokens descending from one authorization
    used: bool = False   # refresh tokens: already rotated

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at


class TokenStore:
    def __init__(self, path: str | Path | None, access_ttl: int = 900, refresh_ttl: int = 30 * 86400):
        self.path = Path(path) if path else None
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self._lock = threading.Lock()
        self._grants: dict[str, Grant] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self._grants = {h: Grant(**g) for h, g in data.get("grants", {}).items()}

    # ---------- persistence ----------
    def _save(self) -> None:
        now = time.time()
        self._grants = {h: g for h, g in self._grants.items() if not g.expired(now)}
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"grants": {h: asdict(g) for h, g in self._grants.items()}}, f)
        os.replace(tmp, self.path)

    # ---------- issuing ----------
    def issue(self, client_id: str, scopes: list[str], resource: str, family: str | None = None) -> dict:
        family = family or secrets.token_hex(8)
        access = ACCESS_PREFIX + secrets.token_urlsafe(32)
        refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._grants[token_hash(access)] = Grant("access", client_id, scopes, resource,
                                                     now + self.access_ttl, family)
            self._grants[token_hash(refresh)] = Grant("refresh", client_id, scopes, resource,
                                                      now + self.refresh_ttl, family)
            self._save()
        return {"access_token": access, "token_type": "Bearer", "expires_in": self.access_ttl,
                "refresh_token": refresh, "scope": " ".join(scopes)}

    # ---------- checking ----------
    def verify_access(self, token: str, resource: str) -> Grant | None:
        if not token.startswith(ACCESS_PREFIX):
            return None
        grant = self._grants.get(token_hash(token))
        if not grant or grant.kind != "access" or grant.expired() or grant.resource != resource:
            return None
        return grant

    def use_refresh(self, token: str, client_id: str) -> Grant | None:
        """Consume a refresh token for rotation. Returns its grant, or None if invalid.

        A second use of the same refresh token revokes the whole family.
        """
        with self._lock:
            h = token_hash(token)
            grant = self._grants.get(h)
            if not grant or grant.kind != "refresh" or grant.client_id != client_id or grant.expired():
                return None
            if grant.used:
                self._revoke_family(grant.family)
                self._save()
                return None
            grant.used = True  # kept until it expires, to detect reuse
            self._save()
            return grant

    def _revoke_family(self, family: str) -> None:
        self._grants = {h: g for h, g in self._grants.items() if g.family != family}

    def revoke_all(self) -> None:
        with self._lock:
            self._grants.clear()
            self._save()
