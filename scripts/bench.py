"""Measure round-trip latency of every tool over Streamable HTTP.

Starts nothing itself: point it at a running server.

    python scripts/bench.py http://127.0.0.1:8000/mcp --token "$TOKEN" -n 50

Each iteration calls all seven tools with the official MCP Python client:
recall, memory_overview, list_due_reviews, propose_memory + confirm_memory (creates a benchmark
fact), supersede_memory + confirm_memory (discarded), resolve_review (retires the
benchmark fact), and reads the memory file resource (memory://memories.md). It WRITES to the memory file, leaving one historical
"Benchmark fact" record per iteration: run it against demo data only.
Get TOKEN with scripts/oauth_token.py (real OAuth flow).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

import anyio
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

TOOLS = ("recall", "memory_overview", "list_due_reviews", "propose_memory", "confirm_memory",
         "supersede_memory", "resolve_review")
MEMORY_URI = "memory://memories.md"
RESOURCE_KEY = "read memories.md"  # timings key for resources/read


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(p * len(s)) - 1)]


async def run_bench(url: str, token: str | None = None, n: int = 50) -> dict[str, list[float]]:
    """Return per-tool round-trip times in milliseconds."""
    timings: dict[str, list[float]] = {t: [] for t in (*TOOLS, RESOURCE_KEY)}
    headers = {"Authorization": f"Bearer {token}"} if token else None
    http = create_mcp_http_client(headers=headers)
    async with Client(streamable_http_client(url, http_client=http)) as c:

        async def call(tool: str, args: dict) -> dict:
            t0 = time.perf_counter()
            result = await c.call_tool(tool, args)
            timings[tool].append((time.perf_counter() - t0) * 1000)
            data = json.loads(result.content[0].text)
            if result.is_error or (isinstance(data, dict) and "error" in data):
                raise RuntimeError(f"{tool} failed: {data}")
            return data

        await call("list_due_reviews", {})  # warm-up (not counted)
        timings["list_due_reviews"].clear()
        for i in range(n):
            await call("recall", {"query": "when is my sister's birthday"})
            await call("list_due_reviews", {})
            await call("memory_overview", {})
            p = await call("propose_memory", {"fact": f"Benchmark fact number {i}.", "category": "other"})
            await call("confirm_memory", {"pending_id": p["pending_id"], "confirmed": True})
            s = await call("supersede_memory", {"old_memory_id": p["pending_id"],
                                                "new_fact": f"Benchmark fact number {i}, corrected."})
            await call("confirm_memory", {"pending_id": s["pending_id"], "confirmed": False})
            await call("resolve_review", {"memory_id": p["pending_id"], "still_true": False})
            t0 = time.perf_counter()
            res = await c.read_resource(MEMORY_URI, cache_mode="bypass")
            timings[RESOURCE_KEY].append((time.perf_counter() - t0) * 1000)
            if p["pending_id"] not in res.contents[0].text:
                raise RuntimeError("memory resource does not show the benchmark fact")
    return timings


def report(timings: dict[str, list[float]], budget_ms: float) -> tuple[str, bool]:
    lines = [f"{'tool / resource':<18}{'calls':>6}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}  p95<{budget_ms:g}ms"]
    ok = True
    for tool, values in timings.items():
        p50, p95, mx = percentile(values, 0.5), percentile(values, 0.95), max(values)
        ok &= p95 < budget_ms
        lines.append(f"{tool:<18}{len(values):>6}{p50:>10.1f}{p95:>10.1f}{mx:>10.1f}  "
                     f"{'PASS' if p95 < budget_ms else 'FAIL'}")
    return "\n".join(lines), ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url", help="server MCP endpoint, e.g. http://127.0.0.1:8000/mcp")
    ap.add_argument("--token", help="access token (scripts/oauth_token.py) or the localhost dev token")
    ap.add_argument("-n", type=int, default=50, help="iterations per tool (default 50)")
    ap.add_argument("--budget-ms", type=float, default=500, help="p95 budget (default 500, Alexa+)")
    ap.add_argument("--rounds", type=int, default=1,
                    help="repeat the run, pausing in between, so background saves overlap tool calls")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between rounds")
    args = ap.parse_args()

    timings: dict[str, list[float]] = {}
    started = time.monotonic()
    for i in range(args.rounds):
        if i:
            time.sleep(args.pause)
        for key, values in anyio.run(run_bench, args.url, args.token, args.n).items():
            timings.setdefault(key, []).extend(values)
    table, ok = report(timings, args.budget_ms)
    span = f", {args.rounds} rounds over {time.monotonic() - started:.0f} s" if args.rounds > 1 else ""
    print(f"Round-trip latency per tool call, {args.url}, n={args.n} iterations{span}")
    print(table)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
