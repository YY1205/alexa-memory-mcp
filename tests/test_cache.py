"""CachedStore: reads from memory, write-behind persistence, refresh and merge."""
import logging
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_data.seed import RECORDS  # noqa: E402
from memory.cache import CachedStore, three_way_merge  # noqa: E402
from memory.schema import MemoryRecord, to_markdown  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from memory.store import LocalFileStore  # noqa: E402


class FakeStore:
    """In-memory backing store; save() can be slow like Google Drive."""

    def __init__(self, records, save_delay=0.0):
        self.records = [replace(r, tags=list(r.tags)) for r in records]
        self.save_delay = save_delay
        self.loads = 0
        self.save_starts = 0
        self.saves = 0

    def load(self):
        self.loads += 1
        return [replace(r, tags=list(r.tags)) for r in self.records]

    def save(self, records):
        self.save_starts += 1
        time.sleep(self.save_delay)
        self.records = [replace(r, tags=list(r.tags)) for r in records]
        self.saves += 1


def edit(records, rid, **changes):
    return [replace(r, **changes) if r.id == rid else r for r in records]


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def stores():
    made = []

    def make(backing, **kw):
        s = CachedStore(backing, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def test_reads_are_served_from_memory(stores):
    backing = FakeStore(RECORDS)
    store = stores(backing, refresh_interval=3600)
    for _ in range(100):
        assert store.load() == RECORDS
    assert backing.loads == 1


def test_load_returns_copies(stores):
    store = stores(FakeStore(RECORDS), refresh_interval=3600)
    store.load()[0].fact = "mutated"
    assert store.load()[0].fact == RECORDS[0].fact


def test_local_file_writes_are_synchronous(tmp_path, stores):
    path = tmp_path / "memories.md"
    path.write_text(to_markdown(RECORDS))
    store = stores(LocalFileStore(path), refresh_interval=3600)
    store.save(edit(store.load(), "MEM-0001", fact="The user's name is Sam and they live in Bend."))
    assert "live in Bend" in path.read_text()
    assert not store.dirty


def test_write_behind_never_blocks_and_flushes_on_close():
    backing = FakeStore(RECORDS, save_delay=1.0)
    store = CachedStore(backing, write_behind=True, debounce=0.05, refresh_interval=3600)
    t0 = time.perf_counter()
    for i in range(20):
        store.save(edit(store.load(), "MEM-0003", fact=f"Variant {i}."))
        assert store.load()[2].fact == f"Variant {i}."
    assert time.perf_counter() - t0 < 0.5  # 20 writes, each backing save takes 1 s
    store.close()
    assert backing.records[2].fact == "Variant 19."
    assert not store.dirty


def test_write_behind_is_debounced():
    backing = FakeStore(RECORDS)
    store = CachedStore(backing, write_behind=True, debounce=0.3, refresh_interval=3600)
    for i in range(10):
        store.save(edit(store.load(), "MEM-0003", fact=f"Variant {i}."))
    assert wait_until(lambda: not store.dirty)
    assert backing.saves == 1
    store.close()


def test_steady_writes_still_persist_within_max_wait():
    backing = FakeStore(RECORDS)
    store = CachedStore(backing, write_behind=True, debounce=0.2, max_wait=0.3, refresh_interval=3600)
    end = time.monotonic() + 1.0
    i = 0
    while time.monotonic() < end:  # never pauses long enough for the debounce
        store.save(edit(store.load(), "MEM-0003", fact=f"Variant {i}."))
        i += 1
        time.sleep(0.01)
    assert backing.saves >= 1
    store.close()
    assert backing.records[2].fact == f"Variant {i - 1}."


def test_refresh_picks_up_hand_edits(stores):
    backing = FakeStore(RECORDS)
    store = stores(backing, refresh_interval=0.1)
    backing.records = edit(backing.records, "MEM-0010", fact="The user is learning Italian.")
    assert wait_until(lambda: store.load()[9].fact == "The user is learning Italian.")


def test_refresh_is_skipped_while_writes_are_unsaved():
    backing = FakeStore(RECORDS)
    store = CachedStore(backing, write_behind=True, debounce=3600, refresh_interval=0.05)
    store.save(edit(store.load(), "MEM-0003", fact="Local edit."))
    backing.records = edit(backing.records, "MEM-0003", fact="Remote edit.")
    time.sleep(0.3)
    assert store.load()[2].fact == "Local edit."
    store.close()


def test_sync_merges_remote_and_local_changes(caplog):
    backing = FakeStore(RECORDS)
    store = CachedStore(backing, write_behind=True, debounce=3600, refresh_interval=3600)
    local = edit(store.load(), "MEM-0003", fact="Local only.")
    local = edit(local, "MEM-0005", fact="Local wins.")
    local = [r for r in local if r.id != "MEM-0009"]  # deleted locally
    store.save(local)

    remote = edit(backing.records, "MEM-0004", fact="Remote only.")
    remote = edit(remote, "MEM-0005", fact="Remote loses.")
    remote.append(MemoryRecord(id="MEM-0050", fact="Added by hand.", status="active"))
    backing.records = remote

    with caplog.at_level(logging.WARNING, logger="memory.cache"):
        store.close()
    saved = {r.id: r.fact for r in backing.records}
    assert saved["MEM-0003"] == "Local only."
    assert saved["MEM-0004"] == "Remote only."
    assert saved["MEM-0005"] == "Local wins."
    assert saved["MEM-0050"] == "Added by hand."
    assert "MEM-0009" not in saved
    assert {r.id: r.fact for r in store.load()} == saved
    assert "MEM-0005" in caplog.text


def test_local_file_write_keeps_recent_hand_edit(tmp_path, stores):
    """A hand edit made since the last refresh is not overwritten by a tool write."""
    path = tmp_path / "memories.md"
    path.write_text(to_markdown(RECORDS))
    store = stores(LocalFileStore(path), refresh_interval=3600)
    path.write_text(to_markdown(edit(RECORDS, "MEM-0010", fact="The user is learning Italian.")))
    store.save(edit(store.load(), "MEM-0003", fact="The user likes long answers."))
    text = path.read_text()
    assert "learning Italian" in text and "long answers" in text


def test_reset_replaces_everything_without_merging(stores):
    backing = FakeStore(RECORDS)
    store = stores(backing, write_behind=True, debounce=3600, refresh_interval=3600)
    store.save(edit(store.load(), "MEM-0003", fact="Unsaved local edit."))
    backing.records = edit(backing.records, "MEM-0004", fact="Hand edit.")
    store.reset(RECORDS)
    assert store.load() == RECORDS and backing.records == RECORDS
    assert not store.dirty


def test_three_way_merge_without_remote_changes_returns_local():
    local = edit(RECORDS, "MEM-0001", fact="x")
    merged, conflicts = three_way_merge(RECORDS, local, RECORDS)
    assert merged == local and conflicts == []


def test_concurrent_tool_calls_stay_consistent(tmp_path, stores):
    path = tmp_path / "memories.md"
    path.write_text(to_markdown(RECORDS))
    store = stores(LocalFileStore(path), refresh_interval=0.01)  # refresh racing writes
    service = MemoryService(store)
    ids, errors = [], []

    def worker(n):
        try:
            for i in range(10):
                p = service.propose(f"The user owns plant number {n}-{i}.", "other")
                service.confirm(p["pending_id"], True)
                ids.append(p["pending_id"])
        except Exception as e:  # pragma: no cover - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(ids)) == 80
    on_disk = LocalFileStore(path).load()
    assert len([r for r in on_disk if r.status == "active"]) == 10 + 80  # 10 seed records + 80 new
