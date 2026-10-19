"""The memory rules, independent of MCP so they can be unit-tested.

Principles carried over from the AI_SHARED_MEMORY plan:
  * no silent writes: an assistant proposes, the user confirms out loud;
  * facts change: a correction supersedes the old record instead of deleting it;
  * temporary facts expire: records can carry a review date and get re-checked;
  * provenance: every record says whether the user confirmed it.
"""
from __future__ import annotations

import re
import threading
from datetime import date, datetime, timedelta, timezone

from retrieval.retrievers import KeywordRetriever, Retriever, strong_match, topic_tokens

from .schema import CATEGORIES, MemoryRecord, now_iso, to_markdown
from .speech import clause
from .store import MemoryStore

PENDING_TTL = timedelta(hours=24)
# Category headings in a shared export, in this order.
EXPORT_HEADINGS = {
    "profile": "About the owner",
    "preference": "Preferences",
    "active_context": "Current plans and situations",
    "project": "Projects",
    "decision": "Decisions",
    "other": "Other",
}


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
        all_active = [r for r in self.store.load() if r.status == "active"]
        active = [r for r in all_active if r.category == category] if category else all_active
        result = self.retriever.retrieve(query, active, limit)
        today = self._today()
        out = {
            "found": bool(result.records),
            "spoken_answer": result.spoken_answer,
            "memories": [r.to_public() for r in result.records],
            "needs_review": [r.id for r in result.records if r.is_due_for_review(today)],
            "engine": result.engine,
            # Always say how much is remembered, so "no match" is never read as "no memory".
            "total_active": len(all_active),
        }
        if not result.records and all_active:
            out["note"] = (f"No memory matched these words, but {len(all_active)} memories exist. "
                           "For general questions use memory_overview, or recall with specific words.")
        return out

    def overview(self, recent: int = 3) -> dict:
        """What is remembered, in general: counts, newest facts, what needs review."""
        records = self.store.load()
        active = [r for r in records if r.status == "active"]
        today = self._today()
        by_category: dict[str, int] = {}
        for r in active:
            by_category[r.category] = by_category.get(r.category, 0) + 1
        newest = sorted(active, key=lambda r: (r.updated_at, r.id), reverse=True)[:recent]
        due = [r for r in active if r.is_due_for_review(today)]
        if not active:
            spoken = "I don't have any memories about you yet."
        else:
            examples = " and ".join(f"that {clause(r.fact)}" for r in newest[:2])
            spoken = f"I remember {len(active)} things about you, including {examples}."
            if due:
                count = "One" if len(due) == 1 else str(len(due))
                spoken += f" {count} of them may need updating."
        return {
            "total_active": len(active),
            "by_category": dict(sorted(by_category.items())),
            "recent": [r.to_public() for r in newest],
            "due_for_review": len(due),
            "pending": sum(r.status == "pending" for r in records),
            "historical": sum(r.status == "historical" for r in records),
            "spoken_summary": spoken,
        }

    def list_due_reviews(self) -> dict:
        today = self._today()
        due = [r for r in self.store.load() if r.is_due_for_review(today)]
        due.sort(key=lambda r: r.review_after or "")
        return {"count": len(due), "memories": [r.to_public() for r in due]}

    # ---------- export (shareable snapshot) ----------
    @staticmethod
    def _owner_sentence(fact: str) -> str:
        """Stored facts say "the user"; a shared file says "the owner"."""
        out = re.sub(r"\bthe user's\b", "the owner's", fact, flags=re.I)
        out = re.sub(r"\bthe user\b", "the owner", out, flags=re.I)
        out = out.strip()
        return out[0].upper() + out[1:] if out else out

    def export(self, categories: list[str] | None = None, include_history: bool = False,
               topic: str | None = None) -> dict:
        """A Markdown snapshot the owner can share with people or other AI assistants.

        Active memories only (plus a "Previously true" section if include_history),
        grouped by category, as plain sentences with their last-confirmed date. No ids,
        no pending records, no internal fields. `categories` and `topic` (keywords
        matched against facts and tags) narrow what is shared.
        """
        wanted = [c for c in (categories or EXPORT_HEADINGS) if c in EXPORT_HEADINGS]
        if categories and not wanted:
            raise MemoryError_(f"I don't know those categories. Use: {', '.join(EXPORT_HEADINGS)}.")
        records = [r for r in self.store.load() if r.category in wanted]
        if topic and topic.strip():
            topic_words = topic_tokens(topic)
            records = [r for r in records if topic_words & topic_tokens(f"{r.fact} {' '.join(r.tags)}")]
        active = sorted((r for r in records if r.status == "active"), key=lambda r: r.id)
        history = sorted((r for r in records if r.status == "historical"), key=lambda r: r.id) \
            if include_history else []
        exported_on = self._today().isoformat()
        scope = ", ".join(EXPORT_HEADINGS[c] for c in wanted) if categories else "everything"
        if topic and topic.strip():
            scope += f" (topic: {topic.strip()})"
        lines = [
            "# Portable Memory export",
            "",
            f"> A memory export that its owner chose to share, exported on {exported_on}.",
            "> Use it as context about the owner: every fact below was confirmed by the owner.",
            f"> It may be out of date after {exported_on}.",
            "",
            f"Shared: {scope}. {len(active)} {'memory' if len(active) == 1 else 'memories'}.",
        ]
        for category in wanted:
            items = [r for r in active if r.category == category]
            if items:
                lines += ["", f"## {EXPORT_HEADINGS[category]}", ""]
                lines += [f"- {self._owner_sentence(r.fact)} (last confirmed {r.updated_at[:10]})"
                          for r in items]
        if history:
            lines += ["", "## Previously true", "", "No longer current; kept as history.", ""]
            lines += [f"- {self._owner_sentence(r.fact)} (until {r.updated_at[:10]})" for r in history]
        if not active and not history:
            lines += ["", "_Nothing matched what was chosen for sharing._"]
        markdown = "\n".join(lines) + "\n"
        what = f"{len(active)} {'memory' if len(active) == 1 else 'memories'}"
        about = f" about {topic.strip()}" if topic and topic.strip() else (
            f" ({scope})" if categories else "")
        return {
            "markdown": markdown,
            "count": len(active),
            "history_count": len(history),
            "categories": wanted,
            "exported_on": exported_on,
            "spoken_summary": f"Your memory export is ready: {what}{about}.",
        }

    # ---------- write (two-step) ----------
    @staticmethod
    def _same_fact(a: str, b: str) -> bool:
        norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()  # noqa: E731
        return norm(a) == norm(b)

    @staticmethod
    def _target(rec: MemoryRecord, active: list[MemoryRecord]) -> str | None:
        """The active memory a pending record would replace (explicitly or by topic)."""
        if rec.supersedes:
            return rec.supersedes
        match = strong_match(rec.fact, active)
        return match.id if match else None

    def _drop_abandoned_pending(self, records: list[MemoryRecord], fact: str,
                                target: str | None) -> list[MemoryRecord]:
        """A new proposal replaces older pending ones for the same fact or the same
        memory, so abandoned proposals can't pile up within a conversation."""
        active = [r for r in records if r.status == "active"]
        return [
            r for r in records
            if not (r.status == "pending" and (
                self._same_fact(r.fact, fact) or (target and self._target(r, active) == target)))
        ]

    def propose(self, fact: str, category: str = "other", review_in_days: int | None = None,
                supersedes: str | None = None) -> dict:
        fact = fact.strip()
        if not fact:
            raise MemoryError_("There is nothing to remember.")
        if category not in CATEGORIES:
            category = "other"
        with self._lock:
            records = self._drop_expired_pending(self.store.load())
            active = [r for r in records if r.status == "active"]
            update_of = None
            if supersedes:
                old = self._get(records, supersedes)
                if old.status != "active":
                    raise MemoryError_(f"Memory {supersedes} is not current, so it can't be replaced.")
                category = old.category
            else:
                update_of = strong_match(fact, active)  # looks like a change to an existing memory
            target = supersedes or (update_of.id if update_of else None)
            records = self._drop_abandoned_pending(records, fact, target)
            new = MemoryRecord(
                id=self._next_id(records), fact=fact, category=category, status="pending",
                review_after=self._review_date(review_in_days), supersedes=supersedes,
            )
            records.append(new)
            self.store.save(records)

        result = {"pending_id": new.id}
        if supersedes:
            result["confirmation_prompt"] = (f"Should I change your note that {clause(old.fact)} "
                                             f"to: {clause(fact)}?")
        elif update_of:
            result["confirmation_prompt"] = (f"This looks like an update to your note that "
                                             f"{clause(update_of.fact)}. Should I change it to: {clause(fact)}?")
            result["replaces"] = update_of.id
        else:
            result["confirmation_prompt"] = f"Should I remember that {clause(fact)}?"
        # Other similar memories, for context (not needed to confirm).
        similar = [] if supersedes else [
            r for r in self.retriever.retrieve(fact, active, 3).records if r is not update_of]
        result["possibly_related"] = [r.to_public() for r in similar]
        return result

    def confirm(self, pending_id: str, confirmed: bool, replaces: str | None = None) -> dict:
        """Save or discard a pending fact. `replaces`: the id of an active memory this
        fact updates (propose's `replaces`); it is kept as history, not deleted."""
        with self._lock:
            records = self._drop_expired_pending(self.store.load())
            rec = self._get(records, pending_id)
            if rec.status != "pending":
                raise MemoryError_(f"Memory {pending_id} is not waiting for confirmation.")
            if not confirmed:
                records.remove(rec)
                self.store.save(records)
                return {"saved": False, "spoken_answer": "Okay, I won't remember that."}

            target = rec.supersedes or replaces
            if replaces and rec.supersedes and replaces != rec.supersedes:
                raise MemoryError_(f"Memory {pending_id} already replaces {rec.supersedes}, not {replaces}.")
            old = None
            if target:
                old = self._get(records, target)
                if old.status != "active" or old.id == rec.id:
                    raise MemoryError_(f"Memory {target} is not current, so it can't be replaced.")

            ts = now_iso()
            rec.status, rec.source_type, rec.confidence, rec.updated_at = "active", "user_explicit", "high", ts
            replaced = None
            if old:
                rec.supersedes = old.id
                if rec.category == "other":
                    rec.category = old.category
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
