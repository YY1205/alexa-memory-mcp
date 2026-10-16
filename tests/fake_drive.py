"""A tiny in-memory stand-in for the Google Drive v3 client (files().list/get/get_media/update).

It has no files().create, so a DriveStore that tried to create the file would fail.
"""
from __future__ import annotations

import threading


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeDrive:
    def __init__(self, content: str | None, folder_id: str = "folder-1", file_id: str = "file-1"):
        self.folder_id, self.file_id = folder_id, file_id
        self.content = content          # None = no memories.md in the folder
        self.version = 1
        self.calls: dict[str, int] = {"list": 0, "get": 0, "get_media": 0, "update": 0}
        self.update_delay = 0.0
        self._lock = threading.Lock()

    # Someone editing the file in Drive (by hand, another app...)
    def external_edit(self, content: str) -> None:
        with self._lock:
            self.content = content
            self.version += 1

    def files(self):
        return self

    def list(self, q, fields, pageSize, **kwargs):
        def run():
            self.calls["list"] += 1
            if self.content is None or f"'{self.folder_id}' in parents" not in q:
                return {"files": []}
            return {"files": [{"id": self.file_id,
                               "webViewLink": f"https://drive.google.com/file/d/{self.file_id}/view"}]}
        return _Call(run)

    def get(self, fileId, fields, **kwargs):
        def run():
            self.calls["get"] += 1
            assert fileId == self.file_id
            return {"version": str(self.version)}
        return _Call(run)

    def get_media(self, fileId, **kwargs):
        def run():
            self.calls["get_media"] += 1
            with self._lock:
                return self.content.encode("utf-8")
        return _Call(run)

    def update(self, fileId, media_body, fields, **kwargs):
        def run():
            import time

            self.calls["update"] += 1
            time.sleep(self.update_delay)
            data = media_body.getbytes(0, media_body.size()).decode("utf-8")
            with self._lock:
                self.content = data
                self.version += 1
                return {"version": str(self.version)}
        return _Call(run)
