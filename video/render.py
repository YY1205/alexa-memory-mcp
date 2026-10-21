"""Stills rendered with headless Chromium: title cards (from SVG), caption overlays, and the
scene 8 montage frames (real screenshots, the real readiness doc, real pytest/eval output)."""
from __future__ import annotations

import base64
import html
import re
import textwrap
from pathlib import Path

from playwright.sync_api import Browser

from . import config as C

ICON = (C.ROOT / "addon-package" / "assets" / "icon.svg").read_text()
ICON_URI = "data:image/svg+xml;base64," + base64.b64encode(ICON.encode()).decode()
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"


def _shot(browser: Browser, markup: str, out: Path, transparent: bool = False) -> Path:
    page = browser.new_page(viewport={"width": C.W, "height": C.H})
    page.set_content(markup)
    page.wait_for_timeout(150)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out), omit_background=transparent)
    page.close()
    return out


def _svg_page(svg: str) -> str:
    return f'<html><body style="margin:0;background:#000">{svg}</body></html>'


def _lines(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width)


def hook_card(browser: Browser, text: str, out: Path) -> Path:
    """Scene 1: black card, white text, the icon and a teal accent."""
    lines = _lines(text, 34)
    y0 = C.H / 2 - (len(lines) - 1) * 44
    tspans = "".join(f'<text x="{C.W / 2}" y="{y0 + i * 88}" text-anchor="middle">{html.escape(l)}</text>'
                     for i, l in enumerate(lines))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{C.W}" height="{C.H}" viewBox="0 0 {C.W} {C.H}">
  <rect width="100%" height="100%" fill="#000"/>
  <image href="{ICON_URI}" x="{C.W / 2 - 36}" y="150" width="72" height="72"/>
  <rect x="{C.W / 2 - 60}" y="{C.H - 230}" width="120" height="6" rx="3" fill="{C.TEAL}"/>
  <g fill="#fff" font-family="{FONT}" font-size="64" font-weight="600">{tspans}</g>
</svg>"""
    return _shot(browser, _svg_page(svg), out)


def closing_card(browser: Browser, out: Path) -> Path:
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{C.W}" height="{C.H}" viewBox="0 0 {C.W} {C.H}">
  <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{C.TEAL}"/><stop offset="1" stop-color="{C.NAVY}"/></linearGradient></defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <image href="{ICON_URI}" x="{C.W / 2 - 110}" y="190" width="220" height="220"/>
  <g fill="#fff" font-family="{FONT}" text-anchor="middle">
    <text x="{C.W / 2}" y="560" font-size="104" font-weight="700">Memory Steward</text>
    <text x="{C.W / 2}" y="650" font-size="48" opacity=".95">It manages your memory. You own it.</text>
    <text x="{C.W / 2}" y="930" font-size="30" opacity=".8">{C.REPO_URL}</text>
  </g>
</svg>"""
    return _shot(browser, _svg_page(svg), out)


def missing_card(browser: Browser, text: str, out: Path) -> Path:
    """Honest placeholder when a real clip doesn't exist yet."""
    lines = _lines(text, 48)
    tspans = "".join(f'<text x="{C.W / 2}" y="{480 + i * 64}" text-anchor="middle">{html.escape(l)}</text>'
                     for i, l in enumerate(lines))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{C.W}" height="{C.H}">
  <rect width="100%" height="100%" fill="#2a1414"/>
  <g fill="#ffd9d9" font-family="{FONT}" font-size="46">{tspans}</g></svg>"""
    return _shot(browser, _svg_page(svg), out)


def short(text: str, limit: int = 300) -> str:
    """A caption-sized version: Markdown marks removed; past `limit`, the first sentence + ' …'
    (the full text stays on screen and in the .srt)."""
    text = re.sub(r"\*\*|__|`", "", " ".join(text.split()))
    if len(text) <= limit:
        return text
    m = re.match(r"(.+?[.!?])\s", text)
    return (m.group(1) if m and len(m.group(1)) <= limit else text[:limit].rsplit(" ", 1)[0]) + " …"


def caption(browser: Browser, speaker: str, text: str, out: Path) -> Path:
    """Transparent full-frame PNG with the caption in the band under the product footage."""
    text = short(text)
    size = 30 if len(text) < 170 else 25 if len(text) < 260 else 21
    label = html.escape(C.SPEAKER_LABEL.get(speaker, speaker))
    color = {"you": "#9fd3ff", "alexa": "#7ee0c3", "other": "#d7c3ff"}.get(speaker, "#fff")
    markup = f"""<html><body style="margin:0;background:transparent">
