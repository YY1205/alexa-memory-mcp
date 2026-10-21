"""Make the demo video from real runs of the local demo.

    DRIVE_FOLDER_ID=... .venv/bin/python -m video.make            # every scene, then the film
    DRIVE_FOLDER_ID=... .venv/bin/python -m video.make --scene 6  # re-record one scene, re-assemble
    .venv/bin/python -m video.make --assemble                     # re-edit from existing recordings

(`video/make.py` works too.) Outputs land in video/out/ (gitignored).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: video/make.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "video"

from playwright.sync_api import sync_playwright  # noqa: E402

from . import config as C  # noqa: E402
from . import record, render  # noqa: E402
from .compose import Piece, Timeline, assemble, render_scene  # noqa: E402
from .tts import duration, speech  # noqa: E402

PRODUCT_SCENES = (2, 3, 4, 5, 6, 7, 8)


def events(n: int) -> list[dict]:
    path = C.WORK / f"scene{n}" / "events.json"
    return json.loads(path.read_text()) if path.exists() else []


def first(evs: list[dict], kind: str, after: float = -1) -> dict | None:
    return next((e for e in evs if e["kind"] == kind and e["t"] > after), None)


class Editor:
    def __init__(self, browser):
        self.browser = browser
        self.ncap = 0

    def cap(self, tl: Timeline, speaker: str, text: str, start: float, end: float) -> None:
        self.ncap += 1
        png = render.caption(self.browser, speaker, text, C.WORK / f"scene{tl.scene}" / f"cap{self.ncap}.png")
        tl.caption(png, start, end)

    def turns(self, tl: Timeline, raw: Path, evs: list[dict], since: float = -1, last_end: float | None = None) -> None:
        """Typing (with the user's voice) -> 0.6 s of thinking -> reply (with Alexa's voice)."""
        starts = [e for e in evs if e["kind"] == "type_start" and e["t"] > since]
        for i, ts in enumerate(starts):
            sent = first(evs, "sent", ts["t"])
            reply = first(evs, "reply", ts["t"])
            ready = first(evs, "ready", ts["t"])
            typing = sent["t"] - ts["t"]
            s = tl.add(Piece(raw, ts["t"] - 0.25, sent["t"]))
            you_dur = tl.voice("you", ts["text"], s + 0.25, "You")
            tl.pieces[-1].hold = max(0.0, you_dur - typing + 0.15)
            tl.add(Piece(raw, sent["t"], sent["t"] + C.THINKING_KEEP))
            self.cap(tl, "you", ts["text"], s, tl.t)
            limit = starts[i + 1]["t"] - 0.3 if i + 1 < len(starts) else (last_end or ready["t"] + 1.0)
            r0, r1 = reply["t"] - 0.1, min(ready["t"] + 0.9, limit)
            s2 = tl.add(Piece(raw, r0, max(r1, r0 + 0.5)))
            dur = tl.voice("alexa", reply["text"], s2 + 0.15, "Alexa")
            tl.pieces[-1].hold = max(0.0, dur + 0.6 - (tl.pieces[-1].b - r0))
            self.cap(tl, "alexa", reply["text"], s2, tl.t)

    def hold_vo(self, tl: Timeline, raw: Path, at_src: float, text: str) -> None:
        """Narration over a held real frame."""
        s = tl.add(Piece(raw, at_src - 0.4, at_src))
        dur = tl.voice("vo", text, s + 0.2)
        tl.pieces[-1].hold = dur + 0.4


def build(n: int, ed: Editor) -> Timeline:
    sc, tl = C.SCENES[n], Timeline(n)
    raw = C.WORK / f"scene{n}" / "raw.webm"
    evs = events(n)
    if sc["kind"] == "cards":
        for i, line in enumerate(sc["vo"]):
            png = render.hook_card(ed.browser, line, C.WORK / f"scene{n}" / f"card{i}.png")
            _, d, _ = speech("vo", line)
            s = tl.add(Piece(png, hold=d + (0.9 if i == 0 else 0.5) + (0.6 if i == len(sc["vo"]) - 1 else 0),
                             framed=False))
            tl.voice("vo", line, s + (0.5 if i == 0 else 0.25))
    elif sc["kind"] == "closing":
        png = render.closing_card(ed.browser, C.WORK / f"scene{n}" / "closing.png")
        tl.add(Piece(png, hold=float(sc["target"]), framed=False))
        tl.voice("vo", sc["vo"][0], 0.8)
        tl.fade_out = 1.2
    elif sc["kind"] == "link":
        loaded, end = first(evs, "loaded"), first(evs, "end")
        s = tl.add(Piece(raw, loaded["t"] - 0.2, end["t"]))
        d = tl.voice("vo", sc["vo"][0], s + 0.4)
        tl.pieces[-1].hold = max(0.0, d + 1.0 - (end["t"] - loaded["t"] + 0.2))
    elif sc["kind"] == "talk":
        idle = first(evs, "idle")["t"]
        if sc.get("vo_pre"):
            ed.hold_vo(tl, raw, idle, sc["vo_pre"])
        ed.turns(tl, raw, evs, last_end=first(evs, "end")["t"])
        if sc.get("vo_post"):
            ed.hold_vo(tl, raw, first(evs, "end")["t"], sc["vo_post"])
    elif sc["kind"] == "hand_edit":
        if not evs:
            png = render.missing_card(ed.browser, "Scene 6 (hand edit) is not recorded yet. "
                                      "Run video/make.py --scene 6 at a terminal.", C.WORK / "scene6" / "missing.png")
            tl.add(Piece(png, hold=4.0, framed=False))
            return tl
        if C.USER_CLIP.exists():
            dur = duration(C.USER_CLIP)
            s = tl.add(Piece(C.USER_CLIP, 0.0, dur, crop=False))
            vo = tl.voice("vo", sc["vo_pre"], s + 0.3)
            # your clip plays as edited (video/user_clip.py); hold its last frame if the narration is longer
            tl.pieces[-1].hold = max(0.0, vo + 1.0 - dur)
        else:
            png = render.missing_card(ed.browser, "Your screen recording of the hand edit goes here "
                                      "(video/in/scene6_edit.mov is missing).", C.WORK / "scene6" / "missing.png")
            s = tl.add(Piece(png, hold=1.0, framed=False))
            tl.pieces[-1].hold = tl.voice("vo", sc["vo_pre"], s + 0.3) + 0.8
        seen = first(evs, "edit_seen")["t"]
        ed.turns(tl, raw, evs, since=seen, last_end=first(evs, "end")["t"])
        ed.hold_vo(tl, raw, first(evs, "end")["t"], sc["vo_post"])
    elif sc["kind"] == "export":
        idle = first(evs, "idle")["t"]
        ed.turns(tl, raw, evs, last_end=first(evs, "export_shown")["t"] + 0.5)
        shown, loaded = first(evs, "export_shown"), first(evs, "other_loaded")
        s = tl.add(Piece(raw, shown["t"], loaded["t"]))
        vo = tl.voice("vo", sc["vo"], s + 0.2)
        pasted, q0, q1, ans, end = (first(evs, k) for k in ("pasted", "q_type_start", "q_sent", "answer", "end"))
        tl.add(Piece(raw, loaded["t"], pasted["t"] + 1.0, crop=False))  # the other page is full width
        qs = tl.add(Piece(raw, q0["t"] - 0.2, q1["t"] + C.THINKING_KEEP, crop=False))
        ed.cap(tl, "you", C.OTHER_QUESTION, qs, tl.t)
        tl.srt.append({"start": qs, "end": tl.t, "text": f"You (to the other assistant): {C.OTHER_QUESTION}"})
        a = tl.add(Piece(raw, ans["t"] - 0.1, end["t"], crop=False))
        tl.pieces[-1].hold = max(0.0, (s + vo + 0.6) - tl.t, 1.5)
        ed.cap(tl, "other", ans["text"], a, tl.t)
        tl.srt.append({"start": a, "end": tl.t, "text": f"Other assistant (Nova Lite): {ans['text']}"})
    elif sc["kind"] == "montage":
        frames = montage_frames(ed.browser)
        d = tl.voice("vo", sc["vo"], 0.4)
        each = (d + 1.2) / len(frames)
        for png in frames:
            tl.add(Piece(png, hold=each, framed=False))
    return tl


def montage_frames(browser) -> list[Path]:
    d = C.WORK / "scene8"
    hood = d / "hood.png"
    if not hood.exists():
        raise SystemExit("scene 8 needs its live screenshot: run with --scene 8")
    ready = render.markdown_section(browser, C.ROOT / "docs" / "ALEXA-READINESS.md", "Authentication checklist",
                                    d / "readiness.png")
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=C.ROOT, capture_output=True, text=True)
    lines = [l for l in tests.stdout.splitlines() if l.strip()]
    pytest_png = render.terminal(browser, "Terminal: the real test run", ["$ python -m pytest -q", *lines[-4:]],
                                 d / "pytest.png")
    ev = (C.ROOT / "proof" / "bedrock-eval.txt").read_text().splitlines()
    ev4 = (C.ROOT / "proof" / "bedrock-eval-conv4.txt").read_text().splitlines()
    summary = [l for l in ev[:4] if l.strip()] + [""] + [l for l in ev4[2:4] if l.strip()]
    eval_png = render.terminal(browser, "proof/bedrock-eval.txt  (golden conversations, Amazon Nova Pro on Bedrock)",
                               summary, d / "eval.png")
    labels = ["Every MCP tool call, live, against the 500 ms Alexa+ budget",
              "OAuth 2.1 + PKCE, mapped to the Alexa+ checklist (docs/ALEXA-READINESS.md)",
              f"{lines[-1].strip('= ')} (pytest)" if lines else "pytest",
              "Golden conversations on Amazon Nova Pro, scored automatically"]
    return [render.montage_frame(browser, lab, img, d / f"frame{i}.png")
            for i, (lab, img) in enumerate(zip(labels, [hood, ready, pytest_png, eval_png]))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scene", type=int, action="append", help="re-record + re-edit only this scene (repeatable)")
    ap.add_argument("--assemble", action="store_true", help="only re-edit and assemble existing recordings")
    args = ap.parse_args()
    todo = args.scene or list(C.SCENES)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        if not args.assemble and any(n in PRODUCT_SCENES for n in todo):
            from .demo import Demo
            from .other_assistant import serve
            with Demo(C.WORK / "logs") as demo:
                other = serve() if 7 in todo else None
                if 2 in todo:
                    print("recording scene 2"); record.scene_link(browser, demo)
                record.ensure_linked(browser, demo)
                for n in todo:
                    if n in (3, 4, 5):
                        print(f"recording scene {n}"); record.scene_talk(browser, demo, n)
                    elif n == 6:
                        print("recording scene 6"); record.scene_hand_edit(browser, demo)
                    elif n == 7:
                        print("recording scene 7"); record.scene_export(browser, demo)
                    elif n == 8:
                        print("capturing scene 8"); record.scene_hood(browser, demo)
                if other:
                    other.shutdown()
        ed = Editor(browser)
        for n in (list(C.SCENES) if args.assemble else todo):
            print(f"editing scene {n}")
            render_scene(build(n, ed), C.WORK / f"scene{n}" / "scene.mp4")
        browser.close()
    total, lengths = assemble(list(C.SCENES))
    voices = sorted({v for n in C.SCENES for v in json.loads((C.WORK / f"scene{n}" / "voices.json").read_text())})
    print(f"\n{C.FINAL}  {total:.1f} s (limit {C.MAX_TOTAL} s)")
    for n, length in lengths.items():
        print(f"  scene {n} {C.SCENES[n]['title']:<28} {length:5.1f} s  (target {C.SCENES[n]['target']} s)")
    print("  voices:", "; ".join(voices))
    return 0 if total <= C.MAX_TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
