"""Public pages of the MCP server: /privacy, /terms (rendered from docs/*.md, the single
source) and /addon-assets/* (icons and images referenced by the Alexa+ listing)."""
from __future__ import annotations

import html
import os
import re
from pathlib import Path

from starlette.responses import HTMLResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

HERE = Path(__file__).parent
DOCS = HERE / "docs"
ASSETS = HERE / "addon-package" / "assets"
_PAGE_LINKS = {"privacy.md": "/privacy", "terms.md": "/terms"}


def _inline(text: str) -> str:
    """Escape, then the inline Markdown our legal docs use: links, <urls>, **b**, *i*, `code`."""
    out = html.escape(text, quote=True)

    def link(m):
        label, url = m.group(1), html.unescape(m.group(2))
        url = _PAGE_LINKS.get(url, url)
        if not (url.startswith(("https://", "http://", "/"))):
            return label
        return f'<a href="{html.escape(url)}">{label}</a>'

    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)
    out = re.sub(r"&lt;(https://[^&\s]+)&gt;", r'<a href="\1">\1</a>', out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def markdown_to_html(md: str) -> str:
    """Headings, paragraphs and one level of bullet lists (with wrapped lines)."""
    blocks, para, items = [], [], []

    def flush():
        nonlocal para, items
        if para:
            blocks.append(f"<p>{_inline(' '.join(para))}</p>")
        if items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
        para, items = [], []

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
        elif m := re.match(r"^(#{1,3})\s+(.*)$", line):
            flush()
            level = len(m.group(1))
            blocks.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif m := re.match(r"^-\s+(.*)$", line):
            if para:
                flush()
            items.append(m.group(1))
        elif items and raw.startswith("  "):
            items[-1] += " " + line.strip()  # wrapped list item
        else:
            para.append(line.strip())
    flush()
    return "\n".join(blocks)


def _page(name: str) -> HTMLResponse:
    contact = os.getenv("PRIVACY_CONTACT") or "the operator of this deployment (see the add-on listing)"
    md = (DOCS / name).read_text(encoding="utf-8").replace("{{CONTACT}}", contact)
    title = md.splitlines()[0].lstrip("# ").strip()
    body = markdown_to_html(md)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root{{--bg:#ffffff;--text:#16181d;--muted:#5d6472;--link:#0b5fa5}}
@media (prefers-color-scheme: dark){{:root{{--bg:#0f1115;--text:#e8ebf0;--muted:#9aa3b2;--link:#7cb8ff}}}}
body{{font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:720px;margin:40px auto;padding:0 16px;background:var(--bg);color:var(--text)}}
h1{{font-size:1.6rem;line-height:1.25}} h2{{font-size:1.15rem;margin-top:1.8em}} a{{color:var(--link)}}
code{{font-size:.9em}} em{{color:var(--muted)}}
</style></head><body>
{body}
</body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "public, max-age=3600",
                                       "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


async def privacy(request) -> Response:
    return _page("privacy.md")


async def terms(request) -> Response:
    return _page("terms.md")


def routes() -> list:
    return [
        Route("/privacy", privacy),
        Route("/terms", terms),
        Mount("/addon-assets", StaticFiles(directory=ASSETS, check_dir=False), name="addon-assets"),
    ]
