from __future__ import annotations

import asyncio
from enum import StrEnum
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, LoadingIndicator, ProgressBar, Static
from textual.worker import Worker

from . import rclone
from .progress import ProgressEvent, SyncEvent, _fmt_bytes
from .state import DownloadRecord


class TransferOutcome(StrEnum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    ERROR = "error"


# ── Download path modal ────────────────────────────────────────────────────────

class DownloadModal(ModalScreen[Path | None]):
    """Prompt user for download destination and (for root) confirm size."""

    DEFAULT_CSS = """
    DownloadModal > Vertical {
        width: 70;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    DownloadModal Label { margin-bottom: 1; }
    DownloadModal #warning { color: $warning; margin-bottom: 1; }
    DownloadModal #size-info { color: $text-muted; margin-bottom: 1; }
    DownloadModal Input { margin-bottom: 1; }
    DownloadModal #btn-row { layout: horizontal; height: auto; margin-top: 1; }
    DownloadModal Button { margin-right: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(
        self,
        remote_path: str,
        default_local: Path,
        is_root: bool = False,
        remote_size: rclone.RemoteSize | None = None,
    ) -> None:
        super().__init__()
        self._remote_path = remote_path
        self._default_local = default_local
        self._is_root = is_root
        self._remote_size = remote_size

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Download  [bold]{self._remote_path}[/bold]")
            if self._is_root:
                yield Label(
                    "⚠  You are downloading the entire Dropbox root.",
                    id="warning",
                )
            if self._remote_size is not None:
                yield Label(f"Size: {self._remote_size}", id="size-info")
            yield Label("Local destination:")
            yield Input(value=str(self._default_local), id="path-input")
            with Center(id="btn-row"):
                yield Button("Download", variant="primary", id="btn-ok")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ok":
            path = Path(self.query_one("#path-input", Input).value).expanduser()
            self.dismiss(path)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── Download progress modal ────────────────────────────────────────────────────

class DownloadProgressModal(ModalScreen[TransferOutcome]):
    """Shows rclone copy progress and supports cancellation."""

    DEFAULT_CSS = """
    DownloadProgressModal > Vertical {
        width: 72;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    DownloadProgressModal Label { margin-bottom: 1; }
    DownloadProgressModal ProgressBar { margin-bottom: 1; }
    DownloadProgressModal #speed { color: $text-muted; }
    DownloadProgressModal #btn-row { layout: horizontal; height: auto; margin-top: 1; }
    DownloadProgressModal Button { margin-right: 1; }
    DownloadProgressModal #btn-done { display: none; }
    DownloadProgressModal.done #btn-cancel { display: none; }
    DownloadProgressModal.done #btn-done { display: block; }
    DownloadProgressModal.done #spinner { display: none; }
    """

    BINDINGS = [Binding("escape", "cancel_or_close", show=False)]

    def __init__(self, remote_path: str, local_path: Path) -> None:
        super().__init__()
        self._remote_path = remote_path
        self._local_path = local_path
        self._copy_worker: Worker[None] | None = None
        self._outcome: TransferOutcome | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Downloading  [bold]{self._remote_path}[/bold]")
            yield Label(f"→ {self._local_path}", id="dest")
            yield ProgressBar(total=100, show_eta=True, id="progress")
            yield Label("", id="speed")
            yield LoadingIndicator(id="spinner")
            with Center(id="btn-row"):
                yield Button("Cancel", variant="default", id="btn-cancel")
                yield Button("Done ✓", variant="success", id="btn-done")

    def on_mount(self) -> None:
        self._run_copy()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.action_cancel_or_close()
            return
        self.dismiss(self._outcome or TransferOutcome.ERROR)

    def action_cancel_or_close(self) -> None:
        if self._outcome is not None:
            self.dismiss(self._outcome)
            return
        if self._copy_worker is not None:
            self.query_one("#speed", Label).update("[yellow]Cancelling download…[/yellow]")
            self._copy_worker.cancel()

    def _run_copy(self) -> None:
        self._copy_worker = self.run_worker(self._do_copy(), exclusive=True)

    def _finish(self, outcome: TransferOutcome, message: str) -> None:
        self._outcome = outcome
        self.query_one("#speed", Label).update(message)
        self.add_class("done")

    async def _do_copy(self) -> None:
        bar = self.query_one("#progress", ProgressBar)
        speed_label = self.query_one("#speed", Label)
        try:
            async for ev in rclone.copy(self._remote_path, str(self._local_path)):
                if isinstance(ev, ProgressEvent):
                    bar.progress = ev.percent()
                    speed_label.update(
                        f"{_fmt_bytes(ev.bytes_done)} / {_fmt_bytes(ev.total_bytes)}"
                        f"  •  {ev.speed_str()}"
                    )
        except asyncio.CancelledError:
            self.dismiss(TransferOutcome.CANCELLED)
            return
        except rclone.RcloneError as exc:
            self._finish(TransferOutcome.ERROR, f"[red]Error: {exc}[/red]")
            return

        bar.progress = 100
        self._finish(TransferOutcome.SUCCESS, "[green]Download complete ✓[/green]")


# ── Sync-on-exit modal ─────────────────────────────────────────────────────────

class SyncModal(ModalScreen[bool]):
    """Runs a dry-run diff, shows results, lets user confirm or skip."""

    DEFAULT_CSS = """
    SyncModal > Vertical {
        width: 80;
        max-height: 30;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    SyncModal Label { margin-bottom: 1; }
    SyncModal #changes { color: $text-muted; height: 10; overflow-y: auto; }
    SyncModal #btn-row { layout: horizontal; height: auto; margin-top: 1; }
    SyncModal Button { margin-right: 1; }
    SyncModal #spinner { margin-bottom: 1; }
    SyncModal #spinner.done { display: none; }
    SyncModal #btn-sync { display: none; }
    SyncModal #btn-sync.visible { display: block; }
    """

    BINDINGS = [Binding("escape", "skip", show=False)]

    def __init__(self, record: DownloadRecord) -> None:
        super().__init__()
        self._record = record
        self._sync_events: list[SyncEvent] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f"Sync  [bold]{self._record.local_path}[/bold]\n"
                f"   →  [bold]{self._record.remote_path}[/bold]"
            )
            yield LoadingIndicator(id="spinner")
            yield Static("", id="changes")
            with Center(id="btn-row"):
                yield Button("Sync", variant="primary", id="btn-sync")
                yield Button("Skip", variant="default", id="btn-skip")

    def on_mount(self) -> None:
        self.run_worker(self._run_dry_run(), exclusive=True)

    async def _run_dry_run(self) -> None:
        changes_widget = self.query_one("#changes", Static)
        spinner = self.query_one("#spinner", LoadingIndicator)
        sync_btn = self.query_one("#btn-sync", Button)

        events: list[SyncEvent] = []
        try:
            async for ev in rclone.sync(
                str(self._record.local_path),
                self._record.remote_path,
                dry_run=True,
            ):
                if isinstance(ev, SyncEvent):
                    events.append(ev)
        except rclone.RcloneError as exc:
            changes_widget.update(f"[red]Dry-run error: {exc}[/red]")
            spinner.add_class("done")
            return

        spinner.add_class("done")
        self._sync_events = events

        if not events:
            changes_widget.update("[dim]No local changes detected — nothing to sync.[/dim]")
        else:
            lines = [f"  {e.operation}  {e.path}" for e in events[:20]]
            if len(events) > 20:
                lines.append(f"  … and {len(events) - 20} more")
            changes_widget.update("\n".join(lines))
            sync_btn.add_class("visible")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-sync":
            self.dismiss(bool(self._sync_events))
        else:
            self.dismiss(False)

    def action_skip(self) -> None:
        self.dismiss(False)


class SyncProgressModal(ModalScreen[TransferOutcome]):
    """Shows rclone sync progress and supports cancellation."""

    DEFAULT_CSS = """
    SyncProgressModal > Vertical {
        width: 72;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    SyncProgressModal Label { margin-bottom: 1; }
    SyncProgressModal ProgressBar { margin-bottom: 1; }
    SyncProgressModal #status { color: $text-muted; }
    SyncProgressModal #btn-row { layout: horizontal; height: auto; margin-top: 1; }
    SyncProgressModal Button { margin-right: 1; }
    SyncProgressModal #btn-done { display: none; }
    SyncProgressModal.done #btn-cancel { display: none; }
    SyncProgressModal.done #btn-done { display: block; }
    SyncProgressModal.done #spinner { display: none; }
    """

    BINDINGS = [Binding("escape", "cancel_or_close", show=False)]

    def __init__(self, record: DownloadRecord) -> None:
        super().__init__()
        self._record = record
        self._sync_worker: Worker[None] | None = None
        self._outcome: TransferOutcome | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f"Syncing  [bold]{self._record.local_path}[/bold]\n"
                f"      →  [bold]{self._record.remote_path}[/bold]"
            )
            yield ProgressBar(total=100, show_eta=True, id="progress")
            yield Label("", id="status")
            yield LoadingIndicator(id="spinner")
            with Center(id="btn-row"):
                yield Button("Cancel", variant="default", id="btn-cancel")
                yield Button("Done ✓", variant="success", id="btn-done")

    def on_mount(self) -> None:
        self._sync_worker = self.run_worker(self._do_sync(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.action_cancel_or_close()
            return
        self.dismiss(self._outcome or TransferOutcome.ERROR)

    def action_cancel_or_close(self) -> None:
        if self._outcome is not None:
            self.dismiss(self._outcome)
            return
        if self._sync_worker is not None:
            self.query_one("#status", Label).update("[yellow]Cancelling sync…[/yellow]")
            self._sync_worker.cancel()

    def _finish(self, outcome: TransferOutcome, message: str) -> None:
        self._outcome = outcome
        self.query_one("#status", Label).update(message)
        self.add_class("done")

    async def _do_sync(self) -> None:
        bar = self.query_one("#progress", ProgressBar)
        status = self.query_one("#status", Label)
        try:
            async for ev in rclone.sync(
                str(self._record.local_path),
                self._record.remote_path,
                dry_run=False,
            ):
                if isinstance(ev, ProgressEvent):
                    bar.progress = ev.percent()
                    status.update(
                        f"{_fmt_bytes(ev.bytes_done)} / {_fmt_bytes(ev.total_bytes)}"
                        f"  •  {ev.speed_str()}"
                    )
        except asyncio.CancelledError:
            self.dismiss(TransferOutcome.CANCELLED)
            return
        except rclone.RcloneError as exc:
            self._finish(TransferOutcome.ERROR, f"[red]Error: {exc}[/red]")
            return

        bar.progress = 100
        self.dismiss(TransferOutcome.SUCCESS)
