import json
import os
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_data.seed import RECORDS  # noqa: E402
from memory.schema import from_markdown, to_markdown  # noqa: E402
from memory.service import MemoryError_, MemoryService  # noqa: E402
from memory.store import LocalFileStore  # noqa: E402

TODAY = date(2026, 10, 13)


@pytest.fixture
def service(tmp_path):
    path = tmp_path / "memories.md"
    path.write_text(to_markdown(RECORDS))
    return MemoryService(LocalFileStore(path), today=lambda: TODAY)


def test_markdown_round_trip():
    assert from_markdown(to_markdown(RECORDS)) == RECORDS


def test_recall_finds_relevant_active_memory(service):
    out = service.recall("when is my sister's birthday")
    assert out["memories"][0]["id"] == "MEM-0004"


def test_recall_flags_overdue_review(service):
    out = service.recall("job interview")
    assert "MEM-0007" in out["needs_review"]


def test_propose_does_not_save_until_confirmed(service):
    p = service.propose("The user's dentist appointment is on Friday at 3pm.", "active_context", 7)
    assert service.recall("dentist appointment")["found"] is False  # still pending
    out = service.confirm(p["pending_id"], True)
    assert out["saved"] and out["memory"]["confirmed_by_user"]
    assert service.recall("dentist appointment")["found"] is True


def test_rejected_proposal_is_discarded(service):
    p = service.propose("The user hates jazz.", "preference")
    service.confirm(p["pending_id"], False)
    assert all(r.id != p["pending_id"] for r in service.store.load())


def test_propose_surfaces_related_memory(service):
    p = service.propose("The user's kitchen contractor now starts on October 27.", "active_context")
    assert "MEM-0006" in [m["id"] for m in p["possibly_related"]]


def test_supersede_keeps_history(service):
    p = service.propose("The user now lives in Seattle.", supersedes="MEM-0001")
    service.confirm(p["pending_id"], True)
    by_id = {r.id: r for r in service.store.load()}
    assert by_id["MEM-0001"].status == "historical"
    assert by_id["MEM-0001"].superseded_by == p["pending_id"]
    assert by_id[p["pending_id"]].category == "profile"
    assert "Seattle" in service.recall("where do I live")["memories"][0]["fact"]


def test_due_reviews_and_resolution(service):
    due = service.list_due_reviews()
    assert [m["id"] for m in due["memories"]] == ["MEM-0007"]
    service.resolve_review("MEM-0007", still_true=False)
    assert service.list_due_reviews()["count"] == 0
    assert service.recall("job interview")["found"] is False


def test_errors_are_speakable(service):
    with pytest.raises(MemoryError_):
        service.confirm("MEM-9999", True)


# ---------- end to end over Streamable HTTP ----------

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def http_server(tmp_path):
    mem = tmp_path / "memories.md"
    mem.write_text(to_markdown(RECORDS))
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "LOCAL_MEMORY_PATH": str(mem), "MCP_BEARER_TOKEN": "test-token",
           "DEMO_ADMIN": "1"}
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(f"{url}/healthz", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(5)


def test_rejects_missing_token(http_server):
    r = httpx.post(f"{http_server}/mcp", json={})
    assert r.status_code == 401


def test_full_voice_flow_over_http(http_server):
    import anyio
    from mcp import Client
    from mcp.client.streamable_http import create_mcp_http_client

    async def run():
        http = create_mcp_http_client(headers={"Authorization": "Bearer test-token"})
        from mcp.client.streamable_http import streamable_http_client
        async with Client(streamable_http_client(f"{http_server}/mcp", http_client=http)) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            assert tools == {"recall", "propose_memory", "supersede_memory", "confirm_memory",
                             "list_due_reviews", "resolve_review"}
            p = await c.call_tool("propose_memory", {"fact": "The user parks on level 3 at work.",
                                                     "category": "other"})
            pending = json.loads(p.content[0].text)
            await c.call_tool("confirm_memory", {"pending_id": pending["pending_id"], "confirmed": True})
            r = await c.call_tool("recall", {"query": "where did I park at work"})
            res = await c.read_resource("memory://memories.md", cache_mode="bypass")
            return json.loads(r.content[0].text), res.contents[0]

    out, memory_file = anyio.run(run)
    assert "level 3" in out["memories"][0]["fact"]
    assert memory_file.mime_type == "text/markdown"
    assert "parks on level 3 at work" in memory_file.text  # resource reflects the write


def test_admin_reset_requires_token_and_restores_demo_data(http_server):
    assert httpx.post(f"{http_server}/admin/reset").status_code == 401
    auth = {"Authorization": "Bearer test-token"}
    import anyio
    from mcp import Client
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    async def call(tool, args):
        http = create_mcp_http_client(headers=auth)
        async with Client(streamable_http_client(f"{http_server}/mcp", http_client=http)) as c:
            r = await c.call_tool(tool, args)
            return json.loads(r.content[0].text)

    anyio.run(call, "resolve_review", {"memory_id": "MEM-0007", "still_true": False})
    assert anyio.run(call, "list_due_reviews", {})["count"] == 0
    r = httpx.post(f"{http_server}/admin/reset", headers=auth)
    assert r.status_code == 200 and r.json()["records"] == len(RECORDS)
    assert anyio.run(call, "list_due_reviews", {})["count"] == 1


def test_admin_reset_is_off_by_default(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    import server

    monkeypatch.delenv("DEMO_ADMIN", raising=False)
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    path = tmp_path / "memories.md"
    path.write_text(to_markdown(RECORDS))
    app = server.build_app(MemoryService(LocalFileStore(path)))
    with TestClient(app) as client:
        assert client.post("/admin/reset").status_code == 404
