"""Simulated Alexa+ web app (port 8001): a real MCP client of the memory server.

Run:  python -m simulator.app        then open http://127.0.0.1:8001
Env:  MCP_SERVER_URL   memory server endpoint (default http://127.0.0.1:8000/mcp)
      OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET  this simulator's client registration on the
                       memory server ("Link account" runs auth code + PKCE, like Alexa)
      SIM_PUBLIC_URL   where the browser reaches the simulator (default http://127.0.0.1:8001);
                       the OAuth redirect URI is SIM_PUBLIC_URL/oauth/callback
      SIM_BRAIN        bedrock (default) | scripted
      BEDROCK_MODEL_ID, AWS_REGION   for SIM_BRAIN=bedrock (default AWS credential chain)
      SIM_ADMIN_TOKEN  if set, "Reset demo" requires it (X-Admin-Token header);
                       if unset, reset is only allowed from this machine
      SIM_HOST, SIM_PORT (default 127.0.0.1, 8001)
      Public demo protection (simulator/guard.py):
      SIM_PASSCODE, SIM_SESSION_SECRET  judge passcode gate (signed session cookie);
                       required on a non-local host. Reset demo and Unlink need it.
      SIM_TRUST_PROXY=1  client address from the load balancer's X-Forwarded-For
      SIM_RATE_PER_IP_MINUTE (10), SIM_RATE_PER_IP_DAY (120), SIM_RATE_GLOBAL_MINUTE (60),
      SIM_DAILY_MODEL_CALLS (300): utterance rate limits and the daily Bedrock call cap
"""
from __future__ import annotations

import hmac
import logging
from urllib.parse import urlencode
import os
from contextlib import asynccontextmanager

import anyio
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .agent import Agent, _unwrap
from .brains import BedrockBrain, Brain, BrainError, ScriptedBrain, UnavailableBrain
from .guard import Guard, GuardSettings
from .linking import AccountLink
from .mcp_link import McpLink, NotLinked
import logsafe
from auth.client import OAuthError

log = logging.getLogger("simulator")
STATIC = Path(__file__).parent / "static"
LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost"}
MAX_UTTERANCE = 500


def build_brain(kind: str | None = None) -> Brain:
    kind = (kind or os.getenv("SIM_BRAIN", "bedrock")).lower()
    if kind == "scripted":
        return ScriptedBrain()
    model_id = os.getenv("BEDROCK_MODEL_ID", "")
    try:
        return BedrockBrain(model_id, os.getenv("AWS_REGION"))
    except BrainError as e:
        log.warning("Bedrock brain unavailable: %s", e)
        return UnavailableBrain("bedrock", str(e), model_id or None)


def build_account(mcp_url: str) -> AccountLink:
    public = os.getenv("SIM_PUBLIC_URL", f"http://127.0.0.1:{os.getenv('SIM_PORT', '8001')}").rstrip("/")
    return AccountLink(mcp_url, os.getenv("OAUTH_CLIENT_ID"), os.getenv("OAUTH_CLIENT_SECRET"),
                       f"{public}/oauth/callback")


def build_guard(mcp_url: str) -> Guard:
    env = os.getenv
    return Guard(GuardSettings(
        passcode=env("SIM_PASSCODE") or None, session_secret=env("SIM_SESSION_SECRET") or None,
        secure_cookie=env("SIM_PUBLIC_URL", "").startswith("https://"),
        trust_proxy=env("SIM_TRUST_PROXY") == "1",
        per_ip_per_minute=int(env("SIM_RATE_PER_IP_MINUTE", "10")),
        per_ip_per_day=int(env("SIM_RATE_PER_IP_DAY", "120")),
        global_per_minute=int(env("SIM_RATE_GLOBAL_MINUTE", "60")),
        daily_model_calls=int(env("SIM_DAILY_MODEL_CALLS", "300")),
        privacy_url=mcp_url.rsplit("/mcp", 1)[0] + "/privacy",
    ))


