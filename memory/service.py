"""The memory rules, independent of MCP so they can be unit-tested.

Principles carried over from the AI_SHARED_MEMORY plan:
  * no silent writes: an assistant proposes, the user confirms out loud;
  * facts change: a correction supersedes the old record instead of deleting it;
  * temporary facts expire: records can carry a review date and get re-checked;
  * provenance: every record says whether the user confirmed it.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone

from retrieval.retrievers import KeywordRetriever, Retriever

from .schema import CATEGORIES, MemoryRecord, now_iso, to_markdown
from .store import MemoryStore

PENDING_TTL = timedelta(hours=24)


class MemoryError_(ValueError):
    """A user-facing problem (unknown id, wrong state). Message is safe to speak."""


class MemoryService:
    def __init__(self, store: MemoryStore, retriever: Retriever | None = None, today=None):
        self.store = store
        self.retriever = retriever or KeywordRetriever()
        self._today = today or (lambda: date.today())
        # One writer at a time (single-user server). A CachedStore exposes its lock so
        # a background reload can't land between our load() and save().
        self._lock = getattr(store, "lock", None) or threading.Lock()

    # ---------- helpers ----------
    def _next_id(self, records: list[MemoryRecord]) -> str:
        nums = [int(r.id.split("-")[1]) for r in records if r.id.startswith("MEM-") and r.id[4:].isdigit()]
        return f"MEM-{(max(nums) + 1) if nums else 1:04d}"

    @staticmethod
    def _get(records: list[MemoryRecord], record_id: str) -> MemoryRecord:
        for r in records:
            if r.id == record_id:
                return r
        raise MemoryError_(f"I couldn't find a memory with id {record_id}.")

    @staticmethod
    def _drop_expired_pending(records: list[MemoryRecord]) -> list[MemoryRecord]:
        cutoff = datetime.now(timezone.utc) - PENDING_TTL
        return [
            r for r in records
            if not (r.status == "pending" and datetime.fromisoformat(r.created_at) < cutoff)
        ]

    def _review_date(self, review_in_days: int | None) -> str | None:
        if not review_in_days or review_in_days <= 0:
            return None
        return (self._today() + timedelta(days=review_in_days)).isoformat()

    # ---------- read ----------
    def export_markdown(self) -> str:
        """The canonical memory file, exactly as it is (or will be) written to storage."""
        return to_markdown(self.store.load())

    def recall(self, query: str, category: str | None = None, limit: int = 5) -> dict:
        active = [r for r in self.store.load() if r.status == "active"]
        if category:
            active = [r for r in active if r.category == category]
        result = self.retriever.retrieve(query, active, limit)
        today = self._today()
        return {
            "found": bool(result.records),
            "spoken_answer": result.spoken_answer,
            "memories": [r.to_public() for r in result.records],
            "needs_review": [r.id for r in result.records if r.is_due_for_review(today)],
            "engine": result.engine,
        }

    def list_due_reviews(self) -> dict:
        today = self._today()
        due = [r for r in self.store.load() if r.is_due_for_review(today)]
        due.sort(key=lambda r: r.review_after or "")
        return {"count": len(due), "memories": [r.to_public() for r in due]}

    # ---------- write (two-step) ----------
    def propose(self, fact: str, category: str = "other", review_in_days: int | None = None,
                supersedes: str | None = None) -> dict:
        fact = fact.strip()
        if not fact:
            raise MemoryError_("There is nothing to remember.")
        if category not in CATEGORIES:
            category = "other"
        with self._lock:
            records = self._drop_expired_pending(self.store.load())
            if supersedes:
                old = self._get(records, supersedes)
                if old.status != "active":
                    raise MemoryError_(f"Memory {supersedes} is not current, so it can't be replaced.")
                category = old.category
            new = MemoryRecord(
                id=self._next_id(records), fact=fact, category=category, status="pending",
                review_after=self._review_date(review_in_days), supersedes=supersedes,
            )
            records.append(new)
            self.store.save(records)

        # Similar active memories, so the assistant can ask "does this replace ...?"
        similar = [] if supersedes else self.retriever.retrieve(
            fact, [r for r in records if r.status == "active"], 3).records
        if supersedes:
            prompt = f'Should I replace "{old.fact}" with "{fact}"?'
        else:
            prompt = f'Should I remember that {fact[0].lower() + fact[1:]}?'
        return {
            "pending_id": new.id,
            "confirmation_prompt": prompt,
            "possibly_related": [r.to_public() for r in similar],
        }

    def confirm(self, pending_id: str, confirmed: bool) -> dict:
        with self._lock:
            records = self._drop_expired_pending(self.store.load())
            rec = self._get(records, pending_id)
            if rec.status != "pending":
                raise MemoryError_(f"Memory {pending_id} is not waiting for confirmation.")
            if not confirmed:
                records.remove(rec)
                self.store.save(records)
                return {"saved": False, "spoken_answer": "Okay, I won't remember that."}

            ts = now_iso()
            rec.status, rec.source_type, rec.confidence, rec.updated_at = "active", "user_explicit", "high", ts
            replaced = None
            if rec.supersedes:
                old = self._get(records, rec.supersedes)
                old.status, old.superseded_by, old.updated_at = "historical", rec.id, ts
                replaced = old.to_public()
            self.store.save(records)
        answer = "Got it, I've updated that." if replaced else "Got it, I'll remember that."
        return {"saved": True, "memory": rec.to_public(), "replaced": replaced, "spoken_answer": answer}

    def resolve_review(self, memory_id: str, still_true: bool, review_in_days: int | None = None) -> dict:
        """User answered "is this still true?". Keep it (with a new date) or retire it."""
        with self._lock:
            records = self.store.load()
            rec = self._get(records, memory_id)
            if rec.status != "active":
                raise MemoryError_(f"Memory {memory_id} is not current.")
            rec.updated_at = now_iso()
            if still_true:
                rec.review_after = self._review_date(review_in_days)
                answer = "Thanks, I'll keep that."
            else:
                rec.status = "historical"
                answer = "Okay, I've marked that as no longer true."
            self.store.save(records)
        return {"memory": rec.to_public(), "spoken_answer": answer}
