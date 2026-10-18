"""Run the golden conversations through the PUBLIC simulator, like a judge's browser.

    SIM_PASSCODE=... OWNER_PASSPHRASE=... python scripts/eval_public.py \\
        https://<simulator host> --out proof/public-conversations.txt

Enters the passcode, links the account on the memory server's sign-in page (if not linked),
then for each conversation: Reset demo, speak the lines, read the memory file the simulator
shows, and judge it with the same rules as scripts/eval_bedrock.py. Paced to stay under the
simulator's per-client rate limit. Secrets come from the environment and are never printed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth import client as oc  # noqa: E402
from eval_bedrock import CONVERSATIONS, judge  # noqa: E402
from memory.schema import from_markdown  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sim_url")
    ap.add_argument("--conversations", default="1,2,3,4,5")
    ap.add_argument("--pace", type=float, default=7.0, help="seconds between utterances (rate limit)")
    ap.add_argument("--out", default="proof/public-conversations.txt")
    args = ap.parse_args()
    sim = args.sim_url.rstrip("/")
    lines = [f"# {datetime.now(timezone.utc).isoformat(timespec='seconds')} | public simulator {sim}"]
    results = {}
    with httpx.Client(timeout=60, follow_redirects=False) as browser:
        r = browser.post(f"{sim}/gate", data={"passcode": os.environ["SIM_PASSCODE"]})
        assert r.status_code == 303, f"passcode gate: {r.status_code}"
        status = browser.get(f"{sim}/api/status").json()
        lines.append(f"# brain {status['brain']} {status['model_id']} | server {status['server_url']}")
        if not status["account"]["linked"]:
            start = browser.get(f"{sim}/oauth/start")
            params = oc.owner_approves(browser, start.headers["location"], os.environ["OWNER_PASSPHRASE"])
            cb = browser.get(f"{sim}/oauth/callback", params=params)
            assert cb.headers.get("location") == "/?linked=1", f"linking failed: {cb.headers.get('location')}"
            lines.append("# linked the account through the server's sign-in page")
        for n in [int(c) for c in args.conversations.split(",")]:
            session = f"public-eval-{n}-{int(time.time())}"
            assert browser.post(f"{sim}/api/reset", json={"session_id": session}).status_code == 200
            lines.append(f"--- conversation {n}")
            replies, errors, mcp_ms = [], [], []
            for text in CONVERSATIONS[n]:
                time.sleep(args.pace)
                r = browser.post(f"{sim}/api/utterance", json={"session_id": session, "text": text})
                out = r.json()
                if r.status_code != 200:
                    errors.append(out.get("error", str(r.status_code)))
                    lines.append(f"  YOU: {text}\n  !! {r.status_code} {out.get('error')}")
                    continue
                replies.append(out["reply_text"])
                if out.get("error"):
                    errors.append(out["error"])
                steps = [s for s in out["trace"] if s.get("kind") == "mcp"]
                mcp_ms += [s["mcp_latency_ms"] for s in steps]
                lines.append(f"  YOU: {text}\n  ALEXA: {out['reply_text']}")
                lines.append(f"     tools: {', '.join(s['tool'] for s in steps) or '-'} | MCP "
                             f"{', '.join(f'{s['mcp_latency_ms']:.0f}' for s in steps) or '-'} ms | model "
                             f"{out['model_latency_ms']:.0f} ms")
            memory = browser.get(f"{sim}/api/memory").json()
            records = {r.id: r for r in from_markdown(memory["markdown"])}
            problems = judge(n, replies, records) + [f"{e} error" for e in errors]
            results[n] = not problems
            lines.append("  RESULT: " + ("PASS" if not problems else "FAIL - " + "; ".join(problems)))
        browser.post(f"{sim}/api/reset", json={})  # leave Sam's fresh memory for the judges
    lines.insert(1, "SUMMARY " + "  ".join(f"#{n} {'PASS' if ok else 'FAIL'}" for n, ok in results.items()))
    text = "\n".join(lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(text)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
