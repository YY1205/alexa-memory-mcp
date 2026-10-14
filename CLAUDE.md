# CLAUDE.md

## Project

`alexa-memory-mcp` ("Portable Memory for Alexa+") is a self-hosted MCP server that gives
Alexa+ a long-term memory the user owns. The memory is a plain Markdown file (local disk
or Google Drive) that the user can read and edit by hand; assistants are clients of that
file, not its owner. Because voice is error-prone, writes are never silent:
`propose_memory` / `supersede_memory` stage a fact and return a read-back sentence, and
only `confirm_memory` saves it. Temporary facts carry review dates (`list_due_reviews`,
`resolve_review`). `recall` is fast keyword search over an in-memory cache; no LLM runs
in the server's request path (Alexa+ does NLU and phrasing). Solo entry for the
Alexa+ track of the "Build, Ship, Shape: Amazon Developer Hackathon" (deadline
Oct 23, 2026, 12:00 PM PT). See README.md for tools, config and layout.

## Hackathon rules that affect code

- MCP spec **2025-11-25 or later**, over **Streamable HTTP** (`/mcp`). Python MCP SDK 2.x
  (`mcp.server.mcpserver.MCPServer`; FastMCP was renamed in 2.x).
- The repo must **actually call** the required tech in code, not just mention it.
- Demo video must be **under 3 minutes**.
- Also targeting the **AWS Builder mini challenge**: Bedrock usage must be documented
  (README "AWS usage" section). Bedrock Converse is called in `simulator/brains.py`
  (`BedrockBrain.converse`), never by the MCP server. Keep that section accurate (App
  Runner deployment is still "to be documented").

## Alexa+ constraints and plan

- Participants have **no access** to the Alexa AI CLI, MCP Toolkit or devices
  (partner-gated). The track allows a simulated Alexa+ experience via a web app, so the
  judged demo is a **web simulator that is a real MCP client** of this server.
- Alexa+ requirements the server must meet:
  * Streamable HTTP, MCP spec 2025-11-25+.
  * Round-trip tool latency **< 500 ms** (proof: `scripts/bench.py`, `tests/test_latency.py`,
    `proof/latency-local.txt`). Keep storage and LLM calls out of the request path:
    reads come from the in-memory cache (`memory/cache.py`), Drive writes are write-behind.
  * Auth: OAuth 2.1 authorization code + PKCE (S256); unauthenticated requests get
    **401 without a `WWW-Authenticate` header**; Protected Resource Metadata per
    RFC 9728; AS metadata at `/.well-known/oauth-authorization-server`.
  * Alexa+ does NLU and response generation itself; the server returns standard MCP
    results (no LLM in the server).
- Simulator (`simulator/`, port 8001): a real MCP client (official Python client, a
  short-lived connection per turn). The page is plain HTML/CSS/JS (`simulator/static/`, no
  build step, must work in Chrome). Brain: `SIM_BRAIN=bedrock` (Converse tool loop) or
  `scripted` (deterministic, offline). `ScriptedBrain` must keep handling exactly the
  golden conversations in `docs/demo-script.md`; `tests/test_simulator.py` checks them. If
  you change a golden line, change all three together.
- No logos or trademarks on the page; call it "Simulated Alexa+".
- Plan (2026):
  * Oct 14: server latency (cache, write-behind, Bedrock out of the server). Done.
  * Oct 15-16: web simulator with Amazon Bedrock as the "Alexa brain". Done early, Oct 14 (Bedrock
    untested until AWS credentials and BEDROCK_MODEL_ID are set up).
  * Oct 17-18: OAuth 2.1 account linking.
  * Oct 19: real Google Drive, Agent Skill, addon.json.
  * Oct 20: deploy. Oct 21: polish/docs. Oct 22: video + submit.

## Working rules

- **Never use real personal data.** Demo data is the fictional persona "Sam" in
  `demo_data/seed.py`. Never point the demo at real memory.
- **Never commit secrets**: `.env`, `service-account*.json`, tokens. Check `.gitignore`
  before committing; `.env.example` holds placeholders only.
- **All tests must pass before every commit.**
- Whenever a tool, SDK or doc causes friction, **add a row to `FRICTION_LOG.md`**
  (it earns a judging bonus).
- Keep commits small, with clear messages.

## Commands

```bash
# setup (Python 3.10+)
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python demo_data/seed.py     # reset demo_data/memories.md to the "Sam" persona

# run server (http://127.0.0.1:8000/mcp; config via env, see .env.example)
.venv/bin/python server.py

# run tests (unit + end-to-end Streamable HTTP test)
.venv/bin/python -m pytest -q

# latency benchmark against a running server (writes benchmark records: demo data only)
.venv/bin/python scripts/bench.py http://127.0.0.1:8000/mcp --token "$MCP_BEARER_TOKEN"

# demo: memory server :8000 + Simulated Alexa+ :8001 on a temp copy of the demo data
SIM_BRAIN=scripted scripts/run_demo.sh                       # offline
BEDROCK_MODEL_ID=... AWS_REGION=... scripts/run_demo.sh      # Bedrock brain

# Bedrock smoke check (manual, costs money): conversations 1-2 -> proof/bedrock-smoke.txt
.venv/bin/python scripts/bedrock_smoke.py http://127.0.0.1:8000/mcp --token "$MCP_BEARER_TOKEN"
```

Never read, print or store AWS credentials; rely on the default credential chain.
