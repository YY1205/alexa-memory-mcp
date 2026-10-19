# Portable Memory for Alexa+

A self-hosted MCP server that gives Alexa+ a long-term memory **the user owns**.
The memory is a plain Markdown file (on Google Drive or local disk) that the user
can open, read and edit by hand. Assistants are clients of that file; none of them
owns it.

> Models are replaceable. Memory belongs to the user.

Built for the Alexa+ track of *Build, Ship, Shape: Amazon Developer Hackathon* (2026).

## Live demo (until Nov 21, 2026)

| | |
|---|---|
| Simulated Alexa+ (judges start here) | https://pm-eed93c7c206e48b49a65ec4b4273adb3.ecs.us-east-1.on.aws |
| Portable Memory MCP server | https://pm-2cbfc2377dac4f9b8a15cc6219028368.ecs.us-east-1.on.aws/mcp |
| Privacy policy / terms | https://pm-2cbfc2377dac4f9b8a15cc6219028368.ecs.us-east-1.on.aws/privacy, https://pm-2cbfc2377dac4f9b8a15cc6219028368.ecs.us-east-1.on.aws/terms |

Step-by-step instructions for judges: [docs/TESTING.md](docs/TESTING.md) (the passcode and
the demo owner passphrase are in the private Devpost testing instructions). Hosted on AWS
ECS Express Mode; see [docs/DEPLOY-PLAN.md](docs/DEPLOY-PLAN.md).

## Why voice needs different memory rules

| Problem | What this server does |
|---|---|
| Speech recognition mishears; assistants over-infer | **No silent writes.** `propose_memory` returns a sentence to read back; only `confirm_memory` saves. |
| Facts change ("the contractor moved to the 27th") | `propose_memory` spots that a new fact updates an existing one and asks once ("This looks like an update to … Should I change it to …?"); one yes saves the new fact and keeps the old one as history. Repeated or abandoned proposals don't pile up. |
| Temporary facts go stale | Records can carry a review date. `recall` flags stale ones; `list_due_reviews` + `resolve_review` let Alexa ask "is this still true?" |
| Trust | Every record says whether the user confirmed it and when it last changed. |

## Tools

| Tool | Purpose |
|---|---|
| `recall(query, category?)` | Current memories relevant to a question (keyword search, served from memory); always reports `total_active` so "no match" never looks like "no memory" |
| `export_memory(categories?, include_history?, topic?)` | A shareable Markdown snapshot (only what the user asked for) for people or other AI assistants; also the resource `memory://export.md` |
| `memory_overview()` | General questions ("What do you know about me?"): counts per category, newest memories, how many need review, a speakable `spoken_summary` |
| `propose_memory(fact, category, review_in_days?)` | Stage a new fact; returns a speakable `confirmation_prompt`, and `replaces` when it looks like an update to an existing memory |
| `supersede_memory(old_memory_id, new_fact)` | Stage an explicit correction of a memory whose id is known |
| `confirm_memory(pending_id, confirmed, replaces?)` | Save or discard a staged fact; with `replaces`, one yes saves the update and keeps the old fact as history |
| `list_due_reviews()` | Temporary facts whose review date has passed |
| `resolve_review(memory_id, still_true, review_in_days?)` | Keep or retire a stale fact |

Resource: `memory://memories.md` (`text/markdown`), the canonical memory file as stored.

Transport: Streamable HTTP at `/mcp`, MCP protocol 2025-11-25 or later (Python SDK `mcp` 2.x).

Auth: OAuth 2.1 account linking built for the Alexa+ checklist: auth code + PKCE (S256) +
`resource`, one pre-registered client, scopes `memory.read` / `memory.write`, 401 without
`WWW-Authenticate`, RFC 9728 and RFC 8414 metadata. See
[docs/ALEXA-READINESS.md](docs/ALEXA-READINESS.md) for every requirement and its proof.

## Simulated Alexa+ (the judged demo)

Hackathon participants can't deploy to real Alexa+ (the Alexa AI CLI, MCP Toolkit and
devices are partner-gated), so `simulator/` plays Alexa+ as a **real MCP client** of this
server: a web page styled like a smart display, with voice in (Web Speech API) and out
(speech synthesis), an *Under the hood* panel showing every MCP tool call and its latency
against the 500 ms budget, and *Your memory file* rendered live from the MCP resource.

