"""OAuth 2.1 authorization server (single owner, one pre-registered client) and the
resource-server check for /mcp, following the Alexa+ MCP authentication checklist:

* authorization code + PKCE (S256 only), `state` and RFC 8707 `resource` required;
* no dynamic client registration, CIMD, OIDC or step-up: one client from env;
* unauthenticated MCP requests get 401 *without* a WWW-Authenticate header;
* RFC 9728 protected resource metadata and RFC 8414 authorization server metadata;
* short-lived access tokens bound to the resource and scopes; rotating refresh tokens.

Why not the MCP SDK's built-in auth: its RequireAuthMiddleware always sends
WWW-Authenticate on 401, its handlers treat `resource` as optional, and its metadata
assumes dynamic registration. Adapting it meant overriding most of it; this module
is the small remainder, using only the standard library and Starlette.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import secrets
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .passphrase import verify_passphrase
from .tokens import TokenStore

READ, WRITE = "memory.read", "memory.write"
SCOPES = (READ, WRITE)
CODE_TTL = 60          # seconds an authorization code stays valid
CONSENT_TTL = 600      # seconds the sign-in page stays valid
MAX_FAILS_PER_CLIENT, MAX_FAILS_GLOBAL = 5, 30  # wrong passphrases per 15 min / per hour
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Scopes of the current MCP request; None = no auth layer (local dev without OAuth).
granted_scopes: ContextVar[frozenset[str] | None] = ContextVar("granted_scopes", default=None)


class ScopeError(PermissionError):
    pass


def require_scope(scope: str) -> None:
    """Raise ScopeError unless the current request's token carries `scope`."""
    scopes = granted_scopes.get()
    if scopes is not None and scope not in scopes:
        raise ScopeError(scope)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@dataclass
class AuthSettings:
    resource: str                       # canonical MCP URI, e.g. https://memory.example.com/mcp
    issuer: str                         # e.g. https://memory.example.com
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uris: list[str] = field(default_factory=list)
    owner_hash: str | None = None
    token_store: str | None = None
    access_ttl: int = 900
    refresh_ttl: int = 30 * 86400
    dev_token: str | None = None        # localhost development only
    trust_proxy: bool = False

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uris and self.owner_hash)

    @classmethod
    def from_env(cls, host: str, port: int, base_dir: Path) -> AuthSettings:
        local = host in LOCAL_HOSTS
        default_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        resource = os.getenv("MCP_RESOURCE_URI", f"http://{default_host}:{port}/mcp")
        store = os.getenv("OAUTH_TOKEN_STORE", str(base_dir / ".oauth" / "tokens.json"))
        settings = cls(
            resource=resource,
            issuer=os.getenv("OAUTH_ISSUER", _origin(resource)),
            client_id=os.getenv("OAUTH_CLIENT_ID"),
            client_secret=os.getenv("OAUTH_CLIENT_SECRET"),
            redirect_uris=[u.strip() for u in os.getenv("OAUTH_REDIRECT_URIS", "").split(",") if u.strip()],
            owner_hash=os.getenv("OWNER_PASSPHRASE_HASH"),
            token_store=store or None,
            access_ttl=int(os.getenv("OAUTH_ACCESS_TTL", "900")),
            refresh_ttl=int(os.getenv("OAUTH_REFRESH_TTL", str(30 * 86400))),
            dev_token=os.getenv("MCP_BEARER_TOKEN") or None,
            trust_proxy=os.getenv("OAUTH_TRUST_PROXY") == "1",
        )
        if not local:
            if settings.dev_token:
                raise SystemExit("MCP_BEARER_TOKEN is for localhost development only; use OAuth.")
            if not settings.oauth_enabled:
                raise SystemExit("Refusing to listen publicly without OAuth (OAUTH_CLIENT_ID, "
                                 "OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URIS, OWNER_PASSPHRASE_HASH).")
            if not settings.resource.startswith("https://"):
                raise SystemExit("MCP_RESOURCE_URI must be https:// when listening publicly.")
        return settings


class _FailureLimiter:
    """Counts wrong passphrases per client address and globally."""

    def __init__(self):
        self._per_key: dict[str, deque] = {}
        self._global: deque = deque()

    @staticmethod
    def _trim(q: deque, window: float, now: float) -> None:
        while q and q[0] < now - window:
            q.popleft()

    def locked(self, key: str) -> bool:
        now = time.time()
        q = self._per_key.setdefault(key, deque())
        self._trim(q, 900, now)
        self._trim(self._global, 3600, now)
        return len(q) >= MAX_FAILS_PER_CLIENT or len(self._global) >= MAX_FAILS_GLOBAL

    def fail(self, key: str) -> None:
        now = time.time()
        self._per_key.setdefault(key, deque()).append(now)
        self._global.append(now)


