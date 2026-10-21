"""Amazon Polly (SynthesizeSpeech only) with a local cache, plus ffmpeg/ffprobe helpers."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from . import config as C

_polly = None


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"{cmd[0]} failed:\n{proc.stderr[-3000:]}")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def speech(role: str, text: str) -> tuple[Path, float, str]:
    """(wav path, seconds, "Voice/engine") for `text` in the voice of `role` (vo, you, alexa)."""
    global _polly
    voice = C.VOICES[role]
    C.TTS_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{voice}|{text}".encode()).hexdigest()[:16]
    wav = C.TTS_CACHE / f"{role}-{key}.wav"
    meta = wav.with_suffix(".json")
    if not wav.exists():
        _polly = _polly or boto3.client("polly", region_name=C.AWS_REGION)
        for engine in ("generative", "neural"):
            try:
                r = _polly.synthesize_speech(Text=text, VoiceId=voice, Engine=engine,
                                             OutputFormat="mp3", SampleRate="24000")
                break
            except ClientError:
                if engine == "neural":
                    raise
        mp3 = wav.with_suffix(".mp3")
        mp3.write_bytes(r["AudioStream"].read())
        # Same loudness for every clip; the final mix is normalised again.
        run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af", "loudnorm=I=-18:TP=-2:LRA=11",
             "-ar", "48000", "-ac", "1", str(wav)])
        mp3.unlink()
        meta.write_text(json.dumps({"voice": voice, "engine": engine, "text": text}))
    info = json.loads(meta.read_text())
    return wav, duration(wav), f"{info['voice']} ({info['engine']})"
