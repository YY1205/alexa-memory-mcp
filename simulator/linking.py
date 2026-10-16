"""Account linking, done the way Alexa does it: the simulator is an OAuth client.

"Link account" sends the browser to the memory server's sign-in page (auth code +
PKCE S256 + resource parameter); the callback comes back here and the simulator
exchanges the code with its client secret. Tokens stay in this process, never in
the browser, and are refreshed automatically (refresh tokens rotate).
"""
from __future__ import annotations

import logging
import secrets
import threading
import time

import anyio
import httpx

from auth import client as oc

log = logging.getLogger(__name__)
PENDING_TTL = 600     # seconds a started link may take
REFRESH_MARGIN = 60   # refresh when the access token has less than this left


class AccountLink:
    def __init__(self, mcp_url: str, client_id: str | None, client_secret: str | None, redirect_uri: str):
        self.mcp_url = mcp_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._endpoints: oc.ServerEndpoints | None = None
        self._pending: dict[str, tuple[str, float]] = {}   # state -> (code_verifier, started)
        self._tokens: oc.Tokens | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def linked(self) -> bool:
        return self._tokens is not None

    def _http(self) -> httpx.Client:
        return httpx.Client(timeout=10)

    def endpoints(self) -> oc.ServerEndpoints:
        if self._endpoints is None:
            with self._http() as http:
                self._endpoints = oc.discover(self.mcp_url, http)
        return self._endpoints

    # ---------- linking ----------
    def start(self) -> str:
        """Begin linking; returns the server's authorize URL for the browser."""
        if not self.configured:
            raise oc.OAuthError("not_configured", "OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET are not set.")
        verifier, challenge = oc.pkce_pair()
        state = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._pending = {s: v for s, v in self._pending.items() if now - v[1] < PENDING_TTL}
            self._pending[state] = (verifier, now)
        return oc.authorize_url(self.endpoints(), self.client_id, self.redirect_uri, state, challenge)

    def finish(self, params: dict) -> None:
        """Handle the redirect back from the server (code or error)."""
        with self._lock:
            pending = self._pending.pop(params.get("state", ""), None)
        if not pending or time.time() - pending[1] > PENDING_TTL:
            raise oc.OAuthError("invalid_state", "This link attempt is unknown or expired. Try again.")
        if params.get("error"):
            raise oc.OAuthError(params["error"], params.get("error_description", ""))
        ep = self.endpoints()
        if params.get("iss") and params["iss"] != ep.issuer:  # RFC 9207 mix-up defence
            raise oc.OAuthError("invalid_issuer", "The response came from an unexpected server.")
        with self._http() as http:
            tokens = oc.exchange_code(ep, self.client_id, self.client_secret, params.get("code", ""),
                                      self.redirect_uri, pending[0], http)
        with self._lock:
            self._tokens = tokens

    def unlink(self) -> None:
        with self._lock:
            self._tokens = None

    # ---------- using ----------
    def _fresh_token(self) -> str | None:
        with self._lock:
            tokens = self._tokens
            if tokens is None:
                return None
            if tokens.expires_at - time.time() > REFRESH_MARGIN:
                return tokens.access_token
            try:
                with self._http() as http:
                    self._tokens = oc.refresh(self.endpoints(), self.client_id, self.client_secret,
                                              tokens.refresh_token, http)
                log.info("Access token refreshed")
                return self._tokens.access_token
            except oc.OAuthError as e:
                log.warning("Refresh failed (%s); account unlinked", e.error)
                self._tokens = None
                return None

    async def access_token(self) -> str | None:
        return await anyio.to_thread.run_sync(self._fresh_token)

    def status(self) -> dict:
        tokens = self._tokens
        if not tokens:
            return {"linked": False, "configured": self.configured}
        return {"linked": True, "configured": True, "scopes": tokens.scope.split(),
                "expires_in": max(0, int(tokens.expires_at - time.time()))}
