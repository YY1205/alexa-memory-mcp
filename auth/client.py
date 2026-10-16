"""OAuth 2.1 client helpers, used the way Alexa account linking uses them: discover
the server from its protected resource metadata, auth code + PKCE (S256) with the
resource parameter, then token exchange and refresh with client_secret_basic.

Shared by the simulator, scripts/oauth_token.py and the tests.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx


@dataclass
class ServerEndpoints:
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: list[str]


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str

    @classmethod
    def from_response(cls, body: dict) -> Tokens:
        return cls(body["access_token"], body["refresh_token"], time.time() + int(body["expires_in"]),
                   body.get("scope", ""))


class OAuthError(Exception):
    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(f"{error}: {description}" if description else error)
        self.error, self.description, self.status = error, description, status


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def discover(mcp_url: str, http: httpx.Client | None = None) -> ServerEndpoints:
    """RFC 9728 then RFC 8414 discovery, starting from the MCP server URL."""
    http = http or httpx.Client(timeout=10)
    parts = urlsplit(mcp_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    prm = http.get(f"{origin}/.well-known/oauth-protected-resource{parts.path.rstrip('/')}")
    if prm.status_code == 404:
        prm = http.get(f"{origin}/.well-known/oauth-protected-resource")
    prm.raise_for_status()
    prm_body = prm.json()
    issuer = prm_body["authorization_servers"][0]
    asm = http.get(urljoin(issuer.rstrip("/") + "/", ".well-known/oauth-authorization-server"))
    asm.raise_for_status()
    meta = asm.json()
    if "S256" not in meta.get("code_challenge_methods_supported", []):
        raise OAuthError("unsupported_server", "The server does not support PKCE S256.")
    return ServerEndpoints(prm_body["resource"], meta["issuer"], meta["authorization_endpoint"],
                           meta["token_endpoint"], prm_body.get("scopes_supported", []))


def authorize_url(ep: ServerEndpoints, client_id: str, redirect_uri: str, state: str, challenge: str,
                  scopes: list[str] | None = None) -> str:
    return ep.authorization_endpoint + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": ep.resource,
        "scope": " ".join(scopes or ep.scopes),
    })


def _token_request(ep: ServerEndpoints, client_id: str, client_secret: str, form: dict,
                   http: httpx.Client | None = None) -> Tokens:
    http = http or httpx.Client(timeout=10)
    r = http.post(ep.token_endpoint, data=form, auth=(client_id, client_secret))
    if r.status_code != 200:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        raise OAuthError(body.get("error", "token_error"), body.get("error_description", ""), r.status_code)
    return Tokens.from_response(r.json())


def exchange_code(ep: ServerEndpoints, client_id: str, client_secret: str, code: str, redirect_uri: str,
                  verifier: str, http: httpx.Client | None = None) -> Tokens:
    return _token_request(ep, client_id, client_secret, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "code_verifier": verifier, "resource": ep.resource}, http)


def refresh(ep: ServerEndpoints, client_id: str, client_secret: str, refresh_token: str,
            http: httpx.Client | None = None) -> Tokens:
    return _token_request(ep, client_id, client_secret, {
        "grant_type": "refresh_token", "refresh_token": refresh_token, "resource": ep.resource}, http)


def owner_approves(http: httpx.Client, url: str, passphrase: str, scopes: list[str] | None = None) -> dict:
    """Play the owner on the sign-in page (tests and dev scripts only): open the
    authorize URL, submit the passphrase, and return the redirect's query params."""
    page = http.get(url)
    if page.status_code != 200:
        if page.status_code in (302, 303):
            return {k: v[0] for k, v in parse_qs(urlsplit(page.headers["location"]).query).items()}
        raise OAuthError("authorize_failed", f"sign-in page returned {page.status_code}", page.status_code)
    nonce = re.search(r'name="nonce" value="([^"]+)"', page.text).group(1)
    offered = re.findall(r'name="scope_([^"]+)"', page.text)
    form = {"nonce": nonce, "passphrase": passphrase, "action": "approve"}
    form.update({f"scope_{s}": "on" for s in offered if scopes is None or s in scopes})
    r = http.post(urljoin(url, "/authorize"), data=form)
    if r.status_code not in (302, 303):
        raise OAuthError("authorize_failed", f"sign-in returned {r.status_code}", r.status_code)
    return {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
