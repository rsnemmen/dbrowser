from __future__ import annotations

from textual.app import App

from .browser import BrowserScreen
from .state import DownloadLedger


class DbrowserApp(App):
    """dbrowser — a yazi-style Dropbox file browser."""

    TITLE = "dbrowser"

    def __init__(self, remote: str) -> None:
        super().__init__()
        self._remote = remote
        self.ledger = DownloadLedger()

    def on_mount(self) -> None:
        self.push_screen(BrowserScreen(remote=self._remote, ledger=self.ledger))
