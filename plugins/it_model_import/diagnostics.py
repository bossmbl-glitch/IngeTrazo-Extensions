"""Persist import failures locally so they can be diagnosed after closing UI."""
from datetime import datetime
import os
from pathlib import Path
import tempfile


def record(message):
    try:
        directory = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "IngeTrazo"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "model-import.log"
        if path.exists() and path.stat().st_size > 4 * 1024 * 1024:
            path.replace(directory / "model-import.previous.log")
        with path.open("a", encoding="utf-8") as out:
            out.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except OSError:
        pass
