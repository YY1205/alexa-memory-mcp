# Privacy policy: Portable Memory

*Hackathon prototype, last updated October 16, 2026. Not reviewed by a lawyer.*

Portable Memory gives a voice assistant a long-term memory that **you own**: a plain
Markdown file in your own storage. This policy explains what data the Portable Memory
server and its web simulator handle, where it goes, and how to remove it.

Contact: {{CONTACT}}

## What is stored, and where

- **Your memories** are stored in one file, `memories.md`, in a Google Drive folder you
  choose (or on the server's local disk if the operator runs it that way). You can open,
  edit or delete that file at any time. The server reads and writes only that file, in
  the one folder you shared with it.
- **While the server runs**, it keeps a copy of the file's contents in memory so it can
  answer quickly. It keeps no other copy and no database.
- **Account linking** uses OAuth. The server stores only SHA-256 hashes of the access
  and refresh tokens it issues, never the tokens themselves. Access tokens expire after
  15 minutes; refresh tokens after 30 days (each use replaces them). The owner's sign-in
  passphrase is stored only as a scrypt hash.
- **Logs**: the server's request logs may record the time, path, status and network
  address of requests. They do not contain your memories or any tokens.

## What is sent to others

- **Alexa+ (Amazon).** When you use Portable Memory through Alexa+, Amazon processes
  your voice and requests under Amazon's own privacy notice. Alexa+ sends the memory
  tool calls (for example a question or a fact to remember) to this server and receives
  the results, which it uses to answer you.
- **Web simulator: Amazon Bedrock.** In the demo web simulator ("Simulated Alexa+"),
  what you say or type, the recent conversation and the memory results needed to answer
  are sent to Amazon Bedrock (AWS, US regions) to generate the replies, under the AWS
  terms of the account running the simulator. AWS states that model providers have no
  access to customer prompts and completions
  (<https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html>). The
  simulator keeps the conversation in memory only (the last 20 turns) and forgets it when
  you unlink, reset the demo, or the simulator restarts. Its OAuth tokens also stay in
  the simulator's memory and never reach your browser.
- **Your browser's speech features.** The simulator's microphone uses your browser's
  speech recognition; in Google Chrome, audio is sent to Google for recognition. Spoken
  replies use your browser's voices; online voices (such as Chrome's "Google" voices)
  send the reply text to that provider. You can type instead of speaking and mute the
  replies.
- **Google Drive.** Your file lives in Google's storage under your Google account and
  Google's policies. Drive keeps its own version history of the file.

Nothing is sold, shared for advertising, or used to train models by this project. There
are no analytics or tracking cookies.

## Unlinking and deleting your data

- **Unlink**: in the Alexa app, disable the Portable Memory add-on (or unlink the
  account); in the simulator, click **Unlink**. The app can then no longer read or change
  your memory. Leftover token hashes on the server stop working when they expire (at most
  30 days), or immediately if the operator clears the server's token store.
- **Delete your memories**: delete `memories.md` (or edit out what you want gone) in your
  Google Drive. Also delete old versions in Drive's version history if you want them gone.
- **Stop all access**: remove the server's service account from the folder's sharing
  settings in Google Drive.

## Children

Portable Memory is not directed to children under 13 and should not be used to store
information about children.

## Sensitive information

Don't store passwords, financial account numbers, health information or other
sensitive data in your memory. It is a hackathon prototype.

## Changes

This policy may change as the prototype changes; the date at the top shows the latest
version.
