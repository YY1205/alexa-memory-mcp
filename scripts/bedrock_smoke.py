"""Manual smoke check: demo conversations 1 and 2 against real Amazon Bedrock.

Not part of the test suite (it costs money and needs AWS). Start the servers first
(scripts/run_demo.sh, or server.py with DEMO_ADMIN=1), then:

    BEDROCK_MODEL_ID=... AWS_REGION=... python scripts/bedrock_smoke.py \\
        http://127.0.0.1:8000/mcp --token "$TOKEN" --out proof/bedrock-smoke.txt

AWS credentials come from the default credential chain; this script never reads
or prints them. The output contains the model id, region, replies, tool calls
and latencies only.
Get TOKEN with scripts/oauth_token.py (real OAuth flow).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator.agent import Agent  # noqa: E402
from simulator.brains import BedrockBrain, BrainError  # noqa: E402
from simulator.mcp_link import McpLink  # noqa: E402

CONVERSATIONS = {
    "1 Recall": ["When is my sister's birthday?"],
    "2 Save with confirmation": ["Remember that my dentist appointment is Friday at 3.", "Yes."],
}


async def run(url: str, token: str | None) -> tuple[list[str], bool]:
    link = McpLink(url, token)
    brain = BedrockBrain(os.getenv("BEDROCK_MODEL_ID", ""), os.getenv("AWS_REGION"))
    agent = Agent(link, brain)
    lines, ok = [], True
    try:
        await link.reset_demo()
        lines.append("demo data reset via /admin/reset")
    except Exception as e:  # reset is optional for a smoke check
        lines.append(f"(no demo reset: {e})")
    for name, turns in CONVERSATIONS.items():
        lines.append(f"\n=== Conversation {name} ===")
        for text in turns:
            out = await agent.handle(f"smoke-{name}", text)
            lines.append(f"USER:  {text}")
            lines.append(f"ALEXA: {out['reply_text']}")
            lines.append(f"       model latency {out['model_latency_ms']} ms")
            for step in out["trace"]:
                lines.append(f"  -> {step['tool']} {json.dumps(step['arguments'])}  "
                             f"[{step['mcp_latency_ms']} ms]")
                lines.append(f"     {json.dumps(step['result'])[:400]}")
            if out.get("error"):
                ok = False
                lines.append(f"  !! error: {out['error']}")
    return lines, ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url")
    ap.add_argument("--token")
    ap.add_argument("--out", default="proof/bedrock-smoke.txt")
    args = ap.parse_args()
    try:
        lines, ok = anyio.run(run, args.url, args.token)
    except BrainError as e:
        print(f"Bedrock not usable: {e}", file=sys.stderr)
        return 2
    header = [f"# {datetime.now(timezone.utc).isoformat(timespec='seconds')} | Bedrock Converse smoke check",
              f"# model: {os.getenv('BEDROCK_MODEL_ID')} | region: {os.getenv('AWS_REGION')} | server: {args.url}"]
    text = "\n".join(header + lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
