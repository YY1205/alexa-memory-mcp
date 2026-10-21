"""Edit each scene from its real recording + event log, then assemble the film with ffmpeg.

A scene is a list of pieces (a trimmed span of a real clip, or a still), each optionally held
on its last frame, plus voice cues and caption overlays placed on the scene's own clock.
Long model waits are cut to THINKING_KEEP seconds of the real "Thinking…" state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C
from .tts import duration, run, speech


@dataclass
class Piece:
    src: Path
    a: float = 0.0
    b: float | None = None  # None: still image, shown for `hold` seconds
    hold: float = 0.0
    framed: bool = True     # product footage: placed in the frame above the caption band
    crop: bool = False      # crop the centre 1728x972 1:1 instead of scaling (unused by default)
    speed: float = 1.0

    @property
    def length(self) -> float:
        return (0 if self.b is None else (self.b - self.a) / self.speed) + self.hold


@dataclass
class Timeline:
    scene: int
    pieces: list[Piece] = field(default_factory=list)
    cues: list[tuple[Path, float]] = field(default_factory=list)
    captions: list[tuple[Path, float, float]] = field(default_factory=list)
    srt: list[dict] = field(default_factory=list)
    voices: set[str] = field(default_factory=set)
    fade_out: float = 0.0

    @property
    def t(self) -> float:
        return sum(p.length for p in self.pieces)

    def add(self, piece: Piece) -> float:
        start = self.t
        self.pieces.append(piece)
        return start

    def voice(self, role: str, text: str, at: float, label: str | None = None) -> float:
        wav, dur, used = speech(role, text)
        self.voices.add(f"{role}: {used}")
        self.cues.append((wav, at))
        self.srt.append({"start": at, "end": at + dur, "text": f"{label}: {text}" if label else text})
        return dur

    def caption(self, png: Path, start: float, end: float) -> None:
        self.captions.append((png, start, end))


def render_scene(tl: Timeline, out: Path) -> float:
    total = tl.t
    args = ["ffmpeg", "-y", "-v", "error"]
    chains, labels = [], []
    for i, p in enumerate(tl.pieces):
        if p.b is None:
            args += ["-loop", "1", "-framerate", str(C.FPS), "-t", f"{p.hold:.3f}", "-i", str(p.src)]
            chain = f"[{i}:v]fps={C.FPS},scale={C.W}:{C.H},setsar=1"
        else:
            args += ["-i", str(p.src)]
            chain = (f"[{i}:v]trim=start={p.a:.3f}:end={p.b:.3f},setpts=(PTS-STARTPTS)/{p.speed},fps={C.FPS}")
            if p.framed and p.crop:
                chain += (f",crop={C.SHOT_W}:{C.SHOT_H}:{(C.W - C.SHOT_W) // 2}:0,"
                          f"pad={C.W}:{C.H}:{(C.W - C.SHOT_W) // 2}:0:color={C.BG}")
            elif p.framed:
                chain += (f",scale={C.SHOT_W}:{C.SHOT_H}:force_original_aspect_ratio=decrease,"
                          f"pad={C.SHOT_W}:{C.SHOT_H}:(ow-iw)/2:(oh-ih)/2:color={C.BG},"
                          f"pad={C.W}:{C.H}:{(C.W - C.SHOT_W) // 2}:0:color={C.BG}")
            else:
                chain += f",scale={C.W}:{C.H}"
            chain += ",setsar=1"
            if p.hold > 0:
                chain += f",tpad=stop_mode=clone:stop_duration={p.hold:.3f}"
        chains.append(f"{chain}[p{i}]")
        labels.append(f"[p{i}]")
    n = len(tl.pieces)
    chains.append(f"{''.join(labels)}concat=n={n}:v=1:a=0[v0]")
    last = "v0"
    for k, (png, s, e) in enumerate(tl.captions):
        args += ["-loop", "1", "-framerate", str(C.FPS), "-t", f"{total:.3f}", "-i", str(png)]
        chains.append(f"[{last}][{n + k}:v]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'[v{k + 1}]")
        last = f"v{k + 1}"
    if tl.fade_out:
        chains.append(f"[{last}]fade=t=out:st={total - tl.fade_out:.3f}:d={tl.fade_out:.3f}[vf]")
        last = "vf"
    base = n + len(tl.captions)
    if tl.cues:
        for j, (wav, at) in enumerate(tl.cues):
            args += ["-i", str(wav)]
            chains.append(f"[{base + j}:a]adelay={int(at * 1000)}:all=1[a{j}]")
        chains.append(f"{''.join(f'[a{j}]' for j in range(len(tl.cues)))}amix=inputs={len(tl.cues)}:"
                      f"normalize=0:dropout_transition=0,apad[aout]")
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
        chains.append(f"[{base}:a]anull[aout]")
    args += ["-filter_complex", ";".join(chains), "-map", f"[{last}]", "-map", "[aout]",
             "-t", f"{total:.3f}", "-r", str(C.FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    run(args)
    (out.parent / "srt.json").write_text(json.dumps(tl.srt, indent=1))
    (out.parent / "voices.json").write_text(json.dumps(sorted(tl.voices)))
    return duration(out)


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def assemble(scenes: list[int]) -> tuple[float, dict[int, float]]:
    parts = [C.WORK / f"scene{n}" / "scene.mp4" for n in scenes]
    missing = [str(p) for p in parts if not p.exists()]
    if missing:
        raise SystemExit(f"Missing scene renders: {missing}")
    lengths = {n: duration(p) for n, p in zip(scenes, parts)}
    listing = C.WORK / "concat.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", str(C.FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", str(C.FINAL)])
    entries, offset = [], 0.0
    for n in scenes:
        for e in json.loads((C.WORK / f"scene{n}" / "srt.json").read_text()):
            entries.append((offset + e["start"], offset + e["end"], e["text"]))
        offset += lengths[n]
    entries.sort()
    C.SRT.write_text("".join(f"{i}\n{_ts(s)} --> {_ts(e)}\n{t}\n\n" for i, (s, e, t) in enumerate(entries, 1)))
    return duration(C.FINAL), lengths
