"""Simulated Alexa+ web app (port 8001): a real MCP client of the memory server.

Run:  python -m simulator.app        then open http://127.0.0.1:8001
Env:  MCP_SERVER_URL   memory server endpoint (default http://127.0.0.1:8000/mcp)
      MCP_BEARER_TOKEN bearer token of the memory server
      SIM_BRAIN        bedrock (default) | scripted
      BEDROCK_MODEL_ID, AWS_REGION   for SIM_BRAIN=bedrock (default AWS credential chain)
      SIM_ADMIN_TOKEN  if set, "Reset demo" requires it (X-Admin-Token header);
                       if unset, reset is only allowed from this machine
      SIM_HOST, SIM_PORT (default 127.0.0.1, 8001)
"""
from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .agent import Agent, _unwrap
from .brains import BedrockBrain, Brain, BrainError, ScriptedBrain, UnavailableBrain
from .mcp_link import McpLink

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


def build_app(link: McpLink | None = None, brain: Brain | None = None,
              admin_token: str | None = None) -> Starlette:
    link = link or McpLink(os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp"),
                           os.getenv("MCP_BEARER_TOKEN"))
    agent = Agent(link, brain or build_brain())
    admin_token = admin_token if admin_token is not None else os.getenv("SIM_ADMIN_TOKEN")
    state: dict = {"server": None, "server_error": None}

    async def discover() -> None:
        try:
            async with link.connect() as client:
                profile = await link.profile(client)
            state.update(server={"name": profile.name, "tools": [t.name for t in profile.tools],
                                 "has_instructions": bool(profile.instructions)}, server_error=None)
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
        if state["server"] is None:
            await discover()
        b = agent.brain
        return JSONResponse({
            "brain": b.name, "model_id": getattr(b, "model_id", None),
            "brain_error": getattr(b, "reason", None),
            "server_url": link.url, "server": state["server"], "server_error": state["server_error"],
            "reset_needs_token": bool(admin_token),
        })

    async def utterance(request: Request):
        body = await request.json()
        session_id = str(body.get("session_id") or "default")[:64]
        text = str(body.get("text") or "").strip()[:MAX_UTTERANCE]
        if not text:
            return JSONResponse({"error": "Say or type something first."}, status_code=400)
        return JSONResponse(await agent.handle(session_id, text))

    async def memory(request: Request):
        try:
            markdown, latency = await link.read_memory()
        except Exception as e:
            err = _unwrap(e)
            return JSONResponse({"error": f"Can't read the memory file over MCP: {type(err).__name__}"},
                                status_code=502)
        return JSONResponse({"markdown": markdown, "mcp_latency_ms": latency})

    def reset_allowed(request: Request) -> bool:
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

    return Starlette(lifespan=lifespan, routes=[
        Route("/", index),
        Route("/api/status", status),
        Route("/api/utterance", utterance, methods=["POST"]),
        Route("/api/memory", memory),
        Route("/api/reset", reset, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    host = os.getenv("SIM_HOST", "127.0.0.1")
    if host not in LOCAL_CLIENTS and not os.getenv("SIM_ADMIN_TOKEN"):
        raise SystemExit("Refusing to listen publicly without SIM_ADMIN_TOKEN (protects Reset demo).")
    uvicorn.run(build_app(), host=host, port=int(os.getenv("SIM_PORT", "8001")))
