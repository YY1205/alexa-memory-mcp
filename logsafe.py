"""Keep OAuth codes and other query-string values out of access logs.

uvicorn logs `GET /oauth/callback?code=...&state=... HTTP/1.1`; this filter drops the
query string from the request path before the line is written.
"""
from __future__ import annotations

import logging


class StripQueryString(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0] + "?…", *args[3:])
        return True


def install() -> None:
    logging.getLogger("uvicorn.access").addFilter(StripQueryString())
