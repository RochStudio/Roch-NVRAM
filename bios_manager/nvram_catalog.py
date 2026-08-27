# Roch NVRAM -- an editor for AMI SCEWIN NVRAM exports.
# Copyright (C) 2026 Roch Studio
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import csv
import json
import os
import shutil
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class CatalogEntry:
    """A single catalogued NVRAM capture."""

    timestamp: str
    source: str
    path: str
    hii_crc32: str
    settings: int
    sha256: str
    amisce_version: str = ""
    archived_path: str = ""

    @classmethod
    def from_json(cls, data: dict) -> "CatalogEntry":
        return cls(
            timestamp=str(data.get("timestamp") or ""),
            source=str(data.get("source") or ""),
            path=str(data.get("path") or ""),
            hii_crc32=str(data.get("hii_crc32") or ""),
            settings=int(data.get("settings") or 0),
            sha256=str(data.get("sha256") or ""),
            amisce_version=str(data.get("amisce_version") or ""),
            archived_path=str(data.get("archived_path") or ""),
        )


class NvramCatalog:
    """Persistent catalog of NVRAM captures.

    Unlike the activity log, this does not record individual changes. Every time
    the tool opens an NVRAM export (startup, manual open, live refresh, or a
    comparison load) one dated record is appended describing that NVRAM file, and
    a verbatim copy of the actual nvram.txt is archived alongside the catalog so
    it can be reopened later.
    """

    def __init__(self, app_root: str | Path | None = None):
        if app_root is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                app_root = Path(local_app_data) / "NVRAM"
            else:
                app_root = Path.home() / ".nvram"
        self.app_root = Path(app_root)
        self.catalog_dir = self.app_root / "logs"
        self.catalog_path = self.catalog_dir / "nvram_catalog.jsonl"
        self.archive_dir = self.catalog_dir / "nvram"
        self._lock = threading.Lock()
        self.catalog_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _archive_file(self, source: Path, hii_crc32: str, sha256: str) -> str:
        """Copy the actual nvram.txt into the catalog archive and return its path.

        Files are content-addressed by SHA-256 so re-opening an identical NVRAM
        does not create duplicate copies. Every catalog row still points at the
        verbatim file it was captured from.
        """
        if not source.is_file():
            return ""
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        key = (sha256 or "")[:16] or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stem = f"{hii_crc32 or 'NVRAM'}_{key}"
        destination = self.archive_dir / f"{stem}.txt"
        try:
            if not destination.exists():
                shutil.copy2(source, destination)
        except OSError:
            return ""
        return str(destination)

    def record(
        self,
        source: str,
        path: str | Path,
        hii_crc32: str,
        settings: int,
        sha256: str,
        amisce_version: str = "",
        archive_source: str | Path | None = None,
    ) -> CatalogEntry:
        hii = str(hii_crc32 or "").upper()
        sha = str(sha256 or "")
        archived_path = ""
        if archive_source is not None:
            archived_path = self._archive_file(Path(archive_source), hii, sha)

        entry = CatalogEntry(
            timestamp=self.timestamp(),
            source=" ".join(str(source).split()),
            path=str(path),
            hii_crc32=hii,
            settings=int(settings or 0),
            sha256=sha,
            amisce_version=str(amisce_version or ""),
            archived_path=archived_path,
        )
        with self._lock:
            self.catalog_dir.mkdir(parents=True, exist_ok=True)
            with self.catalog_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(asdict(entry)) + "\n")
        return entry

    def entries(self) -> list[CatalogEntry]:
        if not self.catalog_path.exists():
            return []
        entries: list[CatalogEntry] = []
        text = self.catalog_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(CatalogEntry.from_json(json.loads(line)))
            except (ValueError, TypeError):
                continue
        return entries

    def export_csv(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "Date & Time",
                    "Source",
                    "SCEWIN Version",
                    "HII CRC32",
                    "Settings",
                    "SHA-256",
                    "Original Path",
                    "Archived NVRAM File",
                ]
            )
            for entry in self.entries():
                writer.writerow(
                    [
                        entry.timestamp,
                        entry.source,
                        entry.amisce_version,
                        entry.hii_crc32,
                        entry.settings,
                        entry.sha256,
                        entry.path,
                        entry.archived_path,
                    ]
                )
        return output
