from __future__ import annotations

import asyncio

from textual.app import App
from textual.worker import Worker

from .browser import BrowserScreen
from .modals import run_sync_prompt
from .state import DownloadLedger, LedgerError


class DbrowserApp(App):
    """dbrowser — a yazi-style Dropbox file browser."""

    TITLE = "dbrowser"

    def __init__(self, remote: str) -> None:
        super().__init__()
        self._remote = remote
        self._ledger_load_error: str | None = None
        self._startup_sync_worker: Worker[None] | None = None
        self._sync_flow_lock: asyncio.Lock | None = None
        try:
            self.ledger = DownloadLedger.load_default()
        except LedgerError as exc:
            self.ledger = DownloadLedger()
            self._ledger_load_error = str(exc)

    def on_mount(self) -> None:
        self.push_screen(BrowserScreen(remote=self._remote, ledger=self.ledger))
        self._sync_flow_lock = asyncio.Lock()
        if self._ledger_load_error is not None:
            self.notify(self._ledger_load_error, title="Download ledger")
        self.set_timer(0, self._start_startup_sync_check)

    def _start_startup_sync_check(self) -> None:
        self._startup_sync_worker = self.run_worker(
            self._run_startup_sync_check(),
            exclusive=True,
        )

    async def _run_startup_sync_check(self) -> None:
        await self.sync_tracked_downloads()

    async def sync_tracked_downloads(self) -> None:
        if self._sync_flow_lock is None:
            return

        async with self._sync_flow_lock:
            try:
                records = self.ledger.tracked_records()
            except LedgerError as exc:
                self.notify(str(exc), title="Download ledger")
                return

            for record in records:
                await run_sync_prompt(self, record)
