"""DriveStore + CachedStore against a fake Drive client (offline). Real Drive: scripts/drive_smoke.py."""
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from memory.cache import CachedStore  # noqa: E402
from memory.schema import from_markdown, to_markdown  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from memory.store import DriveStore, MemoryFileMissing  # noqa: E402
from tests.conftest import TEST_RECORDS, TEST_TODAY  # noqa: E402
from tests.fake_drive import FakeDrive  # noqa: E402
from tests.test_cache import FakeStore, edit, wait_until  # noqa: E402


@pytest.fixture
def drive():
    return FakeDrive(to_markdown(TEST_RECORDS))


def drive_store(drive):
    return DriveStore(drive.folder_id, service=drive)


def facts(drive) -> dict:
    return {r.id: r.fact for r in from_markdown(drive.content)}


def test_missing_memory_file_fails_at_startup_with_upload_hint():
    with pytest.raises(MemoryFileMissing, match="Upload memories.md"):
        DriveStore("folder-1", service=FakeDrive(None))


def test_store_updates_the_existing_file_and_never_creates_one(drive):
    store = drive_store(drive)
    assert store.web_view_link == "https://drive.google.com/file/d/file-1/view"
    store.save(edit(store.load(), "MEM-0003", fact="Local edit."))
    assert facts(drive)["MEM-0003"] == "Local edit."
    assert drive.calls["update"] == 1 and store.last_version == str(drive.version)
    # FakeDrive has no files().create: a create attempt would have raised.


def test_refresh_downloads_only_when_the_drive_version_changes(drive):
    cache = CachedStore(drive_store(drive), write_behind=True, debounce=3600, refresh_interval=3600)
    downloads = drive.calls["get_media"]
    for _ in range(5):
        cache.refresh()
    assert drive.calls["get_media"] == downloads  # only cheap version checks
    drive.external_edit(to_markdown(edit(TEST_RECORDS, "MEM-0009", fact="The user's dog Pepper needs a pill.")))
    cache.refresh()
    assert drive.calls["get_media"] == downloads + 1
    assert {r.id: r.fact for r in cache.load()}["MEM-0009"] == "The user's dog Pepper needs a pill."
    cache.close()


def test_own_write_is_not_downloaded_again(drive):
    cache = CachedStore(drive_store(drive), write_behind=True, debounce=0.01, refresh_interval=3600)
    cache.save(edit(cache.load(), "MEM-0003", fact="Local edit."))
    assert wait_until(lambda: not cache.dirty)
    downloads = drive.calls["get_media"]
    cache.refresh()
    assert drive.calls["get_media"] == downloads
    cache.close()


def test_hand_edit_in_drive_reaches_recall(drive):
    cache = CachedStore(drive_store(drive), write_behind=True, debounce=0.05, refresh_interval=0.1)
    service = MemoryService(cache, today=lambda: TEST_TODAY)
    drive.external_edit(to_markdown(edit(TEST_RECORDS, "MEM-0009",
                                         fact="The user's dog Pepper needs his heartworm pill on the first day of each month.")))
    assert wait_until(lambda: any("Pepper" in m["fact"] for m in service.recall("my dog")["memories"]))
    cache.close()


def test_write_behind_and_conflict_merge_with_drive(drive):
    cache = CachedStore(drive_store(drive), write_behind=True, debounce=3600, refresh_interval=3600)
    cache.save(edit(cache.load(), "MEM-0003", fact="Local only."))
    cache.save(edit(cache.load(), "MEM-0005", fact="Local wins."))
    remote = edit(TEST_RECORDS, "MEM-0004", fact="Remote only.")
    drive.external_edit(to_markdown(edit(remote, "MEM-0005", fact="Remote loses.")))
    cache.flush()
    saved = facts(drive)
    assert saved["MEM-0003"] == "Local only." and saved["MEM-0004"] == "Remote only."
    assert saved["MEM-0005"] == "Local wins."
    cache.close()


def test_remote_edit_survives_writes_that_land_during_a_slow_save():
    """Regression: writes arriving while a save is in flight must keep the remote edits
    that save merged in; otherwise the next save reverts the user's hand edit."""
    backing = FakeStore(TEST_RECORDS, save_delay=0.3)
    cache = CachedStore(backing, write_behind=True, debounce=0.01, max_wait=0.05, refresh_interval=3600)
    backing.records = edit(backing.records, "MEM-0004", fact="Hand edit in Drive.")
    cache.save(edit(cache.load(), "MEM-0003", fact="First write."))
    assert wait_until(lambda: backing.save_starts >= 1)
    cache.save(edit(cache.load(), "MEM-0005", fact="Write during the save."))
    cache.close()
    final = {r.id: r.fact for r in backing.records}
    assert final["MEM-0004"] == "Hand edit in Drive."
    assert final["MEM-0003"] == "First write." and final["MEM-0005"] == "Write during the save."


def test_tool_calls_never_wait_on_drive(drive):
    drive.update_delay = 1.0
    cache = CachedStore(drive_store(drive), write_behind=True, debounce=0.01, max_wait=0.05, refresh_interval=3600)
    service = MemoryService(cache, today=lambda: TEST_TODAY)
    t0 = time.perf_counter()
    for i in range(10):
        p = service.propose(f"The user likes item{i}.", "preference")
        service.confirm(p["pending_id"], True)
    assert time.perf_counter() - t0 < 0.5  # ten writes while 1 s Drive uploads run
    cache.close()
    assert "item9" in drive.content


def test_reset_overwrites_drive_only_when_triggered(drive):
    drive.external_edit(to_markdown([replace(r, fact="old seed") for r in TEST_RECORDS]))
    cache = CachedStore(drive_store(drive), write_behind=True, refresh_interval=3600)
    assert drive.calls["update"] == 0  # startup never writes
    cache.reset(TEST_RECORDS)
    assert facts(drive) == {r.id: r.fact for r in TEST_RECORDS}
    cache.close()


def test_memory_resource_advertises_the_drive_link(drive, tmp_path, monkeypatch):
    for key in ("MCP_BEARER_TOKEN", "OAUTH_CLIENT_ID", "HOST", "PORT", "ALLOWED_HOSTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OAUTH_TOKEN_STORE", str(tmp_path / "t.json"))
    cache = CachedStore(drive_store(drive), write_behind=True, refresh_interval=3600)
    app = server.build_app(MemoryService(cache))
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        r = c.post("/mcp", headers={"Accept": "application/json, text/event-stream",
                                    "Content-Type": "application/json", "MCP-Protocol-Version": "2025-11-25"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    resource = next(x for x in r.json()["result"]["resources"] if x["uri"] == "memory://memories.md")
    assert resource["_meta"]["webViewLink"] == "https://drive.google.com/file/d/file-1/view"
    assert resource["_meta"]["backend"] == "google-drive"
    cache.close()
