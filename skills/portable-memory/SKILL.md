---
name: portable-memory
description: Use the Portable Memory MCP server as a voice assistant's long-term memory that the user owns (a Markdown file in their own storage). Use when the user asks about themselves, their plans, preferences or past decisions, tells you something to remember, corrects a fact, or asks what needs updating. Covers confirm-before-save, one-answer updates, corrections, reviews and how to speak results.
license: MIT
compatibility: Any agent connected to the Portable Memory MCP server (tools recall, propose_memory, supersede_memory, confirm_memory, list_due_reviews, resolve_review).
metadata:
  version: "1.0"
  mcp-server: "portable-memory"
---

# Portable Memory

The user's memory is a file they own and can edit by hand. You are a client of it:
never save anything the user did not agree to out loud, and never pretend to remember
something the tools did not return.

## Speaking

- Reply in one or two short, natural sentences. No lists, markdown or emoji.
- Talk to the user as "you". Memories are stored in the third person ("The user's
  sister ..."); say them in the second person ("Your sister ...").
- Never read ids aloud (MEM-0004, pending ids).
- When a tool result has `confirmation_prompt`, say exactly that sentence, add
  nothing, and wait for the answer. It is already written for speech.
- If a result has `error`, say its `spoken_answer`.

## Remembering something new

1. Call `propose_memory` with the fact as a full third-person sentence ("The user's
   dentist appointment is Friday at 3."). Set `review_in_days` for temporary facts
   (plans, appointments, ongoing situations).
2. Say `confirmation_prompt`.
3. Call `confirm_memory` with the user's answer: yes -> `confirmed=true`,
   no -> `confirmed=false`.

Ask once. One answer settles it; never ask the user to confirm the same thing twice.

## Updates: one yes

If `propose_memory` returns `replaces`, the new fact looks like an update to that
existing memory, and `confirmation_prompt` already asks about the change
("This looks like an update to ... Should I change it to ...?").

- Yes -> `confirm_memory(pending_id, confirmed=true, replaces=<that id>)`. This saves
  the new fact and keeps the old one as history.
- "No, keep both" -> `confirm_memory(pending_id, confirmed=true)` without `replaces`.
- No -> `confirm_memory(pending_id, confirmed=false)`.

Do not call `supersede_memory` after a `replaces` proposal; the update is already staged.

## Explicit corrections

Use `supersede_memory(old_memory_id, new_fact)` only when the user corrects a specific
memory you just told them about (you know its id). Say its `confirmation_prompt`, then
`confirm_memory` with the answer.

## Reviews

When the user asks what needs updating, or `recall` returns `needs_review` ids, call
`list_due_reviews` and ask about the first memory: "I have a note that ... Is that
still true?"

- Still true -> `resolve_review(memory_id, still_true=true)` (optionally `review_in_days`).
- No longer true -> `resolve_review(memory_id, still_true=false)`.
- New information instead ("I got the job, I start in November") ->
  `resolve_review(memory_id, still_true=false)`, then `propose_memory` with the new fact
  and say its `confirmation_prompt`.

## Answering questions

Call `recall` with the user's question (or a few good search words). Answer from the
returned memories only. If nothing is found, say you don't have anything about that yet.

## More detail

- Tool-by-tool contract and result fields: [references/TOOLS.md](references/TOOLS.md)
- Example dialogues with the exact tool calls: [references/DIALOGUES.md](references/DIALOGUES.md)
