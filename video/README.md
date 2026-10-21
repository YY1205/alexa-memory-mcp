# Demo video pipeline

Makes `video/out/memory-steward-demo.mp4` (1920x1080, 30 fps, H.264/AAC) and
`memory-steward-demo.srt` from **real runs of the local demo**. It uses the product exactly
as it is (code freeze, `v1.4-submission`) and lives only in `video/`.

**Honesty rules:**
- Every product shot is a real Playwright recording of the real simulator and server: Google
  Drive backend, Amazon Nova Pro. No UI, reply or result is mocked.
- Long model waits are cut down to 0.6 s of the real "Thinking…" state.
- Only the narration, the voices and the captions are synthesised:
  - Amazon Polly speaks the narrator, "You" and Alexa.
  - Alexa's audio comes from the reply text captured in that run.
- A missing clip becomes a title card that says it's missing.

## Setup (once)

```bash
brew install ffmpeg
.venv/bin/python -m pip install -r requirements-video.txt
.venv/bin/python -m playwright install chromium
```

AWS: the default credential chain (`aws login`) needs:
- `polly:SynthesizeSpeech`;
- Bedrock access to Nova Pro (the simulator's brain) and Nova Lite (scene 7).

The Drive backend needs:
- `DRIVE_FOLDER_ID`: the demo folder;
- `GOOGLE_APPLICATION_CREDENTIALS`: defaults to `~/.config/alexa-memory-mcp/service-account.json`.

## Run

```bash
DRIVE_FOLDER_ID=... .venv/bin/python video/make.py              # all scenes, then the film
DRIVE_FOLDER_ID=... .venv/bin/python video/make.py --scene 5    # re-record one scene, re-assemble
.venv/bin/python video/make.py --assemble                       # re-edit from existing recordings
```

The pipeline starts its own demo: memory server on `:8020`, simulator on `:8021`, and the
scene 7 page on `:8022`. It uses a fresh OAuth setup and a random owner passphrase that is
kept in memory only. That passphrase is typed into the masked sign-in field and is never
printed or logged.

## Scene 6: your hand edit

Scene 6 needs you at a terminal, so the full run skips it unless stdin is a terminal:

1. Start screen recording (QuickTime: File > New Screen Recording).
2. Run `DRIVE_FOLDER_ID=... .venv/bin/python video/make.py --scene 6`.
3. When it prints "Edit memories.md in TextEdit now (Biscuit -> Pepper), save, then press
   Enter", edit the demo `memories.md` and save it. The file has to reach Google Drive,
   e.g. through Google Drive for desktop.
4. Stop recording and save the clip as `video/in/scene6_edit.mov`.
5. Press Enter. The pipeline waits until the server sees "Pepper", asks the question, and
   re-assembles the film with your clip.

If you record the clip later, put it in `video/in/` and run `video/make.py --assemble`.

## Files

| File | What it does |
|---|---|
| `config.py` | Scenes, narration, voices, ports, target timings |
| `demo.py` | Starts the real server and simulator (Drive, Nova Pro) |
| `record.py` | Playwright scene recordings and event logs (`out/work/sceneN/`) |
| `other_assistant.py` | Scene 7: a page that sends only the pasted export and the question to Nova Lite |
| `tts.py` | Amazon Polly with a cache and loudness normalisation |
| `render.py` | Title cards (SVG), caption overlays, montage frames |
| `compose.py` | Turns event logs into edits; one ffmpeg call per scene, then the film and the `.srt` |
| `make.py` | The command line |
