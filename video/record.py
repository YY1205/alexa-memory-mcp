"""Record each scene from the real running demo with Playwright (headless Chromium, 1920x1080).

Every scene is its own clip (work/sceneN/raw.webm) with an event log (events.json) of the
moments the editor needs: typing started, utterance sent, reply shown, memory panel reloaded.
Nothing on screen is mocked: the page is the real simulator talking to the real server.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import Browser, Page

from . import config as C
from .demo import Demo

# The simulator's own "mute spoken replies" setting (Polly voices replace the browser's).
MUTE = f"""if (location.port === "{C.SIM_PORT}") {{ try {{ localStorage.setItem("sim-muted", "1"); }} catch (e) {{}} }}"""


class Clip:
    """A recorded page plus its event log (seconds since the recording started)."""

    def __init__(self, browser: Browser, scene: int, record: bool = True, pixel_ratio: float = 1):
        self.dir = C.WORK / f"scene{scene}"
        self.dir.mkdir(parents=True, exist_ok=True)
        opts = {"viewport": {"width": C.W, "height": C.H}, "device_scale_factor": pixel_ratio}
        if record:
            opts |= {"record_video_dir": str(self.dir / "tmp"), "record_video_size": {"width": C.W, "height": C.H}}
        self.context = browser.new_context(**opts)
        self.context.add_init_script(MUTE)
        self.page: Page = self.context.new_page()
        self.t0 = time.monotonic()
        self.events: list[dict] = []
        self.record = record

    def ev(self, kind: str, **data) -> None:
        self.events.append({"t": round(time.monotonic() - self.t0, 3), "kind": kind, **data})

    def close(self) -> None:
        video = self.page.video
        self.context.close()
        if self.record and video:
            shutil.move(video.path(), self.dir / "raw.webm")
            shutil.rmtree(self.dir / "tmp", ignore_errors=True)
        (self.dir / "events.json").write_text(json.dumps(self.events, indent=1))


# ---------------------------------------------------------------- page helpers
def open_sim(clip: Clip, demo: Demo) -> None:
    p = clip.page
    p.goto(demo.sim_url)
    p.wait_for_function("document.getElementById('link-chip').textContent.startsWith('account: ') && "
                        "!document.getElementById('link-chip').textContent.includes('…')")


def wait_memory(p: Page) -> None:
    p.wait_for_function("document.getElementById('mem-meta').textContent.includes('read over MCP')", timeout=60000)


def reset(clip: Clip) -> None:
    p = clip.page
    p.click("#reset-btn")
    p.wait_for_function("document.getElementById('conversation').textContent.includes('Demo data restored')",
                        timeout=60000)
    wait_memory(p)


def say(clip: Clip, text: str) -> str:
    """Type the utterance (visible typing), send it, wait for the reply and the memory reload."""
    p = clip.page
    count = "document.querySelectorAll('#conversation .bubble.alexa, #conversation .bubble.error').length"
    before = p.evaluate(count)
    p.click("#text-input")
    clip.ev("type_start", text=text)
    p.locator("#text-input").press_sequentially(text, delay=C.TYPE_DELAY_MS)
    clip.ev("sent", text=text)
    p.keyboard.press("Enter")
    p.wait_for_function(f"{count} > {before}", timeout=120000)
    reply_el = p.locator("#conversation .bubble.alexa, #conversation .bubble.error").last
    kind = "error" if "error" in (reply_el.get_attribute("class") or "") else "alexa"
    reply = reply_el.inner_text()
    clip.ev("reply", text=reply, bubble=kind)
    if kind == "error":
        raise SystemExit(f"The simulator answered with an error bubble: {reply!r}")
    p.wait_for_function("document.getElementById('state-label').textContent === 'Ready'", timeout=60000)
    clip.ev("ready")
    return reply


def link_account(clip: Clip, demo: Demo, slow: bool = True) -> None:
    p = clip.page
    p.click("#link-btn")
    p.wait_for_selector("#pp")
    clip.ev("signin_page")
    p.wait_for_timeout(1500 if slow else 0)
    p.click("#pp")
    # Masked field: the passphrase is never visible on screen and never logged.
    p.locator("#pp").press_sequentially(demo.passphrase, delay=70 if slow else 0)
    p.wait_for_timeout(700 if slow else 0)
    clip.ev("allow")
    p.click("button[value=approve]")
    p.wait_for_url(f"{demo.sim_url}/**")
    p.wait_for_function("document.getElementById('link-chip').textContent === 'account: linked'", timeout=30000)
    wait_memory(p)
    clip.ev("linked")


def is_linked(browser: Browser, demo: Demo) -> bool:
    import httpx
    return httpx.get(f"{demo.sim_url}/api/status", timeout=20).json()["account"]["linked"]


def ensure_linked(browser: Browser, demo: Demo) -> None:
    """Link through the real sign-in page (not recorded) when scene 2 isn't being recorded."""
    if is_linked(browser, demo):
        return
    clip = Clip(browser, 0, record=False)
    open_sim(clip, demo)
    link_account(clip, demo, slow=False)
    clip.context.close()


