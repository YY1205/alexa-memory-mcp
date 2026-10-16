# Portable Memory: tool contract

All tools return a JSON object. Errors come back as a normal result with `error` (for
logs) and `spoken_answer` (safe to say). Scopes: reading needs `memory.read`, every
tool that changes memory needs `memory.write`; without it you get
`error: "insufficient_scope: memory.write required"`.

## recall(query, category?)

Find current (active) memories relevant to a question. `category` is optional:
`profile`, `preference`, `active_context`, `project`, `decision`, `other`.

Returns: `found` (bool), `memories` (list, most relevant first: `id`, `fact`,
`category`, `status`, `confirmed_by_user`, `updated_at`, `review_after`),
`needs_review` (ids among them whose review date has passed), `spoken_answer`
(always null; you phrase the answer), `engine`.

Retrieval is keyword-based: search words matter more than full sentences.

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

Fails (speakably) if the pending id is unknown or already handled, or if `replaces`
is not a current memory.

## supersede_memory(old_memory_id, new_fact)

Stage an explicit correction of a known memory. Returns `pending_id` and
`confirmation_prompt` ("Should I change your note that ... to: ...?"). Finish with
`confirm_memory(pending_id, confirmed)`; on yes the old memory becomes historical.

## list_due_reviews()

Temporary memories whose review date has passed, oldest first: `count`, `memories`.

## resolve_review(memory_id, still_true, review_in_days?)

The user's answer to "is this still true?". `still_true=true` keeps it (optionally with
a new review date); `false` retires it as historical.

## Resource: memory://memories.md

The whole memory file as stored (Markdown). Its `_meta` says where it lives
(`backend`: `local-file` or `google-drive`) and, for Drive, a `webViewLink` the user can
open. Read it when you need the full picture; prefer `recall` for answering questions.
