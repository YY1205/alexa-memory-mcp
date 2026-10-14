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
| `recall(query, category?)` | Current memories relevant to a question, plus a short spoken answer (Bedrock) |
| `propose_memory(fact, category, review_in_days?)` | Stage a new fact; returns `confirmation_prompt` and `possibly_related` |
| `supersede_memory(old_memory_id, new_fact)` | Stage a correction |
| `confirm_memory(pending_id, confirmed)` | Save or discard a staged fact |
| `list_due_reviews()` | Temporary facts whose review date has passed |
| `resolve_review(memory_id, still_true, review_in_days?)` | Keep or retire a stale fact |

Transport: Streamable HTTP at `/mcp`, MCP protocol 2025-11-25 (Python SDK `mcp` 2.x).

## Run locally

```bash
pip install -r requirements.txt
python demo_data/seed.py          # fictional demo persona "Sam"
python server.py                  # http://127.0.0.1:8000/mcp
python -m pytest -q               # unit tests + an end-to-end HTTP test
```

Inspect it with the MCP Inspector: `npx @modelcontextprotocol/inspector`, connect to
`http://127.0.0.1:8000/mcp` (Streamable HTTP).

## Configuration

See `.env.example`.

* **Google Drive backend** (`MEMORY_BACKEND=drive`): create a service account, create a
  *demo* folder in Drive, share only that folder with the service account e-mail, and set
  `DRIVE_FOLDER_ID` and `GOOGLE_APPLICATION_CREDENTIALS`. The server reads and writes
  `memories.md` in that folder.
* **Amazon Bedrock** (`RETRIEVER=bedrock`): set `AWS_REGION` and `BEDROCK_MODEL_ID`. The
  Converse API picks relevant records and phrases the spoken answer. On any Bedrock error
  the server falls back to keyword search, so a demo never dead-ends.
* **Public deployment**: set `HOST=0.0.0.0`, `MCP_BEARER_TOKEN` and `ALLOWED_HOSTS`. The
  server refuses to listen publicly without a token.

## AWS usage (AWS Builder mini challenge)

* Amazon Bedrock Runtime, Converse API — `retrieval/retrievers.py`
* Deployment target: AWS App Runner (container) — *to be documented*

## Layout

```
server.py              MCP server, tool definitions, auth middleware
memory/schema.py       record fields + Markdown (de)serialisation
memory/store.py        local file and Google Drive backends
memory/service.py      memory rules (confirm, supersede, review)
retrieval/retrievers.py  keyword and Bedrock retrieval
demo_data/seed.py      fictional demo data
tests/                 pytest suite
FRICTION_LOG.md        hackathon friction log
```

## Known limitations

* Single user per deployment (a `scope` field is reserved for household memory).
* Keyword retrieval only matches shared words; use Bedrock for real conversations.
* Bearer-token auth is a placeholder until the Alexa+ connection requirements are confirmed.
