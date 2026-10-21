"""Scenes, narration, voices and paths for the demo video (see video/README.md)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / "video"
OUT = VIDEO / "out"
WORK = OUT / "work"
TTS_CACHE = OUT / "tts"
USER_CLIP = VIDEO / "in" / "scene6_edit.mov"
FINAL = OUT / "memory-steward-demo.mp4"
SRT = OUT / "memory-steward-demo.srt"

W, H, FPS = 1920, 1080, 30
# Product footage is scaled into the top of the frame; captions get their own band below,
# so they never cover the real UI.
SHOT_SCALE = 0.9
SHOT_W, SHOT_H = int(W * SHOT_SCALE), int(H * SHOT_SCALE)  # 1728 x 972
CAPTION_BAND = H - SHOT_H                                  # 108 px
BG = "#0b1220"
TEAL, NAVY = "#0f766e", "#1e3a8a"  # the project's icon colours

SERVER_PORT, SIM_PORT, OTHER_PORT = 8020, 8021, 8022
MODEL_ID = "us.amazon.nova-pro-v1:0"
OTHER_MODEL_ID = "us.amazon.nova-lite-v1:0"
AWS_REGION = "us-east-1"
TYPE_DELAY_MS = 40
THINKING_KEEP = 0.6  # seconds of the "Thinking…" state kept from each model wait
REPO_URL = "github.com/YY1205/alexa-memory-mcp"

# Amazon Polly voices (generative engine; neural as fallback).
VOICES = {"vo": "Matthew", "you": "Danielle", "alexa": "Ruth"}
SPEAKER_LABEL = {"you": "You", "alexa": "Alexa", "other": "Other assistant (Nova Lite)"}

OTHER_QUESTION = "Is grilled salmon a good dinner idea for me?"

# target: the planned length in seconds (the report compares it with the real length).
SCENES: dict[int, dict] = {
    1: {"title": "Hook", "kind": "cards", "target": 15,
        "vo": ["Every voice assistant wants to remember you.",
               "But that memory lives on someone else's server, in a format you can't read, "
               "and it changes without asking.",
               "What if your assistant's memory belonged to you?"]},
    2: {"title": "Link account", "kind": "link", "target": 20,
        "vo": ["This is Memory Steward, an MCP server for Alexa+. Your memory is a plain Markdown "
               "file in your own Google Drive. Alexa links to it with OAuth, exactly like a real "
               "Alexa+ add-on. It manages your memory. You own it."]},
    3: {"title": "What do you know about me?", "kind": "talk", "target": 15,
        "turns": ["What do you know about me?"],
        "vo_post": "First, it actually knows what it knows."},
    4: {"title": "No silent writes", "kind": "talk", "target": 20,
        "turns": ["Remember that my dentist appointment is Friday at 3.", "Yes."],
        "vo_pre": "Rule one: no silent writes. Nothing is saved until you say yes. "
                  "Watch the file: pending, then active."},
    5: {"title": "One-yes update", "kind": "talk", "target": 25,
        "turns": ["The kitchen contractor now starts on the 27th.", "Yes."],
        "vo_post": "Rule two: facts change, history stays. One yes replaces the old fact, and the "
                   "old one is kept on the record instead of being deleted."},
    6: {"title": "Hand edit", "kind": "hand_edit", "target": 25,
        "turns": ["When does my dog need his heartworm pill?"],
        "vo_pre": "Rule three: it's really yours. I'm editing the file by hand, in a plain text editor.",
        "vo_post": "Seconds later, Alexa knows."},
    7: {"title": "Export", "kind": "export", "target": 20,
        "turns": ["Share my food preferences."],
        "vo": "Take your memory anywhere, and share only what you choose. The model never even "
              "sees the file. It goes straight to your screen."},
    8: {"title": "Under the hood", "kind": "montage", "target": 20,
        "turns": ["When is my sister's birthday?"],
        "vo": "Under the hood: a self-hosted MCP server over Streamable HTTP, OAuth 2.1 with PKCE "
              "built to the Alexa+ checklist, and every tool call in milliseconds against a 500 "
              "millisecond budget. The simulated Alexa runs on Amazon Nova Pro through Bedrock, "
              "and the memory rules ship as a portable Agent Skill."},
    9: {"title": "Closing", "kind": "closing", "target": 10,
        "vo": ["Memory Steward. It manages your memory. You own it."]},
}
MAX_TOTAL = 175  # 2:55
