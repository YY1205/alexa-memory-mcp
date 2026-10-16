"""Portable Memory: a self-hosted MCP server (Streamable HTTP) that gives Alexa+
a user-owned memory kept as a human-readable Markdown file.

Run locally:   python server.py            (http://127.0.0.1:8000/mcp)
Configuration: see .env.example
"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from auth.oauth import READ, WRITE, AuthSettings, OAuthServer, ScopeError, require_scope

from memory.cache import CachedStore
from memory.service import MemoryError_, MemoryService
from memory.store import DriveStore, LocalFileStore
from retrieval.retrievers import KeywordRetriever

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
HERE = Path(__file__).parent
MEMORY_URI = "memory://memories.md"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

INSTRUCTIONS = """\
This server is the user's own long-term memory. The user owns it; it is stored
as a readable file they can open and edit.
Voice rules:
- Never save anything silently. To save something the user tells you, call
  propose_memory, read its confirmation_prompt to the user exactly as given (it is
  already phrased for speech), then call confirm_memory with their answer. Ask only
  once: one answer is enough.
- If propose_memory returns `replaces`, the new fact looks like an update to that
  memory and confirmation_prompt already asks about the change. If the user agrees,
  call confirm_memory with confirmed=true and replaces=<that id>: this saves the new
  fact and keeps the old one as history. If they want to keep both, confirm without
  replaces. If they decline, confirmed=false.
- Use supersede_memory only when the user corrects a specific memory you have just
  told them (you know its id); then read its confirmation_prompt and confirm.
- When recall returns needs_review ids, or the user asks what needs updating, call
  list_due_reviews, ask whether each fact is still true, and record the answer with
  resolve_review. If the user gives new information instead, retire the old fact
  (still_true=false) and propose the new one.
- Memories are written about "the user" in the third person; speak to the user in
  the second person. Keep spoken answers short. Do not read ids aloud.
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


def build_server(service: MemoryService, oauth: OAuthServer | None = None) -> MCPServer:
    mcp = MCPServer(name="portable-memory", title="Portable Memory", instructions=INSTRUCTIONS,
                    version="0.1.0")

    def safe(scope: str, fn, *args, **kwargs) -> dict:
        try:
            require_scope(scope)  # memory.read / memory.write from the access token
            return fn(*args, **kwargs)
        except ScopeError:
            message = ("This app can read your memory but isn't allowed to change it. "
                       "Link your account again with permission to save.")
            return {"error": f"insufficient_scope: {scope} required", "spoken_answer": message}
        except MemoryError_ as e:
            return {"error": str(e), "spoken_answer": str(e)}

    @mcp.tool()
    def recall(query: str, category: str | None = None) -> dict:
        """Look up what the user has asked you to remember. Use whenever the user asks
        about themselves, their plans, preferences or past decisions ("what did I say
        about...", "when is my..."). Returns current memories only; phrase the spoken
        answer from them. category is optional: profile, preference, active_context,
        project, decision, other."""
        return safe(READ, service.recall, query, category)

    @mcp.tool()
    def propose_memory(fact: str, category: str = "other", review_in_days: int | None = None) -> dict:
        """Prepare to save a new fact the user wants remembered. Does NOT save yet:
        read confirmation_prompt to the user as given, then call confirm_memory with
        their answer. If the result includes `replaces`, the fact looks like an update
        to that existing memory; pass replaces=<that id> to confirm_memory if the user
        agrees. Write fact as a full sentence about the user in third person ("The
        user's dentist appointment is on Friday."). Set review_in_days for temporary
        facts (plans, deadlines, ongoing situations) so they get re-checked later.
        Proposing the same fact again replaces the earlier unconfirmed proposal."""
        return safe(WRITE, service.propose, fact, category, review_in_days)

    @mcp.tool()
    def supersede_memory(old_memory_id: str, new_fact: str) -> dict:
        """Prepare to replace a specific memory you already know the id of, when the
        user corrects it. The old memory is kept as history, not deleted. Does NOT
        save yet: read confirmation_prompt to the user, then call confirm_memory. For
        new facts that might update an old one, use propose_memory instead."""
        return safe(WRITE, service.propose, new_fact, supersedes=old_memory_id)

    @mcp.tool()
    def confirm_memory(pending_id: str, confirmed: bool, replaces: str | None = None) -> dict:
        """Finish a propose_memory or supersede_memory call with the user's answer:
        confirmed=true saves it, false discards it. replaces: the id from
        propose_memory's `replaces` when the user agreed to the update; the old
        memory is then kept as history."""
        return safe(WRITE, service.confirm, pending_id, confirmed, replaces)

    @mcp.tool()
    def list_due_reviews() -> dict:
        """List temporary memories whose review date has passed, so you can ask the
        user whether each one is still true."""
        return safe(READ, service.list_due_reviews)

    @mcp.tool()
    def resolve_review(memory_id: str, still_true: bool, review_in_days: int | None = None) -> dict:
        """Record the user's answer to "is this still true?". still_true=false retires
        the memory (kept as history). Optionally schedule the next check."""
        return safe(WRITE, service.resolve_review, memory_id, still_true, review_in_days)

    @mcp.resource(MEMORY_URI, name="memories.md", title="Your memory file",
                  description="The user's canonical memory file (Markdown), as stored.",
                  mime_type="text/markdown")
    def memory_file() -> str:
        require_scope(READ)
        return service.export_markdown()

    if os.getenv("DEMO_ADMIN") == "1":
        # Demo helper: restore the fictional persona. Off by default. Callers from this
        # machine (local server) may use it freely; anyone else needs an access token
        # with memory.write.
        @mcp.custom_route("/admin/reset", methods=["POST"], include_in_schema=False)
        async def reset_demo(request):
            from demo_data.seed import build_records

            local = (os.getenv("HOST", "127.0.0.1") in LOCAL_HOSTS
                     and request.client is not None and request.client.host in LOCAL_HOSTS)
            scopes = oauth.check_bearer(request.headers.get("authorization")) if oauth else None
            if not local and not (scopes and WRITE in scopes):
                return JSONResponse({"error": "invalid_token"}, status_code=401)

            records = build_records(today())  # review dates relative to today
            if hasattr(service.store, "reset"):
                service.store.reset(records)
            else:
                service.store.save(records)
            return JSONResponse({"ok": True, "records": len(records)})

    return mcp


def build_app(service: MemoryService | None = None):
    host = os.getenv("HOST", "127.0.0.1")
    security = None
    if allowed := os.getenv("ALLOWED_HOSTS"):  # e.g. "memory.example.com,localhost:*"
        hosts = [h.strip() for h in allowed.split(",") if h.strip()]
        security = TransportSecuritySettings(
            allowed_hosts=hosts, allowed_origins=[f"https://{h}" for h in hosts])
    # OAuth 2.1 (see auth/oauth.py). MCP_BEARER_TOKEN is a localhost-only dev token.
    oauth = OAuthServer(AuthSettings.from_env(host, int(os.getenv("PORT", "8000")), HERE))
    app = build_server(service or build_service(), oauth).streamable_http_app(
        stateless_http=True, json_response=True, host=host, transport_security=security)
    app.router.routes.extend(oauth.routes())
    app.router.routes.append(Route("/healthz", lambda request: JSONResponse({"ok": True})))
    return oauth.middleware(app)


if __name__ == "__main__":
    import uvicorn

    service = build_service()
    try:
        uvicorn.run(build_app(service), host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
    finally:
        service.store.close()  # flush write-behind changes before exiting
