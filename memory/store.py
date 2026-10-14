"""Storage backends. Both keep one Markdown file as the canonical copy."""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Protocol

from .schema import MemoryRecord, from_markdown, to_markdown

MEMORY_FILENAME = "memories.md"


class MemoryStore(Protocol):
    def load(self) -> list[MemoryRecord]: ...
    def save(self, records: list[MemoryRecord]) -> None: ...


class LocalFileStore:
    """A Markdown file on local disk. Used for development, tests and the demo."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> list[MemoryRecord]:
        with self._lock:
            if not self.path.exists():
                return []
            return from_markdown(self.path.read_text(encoding="utf-8"))

    def save(self, records: list[MemoryRecord]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(to_markdown(records), encoding="utf-8")
            tmp.replace(self.path)


class DriveStore:
    """`memories.md` inside one Google Drive folder, accessed with a service account.

    Share ONLY the demo folder with the service account's e-mail address, so the
    server cannot see anything else in your Drive.
    """

    def __init__(self, folder_id: str, credentials_file: str | None = None):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        scopes = ["https://www.googleapis.com/auth/drive"]
        if credentials_file:
            creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=scopes)
        else:
            import google.auth

            creds, _ = google.auth.default(scopes=scopes)
        self._drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.folder_id = folder_id
        self._file_id: str | None = None
        self._lock = threading.Lock()

    def _find_file(self) -> str | None:
        if self._file_id:
            return self._file_id
        q = f"name = '{MEMORY_FILENAME}' and '{self.folder_id}' in parents and trashed = false"
        resp = self._drive.files().list(q=q, fields="files(id)", pageSize=1).execute()
        files = resp.get("files", [])
        self._file_id = files[0]["id"] if files else None
        return self._file_id

    def load(self) -> list[MemoryRecord]:
        with self._lock:
            file_id = self._find_file()
            if not file_id:
                return []
            data = self._drive.files().get_media(fileId=file_id).execute()
            return from_markdown(data.decode("utf-8"))

    def save(self, records: list[MemoryRecord]) -> None:
        from googleapiclient.http import MediaIoBaseUpload

        with self._lock:
            body = io.BytesIO(to_markdown(records).encode("utf-8"))
            media = MediaIoBaseUpload(body, mimetype="text/markdown", resumable=False)
            file_id = self._find_file()
            if file_id:
                self._drive.files().update(fileId=file_id, media_body=media).execute()
            else:
                meta = {"name": MEMORY_FILENAME, "parents": [self.folder_id], "mimeType": "text/markdown"}
                created = self._drive.files().create(body=meta, media_body=media, fields="id").execute()
                self._file_id = created["id"]