def client_address(request: Request, trust_proxy: bool) -> str:
    """The caller's address. Behind one trusted proxy (an ALB) use the RIGHTMOST
    X-Forwarded-For entry: that's the one the proxy appended; entries to its left come
    from the client and can be forged."""
    if trust_proxy and (fwd := request.headers.get("x-forwarded-for")):
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def _json_error(status: int, error: str, description: str) -> JSONResponse:
    # Deliberately no WWW-Authenticate header (Alexa+ checklist). `message` mirrors the
    # 401 example in the Alexa+ account-linking docs; `error_description` is RFC 6749.
    return JSONResponse({"error": error, "error_description": description, "message": description},
                        status_code=status, headers={"Cache-Control": "no-store"})


def _same_resource(given: str | None, canonical: str) -> bool:
    """Alexa sends the manifest URL, which may differ from ours by a trailing slash."""
    return given is not None and given.rstrip("/") == canonical.rstrip("/")


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


class OAuthServer:
    def __init__(self, settings: AuthSettings):
        self.s = settings
        self.store = TokenStore(settings.token_store, settings.access_ttl, settings.refresh_ttl)
        self._codes: dict[str, dict] = {}      # in memory: codes live 60 s
        self._consents: dict[str, dict] = {}   # sign-in page nonces
        self._limiter = _FailureLimiter()

    # ---------------------------------------------------------------- resource server
    def check_bearer(self, authorization: str | None) -> frozenset[str] | None:
        """Scopes granted to the request's bearer token, or None if it isn't valid."""
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        token = authorization[7:].strip()
        if self.s.dev_token and hmac.compare_digest(token.encode(), self.s.dev_token.encode()):
            return frozenset(SCOPES)
        if not self.s.oauth_enabled:
            return None
        grant = self.store.verify_access(token, self.s.resource)
        return frozenset(grant.scopes) if grant else None

    @property
    def enforcing(self) -> bool:
        return self.s.oauth_enabled or bool(self.s.dev_token)

    def middleware(self, app):
        """ASGI middleware protecting /mcp: 401 (no WWW-Authenticate) or scopes in context."""
        oauth = self

        async def asgi(scope, receive, send):
            path = scope.get("path", "")
            if scope["type"] != "http" or not oauth.enforcing or not (path == "/mcp" or path.startswith("/mcp/")):
                await app(scope, receive, send)
                return
            headers = dict(scope.get("headers") or [])
            scopes = oauth.check_bearer(headers.get(b"authorization", b"").decode("latin-1"))
            if scopes is None:
                await _json_error(401, "invalid_token", "A valid access token is required.")(scope, receive, send)
                return
            reset = granted_scopes.set(scopes)
            try:
                await app(scope, receive, send)
            finally:
                granted_scopes.reset(reset)

        return asgi

    # ---------------------------------------------------------------- metadata
    def protected_resource_metadata(self, request: Request) -> JSONResponse:
        return JSONResponse({
            "resource": self.s.resource,
            "authorization_servers": [self.s.issuer],
            "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": "Portable Memory",
        })

    def authorization_server_metadata(self, request: Request) -> JSONResponse:
        return JSONResponse({
            "issuer": self.s.issuer,
            "authorization_endpoint": f"{self.s.issuer}/authorize",
            "token_endpoint": f"{self.s.issuer}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "scopes_supported": list(SCOPES),
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "authorization_response_iss_parameter_supported": True,
        })

    # ---------------------------------------------------------------- /authorize
    def _client_key(self, request: Request) -> str:
        return client_address(request, self.s.trust_proxy)

    def _redirect(self, redirect_uri: str, **params) -> RedirectResponse:
        params = {k: v for k, v in params.items() if v is not None}
        params["iss"] = self.s.issuer
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

    def _validate_authorize(self, p) -> tuple[dict | None, Response | None]:
        client_id, redirect_uri = p.get("client_id"), p.get("redirect_uri")
        if not self.s.oauth_enabled:
            return None, _page("Account linking is not configured on this server.", status=404)
        if client_id != self.s.client_id or redirect_uri not in self.s.redirect_uris:
            # Never redirect to an unverified URI.
            return None, _page("Unknown app or redirect address.", status=400)
        state = p.get("state")

        def fail(error, description):
            return None, self._redirect(redirect_uri, error=error, error_description=description, state=state)

        if p.get("response_type") != "code":
            return fail("unsupported_response_type", "Only response_type=code is supported.")
        if not state:
            return fail("invalid_request", "state is required.")
        challenge = p.get("code_challenge") or ""
        if p.get("code_challenge_method") != "S256" or not (43 <= len(challenge) <= 128):
            return fail("invalid_request", "PKCE with code_challenge_method=S256 is required.")
        if not p.get("resource"):
            return fail("invalid_request", "The resource parameter is required.")
        if not _same_resource(p.get("resource"), self.s.resource):
            return fail("invalid_target", "Unknown resource.")
        scopes = (p.get("scope") or " ".join(SCOPES)).split()
        if not scopes or any(s not in SCOPES for s in scopes):
            return fail("invalid_scope", "Unknown scope.")
        return {"client_id": client_id, "redirect_uri": redirect_uri, "state": state,
                "code_challenge": challenge, "resource": self.s.resource, "scopes": scopes}, None

    def _consent_page(self, request: Request, req: dict, error: str | None = None, status: int = 200):
        nonce = secrets.token_urlsafe(24)
        now = time.time()
        self._consents = {k: v for k, v in self._consents.items() if v["expires"] > now}
        self._consents[nonce] = {**req, "expires": now + CONSENT_TTL}
        return _page(None, status=status, consent=req, nonce=nonce, error=error,
                     form_targets=[_origin(u) for u in self.s.redirect_uris])

    async def authorize(self, request: Request) -> Response:
        if request.method == "GET":
            req, err = self._validate_authorize(request.query_params)
            return err or self._consent_page(request, req)

        form = await request.form()
        req = self._consents.pop(str(form.get("nonce", "")), None)
        if not req or req["expires"] < time.time():
            return _page("This sign-in page has expired. Start linking again from the app.", status=400)
        if form.get("action") != "approve":
            return self._redirect(req["redirect_uri"], error="access_denied",
                                  error_description="The owner declined.", state=req["state"])
        key = self._client_key(request)
        if self._limiter.locked(key):
            return _page("Too many wrong passphrases. Try again later.", status=429)
        if not verify_passphrase(str(form.get("passphrase", "")), self.s.owner_hash or ""):
            self._limiter.fail(key)
            return self._consent_page(request, req, error="That passphrase is not right.", status=401)
        chosen = [s for s in req["scopes"] if form.get(f"scope_{s}") == "on"]
        if not chosen:
            return self._consent_page(request, req, error="Choose at least one permission.", status=400)
        code = secrets.token_urlsafe(32)
        now = time.time()
        self._codes = {k: v for k, v in self._codes.items() if v["expires"] > now}
        self._codes[hashlib.sha256(code.encode()).hexdigest()] = {
            **req, "scopes": chosen, "expires": now + CODE_TTL}
        return self._redirect(req["redirect_uri"], code=code, state=req["state"])

    # ---------------------------------------------------------------- /token
    def _client_from(self, request: Request, form) -> str | None:
        client_id = secret = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                raw = base64.b64decode(auth[6:].strip()).decode()
                client_id, _, secret = raw.partition(":")
                client_id, secret = unquote(client_id), unquote(secret)
            except (ValueError, UnicodeDecodeError):
                return None
        else:
            client_id, secret = form.get("client_id"), form.get("client_secret")
        if (client_id and secret and self.s.client_id and self.s.client_secret
                and hmac.compare_digest(str(client_id).encode(), self.s.client_id.encode())
                and hmac.compare_digest(str(secret).encode(), self.s.client_secret.encode())):
            return str(client_id)
        return None

    async def token(self, request: Request) -> Response:
        if not self.s.oauth_enabled:
            return _json_error(404, "not_found", "OAuth is not configured.")
        form = await request.form()
        client_id = self._client_from(request, form)
        if not client_id:
            return _json_error(401, "invalid_client", "Client authentication failed.")
        grant_type = form.get("grant_type")
        resource = form.get("resource")
        if resource is not None and not _same_resource(resource, self.s.resource):
            return _json_error(400, "invalid_target", "Unknown resource.")

        if grant_type == "authorization_code":
            code = self._codes.pop(hashlib.sha256(str(form.get("code", "")).encode()).hexdigest(), None)
            if not code or code["expires"] < time.time() or code["client_id"] != client_id:
                return _json_error(400, "invalid_grant", "The authorization code is invalid or expired.")
            if form.get("redirect_uri") != code["redirect_uri"]:
                return _json_error(400, "invalid_grant", "redirect_uri does not match.")
            if not resource:
                return _json_error(400, "invalid_request", "The resource parameter is required.")
            verifier = str(form.get("code_verifier") or "")
            if not (43 <= len(verifier) <= 128) or not hmac.compare_digest(_s256(verifier), code["code_challenge"]):
                return _json_error(400, "invalid_grant", "PKCE verification failed.")
            body = self.store.issue(client_id, code["scopes"], code["resource"])

        elif grant_type == "refresh_token":
            grant = self.store.use_refresh(str(form.get("refresh_token", "")), client_id)
            if not grant:
                return _json_error(400, "invalid_grant", "The refresh token is invalid, expired or already used.")
            scopes = grant.scopes
            if form.get("scope"):
                scopes = str(form.get("scope")).split()
                if any(s not in grant.scopes for s in scopes):
                    return _json_error(400, "invalid_scope", "Scope exceeds the original grant.")
            body = self.store.issue(client_id, scopes, grant.resource, family=grant.family)
        else:
            return _json_error(400, "unsupported_grant_type", "Use authorization_code or refresh_token.")
        return JSONResponse(body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    # ---------------------------------------------------------------- routes
    def routes(self) -> list[Route]:
        path = urlsplit(self.s.resource).path.rstrip("/")
        prm = [Route("/.well-known/oauth-protected-resource", self.protected_resource_metadata)]
        if path:  # RFC 9728: metadata URL for a resource with a path
            prm.append(Route(f"/.well-known/oauth-protected-resource{path}", self.protected_resource_metadata))
        return prm + [
            Route("/.well-known/oauth-authorization-server", self.authorization_server_metadata),
            Route("/authorize", self.authorize, methods=["GET", "POST"]),
            Route("/token", self.token, methods=["POST"]),
        ]


# ---------------------------------------------------------------- sign-in page
_SCOPE_TEXT = {READ: "Read your memories", WRITE: "Save and change memories (only after you confirm out loud)"}


def _page(message: str | None, status: int = 200, consent: dict | None = None, nonce: str | None = None,
          error: str | None = None, form_targets: list[str] | None = None) -> HTMLResponse:
    esc = html.escape
    if consent:
        boxes = "".join(
            f'<label><input type="checkbox" name="scope_{esc(s)}" checked> {esc(_SCOPE_TEXT[s])}</label>'
            for s in consent["scopes"])
        body = f"""
<h1>Link your memory</h1>
<p>An app wants access to your Portable Memory.</p>
<form method="post" action="/authorize">
  <input type="hidden" name="nonce" value="{esc(nonce or '')}">
  <fieldset><legend>Allow it to</legend>{boxes}</fieldset>
  <label for="pp">Owner passphrase</label>
  <input id="pp" name="passphrase" type="password" autocomplete="current-password" autofocus>
  {f'<p class="err" role="alert">{esc(error)}</p>' if error else ''}
  <div class="row"><button name="action" value="approve">Allow</button>
  <button name="action" value="deny" class="secondary">Deny</button></div>
</form>"""
    else:
        body = f"<h1>Portable Memory</h1><p>{esc(message or '')}</p>"
    targets = " ".join(sorted(set(form_targets or [])))
    csp = (f"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' {targets}; "
           "frame-ancestors 'none'; base-uri 'none'")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Link your memory</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:420px;margin:48px auto;padding:0 16px;color:#16181d;background:#f3f4f7}}
form,body>p{{background:#fff;border:1px solid #dde1e8;border-radius:12px;padding:16px}}
fieldset{{border:0;padding:0;margin:0 0 12px}} legend{{font-weight:600;margin-bottom:4px}}
label{{display:block;margin:6px 0}} input[type=password]{{width:100%;box-sizing:border-box;padding:8px;font:inherit;border:1px solid #c9ced8;border-radius:8px}}
.row{{display:flex;gap:8px;margin-top:12px}} button{{font:inherit;padding:8px 16px;border-radius:8px;border:1px solid #0b7fd6;background:#0b7fd6;color:#fff;cursor:pointer}}
button.secondary{{background:#fff;color:#16181d;border-color:#c9ced8}} .err{{color:#c62828}}
@media (prefers-color-scheme: dark){{body{{background:#0f1115;color:#e8ebf0}} form,body>p{{background:#171a20;border-color:#2a2f38}} input[type=password]{{background:#11141a;color:#e8ebf0;border-color:#2a2f38}} button.secondary{{background:#171a20;color:#e8ebf0;border-color:#2a2f38}}}}
</style></head><body>{body}</body></html>"""
    return HTMLResponse(page, status_code=status, headers={
        "Content-Security-Policy": csp, "X-Frame-Options": "DENY", "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer"})
