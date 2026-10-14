"""Memory record schema and its human-readable Markdown form.

The canonical copy of the memory is a plain Markdown file that a person can
open, read and edit by hand. Every record uses the same field layout, so the
file stays portable across tools and memory engines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone

# Lifecycle of a record:
#   pending    -> proposed by an assistant, waiting for the user's spoken "yes"
#   active     -> confirmed and current
#   historical -> was true, replaced by a newer record (kept, never deleted)
#   archived   -> the user asked to stop using it
STATUSES = ("pending", "active", "historical", "archived")
CATEGORIES = ("profile", "preference", "active_context", "project", "decision", "other")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class MemoryRecord:
    id: str
    fact: str
    category: str = "other"
    status: str = "pending"
    source_type: str = "assistant_proposed"
    confidence: str = "medium"
    scope: str = "personal"  # reserved for future household / shared scopes
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    review_after: str | None = None  # ISO date; ask the user again on/after this day
    supersedes: str | None = None
    superseded_by: str | None = None
    tags: list[str] = field(default_factory=list)

    def is_due_for_review(self, today: date) -> bool:
        if self.status != "active" or not self.review_after:
            return False
        return date.fromisoformat(self.review_after) <= today

    def to_public(self) -> dict:
        """The fields a voice assistant needs, without internal bookkeeping."""
        return {
            "id": self.id,
            "fact": self.fact,
            "category": self.category,
            "status": self.status,
            "confirmed_by_user": self.source_type == "user_explicit",
            "updated_at": self.updated_at,
            "review_after": self.review_after,
        }


_FIELD_NAMES = [f.name for f in fields(MemoryRecord) if f.name != "id"]
_HEADER = re.compile(r"^##\s+(\S+)\s*$")
_LINE = re.compile(r"^-\s+([a-z_]+):\s*(.*)$")


def to_markdown(records: list[MemoryRecord], title: str = "Shared Memory") -> str:
    out = [
        f"# {title}",
        "",
        "Human-readable canonical memory. Safe to edit by hand: keep one `## ID` "
        "heading per record and one `- field: value` line per field.",
        "",
    ]
    for r in sorted(records, key=lambda r: r.id):
        out.append(f"## {r.id}")
        for name in _FIELD_NAMES:
            value = getattr(r, name)
            if value is None or value == []:
                continue
            if isinstance(value, list):
                value = ", ".join(value)
            # one line per field keeps the file easy to read and to diff
            out.append(f"- {name}: {str(value).replace(chr(10), ' ')}")
        out.append("")
    return "\n".join(out)


def from_markdown(text: str) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    current: dict | None = None

    def flush():
        if current and current.get("fact"):
            records.append(MemoryRecord(**current))

    for raw in text.splitlines():
        line = raw.strip()
        if m := _HEADER.match(line):
            flush()
            current = {"id": m.group(1)}
        elif current is not None and (m := _LINE.match(line)):
            key, value = m.group(1), m.group(2).strip()
            if key not in _FIELD_NAMES:
                continue  # unknown fields added by hand are ignored, not fatal
            if key == "tags":
                current[key] = [t.strip() for t in value.split(",") if t.strip()]
            else:
                current[key] = value or None
    flush()
    return records
