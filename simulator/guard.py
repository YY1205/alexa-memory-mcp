"""Protection for a public simulator: judge passcode gate, rate limits, daily model cap.

All off unless configured (SIM_PASSCODE ...), so local demos and tests stay open.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth.oauth import client_address

COOKIE = "sim_session"
SESSION_TTL = 12 * 3600
OPEN_PATHS = {"/healthz", "/gate"}


class SlidingWindow:
    """At most `limit` events per `window` seconds, per key."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str = "*") -> bool:
        now = time.time()
        with self._lock:
            q = self._events.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


class DailyCap:
    """Counts model calls per UTC day."""

    def __init__(self, limit: int):
        self.limit = limit
        self._day, self._count = "", 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= self.limit:
                return False
            self._count += 1
            return True

    @property
    def used(self) -> int:
        return self._count if self._day == datetime.now(timezone.utc).date().isoformat() else 0


@dataclass
class GuardSettings:
    passcode: str | None = None
    session_secret: str | None = None
    secure_cookie: bool = False
    trust_proxy: bool = False
    per_ip_per_minute: int = 10
    per_ip_per_day: int = 120
    global_per_minute: int = 60
    daily_model_calls: int = 300
    privacy_url: str | None = None


@dataclass
class Guard:
    s: GuardSettings
    ip_minute: SlidingWindow = field(init=False)
    ip_day: SlidingWindow = field(init=False)
    global_minute: SlidingWindow = field(init=False)
    gate_fails: SlidingWindow = field(init=False)
    gate_fails_global: SlidingWindow = field(init=False)
    model_cap: DailyCap = field(init=False)

    def __post_init__(self):
        self.ip_minute = SlidingWindow(self.s.per_ip_per_minute, 60)
        self.ip_day = SlidingWindow(self.s.per_ip_per_day, 86400)
        self.global_minute = SlidingWindow(self.s.global_per_minute, 60)
        self.gate_fails = SlidingWindow(10, 900)
        self.gate_fails_global = SlidingWindow(50, 3600)
        self.model_cap = DailyCap(self.s.daily_model_calls)

    @property
    def gated(self) -> bool:
        return bool(self.s.passcode)

    def client(self, request: Request) -> str:
        return client_address(request, self.s.trust_proxy)

    # ---------- session cookie ----------
    def _sign(self, payload: bytes) -> str:
        return hmac.new((self.s.session_secret or "").encode(), payload, hashlib.sha256).hexdigest()

    def make_cookie(self) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + SESSION_TTL}).encode())
        return f"{payload.decode()}.{self._sign(payload)}"

    def valid_cookie(self, value: str | None) -> bool:
        if not value or "." not in value:
            return False
        payload, sig = value.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(payload.encode())):
            return False
        try:
            return json.loads(base64.urlsafe_b64decode(payload))["exp"] > time.time()
        except (ValueError, KeyError):
            return False

    # ---------- rate limits ----------
    def allow_utterance(self, request: Request) -> str | None:
        """None if allowed, else a polite message. Only enforced in public (gated) mode."""
        if not self.gated:
            return None
        ip = self.client(request)
        if not self.global_minute.allow():
            return "The demo is busy right now. Please try again in a minute."
        if not self.ip_minute.allow(ip):
            return "You're going a bit fast. Please wait a moment and try again."
        if not self.ip_day.allow(ip):
            return "You've reached today's limit for this demo. Please come back tomorrow."
        return None

    # ---------- passcode gate (ASGI middleware) ----------
    def middleware(self, app):
        guard = self

        async def asgi(scope, receive, send):
            if scope["type"] != "http" or not guard.gated or scope.get("path") in OPEN_PATHS:
                await app(scope, receive, send)
                return
            request = Request(scope)
            if guard.valid_cookie(request.cookies.get(COOKIE)):
                await app(scope, receive, send)
                return
            if scope["path"].startswith("/api/"):
                response = JSONResponse({"error": "Enter the demo passcode first.", "gate": True}, status_code=401)
            else:
                response = RedirectResponse("/gate", status_code=303)
            await response(scope, receive, send)

        return asgi

    async def gate(self, request: Request):
        if request.method == "GET":
            return _gate_page(privacy_url=self.s.privacy_url)
        ip = self.client(request)
        body = parse_qs((await request.body()).decode())
        given = (body.get("passcode") or [""])[0]
        if not self.gate_fails.allow(ip) or not self.gate_fails_global.allow():
            return _gate_page("Too many attempts. Try again later.", status=429, privacy_url=self.s.privacy_url)
        if not hmac.compare_digest(given.encode(), (self.s.passcode or "").encode()):
            return _gate_page("That passcode is not right.", status=401, privacy_url=self.s.privacy_url)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, self.make_cookie(), max_age=SESSION_TTL, httponly=True,
                            secure=self.s.secure_cookie, samesite="lax", path="/")
        return response


def _gate_page(error: str | None = None, status: int = 200, privacy_url: str | None = None) -> HTMLResponse:
    err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    privacy = (f'<p class="muted"><a href="{html.escape(privacy_url)}">Privacy policy</a></p>'
               if privacy_url and privacy_url.startswith("https://") else "")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Simulated Alexa+</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:420px;margin:64px auto;padding:0 16px;background:#f3f4f7;color:#16181d}}
form{{background:#fff;border:1px solid #dde1e8;border-radius:12px;padding:16px}}
input{{width:100%;box-sizing:border-box;padding:8px;font:inherit;border:1px solid #c9ced8;border-radius:8px}}
button{{margin-top:12px;font:inherit;padding:8px 16px;border-radius:8px;border:1px solid #0b7fd6;background:#0b7fd6;color:#fff}}
.err{{color:#c62828}} p.muted{{color:#5d6472;font-size:.9rem}}
@media (prefers-color-scheme: dark){{body{{background:#0f1115;color:#e8ebf0}} form{{background:#171a20;border-color:#2a2f38}} input{{background:#11141a;color:#e8ebf0;border-color:#2a2f38}}}}
</style></head><body>
<h1>Simulated Alexa+</h1>
<p class="muted">Hackathon demo of Portable Memory. Judges: the passcode is in the testing instructions.</p>
<form method="post" action="/gate">
  <label for="pc">Demo passcode</label>
  <input id="pc" name="passcode" type="password" autocomplete="off" autofocus>
  {err}
  <button>Enter</button>
</form>
{privacy}
</body></html>"""
    return HTMLResponse(page, status_code=status, headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"})
