"""Portable Memory: a self-hosted MCP server (Streamable HTTP) that gives Alexa+
a user-owned memory kept as a human-readable Markdown file.

Run locally:   python server.py            (http://127.0.0.1:8000/mcp)
Configuration: see .env.example
"""
from __future__ import annotations

import hmac
import logging
import os
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from memory.cache import CachedStore
from memory.service import MemoryError_, MemoryService
from memory.store import DriveStore, LocalFileStore
from retrieval.retrievers import KeywordRetriever

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
HERE = Path(__file__).parent
MEMORY_URI = "memory://memories.md"

INSTRUCTIONS = """\
This server is the user's own long-term memory. The user owns it; it is stored
as a readable file they can open and edit.
Voice rules:
- Never save anything silently. To save, call propose_memory (or supersede_memory
  for a correction), read the returned confirmation_prompt to the user, and call
  confirm_memory only with their answer.
- If propose_memory returns possibly_related memories, ask whether the new fact
  replaces one of them; if yes, discard the proposal and use supersede_memory.
- When recall returns needs_review ids, or at a natural moment, ask whether those
  facts are still true and record the answer with resolve_review.
- Keep spoken answers short. Do not read ids aloud.
"""


def today() -> date:
    """Real date, unless DEMO_TODAY (ISO date) pins it for tests and demo recordings."""
    pinned = os.getenv("DEMO_TODAY")
    return date.fromisoformat(pinned) if pinned else date.today()


def build_service() -> MemoryService:
    # Reads come from memory. Drive writes go out in the background so no tool call
    # waits on a network request; local file writes are fast and stay synchronous.
    if os.getenv("MEMORY_BACKEND", "local") == "drive":
        backing = DriveStore(os.environ["DRIVE_FOLDER_ID"], os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        store = CachedStore(backing, write_behind=True)
    else:
        backing = LocalFileStore(os.getenv("LOCAL_MEMORY_PATH", HERE / "demo_data" / "memories.md"))
        store = CachedStore(backing, write_behind=False)
    return MemoryService(store, KeywordRetriever(), today=today)


def build_server(service: MemoryService) -> MCPServer:
    mcp = MCPServer(name="portable-memory", title="Portable Memory", instructions=INSTRUCTIONS,
                    version="0.1.0")

    def safe(fn, *args, **kwargs) -> dict:
        try:
            return fn(*args, **kwargs)
        except MemoryError_ as e:
            return {"error": str(e), "spoken_answer": str(e)}

    @mcp.tool()
    def recall(query: str, category: str | None = None) -> dict:
        """Look up what the user has asked you to remember. Use whenever the user asks
        about themselves, their plans, preferences or past decisions ("what did I say
        about...", "when is my..."). Returns current memories only; phrase the spoken
        answer from them. category is optional: profile, preference, active_context,
        project, decision, other."""
        return safe(service.recall, query, category)

    @mcp.tool()
    def propose_memory(fact: str, category: str = "other", review_in_days: int | None = None) -> dict:
        """Prepare to save a new fact the user wants remembered. Does NOT save yet:
        read confirmation_prompt aloud, then call confirm_memory with the answer.
        Write fact as a full sentence about the user in third person ("The user's
        dentist appointment is on Friday."). Set review_in_days for temporary facts
        (plans, deadlines, ongoing situations) so they get re-checked later."""
        return safe(service.propose, fact, category, review_in_days)

    @mcp.tool()
    def supersede_memory(old_memory_id: str, new_fact: str) -> dict:
        """Prepare to replace a memory that is no longer true (the user corrected it or
        things changed). The old memory is kept as history, not deleted. Does NOT save
        yet: read confirmation_prompt aloud, then call confirm_memory."""
        return safe(service.propose, new_fact, supersedes=old_memory_id)

    @mcp.tool()
    def confirm_memory(pending_id: str, confirmed: bool) -> dict:
        """Finish a propose_memory or supersede_memory call with the user's spoken
        answer: confirmed=true saves it, false discards it."""
        return safe(service.confirm, pending_id, confirmed)

    @mcp.tool()
    def list_due_reviews() -> dict:
        """List temporary memories whose review date has passed, so you can ask the
        user whether each one is still true."""
        return safe(service.list_due_reviews)

    @mcp.tool()
    def resolve_review(memory_id: str, still_true: bool, review_in_days: int | None = None) -> dict:
        """Record the user's answer to "is this still true?". still_true=false retires
        the memory (kept as history). Optionally schedule the next check."""
        return safe(service.resolve_review, memory_id, still_true, review_in_days)

    @mcp.resource(MEMORY_URI, name="memories.md", title="Your memory file",
                  description="The user's canonical memory file (Markdown), as stored.",
                  mime_type="text/markdown")
    def memory_file() -> str:
        return service.export_markdown()

    if os.getenv("DEMO_ADMIN") == "1":
        # Local demo helper: restore the fictional persona. Off by default; when the
        # server has MCP_BEARER_TOKEN, this route requires it like /mcp does.
        @mcp.custom_route("/admin/reset", methods=["POST"], include_in_schema=False)
        async def reset_demo(request):
            from demo_data.seed import build_records

            records = build_records(today())  # review dates relative to today
            if hasattr(service.store, "reset"):
                service.store.reset(records)
            else:
                service.store.save(records)
            return JSONResponse({"ok": True, "records": len(records)})

    return mcp


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Minimal protection for a public endpoint holding personal data.
    Replace with OAuth if the Alexa+ integration requires it."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self.expected = f"Bearer {token}"

    async def dispatch(self, request, call_next):
        if request.url.path == "/healthz":
            return JSONResponse({"ok": True})
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given, self.expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app(service: MemoryService | None = None):
    host = os.getenv("HOST", "127.0.0.1")
    security = None
    if allowed := os.getenv("ALLOWED_HOSTS"):  # e.g. "memory.example.com,localhost:*"
        hosts = [h.strip() for h in allowed.split(",") if h.strip()]
        security = TransportSecuritySettings(
            allowed_hosts=hosts, allowed_origins=[f"https://{h}" for h in hosts])
    app = build_server(service or build_service()).streamable_http_app(
        stateless_http=True, json_response=True, host=host, transport_security=security)
    if token := os.getenv("MCP_BEARER_TOKEN"):
        app.add_middleware(BearerTokenMiddleware, token=token)
    elif host not in ("127.0.0.1", "localhost"):
        raise SystemExit("Refusing to listen publicly without MCP_BEARER_TOKEN set.")
    return app


if __name__ == "__main__":
    import uvicorn

    service = build_service()
    try:
        uvicorn.run(build_app(service), host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
    finally:
        service.store.close()  # flush write-behind changes before exiting
