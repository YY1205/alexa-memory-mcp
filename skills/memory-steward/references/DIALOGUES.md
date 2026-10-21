# Memory Steward: example dialogues

Ids are illustrative. Say `confirmation_prompt` exactly as returned.

## Answer a question

> User: When is my sister's birthday?

`recall({"query": "When is my sister's birthday?"})` -> MEM-0004 "The user's sister
Maya's birthday is on November 2."

> Assistant: Your sister Maya's birthday is on November 2.

## What do you know about me?

> User: What do you know about me?

`memory_overview({})` -> `total_active: 10`, `spoken_summary`.

> Assistant: I remember 10 things about you, including that you are learning Spanish and practice 15 minutes a day and that your dog Biscuit needs his heartworm pill on the first day of each month. One of them may need updating.

Not `recall({"query": "what do you know about me"})`: that finds nothing, and "I don't have
any memories of you" would be wrong.

## Save with confirmation

> User: Remember that my dentist appointment is Friday at 3.

`propose_memory({"fact": "The user's dentist appointment is Friday at 3.", "category": "active_context", "review_in_days": 7})`

> Assistant: Should I remember that your dentist appointment is Friday at 3?
> User: Yes.

`confirm_memory({"pending_id": "MEM-0011", "confirmed": true})`

> Assistant: Got it, I'll remember that.

## Update with one yes

> User: The kitchen contractor now starts on the 27th.

`propose_memory({"fact": "The user's kitchen contractor now starts on the 27th.", "category": "active_context"})`
-> `replaces: "MEM-0006"`

> Assistant: This looks like an update to your note that you are renovating the kitchen and the contractor starts on October 28. Should I change it to: your kitchen contractor now starts on the 27th?
> User: Yes.

`confirm_memory({"pending_id": "MEM-0011", "confirmed": true, "replaces": "MEM-0006"})`

> Assistant: Got it, I've updated that.

Keep both instead:

> User: No, keep both.

`confirm_memory({"pending_id": "MEM-0011", "confirmed": true})`

## Review with new information

> User: Is there anything I should update?

`list_due_reviews({})` -> MEM-0007 "The user is waiting to hear back about a job interview at a design studio.",
`review_question`: "I have a note that you are waiting to hear back about a job interview at a design studio. Is that still true?"

> Assistant: I have a note that you are waiting to hear back about a job interview at a design studio. Is that still true?

(No tool call here: the turn ends with the question. Wrong: calling `resolve_review` now,
before the user has answered.)

> User: I got the job, I start in November.

`resolve_review({"memory_id": "MEM-0007", "still_true": false})`, then
`propose_memory({"fact": "The user got the job, they start in November.", "category": "profile"})`

> Assistant: Thanks for the update. Should I remember that you got the job, you start in November?
> User: Yes.

`confirm_memory({"pending_id": "MEM-0011", "confirmed": true})`

## Decline

> User: Remember that I hate jazz.

`propose_memory({"fact": "The user hates jazz.", "category": "preference"})`

> Assistant: Should I remember that you hate jazz?
> User: No.

`confirm_memory({"pending_id": "MEM-0011", "confirmed": false})`

> Assistant: Okay, I won't remember that.

## Export only what was asked

> User: Share my food preferences.

`export_memory({"categories": ["preference"], "topic": "food"})` -> 1 memory ("The owner is
vegetarian and does not eat fish."), not the other preferences.

> Assistant: Your memory export is ready: 1 memory about food. It's on the screen, ready to download or copy.

> User: Share some of my memory with my new doctor.

Ambiguous: ask first, e.g. "Which parts should I include: your profile, preferences,
or current plans?" Then export just those.

## The user edited the file by hand

The file is the user's: they may change it in their own storage at any time. Always
answer from what `recall` returns now, not from earlier in the conversation.

> User: When does my dog need his heartworm pill?

`recall(...)` -> "The user's dog Pepper needs his heartworm pill on the first day of each month."

> Assistant: Your dog Pepper needs his heartworm pill on the first day of each month.
