"""Run the five golden demo conversations against Bedrock models and score them.

Manual (costs money, needs AWS). Start a memory server with DEMO_ADMIN=1 first, then:

    AWS_REGION=us-east-1 python scripts/eval_bedrock.py http://127.0.0.1:8000/mcp \\
        --token "$TOKEN" --repeats 3 --out proof/bedrock-eval.txt \\
        us.amazon.nova-2-lite-v1:0 us.amazon.nova-pro-v1:0

Each conversation starts from freshly reset demo data. Pass/fail is judged from the
memory file (read over MCP) and the replies, not from exact wording:
  1 recall      reply has the birthday; nothing written
  2 save        dentist fact active and user-confirmed; nothing pending
  3 correction  MEM-0006 historical, replaced by an active "27th" fact; nothing pending
  4 review      MEM-0007 retired; an active fact about the new job; nothing pending
  5 decline     no jazz fact anywhere; nothing pending
  6 hand edit   the memory file is edited by hand (dog Biscuit -> Pepper) before the
                question; the reply uses Pepper (needs --memory-file: the server's local
                file, and a server MEMORY_REFRESH_SECONDS of about 1)
  every turn    no ids or "the user" read aloud, no brain/MCP errors
Get TOKEN with scripts/oauth_token.py (real OAuth flow).
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.schema import from_markdown  # noqa: E402
from simulator.agent import Agent  # noqa: E402
from simulator.brains import BedrockBrain  # noqa: E402
from simulator.mcp_link import McpLink  # noqa: E402

CONVERSATIONS = {
    1: ["When is my sister's birthday?"],
    2: ["Remember that my dentist appointment is Friday at 3.", "Yes."],
    3: ["The kitchen contractor now starts on the 27th.", "Yes."],
    4: ["Is there anything I should update?", "I got the job, I start in November.", "Yes."],
    5: ["Remember that I hate jazz.", "No."],
    6: ["When does my dog need his heartworm pill?"],  # after a hand edit: Biscuit -> Pepper
}


def judge(n: int, replies: list[str], records: dict) -> list[str]:
    """Return the problems found (empty list = pass)."""
    problems = []
    pending = [r.id for r in records.values() if r.status == "pending"]
    if pending:
        problems.append(f"left pending: {', '.join(pending)}")
    for reply in replies:
        if "MEM-" in reply:
            problems.append("read an id aloud")
        if "the user" in reply.lower():
            problems.append('said "the user"')
    new = [r for r in records.values() if int(r.id[4:]) > 10]
    if n == 1:
        if not any("November 2" in r for r in replies):
            problems.append("birthday not answered")
        if new or any(r.status != "active" for r in records.values()):
            problems.append("memory changed")
    elif n == 2:
        if not any("dentist" in r.fact.lower() and r.status == "active" and r.source_type == "user_explicit"
                   for r in new):
            problems.append("dentist fact not saved")
    elif n == 3:
        old = records["MEM-0006"]
        if old.status != "historical":
            problems.append(f"MEM-0006 still {old.status}")
        elif not (old.superseded_by in records and records[old.superseded_by].status == "active"
                  and "27" in records[old.superseded_by].fact):
            problems.append("MEM-0006 not replaced by the 27th fact")
    elif n == 4:
        if records["MEM-0007"].status == "active":
            problems.append("MEM-0007 not retired")
        if not any("job" in r.fact.lower() and r.status == "active" for r in new):
            problems.append("new job fact not saved")
    elif n == 5:
        if any("jazz" in r.fact.lower() for r in records.values()):
            problems.append("jazz fact written")
    elif n == 6:
        if not any("Pepper" in r for r in replies):
            problems.append("reply does not use the hand edit (Pepper)")
    return problems


async def hand_edit(memory_file: str | None, wait: float) -> None:
    if not memory_file:
        raise SystemExit("conversation 6 needs --memory-file (the server's local memories.md)")
    path = Path(memory_file)
    path.write_text(path.read_text().replace("Biscuit", "Pepper"))
    await anyio.sleep(wait)  # let the server's refresh pick it up


async def evaluate(url: str, token: str | None, models: list[str], repeats: int,
                   conversations: list[int], memory_file: str | None, wait: float) -> list[str]:
    link = McpLink(url, token)
    lines = []
    summary = []
    for model in models:
        agent = Agent(link, BedrockBrain(model, os.getenv("AWS_REGION")))
        passes = {n: 0 for n in conversations}
        model_ms: list[float] = []
        lines.append(f"\n################ {model}")
        for rep in range(1, repeats + 1):
            for n in conversations:
                turns = CONVERSATIONS[n]
                await link.reset_demo()
                if n == 6:
                    await hand_edit(memory_file, wait)
                replies, errors = [], []
                lines.append(f"--- run {rep}, conversation {n}")
                for text in turns:
                    out = await agent.handle(f"eval-{model}-{rep}-{n}", text)
                    replies.append(out["reply_text"])
                    if out.get("error"):
                        errors.append(out["error"])
                    else:
                        model_ms.append(out["model_latency_ms"])
                    calls = ", ".join(s["tool"] for s in out["trace"]) or "-"
                    lines.append(f"  YOU: {text}")
                    lines.append(f"  ALEXA: {out['reply_text']}")
                    lines.append(f"     tools: {calls} | model {out['model_latency_ms']:.0f} ms")
                markdown, _, _ = await link.read_memory()
                records = {r.id: r for r in from_markdown(markdown)}
                problems = judge(n, replies, records) + [f"{e} error" for e in errors]
                passes[n] += not problems
                lines.append("  RESULT: " + ("PASS" if not problems else "FAIL - " + "; ".join(problems)))
        total = sum(passes.values())
        med = statistics.median(model_ms) if model_ms else float("nan")
        mx = max(model_ms) if model_ms else float("nan")
        per = "  ".join(f"#{n} {passes[n]}/{repeats}" for n in conversations)
        summary.append(f"{model:<44} {total:>2}/{repeats * len(conversations)} pass  [{per}]  "
                       f"model latency per turn: median {med:.0f} ms, max {mx:.0f} ms")
    return ["SUMMARY", *summary, *lines]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url")
    ap.add_argument("models", nargs="+")
    ap.add_argument("--token")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="proof/bedrock-eval.txt")
    ap.add_argument("--conversations", default="1,2,3,4,5", help="e.g. 1,2,3,4,5,6")
    ap.add_argument("--memory-file", help="server's local memories.md, for conversation 6")
    ap.add_argument("--wait", type=float, default=2.0, help="seconds after the hand edit")
    args = ap.parse_args()
    conversations = [int(c) for c in args.conversations.split(",")]
    lines = anyio.run(evaluate, args.url, args.token, args.models, args.repeats, conversations,
                      args.memory_file, args.wait)
    header = [f"# {datetime.now(timezone.utc).isoformat(timespec='seconds')} | golden conversations "
              f"{args.conversations} x{args.repeats} | region {os.getenv('AWS_REGION')} | server {args.url}",
              "# brain prompt: persona + server instructions + Agent Skill skills/portable-memory "
              "(loaded on first memory-tool use)"]
    text = "\n".join(header + lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print("\n".join(header + lines[: len(args.models) + 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
