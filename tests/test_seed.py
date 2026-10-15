"""Demo seed: review dates are relative to the day it runs."""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_data.seed import build_records, spoken_date  # noqa: E402


@pytest.mark.parametrize("today", [date(2026, 10, 14), date(2026, 10, 22), date(2027, 2, 28), date(2026, 12, 30)])
def test_exactly_one_overdue_review_on_any_day(today):
    due = [r.id for r in build_records(today) if r.is_due_for_review(today)]
    assert due == ["MEM-0007"]


def test_review_dates_are_relative():
    today = date(2026, 10, 22)
    by_id = {r.id: r for r in build_records(today)}
    assert by_id["MEM-0007"].review_after == (today - timedelta(days=3)).isoformat()
    assert by_id["MEM-0006"].review_after == (today + timedelta(days=7)).isoformat()
    race = today + timedelta(weeks=4)
    assert spoken_date(race) in by_id["MEM-0005"].fact
    assert by_id["MEM-0005"].review_after == (race + timedelta(days=1)).isoformat()
    assert all(r.created_at < today.isoformat() for r in by_id.values())


def test_seed_writes_a_file(tmp_path):
    import subprocess

    out = tmp_path / "m.md"
    subprocess.run([sys.executable, "demo_data/seed.py", str(out)], cwd=ROOT, check=True, capture_output=True)
    assert out.read_text().count("## MEM-") == 10