<div style="position:absolute;left:0;right:0;bottom:0;height:{C.CAPTION_BAND}px;display:flex;
 align-items:center;justify-content:center;padding:0 80px;box-sizing:border-box;font:{size}px/1.25 {FONT};
 color:#fff;text-align:center"><div><b style="color:{color}">{label}:</b> {html.escape(text)}</div></div>
</body></html>"""
    return _shot(browser, markup, out, transparent=True)


def montage_frame(browser: Browser, label: str, image: Path, out: Path) -> Path:
    data = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
    markup = f"""<html><body style="margin:0;background:{C.BG};font-family:{FONT}">
<div style="height:96px;display:flex;align-items:center;justify-content:center;color:#cfe8e3;font-size:36px;
 font-weight:600">{html.escape(label)}</div>
<div style="height:{C.H - 96 - 30}px;display:flex;align-items:center;justify-content:center">
<img src="{data}" style="height:{C.H - 96 - 40}px;max-width:1820px;object-fit:contain;border-radius:10px;
 box-shadow:0 10px 40px rgba(0,0,0,.5)"></div></body></html>"""
    return _shot(browser, markup, out)


def terminal(browser: Browser, title: str, lines: list[str], out: Path) -> Path:
    body = "\n".join(html.escape(l) for l in lines)
    body = re.sub(r"(\d+ passed|\d+/\d+ pass|PASS)", r'<span style="color:#7ee787">\1</span>', body)
    markup = f"""<html><body style="margin:0;background:#1d1f21;width:1700px">
<div style="background:#2d2f33;color:#aaa;font:18px {FONT};padding:10px 18px">{html.escape(title)}</div>
<pre style="margin:0;padding:26px 30px;color:#e6e6e6;font:22px/1.5 Menlo,monospace;white-space:pre-wrap">{body}</pre>
</body></html>"""
    page = browser.new_page(viewport={"width": 1700, "height": 400})
    page.set_content(markup)
    page.screenshot(path=str(out), full_page=True)
    page.close()
    return out


def _inline(text: str) -> str:
    t = html.escape(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)


def markdown_section(browser: Browser, md_path: Path, heading: str, out: Path) -> Path:
    """Render one '## heading' section of a Markdown file (headings, paragraphs, tables)."""
    text = md_path.read_text()
    start = text.index(f"## {heading}")
    end = text.find("\n## ", start + 3)
    rows, parts = [], [f"<h2>{_inline(heading)}</h2>"]
    for line in text[start:end if end > 0 else None].splitlines()[1:]:
        if line.startswith("|"):
            if not re.match(r"^\|[-| :]+\|$", line):
                rows.append([c.strip() for c in line.strip("|").split("|")])
            continue
        if rows:
            head, *body = rows
            parts.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>" +
                         "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body) +
                         "</table>")
            rows = []
        if line.strip():
            parts.append(f"<p>{_inline(line)}</p>")
    if rows:  # a table that ends the section
        head, *body = rows
        parts.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>" +
                     "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body) +
                     "</table>")
    markup = f"""<html><body style="margin:0;background:#fff;width:1800px;font:15px/1.35 {FONT};color:#1f2328">
<div style="padding:24px 34px"><div style="color:#57606a;font-size:14px">docs/ALEXA-READINESS.md</div>
<style>h2{{font-size:26px;margin:6px 0 12px}} table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #d0d7de;padding:5px 9px;vertical-align:top;text-align:left}} th{{background:#f6f8fa}}
code{{background:#eff1f3;padding:0 4px;border-radius:4px;font-size:13px}} p{{margin:6px 0}}</style>
{''.join(parts)}</div></body></html>"""
    page = browser.new_page(viewport={"width": 1800, "height": 600})
    page.set_content(markup)
    page.screenshot(path=str(out), full_page=True)
    page.close()
    return out
