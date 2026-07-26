from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path


class ActivityLogger:
    """Persistent timestamped logger for NVRAM-related activity."""

    def __init__(self, app_root: str | Path | None = None):
        if app_root is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                app_root = Path(local_app_data) / "NVRAM"
            else:
                app_root = Path.home() / ".nvram"
        self.app_root = Path(app_root)
        self.log_dir = self.app_root / "logs"
        self.log_path = self.log_dir / "nvram.log"
        self._lock = threading.Lock()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def write(self, event: str, message: str) -> str:
        clean_event = " ".join(str(event).split()).upper()
        clean_message = " ".join(str(message).replace("\r", " ").replace("\n", " ").split())
        line = f"{self.timestamp()} | {clean_event} | {clean_message}"
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
        return line

    def read_text(self) -> str:
        if not self.log_path.exists():
            return ""
        return self.log_path.read_text(encoding="utf-8", errors="replace")
