---
name: memory-steward
description: Use the Memory Steward MCP server as a voice assistant's long-term memory that the user owns (a Markdown file in their own storage). Use when the user asks about themselves, their plans, preferences or past decisions, asks what you know or remember about them, tells you something to remember, corrects a fact, asks what needs updating, or wants to export or share their memory. Covers confirm-before-save, one-answer updates, corrections, reviews, exports and how to speak results.
license: MIT
compatibility: Any agent connected to the Memory Steward MCP server (tools recall, propose_memory, supersede_memory, confirm_memory, list_due_reviews, resolve_review).
metadata:
  version: "1.0"
  mcp-server: "memory-steward"
---

# Memory Steward

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
- If a result has `error`, the action did **not** happen. Say its `spoken_answer` (e.g.
  "I couldn't save that.") and never claim it was saved, updated or done.

## Wait for the user's answer

Every change needs the user's own words first. When you ask a question ("Is that still
true?", a `confirmation_prompt`), end your turn there. Call `confirm_memory`,
`resolve_review` or any other tool that changes memory only in a later turn, after the
user has answered. Never assume, guess or fill in their answer, and never answer your
own question.

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
still true?" Then stop and wait: listing a review is not an answer to it. Call
`resolve_review` only with what the user says next.

- Still true -> `resolve_review(memory_id, still_true=true)` (optionally `review_in_days`).
- No longer true -> `resolve_review(memory_id, still_true=false)`.
- New information instead ("I got the job, I start in November") ->
  `resolve_review(memory_id, still_true=false)`, then `propose_memory` with the new fact
  and say its `confirmation_prompt`. On yes, `confirm_memory(pending_id, confirmed=true)`
  (the old note is already retired; if `replaces` comes back anyway, passing it is fine).

## Answering questions

- **About a topic** ("When is my sister's birthday?"): call `recall` with good search
  words ("sister birthday"). Answer from the returned memories only.
- **In general** ("What do you know about me?", "Do you have any memory of me?", "What have
  I told you?"): call `memory_overview` and say its `spoken_summary`. Don't use `recall`
  for these: keyword search finds nothing for them.
- **Never say you have no memory of the user without checking.** If `recall` finds nothing
  but `total_active` is above 0, say you don't have anything *about that topic* (and offer
  what you do know via `memory_overview`), not that you know nothing.

## Exporting and sharing

`export_memory` makes a Markdown snapshot the user can download, send to someone, or paste
into another AI assistant.

- **Never export more than asked.** Map the request to `categories` and/or `topic`:
  "share my food preferences" -> `categories: ["preference"], topic: "food"`; "export my
  plans" -> `categories: ["active_context"]`; "export my memory" -> no filter.
- If it's unclear what to include (e.g. "share some of my memory with my doctor"), ask which
  topics or categories first; don't guess broad.
- `include_history: true` only when the user asks for past or changed facts.
- Say the result's `spoken_summary` (and where the file is, if your device shows it), and
  nothing from the file: don't read, list or summarize the exported facts aloud. The user
  is sharing them with someone else, not asking to hear them.

## More detail

- Tool-by-tool contract and result fields: [references/TOOLS.md](references/TOOLS.md)
- Example dialogues with the exact tool calls: [references/DIALOGUES.md](references/DIALOGUES.md)
