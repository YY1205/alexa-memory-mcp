# Memory Steward: tool contract

All tools return a JSON object. Errors come back as a normal result with `error` (details,
for logs) and `spoken_answer`, a plain sentence saying it did not work ("I couldn't save
that.", "I couldn't update that note.", "I couldn't look that up right now."). An error
means nothing changed: say `spoken_answer`, never claim success. Scopes: reading needs `memory.read`, every
tool that changes memory needs `memory.write`; without it you get
`error: "insufficient_scope: memory.write required"`.

## recall(query, category?)

Find current (active) memories relevant to a question. `category` is optional:
`profile`, `preference`, `active_context`, `project`, `decision`, `other`.

Returns: `found` (bool), `memories` (list, most relevant first: `id`, `fact`,
`category`, `status`, `confirmed_by_user`, `updated_at`, `review_after`),
`needs_review` (ids among them whose review date has passed), `spoken_answer`
(always null; you phrase the answer), `engine`, `total_active` (number of active memories
overall), and `note` when nothing matched although memories exist.

Retrieval is keyword-based: search words matter more than full sentences. An empty query
or a meta question ("what do you know about me") matches nothing: use `memory_overview`.

## memory_overview()

General picture of what is remembered. Returns `total_active`, `by_category` (counts),
`recent` (the three most recently updated memories, same fields as `recall`),
`due_for_review`, `pending`, `historical`, and `spoken_summary` (speakable, second person,
e.g. "I remember 10 things about you, including that ... One of them may need updating.").

## export_memory(categories?, include_history=false, topic?)

A shareable Markdown snapshot (read-only). `categories` is a list from `profile`,
`preference`, `active_context`, `project`, `decision`, `other` (default: all); `topic` is
keywords matched against facts and tags (e.g. `"food"`); `include_history` adds a
"Previously true" section.

You get a summary: `spoken_summary` ("Your memory export is ready: 1 memory about food."),
`count`, `history_count`, `categories`, `exported_on`. The file itself is **not** given to
you: it travels in the result's `_meta` (`memory_steward/export`: `markdown`, `mimeType`,
`filename`) for the client to show or download. It has a preamble saying it's an
owner-approved export with its date, then the active memories as "The owner ..." sentences
under category headings with their last-confirmed date; no ids, no pending records.
Unknown categories return an `error`. The latest full export is also the resource
`memory://export.md`.

## propose_memory(fact, category="other", review_in_days?)

Stage a fact. Nothing is saved yet.

- `fact`: one full sentence about the user in the third person.
- `review_in_days`: for temporary facts; the memory comes up in `list_due_reviews`
  after that many days.

Returns: `pending_id`, `confirmation_prompt` (speakable, second person),
`possibly_related` (other similar memories, for context), and `replaces` (id) when the
fact looks like an update to an existing memory.

A repeated proposal of the same fact, or a new proposal for the same target memory,
replaces the earlier unconfirmed one. Unconfirmed proposals expire after 24 hours.

## confirm_memory(pending_id, confirmed, replaces?)

Finish a proposal with the user's answer.

- `confirmed=false`: the proposal is discarded. `spoken_answer`: "Okay, I won't remember that."
- `confirmed=true`: saved as active and "confirmed by you".
- `replaces=<id>` (from `propose_memory`): the old memory becomes `historical`
  (kept, linked to the new one). `spoken_answer`: "Got it, I've updated that."

Call it only after the user has answered. If `replaces` is no longer a current memory
(for example it was just retired with `resolve_review`), the new fact is still saved on
its own and the result has a `note` saying so. Fails (with `spoken_answer`) if the pending
id is unknown or already handled.

## supersede_memory(old_memory_id, new_fact)

Stage an explicit correction of a known memory. Returns `pending_id` and
`confirmation_prompt` ("Should I change your note that ... to: ...?"). Finish with
`confirm_memory(pending_id, confirmed)`; on yes the old memory becomes historical.

## list_due_reviews()

Temporary memories whose review date has passed, oldest first: `count`, `memories`.

## resolve_review(memory_id, still_true, review_in_days?)

The user's answer to "is this still true?". Call it only after the user has answered in
their own words; `list_due_reviews` is not an answer. `still_true=true` keeps it (optionally with
a new review date); `false` retires it as historical.

## Resource: memory://memories.md

The whole memory file as stored (Markdown). Its `_meta` says where it lives
(`backend`: `local-file` or `google-drive`) and, for Drive, a `webViewLink` the user can
open. Read it when you need the full picture; prefer `recall` for answering questions.
