# Portable Memory for Alexa+

A self-hosted MCP server that gives Alexa+ a long-term memory **the user owns**.
The memory is a plain Markdown file (on Google Drive or local disk) that the user
can open, read and edit by hand. Assistants are clients of that file; none of them
owns it.

> Models are replaceable. Memory belongs to the user.

Built for the Alexa+ track of *Build, Ship, Shape: Amazon Developer Hackathon* (2026).

## Why voice needs different memory rules

| Problem | What this server does |
|---|---|
| Speech recognition mishears; assistants over-infer | **No silent writes.** `propose_memory` returns a sentence to read back; only `confirm_memory` saves. |
| Facts change ("the contractor moved to the 27th") | `supersede_memory` replaces a fact and keeps the old one as history. New proposals surface *possibly related* memories so Alexa can ask "does this replace…?" |
| Temporary facts go stale | Records can carry a review date. `recall` flags stale ones; `list_due_reviews` + `resolve_review` let Alexa ask "is this still true?" |
| Trust | Every record says whether the user confirmed it and when it last changed. |

## Tools

| Tool | Purpose |
|---|---|
| `recall(query, category?)` | Current memories relevant to a question (keyword search, served from memory) |
| `propose_memory(fact, category, review_in_days?)` | Stage a new fact; returns `confirmation_prompt` and `possibly_related` |
| `supersede_memory(old_memory_id, new_fact)` | Stage a correction |
| `confirm_memory(pending_id, confirmed)` | Save or discard a staged fact |
| `list_due_reviews()` | Temporary facts whose review date has passed |
| `resolve_review(memory_id, still_true, review_in_days?)` | Keep or retire a stale fact |

Resource: `memory://memories.md` (`text/markdown`), the canonical memory file as stored.

Transport: Streamable HTTP at `/mcp`, MCP protocol 2025-11-25 (Python SDK `mcp` 2.x).

## Simulated Alexa+ (the judged demo)

Hackathon participants can't deploy to real Alexa+ (the Alexa AI CLI, MCP Toolkit and
devices are partner-gated), so `simulator/` plays Alexa+ as a **real MCP client** of this
server: a web page styled like a smart display, with voice in (Web Speech API) and out
(speech synthesis), an *Under the hood* panel showing every MCP tool call and its latency
against the 500 ms budget, and *Your memory file* rendered live from the MCP resource.

```bash
SIM_BRAIN=scripted scripts/run_demo.sh      # offline, deterministic demo brain
# or with Amazon Bedrock as the "Alexa brain" (default AWS credential chain):
BEDROCK_MODEL_ID=us.amazon.nova-2-lite-v1:0 AWS_REGION=us-east-1 scripts/run_demo.sh
```

Then open http://127.0.0.1:8001 in Chrome. The golden conversations are in
[docs/demo-script.md](docs/demo-script.md).

## Run locally

```bash
pip install -r requirements.txt
python demo_data/seed.py          # fictional demo persona "Sam"
python server.py                  # http://127.0.0.1:8000/mcp
python -m pytest -q               # unit, end-to-end HTTP, latency and simulator tests
python scripts/bench.py http://127.0.0.1:8000/mcp   # p50/p95/max per tool (demo data only)
```

Inspect it with the MCP Inspector: `npx @modelcontextprotocol/inspector`, connect to
`http://127.0.0.1:8000/mcp` (Streamable HTTP).

## Configuration

See `.env.example`.

* **Google Drive backend** (`MEMORY_BACKEND=drive`): create a service account, create a
  *demo* folder in Drive, share only that folder with the service account e-mail, and set
  `DRIVE_FOLDER_ID` and `GOOGLE_APPLICATION_CREDENTIALS`. The server reads and writes
  `memories.md` in that folder.
* **No LLM in the request path.** Alexa+ does the language understanding and phrases the
  answer, so the server only does fast keyword retrieval over an in-memory cache. This
  keeps every tool call under the Alexa+ 500 ms budget.
* **Public deployment**: set `HOST=0.0.0.0`, `MCP_BEARER_TOKEN` and `ALLOWED_HOSTS`. The
  server refuses to listen publicly without a token.
* **Demo reset** (`DEMO_ADMIN=1`): enables `POST /admin/reset`, which restores the fictional
  demo data. Off by default; behind the bearer token when one is set.
* **Simulator** (`python -m simulator.app`, port 8001): `MCP_SERVER_URL`, `MCP_BEARER_TOKEN`,
  `SIM_BRAIN=bedrock|scripted`, `BEDROCK_MODEL_ID`, `AWS_REGION`. On a non-local host it
  requires `SIM_ADMIN_TOKEN`, which then protects *Reset demo*.

## AWS usage (AWS Builder mini challenge)

* **Amazon Bedrock Runtime, Converse API with tool use**: the simulator's "Alexa brain".
  - Call site: `simulator/brains.py`, `BedrockBrain.converse()`: `boto3`
    `bedrock-runtime` client, `client.converse(modelId=..., system=..., messages=...,
    toolConfig=...)`.
  - The tool loop is in `simulator/agent.py`, `Agent._turn()`: MCP `tools/list` schemas are
    converted to Bedrock `toolSpec`s (`simulator/mcp_link.py`, `to_bedrock_tool()`); each
    `toolUse` the model returns is executed on the MCP server and fed back as `toolResult`.
  - Config: `SIM_BRAIN=bedrock` (default), `BEDROCK_MODEL_ID`, `AWS_REGION`; credentials
    from the default AWS credential chain (never read or stored by the app).
  - Tested model: Amazon Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`, us-east-1).
  - Manual check: `scripts/bedrock_smoke.py` → `proof/bedrock-smoke.txt`.
* The MCP server itself never calls Bedrock (keeps tool calls < 500 ms; enforced by
  `tests/test_latency.py::test_server_never_imports_boto3`).
* Deployment target: AWS App Runner (container): *to be documented*

## Layout

```
server.py              MCP server, tool definitions, auth middleware
memory/schema.py       record fields + Markdown (de)serialisation
memory/store.py        local file and Google Drive backends
memory/cache.py        in-memory cache, Drive write-behind, refresh + merge
memory/service.py      memory rules (confirm, supersede, review)
retrieval/retrievers.py  keyword retrieval
demo_data/seed.py      fictional demo data
simulator/app.py       Simulated Alexa+ web app (Starlette, port 8001)
simulator/agent.py     Converse tool-use loop, per-session history, Alexa system prompt
simulator/brains.py    BedrockBrain (Converse) and ScriptedBrain (offline demo)
simulator/mcp_link.py  official MCP client wrapper, MCP schema -> Bedrock toolSpec
simulator/static/      the web page (plain HTML/CSS/JS)
scripts/run_demo.sh    start server + simulator on a temp copy of the demo data
scripts/bench.py       latency benchmark;  scripts/bedrock_smoke.py  Bedrock smoke check
docs/demo-script.md    golden demo conversations
proof/                 latency, Inspector and Bedrock evidence
tests/                 pytest suite
FRICTION_LOG.md        hackathon friction log
```

## Known limitations

* Single user per deployment (a `scope` field is reserved for household memory).
* Keyword retrieval only matches shared words; the client (Alexa+ or the simulator) is
  expected to turn the user's question into good search words.
* Bearer-token auth is a placeholder until the Alexa+ connection requirements are confirmed.
