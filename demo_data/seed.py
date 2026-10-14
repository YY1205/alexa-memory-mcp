"""Reset demo_data/memories.md to a fictional persona ("Sam").

All data here is invented for the demo. Never point the demo at real memory.
Run: python demo_data/seed.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.schema import MemoryRecord, to_markdown  # noqa: E402

TS = "2026-10-01T09:00:00+00:00"


def rec(n, fact, category, review_after=None, tags=()):
    return MemoryRecord(
        id=f"MEM-{n:04d}", fact=fact, category=category, status="active",
        source_type="user_explicit", confidence="high", created_at=TS, updated_at=TS,
        review_after=review_after, tags=list(tags),
    )


RECORDS = [
    rec(1, "The user's name is Sam and they live in Portland.", "profile"),
    rec(2, "The user is vegetarian and does not eat fish.", "preference", tags=["food"]),
    rec(3, "The user prefers short, direct answers in the morning.", "preference"),
    rec(4, "The user's sister Maya's birthday is on November 2.", "profile", tags=["family", "birthday"]),
    rec(5, "The user is training for a half marathon on November 15.", "active_context",
        review_after="2026-11-16", tags=["running"]),
    rec(6, "The user is renovating the kitchen and the contractor starts on October 20.", "active_context",
        review_after="2026-10-21", tags=["home"]),
    rec(7, "The user is waiting to hear back about a job interview at a design studio.", "active_context",
        review_after="2026-10-10", tags=["job"]),
    rec(8, "The user decided to buy an electric car next year instead of a hybrid, mainly for lower running costs.",
        "decision", tags=["car"]),
    rec(9, "The user's dog Biscuit needs his heartworm pill on the first day of each month.", "profile",
        tags=["dog", "pet"]),
    rec(10, "The user is learning Spanish and practices 15 minutes a day.", "project", tags=["language"]),
]

if __name__ == "__main__":
    out = Path(__file__).parent / "memories.md"
    out.write_text(to_markdown(RECORDS, title="Shared Memory (demo persona: Sam)"), encoding="utf-8")
    print(f"wrote {len(RECORDS)} records to {out}")
