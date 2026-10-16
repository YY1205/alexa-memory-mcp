"""Storage backends. Both keep one Markdown file as the canonical copy."""
from __future__ import annotations

import io
import os
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


class MemoryFileMissing(RuntimeError):
    """The Drive folder has no memories.md; the user must upload one."""


class DriveStore:
    """`memories.md` inside one Google Drive folder, accessed with a service account.

    Share ONLY the demo folder with the service account's e-mail address, so the
    server cannot see anything else in your Drive. Service accounts can't own files
    in a personal Drive, so this store never creates the file: you upload
    memories.md, it only updates that file's content.

    `version` is Drive's per-file change counter. The cache asks for it (a tiny
    metadata request) and downloads the file only when it changed.
    """

    _SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(self, folder_id: str, credentials_file: str | None = None, service=None):
        if service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            if credentials_file:
                creds = service_account.Credentials.from_service_account_file(
                    os.path.expanduser(credentials_file), scopes=self._SCOPES)
            else:
                import google.auth

                creds, _ = google.auth.default(scopes=self._SCOPES)
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._drive = service
        self.folder_id = folder_id
        self._lock = threading.Lock()
        self.last_version: str | None = None  # Drive version of the content we last read or wrote
        found = self._find_file()
        if not found:
            raise MemoryFileMissing(
                f"There is no {MEMORY_FILENAME} in the Drive folder {folder_id} (or it isn't shared "
                f"with the service account). Upload {MEMORY_FILENAME} to that folder yourself (for "
                f"example demo_data/memories.md); the server only updates an existing file, because "
                f"a service account can't own files in your Drive. Then restart the server.")
        self._file_id, self.web_view_link = found

    def _find_file(self) -> tuple[str, str | None] | None:
        q = f"name = '{MEMORY_FILENAME}' and '{self.folder_id}' in parents and trashed = false"
        resp = self._drive.files().list(q=q, fields="files(id,webViewLink)", pageSize=1,
                                        supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = resp.get("files", [])
        return (files[0]["id"], files[0].get("webViewLink")) if files else None

    def version(self) -> str:
        """Drive's change counter for the file (metadata only, no download)."""
        with self._lock:
            meta = self._drive.files().get(fileId=self._file_id, fields="version",
                                           supportsAllDrives=True).execute()
            return str(meta["version"])

    def load(self) -> list[MemoryRecord]:
        with self._lock:
            # Version first: if the file changes in between, we re-download next time.
            version = self._drive.files().get(fileId=self._file_id, fields="version",
                                              supportsAllDrives=True).execute()["version"]
            data = self._drive.files().get_media(fileId=self._file_id, supportsAllDrives=True).execute()
            self.last_version = str(version)
            return from_markdown(data.decode("utf-8"))

    def save(self, records: list[MemoryRecord]) -> None:
        from googleapiclient.http import MediaIoBaseUpload

        with self._lock:
            body = io.BytesIO(to_markdown(records).encode("utf-8"))
            media = MediaIoBaseUpload(body, mimetype="text/markdown", resumable=False)
            updated = self._drive.files().update(fileId=self._file_id, media_body=media, fields="version",
                                                 supportsAllDrives=True).execute()
            self.last_version = str(updated["version"])