```bash
SIM_BRAIN=scripted scripts/run_demo.sh      # offline, deterministic demo brain
# memory in your Google Drive demo folder instead of a local temp file:
MEMORY_BACKEND=drive DRIVE_FOLDER_ID=<folder id> \
  GOOGLE_APPLICATION_CREDENTIALS=~/.config/alexa-memory-mcp/service-account.json \
  SIM_BRAIN=scripted scripts/run_demo.sh
# or with Amazon Bedrock as the "Alexa brain" (default AWS credential chain):
BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0 AWS_REGION=us-east-1 scripts/run_demo.sh
```

Then open http://127.0.0.1:8001 in Chrome, click **Link account** and sign in on the memory
server's page with the owner passphrase the script prints (exactly like linking an account in
the Alexa app; tokens stay in the simulator). The golden conversations are in
[docs/demo-script.md](docs/demo-script.md).

## Sharing your memory with other assistants

**Today: export.** "Share my food preferences" (or the **Export** button in the memory
panel) produces a short Markdown file: a preamble that says it's an owner-approved export
and when it was made, then the chosen memories as plain sentences under category headings.
No ids, no pending or internal fields, and only the categories/topic the owner picked. Paste
it into any AI assistant or send it to a person; it's a **one-way snapshot**, readable by
anything that reads text.

**Roadmap: live sharing over MCP.** Because the memory is an MCP server with OAuth scopes,
other assistants could link to the same file directly (read-only or read-write, per
assistant), so a fact confirmed in one place is current everywhere. That multi-client work
is out of scope for the hackathon.

## Agent Skill

[`skills/portable-memory/`](skills/portable-memory/SKILL.md) is an
[Agent Skill](https://agentskills.io/specification) that teaches any agent to use this server
well: confirm before saving and read `confirmation_prompt` verbatim, one-yes updates with
`replaces` (or keep both), `supersede_memory` only for explicit corrections, review
handling, short second-person replies, never reading ids aloud. `SKILL.md` stays short;
the tool contract and example dialogues are in `references/` and loaded on demand.

The server's own MCP `instructions` stay the minimal, model-agnostic contract (what real
Alexa+ sees). The simulator loads the skill the way agent runtimes do
(`simulator/skills.py`): the name and description are always in the system prompt, the
`SKILL.md` body is added the first time a session uses the memory tools (shown as "skill
loaded" in *Under the hood*), and reference files are read through a local
`read_skill_reference` tool when needed. With the skill as the only source of the rules,
Nova Pro passes all six golden conversations 3/3 (`proof/bedrock-eval.txt`).

## Alexa+ listing, privacy and terms

* `addon-package/addon.json`: draft add-on manifest using only the documented fields;
  regenerate for a deployment with `python scripts/make_addon.py --base-url https://<host>`.
* `addon-package/assets/`: original icon (six sizes) and carousel image, served at
  `/addon-assets/`; re-export with `scripts/render_assets.sh`.
* `/privacy` and `/terms` are served by the MCP server from [docs/privacy.md](docs/privacy.md)
  and [docs/terms.md](docs/terms.md) (set `PRIVACY_CONTACT`).
* What's documented vs. still open: [docs/ALEXA-READINESS.md](docs/ALEXA-READINESS.md).

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

* **Google Drive backend** (`MEMORY_BACKEND=drive`): create a service account (enable the
  Drive API in its project), create a *demo* folder in Drive, share only that folder with
  the service account's e-mail as Editor, **upload `memories.md` yourself** (for example
  `demo_data/memories.md`), and set `DRIVE_FOLDER_ID` and `GOOGLE_APPLICATION_CREDENTIALS`
  (path to the key; keep it outside the repo). A service account can't own files in a
  personal Drive, so the server never creates the file: it fails at startup if it's missing,
  and only updates its content. Writes go out in the background (2 s debounce, at most 10 s);
  every `MEMORY_REFRESH_SECONDS` (default 30, demo 5) it checks Drive's file `version` and
  downloads only when the file changed, so hand edits show up without any tool call waiting
  on Drive. The simulator links to the file ("Open in Google Drive").
  Real-Drive checks: `scripts/drive_smoke.py` → `proof/drive-smoke.txt`; latency with Drive:
  `proof/latency-drive.txt`.
* **No LLM in the request path.** Alexa+ does the language understanding and phrases the
  answer, so the server only does fast keyword retrieval over an in-memory cache. This
  keeps every tool call under the Alexa+ 500 ms budget.
