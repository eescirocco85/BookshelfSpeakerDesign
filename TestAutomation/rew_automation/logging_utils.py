"""Simple timestamped debug logging for REW automation runs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading


_LOCK = threading.Lock()
_LOG_PATH: Path | None = None


def get_log_path() -> Path:
    global _LOG_PATH
    with _LOCK:
        if _LOG_PATH is None:
            _LOG_PATH = _create_log_path()
            _write_line(_LOG_PATH, f"Log started: {_LOG_PATH}")
        return _LOG_PATH


def write_debug(message: str) -> None:
    with _LOCK:
        path = _LOG_PATH
        if path is None:
            path = _create_log_path()
            globals()["_LOG_PATH"] = path
            _write_line(path, f"Log started: {path}")
        _write_line(path, message)


def _create_log_path() -> Path:
    root = Path(__file__).resolve().parents[1] / "output" / "logs"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return root / f"rew_gui_{stamp}.log"


def _write_line(path: Path, message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")
        handle.flush()
