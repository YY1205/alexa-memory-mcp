import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logsafe import StripQueryString  # noqa: E402


def test_access_log_drops_query_strings():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/oauth/callback?code=SECRET&state=x", "1.1", 303), None)
    StripQueryString().filter(record)
    line = record.getMessage()
    assert "SECRET" not in line and "/oauth/callback?…" in line
