"""In-memory cache in front of a MemoryStore, so tool calls never wait on storage.

* The backing store is loaded once at startup; every read is served from memory.
* Writes update memory immediately. With write_behind=False (local file) they are
  also persisted before save() returns; with write_behind=True (Google Drive) a
  background thread persists them once writes pause for `debounce` seconds (or at
  the latest `max_wait` seconds after the first unsaved write, so a steady stream
  of writes can't postpone saving forever); close() flushes on shutdown.
* While nothing is unsaved, the background thread re-reads the backing store every
  refresh_interval seconds, so hand edits to memories.md are picked up.
* Every persist re-reads the backing store first and does a three-way merge against
  the last synced copy: records changed only remotely are taken from remote,
  records changed locally win (a warning is logged when both sides changed).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

from .schema import MemoryRecord
from .store import MemoryStore

log = logging.getLogger(__name__)


def _copy(records: list[MemoryRecord]) -> list[MemoryRecord]:
    return [replace(r, tags=list(r.tags)) for r in records]


def three_way_merge(base: list[MemoryRecord], local: list[MemoryRecord],
                    remote: list[MemoryRecord]) -> tuple[list[MemoryRecord], list[str]]:
    """Merge local and remote edits of a common base, record by record.

    A record (including its addition or deletion) changed locally wins; otherwise
    the remote version is taken. Returns the merged records and the ids that were
    changed differently on both sides (local won).
    """
    b = {r.id: r for r in base}
    loc = {r.id: r for r in local}
    rem = {r.id: r for r in remote}
    merged, conflicts = [], []
    for rid in sorted(b.keys() | loc.keys() | rem.keys()):
        lr, br, rr = loc.get(rid), b.get(rid), rem.get(rid)
        if lr != br:
            if rr != br and rr != lr:
                conflicts.append(rid)
            chosen = lr
        else:
            chosen = rr
        if chosen is not None:
            merged.append(chosen)
    return merged, conflicts


class CachedStore:
    def __init__(self, backing: MemoryStore, *, write_behind: bool = False,
                 debounce: float = 2.0, max_wait: float = 10.0, refresh_interval: float = 30.0):
        self.backing = backing
        self.write_behind = write_behind
        self.debounce = debounce
        self.max_wait = max_wait
        self.refresh_interval = refresh_interval
        # Held by MemoryService around each load-modify-save, and by this class
        # whenever it replaces the cached records. Never held during backing I/O
        # in write-behind mode.
        self.lock = threading.RLock()
        records = backing.load()
        self._records = _copy(records)
        self._base = _copy(records)   # what the backing store held at the last sync
        self._dirty = False           # local writes not yet persisted
        self._generation = 0          # bumped on every save()
        self._last_write = 0.0
        self._dirty_since = 0.0
        self._last_refresh = time.monotonic()
        # One backing-store sync at a time. Synchronous mode persists inside save(),
        # which runs under self.lock, so it shares that lock (fixed lock order).
        self._io_lock = threading.Lock() if write_behind else self.lock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="memory-sync", daemon=True)
        self._thread.start()

    # ---------- MemoryStore interface ----------
    def load(self) -> list[MemoryRecord]:
        with self.lock:
            return _copy(self._records)

    def save(self, records: list[MemoryRecord]) -> None:
        with self.lock:
            self._records = _copy(records)
            self._generation += 1
            self._last_write = time.monotonic()
            if not self._dirty:
                self._dirty, self._dirty_since = True, self._last_write
            if not self.write_behind:
                self._sync()  # fast local file: persist before returning
                return
        self._wake.set()

    # ---------- lifecycle ----------
    @property
    def dirty(self) -> bool:
        with self.lock:
            return self._dirty

    def flush(self) -> None:
        """Persist unsaved writes now (blocking)."""
        if self.dirty:
            self._sync()

    def reset(self, records: list[MemoryRecord]) -> None:
        """Replace everything, in memory and in the backing store (demo reset).

        Blocking and without merging: unsaved writes and hand edits are discarded.
        """
        with self._io_lock:
            self.backing.save(records)
            with self.lock:
                self._records = _copy(records)
                self._base = _copy(records)
                self._generation += 1
                self._dirty = False

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=10)
        try:
            self.flush()
        except Exception:
            log.exception("Final flush of memory failed; unsaved changes may be lost")

    # ---------- background work ----------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=min(self.debounce, self.refresh_interval, 1.0) or 0.05)
            self._wake.clear()
            if self._stop.is_set():
                break
            now = time.monotonic()
            try:
                with self.lock:
                    dirty, last_write, dirty_since = self._dirty, self._last_write, self._dirty_since
                if dirty:
                    if now - last_write >= self.debounce or now - dirty_since >= self.max_wait:
                        self._sync()
                elif now - self._last_refresh >= self.refresh_interval:
                    self.refresh()
            except Exception:
                log.exception("Background memory sync failed; will retry")
                time.sleep(min(self.debounce, 5.0))

    def refresh(self) -> None:
        """Reload from the backing store, unless there are unsaved local writes."""
        with self._io_lock:
            with self.lock:
                if self._dirty:
                    return
                generation = self._generation
            remote = self.backing.load()  # slow for Drive; no cache lock held
            self._last_refresh = time.monotonic()
            with self.lock:
                if self._dirty or self._generation != generation:
                    return  # a write landed meanwhile; the next sync merges instead
                if remote != self._records:
                    log.info("Memory file changed outside the server; reloaded %d records", len(remote))
                self._records = _copy(remote)
                self._base = _copy(remote)

    def _sync(self) -> None:
        """Merge with the backing store and persist the result."""
        with self._io_lock:
            with self.lock:
                if not self._dirty:
                    return
                local, base, generation = _copy(self._records), _copy(self._base), self._generation
            # In write-behind mode the cache lock is released here, so tool calls
            # keep running while we talk to the backing store.
            remote = self.backing.load()
            merged = local
            if remote != base:
                merged, conflicts = three_way_merge(base, local, remote)
                log.warning("Memory file changed remotely while local writes were unsaved; "
                            "merged (local wins on conflict%s)",
                            f": {', '.join(conflicts)}" if conflicts else "")
            self.backing.save(merged)
            self._last_refresh = time.monotonic()
            with self.lock:
                self._base = _copy(merged)
                if self._generation == generation:
                    self._records = _copy(merged)
                    self._dirty = False
                elif self._dirty_since:
                    self._dirty_since = time.monotonic()  # newer writes get a fresh max_wait
                else:
                    # Writes arrived during the save: keep them on top of the merge.
                    self._records, _ = three_way_merge(local, self._records, merged)
