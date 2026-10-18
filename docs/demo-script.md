# Demo script: Simulated Alexa+ with Portable Memory

The golden conversations for the demo video (< 3 minutes). All data is the fictional
persona "Sam" from `demo_data/seed.py`. Its dates are relative to the day it runs (Reset demo
re-seeds), so the demo looks the same on any day: the job interview is always 3 days overdue
for review, the kitchen contractor starts in 6 days, and the half marathon is 4 weeks out.
`ScriptedBrain` handles exactly these lines, so the demo also runs offline
(`SIM_BRAIN=scripted`). The server writes every read-back question itself, already in the
second person, so with Bedrock those questions are identical; other replies may be worded
differently, but the tool calls and memory-file changes should match.

Tests: `tests/test_simulator.py` runs conversations 0-6 end to end against a real server.

## Setup

```bash
SIM_BRAIN=scripted scripts/run_demo.sh     # or: BEDROCK_MODEL_ID=... AWS_REGION=... scripts/run_demo.sh
```

Open http://127.0.0.1:8001 in Chrome, click **Link account** and sign in on the memory
server's page with the owner passphrase the script prints (set `OWNER_PASSPHRASE` for a
recording). Linking can be part of the video: it is the Alexa account-linking step.
Click **Reset demo** before recording. Mic: click the
blue button and speak; or type and press Enter.

Show the three areas while talking: the device, **Under the hood** (each MCP call and its
latency against the 500 ms budget) and **Your memory file** (the real `memories.md`, read
through the MCP resource after every turn).

## 0. What do you know about me? (about 15 s)

| Speaker | Line |
|---|---|
| You | What do you know about me? |
| Alexa | I remember 10 things about you, including that you are learning Spanish and practice 15 minutes a day and that your dog Biscuit needs his heartworm pill on the first day of each month. One of them may need updating. |

- Under the hood: `memory_overview` (counts per category, newest memories, how many are due
  for review). A general question needs the overview, not keyword `recall`, which would find
  nothing and make Alexa wrongly say it knows nothing about you.
- Good opener for the video: it shows the memory exists before anything else happens.

## 1. Recall (about 15 s)

| Speaker | Line |
|---|---|
| You | When is my sister's birthday? |
| Alexa | Your sister Maya's birthday is on November 2. |

- Under the hood: `recall {"query": "When is my sister's birthday?"}`, a few ms of the 500 ms budget.
- Memory file: unchanged. Point at MEM-0004.

## 2. Save with confirmation (about 25 s)

| Speaker | Line |
|---|---|
| You | Remember that my dentist appointment is Friday at 3. |
| Alexa | Should I remember that your dentist appointment is Friday at 3? |
| You | Yes. |
| Alexa | Got it, I'll remember that. |

- Turn 1: `propose_memory`. The memory file shows a new **pending** record (dashed, amber):
  nothing is saved silently.
- Turn 2: `confirm_memory {"confirmed": true}`. The record turns **active**, "confirmed by you",
  with a review date a week out (temporary fact).

## 3. Correction (about 30 s)

| Speaker | Line |
|---|---|
| You | The kitchen contractor now starts on the 27th. |
| Alexa | This looks like an update to your note that you are renovating the kitchen and the contractor starts on *<6 days from today, e.g. October 28>*. Should I change it to: your kitchen contractor now starts on the 27th? |
| You | Yes. |
| Alexa | Got it, I've updated that. |

- Turn 1: `propose_memory` notices that the new fact is about the same thing as MEM-0006 and
  returns `replaces: "MEM-0006"` with a confirmation_prompt that asks about the change
  once, instead of storing a contradiction.
- Turn 2: one yes → `confirm_memory {"confirmed": true, "replaces": "MEM-0006"}` saves the
  new fact and retires the old one in a single call.
- Memory file: MEM-0006 becomes **historical** (struck through, "replaced by …"); the new
  record is active and says "replaces MEM-0006". History is kept, not deleted.

## 4. Review (about 35 s)

| Speaker | Line |
|---|---|
| You | Is there anything I should update? |
| Alexa | I have a note that you are waiting to hear back about a job interview at a design studio. Is that still true? |
| You | I got the job, I start in November. |
| Alexa | Thanks for the update. Should I remember that you got the job, you start in November? |
| You | Yes. |
| Alexa | Got it, I'll remember that. |

- Turn 1: `list_due_reviews` returns exactly one memory, MEM-0007 (its review date was 3 days
  ago; every other review date is in the future).
- Turn 2: `resolve_review {"memory_id": "MEM-0007", "still_true": false}` → MEM-0007 turns
  historical; `propose_memory` stages the new fact (pending).
- Turn 3: `confirm_memory` → the new fact is active.

## 5. Decline (about 15 s)

| Speaker | Line |
|---|---|
| You | Remember that I hate jazz. |
| Alexa | Should I remember that you hate jazz? |
| You | No. |
| Alexa | Okay, I won't remember that. |

- Turn 1: `propose_memory` (pending record appears).
- Turn 2: `confirm_memory {"confirmed": false}`: the pending record disappears. Nothing saved.

## 6. You own the file: edit it by hand (about 25 s)

Run the demo on Google Drive (`MEMORY_BACKEND=drive ... scripts/run_demo.sh`, refresh 5 s).

| Speaker | Line |
|---|---|
| (You) | Click **Open in Google Drive** in the memory panel and change the dog's name in `memories.md` from *Biscuit* to *Pepper*; save. |
| (wait) | About 5 seconds. MEM-0009 in the memory panel flashes "just changed" after the next turn. |
| You | When does my dog need his heartworm pill? |
| Alexa | Your dog Pepper needs his heartworm pill on the first day of each month. |

- The server noticed the new Drive file version (a metadata check every 5 s), downloaded the
  file and answered from it: the memory is the user's own file, not the assistant's.
- `recall` stays a few milliseconds; Drive is never on the tool-call path.
- How to edit a `.md` file in Drive: the Drive web page only previews Markdown. Use **Google
  Drive for desktop** (edit `memories.md` in the synced folder with any text editor; it
  syncs in seconds), or a Drive-connected text editor (*Open with* in Drive). Practise once
  before recording, and click **Reset demo** afterwards to restore *Biscuit*.
- Offline the same thing works with the local file (`tests/test_simulator.py::
  test_conversation_6_hand_edit_is_used`).

## Closing shot (about 10 s)

Switch the memory panel to **Markdown**: "This is the user's own file. Any assistant can read
it; none of them owns it."

## Timing budget

About 2 min 50 s of conversation plus intro/outro. Keep it under 3 minutes: if it runs long,
drop conversation 5 (decline) or shorten the closing shot.
