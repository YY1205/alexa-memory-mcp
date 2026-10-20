# Testing Memory Steward (for judges)

*It manages your memory. You own it.*

Real Alexa+ add-on tooling is partner-gated, so the judged experience is **Simulated
Alexa+**, a web app that talks to the Memory Steward server exactly like Alexa+ would: it
links your account with OAuth 2.1 and calls the server's MCP tools over Streamable HTTP.
The "Alexa brain" is Amazon Nova Pro on Amazon Bedrock. All data is a fictional persona,
"Sam".

| What | URL |
|---|---|
| Simulated Alexa+ (start here) | https://pm-eed93c7c206e48b49a65ec4b4273adb3.ecs.us-east-1.on.aws |
| Memory Steward MCP server | https://pm-2cbfc2377dac4f9b8a15cc6219028368.ecs.us-east-1.on.aws/mcp |
| Privacy policy / terms | …/privacy and …/terms on the MCP server |

You need two values from the **private testing instructions on Devpost**:
the **demo passcode** (to open the simulator) and the **demo owner passphrase** (to link
the account, like signing in during Alexa account linking).

Use desktop Chrome for voice. Typing works in any browser.

## 1. Open the simulator (1 minute)

1. Open the simulator URL and enter the **demo passcode**.
2. If the header shows **account: linked**, skip to step 3. Otherwise click **Link
   account**. You are sent to the memory server's "Link your memory" page: leave both
   permissions ticked, enter the **demo owner passphrase**, click **Allow**. You come back
   to the simulator with "Account linked".
3. Click **Reset demo** to start from Sam's fresh memory file.

## 2. Talk to it (about 4 minutes)

Click the blue microphone and speak, or type and press Enter. Say each line, wait for the
answer, then say the next.

| # | You say | What should happen |
|---|---|---|
| 0 | What do you know about me? | "I remember 10 things about you, including ..." One `memory_overview` call (counts per category, newest memories, what needs review). |
| 1 | When is my sister's birthday? | "Your sister Maya's birthday is on November 2." One `recall` call. |
| 2 | Remember that my dentist appointment is Friday at 3. -> Yes. | It reads the fact back first. A **pending** record (dashed, amber) appears in the memory file; after "Yes." it turns **active**. Nothing is saved silently. |
| 3 | The kitchen contractor now starts on the 27th. -> Yes. | It notices this updates the kitchen note and asks **once**. One "Yes." saves the new fact; the old one stays as **historical** (struck through, "replaced by ..."). |
| 4 | Is there anything I should update? -> I got the job, I start in November. -> Yes. | It finds the overdue job-interview note and **asks** whether it's still true (nothing changes until you answer). Your news retires it, then it offers to save the new fact. |
| 5 | Remember that I hate jazz. -> No. | Nothing is saved; the pending record disappears. |
| 7 | Share my food preferences. | An export of **only** the food fact opens in the memory panel (Copy / Download .md). Alexa just says it's ready; the model never even receives the file. |

Wording of Alexa's replies varies (it's a real model); the memory-file changes are the
thing to check.

## 3. What to look at

* **Under the hood**: every MCP tool call with its arguments, the result (expand
  "Result"), and its round-trip time drawn against Alexa+'s **500 ms** budget (typically a
  few ms on the server plus network). "skill loaded" marks when the Memory Steward Agent
  Skill was added to the model's instructions. Model time is shown separately.
* **Your memory file**: the real `memories.md`, read through the MCP resource after every
  turn. Changed records flash; pending and historical records look different from active
  ones. **Markdown** shows the raw file: this is the file the user owns.
* **Export** (button in the memory panel): pick categories (all ticked by default) and
  whether to include history, **Preview**, then **Copy** or **Download .md**. Paste the file
  into any other AI assistant and ask it something about Sam: e.g. after exporting food
  preferences, "I'm cooking dinner for this person. Is grilled salmon a good idea?"
  This one-way snapshot is how the memory travels to other assistants today.
* **Privacy**: the memory server's `/privacy` explains what goes where.

## 4. Optional: check the MCP server directly

* Without a token, `POST /mcp` returns **401 with no `WWW-Authenticate` header** (Alexa+
  requirement): `curl -i -X POST <server>/mcp`
* OAuth metadata: `<server>/.well-known/oauth-protected-resource` and
  `<server>/.well-known/oauth-authorization-server` (PKCE S256, no dynamic registration).
* Evidence for every Alexa+ requirement: [docs/ALEXA-READINESS.md](ALEXA-READINESS.md);
  public latency: [proof/latency-public.txt](../proof/latency-public.txt).

## Good to know

* **Shared demo**: all judges use the same simulator, linked account and memory file. If it
  looks odd, click **Reset demo**.
* **Limits**: about 10 requests per minute per person and a daily cap on model calls; if you
  hit one, the simulator says so politely. Please try again a little later.
* **Restarts**: if the service restarts, memory goes back to Sam's seed and the account must
  be linked again (step 1.2). This is by design for a public demo.
* **Microphone**: Chrome's speech recognition sends audio to Google; you can type instead.
  The speaker icon mutes spoken replies.
* The demo runs until **November 21, 2026**.
