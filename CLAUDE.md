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
  (README "AWS usage" section). Bedrock is being moved out of the server into the web
  simulator (Oct 15); until then no code calls it, so it must land before submission.
  Keep that section accurate (App Runner deployment is still "to be documented").

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
```
