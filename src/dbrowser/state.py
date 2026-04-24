from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class DownloadRecord:
    remote_path: str
    local_path: Path
    downloaded_at: datetime = field(default_factory=datetime.now)


class DownloadLedger:
    def __init__(self) -> None:
        self._records: list[DownloadRecord] = []

    def record(self, remote_path: str, local_path: Path) -> None:
        self._records = [r for r in self._records if r.remote_path != remote_path]
        self._records.append(DownloadRecord(remote_path=remote_path, local_path=local_path))

    def pending_syncs(self) -> list[DownloadRecord]:
        return [r for r in self._records if r.local_path.exists()]

    def clear(self) -> None:
        self._records.clear()
