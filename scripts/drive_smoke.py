"""Manual check against real Google Drive (not in the test suite: needs network and a key).

    DRIVE_FOLDER_ID=... GOOGLE_APPLICATION_CREDENTIALS=~/.config/alexa-memory-mcp/service-account.json \\
        python scripts/drive_smoke.py --out proof/drive-smoke.txt

Uses the server's own cache + DriveStore + MemoryService on the demo folder:
  0. reset memories.md to the current seed (explicit demo reset)
  1. write-behind: a confirmed write returns at once and reaches Drive in the background
  2. outside edit: another Drive client changes the dog's name; the refresh picks it up
  3. conflict merge: unsaved local writes + outside edits -> merged, local wins on conflict
  4. reset to the seed again (skip with --keep)
The "outside edit" goes through a second Drive API client, which Drive treats like any
other editor. Output has no e-mail addresses or keys; folder/file ids are shortened.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_data.seed import build_records  # noqa: E402
from memory.cache import CachedStore  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from memory.store import DriveStore  # noqa: E402

LINES: list[str] = []
RESULTS: dict[str, bool] = {}


def say(line: str = "") -> None:
    print(line, flush=True)
    LINES.append(line)


def short(identifier: str) -> str:
    return f"...{identifier[-4:]}"


def wait_for(cond, timeout: float, step: float = 0.25) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return time.monotonic() - t0
        time.sleep(step)
    return None


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, (time.perf_counter() - t0) * 1000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--folder", default=os.getenv("DRIVE_FOLDER_ID"))
    ap.add_argument("--credentials", default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
    ap.add_argument("--refresh", type=float, default=3.0, help="cache refresh interval (s)")
    ap.add_argument("--out", default="proof/drive-smoke.txt")
    ap.add_argument("--keep", action="store_true", help="leave the test edits in Drive")
    args = ap.parse_args()
    if not args.folder:
        ap.error("set DRIVE_FOLDER_ID or --folder")
    warnings: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    logging.getLogger("memory.cache").addHandler(Capture())
    tool_ms: list[float] = []

    say(f"# {datetime.now(timezone.utc).isoformat(timespec='seconds')} | Google Drive smoke check")
    store = DriveStore(args.folder, args.credentials)
    other_editor = DriveStore(args.folder, args.credentials)  # a separate Drive client
    say(f"# folder {short(args.folder)} | file memories.md {short(store._file_id)} | "
        f"version {store.version()} | refresh every {args.refresh:g} s")

    say("\n## 0. Demo reset (explicit) -> current seed")
    seed = build_records(date.today())
    cache = CachedStore(store, write_behind=True, debounce=1.0, max_wait=5.0, refresh_interval=args.refresh)
    _, ms = timed(cache.reset, seed)
    say(f"reset wrote {len(seed)} records in {ms:.0f} ms (admin action, not a tool call); "
        f"Drive version now {store.last_version}")
    service = MemoryService(cache)

    say("\n## 1. Write-behind")
    p, ms1 = timed(service.propose, "The user's favourite tea is genmaicha.", "preference")
    c, ms2 = timed(service.confirm, p["pending_id"], True)
    tool_ms += [ms1, ms2]
    say(f"propose_memory {ms1:.1f} ms, confirm_memory {ms2:.1f} ms (tool calls return before Drive is written)")
    secs = wait_for(lambda: not cache.dirty, 30)
    in_drive = any("genmaicha" in r.fact and r.status == "active" for r in other_editor.load())
    say(f"reached Drive after {secs:.1f} s (debounce 1 s), visible to another Drive client: {in_drive}"
        if secs is not None else "did not reach Drive within 30 s")
    RESULTS["write-behind"] = secs is not None and in_drive

    say("\n## 2. Outside edit picked up by the refresh")
    records = other_editor.load()
    records = [replace(r, fact=r.fact.replace("Biscuit", "Pepper")) if r.id == "MEM-0009" else r for r in records]
    other_editor.save(records)
    say(f"another client renamed the dog Biscuit -> Pepper (Drive version {other_editor.last_version})")

    def recall_has_pepper():
        out, ms = timed(service.recall, "my dog")
        tool_ms.append(ms)
        return any("Pepper" in m["fact"] for m in out["memories"])

    secs = wait_for(recall_has_pepper, args.refresh * 4 + 10, step=0.5)
    say(f"recall('my dog') answers with Pepper after {secs:.1f} s" if secs is not None
        else "recall never saw the edit")
    RESULTS["outside edit picked up"] = secs is not None

    say("\n## 3. Conflict merge (both sides changed)")
    cache.debounce = cache.max_wait = 3600  # hold local writes back
    p, ms1 = timed(service.propose, "The user's bike is green.", "profile")
    _, ms2 = timed(service.confirm, p["pending_id"], True)
    local = cache.load()
    cache.save([replace(r, fact="The user is learning Spanish and practices 20 minutes a day.")
                if r.id == "MEM-0010" else r for r in local])
    tool_ms += [ms1, ms2]
    remote = other_editor.load()
    remote = [replace(r, fact="The user is vegetarian and also avoids eggs.") if r.id == "MEM-0002" else
              replace(r, fact="The user is learning Spanish and practices 5 minutes a day.") if r.id == "MEM-0010"
              else r for r in remote]
    other_editor.save(remote)
    say("local (unsaved): new bike fact; MEM-0010 -> 20 minutes")
    say("outside edit:    MEM-0002 -> also avoids eggs; MEM-0010 -> 5 minutes")
    warnings.clear()
    cache.flush()
    final = {r.id: r for r in other_editor.load()}
    checks = {
        "local new fact kept": any("bike is green" in r.fact for r in final.values()),
        "outside-only edit kept": "avoids eggs" in final["MEM-0002"].fact,
        "conflict: local wins": "20 minutes" in final["MEM-0010"].fact,
        "conflict logged": any("MEM-0010" in w for w in warnings),
    }
    for name, ok in checks.items():
        say(f"{'PASS' if ok else 'FAIL'}  {name}")
    RESULTS["conflict merge"] = all(checks.values())

    say("\n## Tool-call latency during the checks (in-process, Drive never on the path)")
    say(f"{len(tool_ms)} calls, max {max(tool_ms):.1f} ms")
    RESULTS["tool calls < 500 ms"] = max(tool_ms) < 500

    if not args.keep:
        cache.reset(build_records(date.today()))
        say(f"\nreset to the seed again (Drive version {store.last_version})")
    cache.close()

    say("\n## Summary")
    for name, ok in RESULTS.items():
        say(f"{'PASS' if ok else 'FAIL'}  {name}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(LINES) + "\n")
    return 0 if all(RESULTS.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
