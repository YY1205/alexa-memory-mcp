"""Every tool answers in < 500 ms (Alexa+ budget) even when persistence is slow.

Runs the real server app over Streamable HTTP, backed by a DriveStore-like fake
whose save() sleeps 1 s, and benchmarks it with scripts/bench.py.
"""
import sys
import threading
import time
from pathlib import Path

import anyio
import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402
from bench import RESOURCE_KEY, TOOLS, percentile, report, run_bench  # noqa: E402
from tests.conftest import TEST_RECORDS as RECORDS  # noqa: E402
from memory.cache import CachedStore  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from tests.test_cache import FakeStore  # noqa: E402
from tests.conftest import (CLIENT_ID, CLIENT_SECRET, PASSPHRASE, SIM_REDIRECT, free_port,  # noqa: E402
                            oauth_env)
from auth import client as oc  # noqa: E402

BUDGET_MS = 500


@pytest.fixture
def slow_drive_server(monkeypatch, tmp_path):
    """The real app with OAuth on, a Drive-like store whose save() sleeps 1 s, and an
    access token obtained through the full auth code + PKCE flow."""
    port = free_port()
    for key, value in oauth_env(tmp_path, port).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PORT", str(port))
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)  # no dev token: OAuth only
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    backing = FakeStore(RECORDS, save_delay=1.0)
    store = CachedStore(backing, write_behind=True, debounce=0.05, max_wait=0.2,
                        refresh_interval=3600)
    srv = uvicorn.Server(uvicorn.Config(server.build_app(MemoryService(store)), host="127.0.0.1",
                                        port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{url}/healthz", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    with httpx.Client(follow_redirects=False) as http:
        ep = oc.discover(f"{url}/mcp", http)
        verifier, challenge = oc.pkce_pair()
        params = oc.owner_approves(http, oc.authorize_url(ep, CLIENT_ID, SIM_REDIRECT, "s", challenge), PASSPHRASE)
        token = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, http)
    yield f"{url}/mcp", backing, store, token.access_token
    srv.should_exit = True
    thread.join(10)
    store.close()


def test_every_tool_p95_under_budget_with_slow_persistence(slow_drive_server):
    url, backing, store, token = slow_drive_server
    timings = anyio.run(run_bench, url, token, 30)
    table, ok = report(timings, BUDGET_MS)
    print("\n" + table)
    assert set(timings) == {*TOOLS, RESOURCE_KEY}  # eight tools + the memory file resource
    for tool, values in timings.items():
        assert percentile(values, 0.95) < BUDGET_MS, f"{tool} too slow:\n{table}"
    assert ok
    # Slow saves really ran while the tools were being called, and close() catches up.
    assert backing.save_starts >= 1
    store.close()
    facts = {r.fact for r in backing.records}
    assert "Benchmark fact number 29." in facts


def test_server_never_imports_boto3():
    """Bedrock lives in the simulator; the MCP server's request path stays model-free."""
    import subprocess

    code = "import sys, server; sys.exit(1 if 'boto3' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 0