# ---------------------------------------------------------------- scenes
def scene_link(browser: Browser, demo: Demo) -> None:
    if is_linked(browser, demo):
        raise SystemExit("Scene 2 needs an unlinked simulator (a fresh demo run).")
    clip = Clip(browser, 2)
    open_sim(clip, demo)
    clip.ev("loaded")
    clip.page.wait_for_timeout(2500)
    link_account(clip, demo)
    clip.page.wait_for_timeout(2000)
    clip.ev("markdown_view")
    clip.page.click(".seg-btn[data-view=raw]")
    clip.page.wait_for_timeout(2500)
    clip.page.click(".seg-btn[data-view=cards]")
    clip.page.wait_for_timeout(1500)
    clip.ev("end")
    clip.close()


def scene_talk(browser: Browser, demo: Demo, n: int) -> None:
    clip = Clip(browser, n)
    open_sim(clip, demo)
    reset(clip)
    clip.page.wait_for_timeout(1200)
    clip.ev("idle")
    clip.page.wait_for_timeout(1200)
    for i, text in enumerate(C.SCENES[n]["turns"]):
        if i:
            clip.page.wait_for_timeout(600)
        say(clip, text)
    clip.page.wait_for_timeout(1800)
    clip.ev("end")
    clip.close()


def scene_hand_edit(browser: Browser, demo: Demo) -> bool:
    """Needs you at the keyboard: you edit memories.md by hand while this waits."""
    if not sys.stdin.isatty():
        print("Scene 6 needs you at a terminal (hand edit): run `video/make.py --scene 6` yourself.")
        return False
    clip = Clip(browser, 6)
    open_sim(clip, demo)
    reset(clip)
    clip.page.wait_for_timeout(4000)  # the reset reaches Drive (write-behind) before you edit
    clip.ev("idle")
    print("\nEdit memories.md in TextEdit now (Biscuit -> Pepper), save, then press Enter.")
    print("(The file is the demo memories.md in your Google Drive folder; it now shows Biscuit.)")
    input()
    clip.ev("enter_pressed")
    deadline = time.monotonic() + 180
    while "Pepper" not in demo.memory_markdown():
        if time.monotonic() > deadline:
            clip.close()
            raise SystemExit("The server never saw 'Pepper' in memories.md (3 min). Was the edit saved to Drive?")
        time.sleep(1)
    clip.ev("edit_seen")
    clip.page.wait_for_timeout(800)
    for text in C.SCENES[6]["turns"]:
        say(clip, text)
    clip.page.wait_for_timeout(1800)
    clip.ev("end")
    clip.close()
    return True


def scene_export(browser: Browser, demo: Demo) -> None:
    clip = Clip(browser, 7)
    clip.context.grant_permissions(["clipboard-read", "clipboard-write"])
    p = clip.page
    open_sim(clip, demo)
    reset(clip)
    p.wait_for_timeout(1000)
    clip.ev("idle")
    for text in C.SCENES[7]["turns"]:
        say(clip, text)
    p.wait_for_selector("#export-preview:not([hidden])")
    clip.ev("export_shown")
    exported = p.inner_text("#export-preview")
    (clip.dir / "export.md").write_text(exported)
    p.wait_for_timeout(2200)
    p.click("#export-copy")
    p.wait_for_timeout(900)
    clip.ev("copied", status=p.inner_text("#export-status"))
    p.goto(f"http://127.0.0.1:{C.OTHER_PORT}/")
    p.wait_for_selector("#file")
    clip.ev("other_loaded")
    p.wait_for_timeout(1000)
    p.click("#file")
    p.keyboard.press("ControlOrMeta+V")  # the real clipboard, if headless Chromium has one
    p.wait_for_timeout(300)
    method = "clipboard"
    if p.input_value("#file").strip() != exported.strip():
        p.fill("#file", exported)  # same text, the export read from the simulator's panel
        method = "fill"
    clip.ev("pasted", method=method)
    p.wait_for_timeout(1200)
    p.click("#q")
    clip.ev("q_type_start", text=C.OTHER_QUESTION)
    p.locator("#q").press_sequentially(C.OTHER_QUESTION, delay=C.TYPE_DELAY_MS)
    clip.ev("q_sent")
    p.click("#ask")
    p.wait_for_selector("#answer[data-done]", timeout=120000)
    answer = p.inner_text("#answer")
    clip.ev("answer", text=answer)
    p.wait_for_timeout(3000)
    clip.ev("end")
    clip.close()


def scene_hood(browser: Browser, demo: Demo) -> None:
    """Scene 8's live screenshot: the Under the hood panel after a real turn."""
    clip = Clip(browser, 8, record=False, pixel_ratio=2)  # a sharp still, shown large
    open_sim(clip, demo)
    reset(clip)
    for text in C.SCENES[8]["turns"]:
        say(clip, text)
    clip.page.wait_for_timeout(500)
    clip.page.locator("section.panel").first.screenshot(path=str(clip.dir / "hood.png"))
    clip.close()