def build_app(mcp_url: str | None = None, brain: Brain | None = None, admin_token: str | None = None,
              account: AccountLink | None = None, guard: Guard | None = None) -> Starlette:
    mcp_url = mcp_url or os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")
    account = account or build_account(mcp_url)
    guard = guard or build_guard(mcp_url)
    link = McpLink(mcp_url, token_provider=account.access_token)
    agent = Agent(link, brain or build_brain(), model_cap=guard.model_cap if guard.gated else None)
    admin_token = admin_token if admin_token is not None else os.getenv("SIM_ADMIN_TOKEN")
    state: dict = {"server": None, "server_error": None}

    async def discover() -> None:
        """Server reachable (OAuth metadata) and, once linked, its tools."""
        try:
            await anyio.to_thread.run_sync(account.endpoints)
            server = {"reachable": True}
            if account.linked:
                async with link.connect() as client:
                    profile = await link.profile(client)
                server.update(name=profile.name, tools=[t.name for t in profile.tools],
                              has_instructions=bool(profile.instructions))
            state.update(server=server, server_error=None)
        except NotLinked:
            state.update(server={"reachable": True}, server_error=None)
        except Exception as e:
            err = _unwrap(e)
            state.update(server=None, server_error=f"{type(err).__name__}: {err}")
            log.warning("Memory server not reachable at %s: %s", link.url, state["server_error"])

    @asynccontextmanager
    async def lifespan(app):
        await discover()
        yield

    async def index(request: Request):
        return FileResponse(STATIC / "index.html")

    async def status(request: Request):
        if state["server"] is None or (account.linked and "tools" not in state["server"]):
            await discover()
        b = agent.brain
        return JSONResponse({
            "brain": b.name, "model_id": getattr(b, "model_id", None),
            "brain_error": getattr(b, "reason", None),
            "server_url": link.url, "server": state["server"], "server_error": state["server_error"],
            "reset_needs_token": bool(admin_token),
            "account": account.status(),
            "gated": guard.gated,
            "model_calls_today": guard.model_cap.used, "daily_model_calls": guard.model_cap.limit,
        })

    async def utterance(request: Request):
        body = await request.json()
        session_id = str(body.get("session_id") or "default")[:64]
        text = str(body.get("text") or "").strip()[:MAX_UTTERANCE]
        if not text:
            return JSONResponse({"error": "Say or type something first."}, status_code=400)
        if limited := guard.allow_utterance(request):
            return JSONResponse({"error": limited, "rate_limited": True}, status_code=429)
        return JSONResponse(await agent.handle(session_id, text))

    async def memory(request: Request):
        try:
            markdown, latency, file_meta = await link.read_memory()
        except Exception as e:
            err = _unwrap(e)
            if isinstance(err, NotLinked):
                return JSONResponse({"error": "Link your account to see your memory file.", "linked": False},
                                    status_code=401)
            return JSONResponse({"error": f"Can't read the memory file over MCP: {type(err).__name__}"},
                                status_code=502)
        link_url = file_meta.get("webViewLink")
        return JSONResponse({"markdown": markdown, "mcp_latency_ms": latency,
                             "backend": file_meta.get("backend"),
                             "web_view_link": link_url if str(link_url or "").startswith("https://") else None})

    async def export(request: Request):
        body = await request.json() if await request.body() else {}
        categories = [str(c) for c in body.get("categories") or []] or None
        try:
            call = await link.export(categories, bool(body.get("include_history")),
                                     str(body.get("topic") or "") or None)
        except Exception as e:
            err = _unwrap(e)
            if isinstance(err, NotLinked):
                return JSONResponse({"error": "Link your account to export your memory.", "linked": False},
                                    status_code=401)
            return JSONResponse({"error": f"Export failed: {type(err).__name__}"}, status_code=502)
        result = call.result if isinstance(call.result, dict) else {}
        if call.is_error:
            return JSONResponse({"error": result.get("spoken_answer") or "Export failed."}, status_code=400)
        return JSONResponse({**result, "mcp_latency_ms": call.mcp_latency_ms})

    def reset_allowed(request: Request) -> bool:
        if guard.gated:
            return True  # the passcode gate already checked this request
        if admin_token:
            return hmac.compare_digest(request.headers.get("x-admin-token", ""), admin_token)
        client = request.client.host if request.client else ""
        return client in LOCAL_CLIENTS

    async def reset(request: Request):
        if not reset_allowed(request):
            return JSONResponse({"error": "Reset needs the admin token."}, status_code=403)
        body = await request.json() if await request.body() else {}
        try:
            result = await link.reset_demo()
        except Exception as e:
            return JSONResponse({"error": str(_unwrap(e))}, status_code=502)
        agent.forget(body.get("session_id"))
        return JSONResponse(result)

    async def link_start(request: Request):
        try:
            url = await anyio.to_thread.run_sync(account.start)
        except Exception as e:  # discovery or config problem: show it in the UI
            return RedirectResponse("/?" + urlencode({"link_error": str(e)}), status_code=303)
        return RedirectResponse(url, status_code=303)

    async def link_callback(request: Request):
        try:
            await anyio.to_thread.run_sync(account.finish, dict(request.query_params))
        except OAuthError as e:
            message = "Linking was cancelled." if e.error == "access_denied" else f"Linking failed: {e}"
            return RedirectResponse("/?" + urlencode({"link_error": message}), status_code=303)
        state["server"] = None  # re-discover tools with the new token
        agent.forget()
        return RedirectResponse("/?linked=1", status_code=303)

    async def unlink(request: Request):
        if not reset_allowed(request):
            return JSONResponse({"error": "Unlinking needs the admin token."}, status_code=403)
        account.unlink()
        agent.forget()
        state["server"] = None
        return JSONResponse({"linked": False})

    async def healthz(request: Request):
        return JSONResponse({"ok": True})

    app = Starlette(lifespan=lifespan, routes=[
        Route("/healthz", healthz),
        Route("/gate", guard.gate, methods=["GET", "POST"]),
        Route("/oauth/start", link_start),
        Route("/oauth/callback", link_callback),
        Route("/api/unlink", unlink, methods=["POST"]),
        Route("/", index),
        Route("/api/status", status),
        Route("/api/utterance", utterance, methods=["POST"]),
        Route("/api/memory", memory),
        Route("/api/export", export, methods=["POST"]),
        Route("/api/reset", reset, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])
    return guard.middleware(app)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    host = os.getenv("SIM_HOST", "127.0.0.1")
    if host not in LOCAL_CLIENTS and not (os.getenv("SIM_PASSCODE") and os.getenv("SIM_SESSION_SECRET")):
        raise SystemExit("Refusing to listen publicly without SIM_PASSCODE and SIM_SESSION_SECRET "
                         "(the judge passcode gate).")
    logsafe.install()
    uvicorn.run(build_app(), host=host, port=int(os.getenv("SIM_PORT", "8001")))
