from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProgressEvent:
    bytes_done: int = 0
    total_bytes: int = 0
    transfers: int = 0
    total_transfers: int = 0
    speed: float = 0.0
    eta: Optional[float] = None
    elapsed: float = 0.0

    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, self.bytes_done / self.total_bytes * 100)

    def speed_str(self) -> str:
        return _fmt_bytes(int(self.speed)) + "/s"


@dataclass
class SyncEvent:
    operation: str  # copy, delete, move, update
    path: str
    is_dry_run: bool = False


def _fmt_bytes(n: int | float) -> str:
    v: float = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024:
            return f"{int(v)} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} PB"


def parse_log_line(line: bytes | str) -> tuple[ProgressEvent | SyncEvent | None, str | None]:
    """Parse a JSON log line from rclone --use-json-log.

    Returns (event, error_message) — at most one is non-None.
    """
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace").strip()
    else:
        line = line.strip()

    if not line:
        return None, None

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None, None

    level = data.get("level", "")
    msg = data.get("msg", "")

    if level == "error":
        return None, msg

    if "stats" in data:
        s = data["stats"]
        return ProgressEvent(
            bytes_done=s.get("bytes", 0),
            total_bytes=s.get("totalBytes", 0),
            transfers=s.get("transfers", 0),
            total_transfers=s.get("totalTransfers", 0),
            speed=s.get("speed", 0.0),
            eta=s.get("eta"),
            elapsed=s.get("elapsedTime", 0.0),
        ), None

    skipped = data.get("skipped")
    if skipped:
        op = "delete" if skipped == "remove directory" else skipped
        path = data.get("object", "")
        return SyncEvent(operation=op, path=path, is_dry_run=True), None

    # Dry-run change lines: "NOTICE: Would copy: path/to/file"
    # or just "Would copy: path/to/file"
    clean = msg.removeprefix("NOTICE: ").strip()
    if clean.startswith("Would "):
        rest = clean[6:]  # strip "Would "
        parts = rest.split(": ", 1)
        op = parts[0].lower() if len(parts) == 2 else "copy"
        path = parts[1] if len(parts) == 2 else rest
        return SyncEvent(operation=op, path=path, is_dry_run=True), None

    return None, None
