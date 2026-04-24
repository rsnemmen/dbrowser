from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


class LedgerError(Exception):
    pass


def _default_state_path() -> Path:
    base = Path(os.environ["XDG_STATE_HOME"]) if os.environ.get("XDG_STATE_HOME") else Path.home() / ".local" / "state"
    return base / "dbrowser" / "download-ledger.json"


def _normalize_local_path(path: Path) -> Path:
    return path.expanduser().resolve()


@dataclass
class DownloadRecord:
    remote_path: str
    local_path: Path
    downloaded_at: datetime = field(default_factory=datetime.now)

    def to_json(self) -> dict[str, str]:
        return {
            "remote_path": self.remote_path,
            "local_path": str(self.local_path),
            "downloaded_at": self.downloaded_at.isoformat(),
        }

    @classmethod
    def from_json(cls, data: object) -> DownloadRecord:
        if not isinstance(data, dict):
            raise LedgerError("Download ledger entries must be JSON objects.")

        remote_path = data.get("remote_path")
        if not isinstance(remote_path, str) or not remote_path:
            raise LedgerError("Download ledger entry is missing a valid remote_path.")

        local_path = data.get("local_path")
        if not isinstance(local_path, str) or not local_path:
            raise LedgerError("Download ledger entry is missing a valid local_path.")

        downloaded_at = data.get("downloaded_at")
        if not isinstance(downloaded_at, str) or not downloaded_at:
            raise LedgerError("Download ledger entry is missing a valid downloaded_at.")

        try:
            timestamp = datetime.fromisoformat(downloaded_at)
        except ValueError as exc:
            raise LedgerError(
                f"Download ledger entry has invalid downloaded_at: {downloaded_at!r}."
            ) from exc

        return cls(
            remote_path=remote_path,
            local_path=_normalize_local_path(Path(local_path)),
            downloaded_at=timestamp,
        )


class DownloadLedger:
    def __init__(
        self,
        records: list[DownloadRecord] | None = None,
        state_path: Path | None = None,
    ) -> None:
        self._records: list[DownloadRecord] = list(records or [])
        self._state_path = state_path or _default_state_path()

    @classmethod
    def load_default(cls) -> DownloadLedger:
        return cls.load(_default_state_path())

    @classmethod
    def load(cls, state_path: Path) -> DownloadLedger:
        if not state_path.exists():
            return cls(state_path=state_path)

        try:
            payload = json.loads(state_path.read_text())
        except OSError as exc:
            raise LedgerError(f"Could not read download ledger {state_path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LedgerError(
                f"Download ledger {state_path} is not valid JSON: {exc.msg}."
            ) from exc

        if not isinstance(payload, list):
            raise LedgerError(f"Download ledger {state_path} must contain a JSON array.")

        return cls(
            records=[DownloadRecord.from_json(item) for item in payload],
            state_path=state_path,
        )

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LedgerError(
                f"Could not create download ledger directory {self._state_path.parent}: {exc}"
            ) from exc

        if not self._records:
            try:
                if self._state_path.exists():
                    self._state_path.unlink()
            except OSError as exc:
                raise LedgerError(
                    f"Could not remove download ledger {self._state_path}: {exc}"
                ) from exc
            return

        temp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        payload = json.dumps([record.to_json() for record in self._records], indent=2) + "\n"

        try:
            temp_path.write_text(payload)
        except OSError as exc:
            raise LedgerError(f"Could not write download ledger {temp_path}: {exc}") from exc

        try:
            temp_path.replace(self._state_path)
        except OSError as exc:
            raise LedgerError(
                f"Could not replace download ledger {self._state_path}: {exc}"
            ) from exc

    def record(self, remote_path: str, local_path: Path) -> None:
        normalized_path = _normalize_local_path(local_path)
        self._records = [
            record
            for record in self._records
            if record.remote_path != remote_path and record.local_path != normalized_path
        ]
        self._records.append(
            DownloadRecord(remote_path=remote_path, local_path=normalized_path)
        )
        self._save()

    def tracked_records(self) -> list[DownloadRecord]:
        return self.prune_missing()

    def prune_missing(self) -> list[DownloadRecord]:
        existing = [record for record in self._records if record.local_path.exists()]
        if len(existing) != len(self._records):
            self._records = existing
            self._save()
        return list(self._records)

    def pending_syncs(self) -> list[DownloadRecord]:
        return self.tracked_records()

    def clear(self) -> None:
        self._records.clear()
        self._save()
