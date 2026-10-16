"""/privacy and /terms (from docs/*.md) and the listing assets are public and accurate."""
import sys
from pathlib import Path

from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from memory.schema import to_markdown  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from memory.store import LocalFileStore  # noqa: E402
from public_pages import markdown_to_html  # noqa: E402
from tests.conftest import TEST_RECORDS, oauth_env  # noqa: E402


def make_client(tmp_path, monkeypatch):
    for key, value in oauth_env(tmp_path, 8000).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PRIVACY_CONTACT", "privacy-contact@example.com")
    path = tmp_path / "memories.md"
    path.write_text(to_markdown(TEST_RECORDS))
    return TestClient(server.build_app(MemoryService(LocalFileStore(path))), base_url="http://127.0.0.1:8000")


def test_privacy_and_terms_are_served_without_auth(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        privacy = c.get("/privacy")
        terms = c.get("/terms")
    assert privacy.status_code == 200 and terms.status_code == 200
    assert "<h1>Privacy policy: Portable Memory</h1>" in privacy.text
    assert "privacy-contact@example.com" in privacy.text and "{{CONTACT}}" not in privacy.text
    assert '<a href="/privacy">privacy policy</a>' in terms.text
    assert "Hackathon prototype" in privacy.text and "Hackathon prototype" in terms.text


def test_privacy_policy_states_the_facts(tmp_path, monkeypatch):
    text = (ROOT / "docs" / "privacy.md").read_text()
    for fact in ("memories.md", "Google Drive", "SHA-256 hashes", "15 minutes", "30 days",
                 "Amazon Bedrock", "Amazon's own privacy notice", "sent to Google",
                 "Nothing is sold", "Unlink", "version history", "scrypt"):
        assert fact in text, fact


def test_listing_assets_are_served(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        for name in ("icon-72.png", "icon-241.png", "carousel-600x900.png"):
            r = c.get(f"/addon-assets/{name}")
            assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert c.get("/addon-assets/../server.py").status_code == 404


def test_markdown_renderer_escapes_html():
    out = markdown_to_html("Hi <script>alert(1)</script> [x](javascript:alert(1)) [ok](https://a.example)")
    assert "<script>" not in out and "javascript:" not in out
    assert '<a href="https://a.example">ok</a>' in out
