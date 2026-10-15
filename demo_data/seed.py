"""Build the fictional demo persona ("Sam"), with dates relative to the day it runs.

All data here is invented for the demo. Never point the demo at real memory.
Review dates are relative, so the demo looks the same on any day: exactly one
memory (the job interview) is overdue for review.

Run: python demo_data/seed.py [output.md]     (default: demo_data/memories.md)
"""
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.schema import MemoryRecord, to_markdown  # noqa: E402

TITLE = "Shared Memory (demo persona: Sam)"


def spoken_date(d: date) -> str:
    return f"{d:%B} {d.day}"  # "October 20"


def build_records(today: date) -> list[MemoryRecord]:
    ts = datetime.combine(today - timedelta(days=14), time(9), timezone.utc).isoformat()
    race = today + timedelta(weeks=4)
    contractor_start = today + timedelta(days=6)

    def rec(n, fact, category, review_after: date | None = None, tags=()):
        return MemoryRecord(
            id=f"MEM-{n:04d}", fact=fact, category=category, status="active",
            source_type="user_explicit", confidence="high", created_at=ts, updated_at=ts,
            review_after=review_after.isoformat() if review_after else None, tags=list(tags),
        )

    return [
        rec(1, "The user's name is Sam and they live in Portland.", "profile"),
        rec(2, "The user is vegetarian and does not eat fish.", "preference", tags=["food"]),
        rec(3, "The user prefers short, direct answers in the morning.", "preference"),
        rec(4, "The user's sister Maya's birthday is on November 2.", "profile", tags=["family", "birthday"]),
        rec(5, f"The user is training for a half marathon on {spoken_date(race)}.", "active_context",
            review_after=race + timedelta(days=1), tags=["running"]),
        rec(6, f"The user is renovating the kitchen and the contractor starts on {spoken_date(contractor_start)}.",
            "active_context", review_after=today + timedelta(days=7), tags=["home"]),
        rec(7, "The user is waiting to hear back about a job interview at a design studio.", "active_context",
            review_after=today - timedelta(days=3), tags=["job"]),
        rec(8, "The user decided to buy an electric car next year instead of a hybrid, mainly for lower running costs.",
            "decision", tags=["car"]),
        rec(9, "The user's dog Biscuit needs his heartworm pill on the first day of each month.", "profile",
            tags=["dog", "pet"]),
        rec(10, "The user is learning Spanish and practices 15 minutes a day.", "project", tags=["language"]),
    ]


RECORDS = build_records(date.today())  # convenience for code that doesn't care about dates


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "memories.md"
    records = build_records(date.today())
    out.write_text(to_markdown(records, title=TITLE), encoding="utf-8")
    print(f"wrote {len(records)} records to {out}")
