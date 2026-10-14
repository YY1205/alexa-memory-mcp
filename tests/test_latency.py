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
from bench import TOOLS, percentile, report, run_bench  # noqa: E402
from demo_data.seed import RECORDS  # noqa: E402
from memory.cache import CachedStore  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from tests.test_cache import FakeStore  # noqa: E402
from tests.test_memory import _free_port  # noqa: E402

BUDGET_MS = 500


@pytest.fixture
def slow_drive_server(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "bench-token")
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    backing = FakeStore(RECORDS, save_delay=1.0)
    store = CachedStore(backing, write_behind=True, debounce=0.05, max_wait=0.2,
                        refresh_interval=3600)
    port = _free_port()
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
    yield f"{url}/mcp", backing, store
    srv.should_exit = True
    thread.join(10)
    store.close()


def test_every_tool_p95_under_budget_with_slow_persistence(slow_drive_server):
    url, backing, store = slow_drive_server
    timings = anyio.run(run_bench, url, "bench-token", 30)
    table, ok = report(timings, BUDGET_MS)
    print("\n" + table)
    assert set(timings) == set(TOOLS)
    for tool, values in timings.items():
        assert percentile(values, 0.95) < BUDGET_MS, f"{tool} too slow:\n{table}"
    assert ok
    # Slow saves really ran while the tools were being called, and close() catches up.
    assert backing.save_starts >= 1
    store.close()
    facts = {r.fact for r in backing.records}
    assert "Benchmark fact number 29." in facts
