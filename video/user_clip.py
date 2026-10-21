"""Cut the owner's own screen recording of the scene 6 hand edit down to the useful moments.

    .venv/bin/python -m video.user_clip   # video/in/scene6_original.mov -> video/in/scene6_edit.mov

Only trims, one fixed crop and speed changes: nothing is added. The crop keeps just the
TextEdit window, so the terminal (command line, Drive folder id, user name, paths), the
desktop and any notifications stay out of the frame. Times were picked by looking at the
frames of this recording (see video/README.md).
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "video"

from . import config as C  # noqa: E402
from .tts import duration, run  # noqa: E402

SOURCE = C.VIDEO / "in" / "scene6_original.mov"
CROP = (1312, 844, 224, 220)  # w, h, x, y: the TextEdit window, still from 30.5 s to 61 s
SEGMENTS = [  # (start, end, speed)
    (32.5, 35.5, 1.0),   # memories.md open in TextEdit
    (37.6, 44.6, 2.0),   # find "bis", Biscuit highlighted, then selected
    (44.6, 49.8, 1.5),   # Biscuit deleted, "Pepper" typed
    (53.6, 56.0, 1.0),   # saved: the title loses "— Edited" at ~54.7 s
]


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"missing {SOURCE}")
    w, h, x, y = CROP
    chains = [f"[0:v]trim=start={a}:end={b},setpts=(PTS-STARTPTS)/{s},crop={w}:{h}:{x}:{y},fps={C.FPS}[s{i}]"
              for i, (a, b, s) in enumerate(SEGMENTS)]
    chains.append("".join(f"[s{i}]" for i in range(len(SEGMENTS))) + f"concat=n={len(SEGMENTS)}:v=1:a=0[v]")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(SOURCE), "-filter_complex", ";".join(chains), "-map", "[v]",
         "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(C.USER_CLIP)])
    print(f"{C.USER_CLIP}  {duration(C.USER_CLIP):.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