* **OAuth 2.1** (`auth/`): `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_REDIRECT_URIS`
  (the client, e.g. Alexa's account-linking redirect URIs), `OWNER_PASSPHRASE_HASH` (create
  it with `python -m auth.passphrase`; the passphrase itself is never stored),
  `MCP_RESOURCE_URI` (canonical `https://<host>/mcp`), optional `OAUTH_ISSUER`,
  `OAUTH_TOKEN_STORE` (default `.oauth/tokens.json`, hashes only), `OAUTH_ACCESS_TTL` (900 s).
* **Public deployment**: set `HOST=0.0.0.0`, the OAuth variables, an `https://`
  `MCP_RESOURCE_URI` and `ALLOWED_HOSTS`. The server refuses to listen publicly without
  OAuth. `MCP_BEARER_TOKEN` is a localhost-only dev token and is refused on a public host.
* **Demo reset** (`DEMO_ADMIN=1`): enables `POST /admin/reset`, which restores the fictional
  demo data. Off by default; free from the same machine, otherwise it needs an access token
  with `memory.write`.
* **Simulator** (`python -m simulator.app`, port 8001): `MCP_SERVER_URL`, `OAUTH_CLIENT_ID`,
  `OAUTH_CLIENT_SECRET`, `SIM_PUBLIC_URL` (redirect URI = `SIM_PUBLIC_URL/oauth/callback`),
  `SIM_BRAIN=bedrock|scripted`, `BEDROCK_MODEL_ID`, `AWS_REGION`. On a non-local host it
  requires `SIM_ADMIN_TOKEN`, which then protects *Reset demo* and *Unlink*.
* **Dev token for tools**: `scripts/oauth_token.py` runs the real OAuth flow headlessly and
  prints an access token for `scripts/bench.py`, the MCP Inspector or the Bedrock scripts.

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
  - Demo model: Amazon Nova Pro (`us.amazon.nova-pro-v1:0`, us-east-1). Nova Pro and Nova 2
    Lite both pass all five golden conversations 3/3 (`scripts/eval_bedrock.py` →
    `proof/bedrock-eval.txt`); Nova Pro reads the server's questions exactly.
  - Manual check: `scripts/bedrock_smoke.py` → `proof/bedrock-smoke.txt`.
* The MCP server itself never calls Bedrock (keeps tool calls < 500 ms; enforced by
  `tests/test_latency.py::test_server_never_imports_boto3`).
* **Deployment**: Amazon ECS Express Mode (Fargate ARM64 + Application Load Balancer with
  ACM certificates), images in Amazon ECR, secrets in AWS Systems Manager Parameter Store
  (SecureString), logs in CloudWatch, cost guard in AWS Budgets. The simulator's task role
  may only call `bedrock:InvokeModel` on Nova Pro; no static AWS keys anywhere.
  Plan, costs and teardown: [docs/DEPLOY-PLAN.md](docs/DEPLOY-PLAN.md); what was created:
  [docs/DEPLOY-LOG.md](docs/DEPLOY-LOG.md).

## Layout

```
server.py              MCP server, tool definitions, per-tool scopes
auth/                  OAuth 2.1: authorization server, token store, client helpers
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
scripts/run_demo.sh    start server + simulator (local temp copy of the demo data, or Drive)
scripts/drive_smoke.py  real Google Drive checks (write-behind, outside edit, conflict merge)
scripts/bench.py       latency benchmark;  scripts/bedrock_smoke.py  Bedrock smoke check
scripts/eval_bedrock.py  golden conversations x N on Bedrock models, pass rate + latency
docs/demo-script.md    golden demo conversations
docs/ALEXA-READINESS.md  Alexa+ requirements -> code and tests
docs/privacy.md, terms.md  served at /privacy and /terms
skills/portable-memory/  Agent Skill (SKILL.md + references/)
addon-package/         draft addon.json and listing assets
public_pages.py        /privacy, /terms, /addon-assets/
proof/                 latency, Inspector and Bedrock evidence
tests/                 pytest suite
FRICTION_LOG.md        hackathon friction log
```

## Known limitations

* Single user per deployment (a `scope` field is reserved for household memory).
* Keyword retrieval only matches shared words; the client (Alexa+ or the simulator) is
  expected to turn the user's question into good search words.
* One owner and one OAuth client per deployment. Not yet tried against real Alexa+ account
  linking (partner-gated); see [docs/ALEXA-READINESS.md](docs/ALEXA-READINESS.md).
