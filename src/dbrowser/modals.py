from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, LoadingIndicator, ProgressBar, Static
from textual.worker import Worker

from . import rclone
from .progress import ProgressEvent, SyncEvent, _fmt_bytes
from .state import DownloadRecord


class TransferOutcome(StrEnum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class _RowState:
    record: DownloadRecord
    events: list[SyncEvent] = field(default_factory=list)
    status: Literal["checking", "clean", "changed", "error", "syncing", "done", "failed"] = "checking"
    error: str | None = None
    selected: bool = False

    def check_cell(self) -> str:
        if self.status not in ("changed",):
            return "   "
        return "[✓]" if self.selected else "[ ]"

    def status_cell(self) -> str:
        if self.status == "checking":
            return "Checking…"
        if self.status == "clean":
            return "No changes"
        if self.status == "changed":
            n = len(self.events)
            return f"● {n} change{'s' if n != 1 else ''}"
        if self.status == "error":
            return f"✗ {self.error or 'Error'}"
        if self.status == "syncing":
            return "→ Syncing…"
        if self.status == "done":
            return "✓ Synced"
        if self.status == "failed":
            return f"✗ {self.error or 'Failed'}"
        return ""


async def collect_sync_events(record: DownloadRecord) -> list[SyncEvent]:
    events: list[SyncEvent] = []
    async for event in rclone.sync(
        str(record.local_path),
        record.remote_path,
        dry_run=True,
    ):
        if isinstance(event, SyncEvent):
            events.append(event)
    return events


async def _stream_sync(
    record: DownloadRecord,
    on_progress: Callable[[ProgressEvent], None],
) -> tuple[Literal["success", "error"], str | None]:
    """Stream a live sync to remote; CancelledError propagates (callers handle it)."""
    try:
        async for ev in rclone.sync(str(record.local_path), record.remote_path, dry_run=False):
            if isinstance(ev, ProgressEvent):
                on_progress(ev)
    except rclone.RcloneError as exc:
        return "error", str(exc)
    return "success", None


async def run_sync_prompt(app: App, record: DownloadRecord) -> TransferOutcome | None:
    try:
        events = await collect_sync_events(record)
    except rclone.RcloneError as exc:
        app.notify(
            f"Sync check failed → {record.remote_path}: {exc}",
            title="Sync check error",
        )
        return None

    if not events:
        return None

    should_sync: bool = await app.push_screen_wait(SyncModal(record, sync_events=events))
    if not should_sync:
        return None

    outcome: TransferOutcome = await app.push_screen_wait(SyncProgressModal(record))
    if outcome == TransferOutcome.CANCELLED:
        app.notify(f"Cancelled sync → {record.remote_path}")
    return outcome


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
                remote_name = self._remote_path.split(":")[0]
                yield Label(
                    f"⚠  You are downloading the entire {remote_name} root.",
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

    def __init__(
        self,
        record: DownloadRecord,
        sync_events: list[SyncEvent] | None = None,
    ) -> None:
        super().__init__()
        self._record = record
        self._sync_events = list(sync_events or [])
        self._has_precomputed_events = sync_events is not None

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
        if self._has_precomputed_events:
            self.query_one("#spinner", LoadingIndicator).add_class("done")
            self._render_events()
            return
        self.run_worker(self._run_dry_run(), exclusive=True)

    async def _run_dry_run(self) -> None:
        changes_widget = self.query_one("#changes", Static)
        spinner = self.query_one("#spinner", LoadingIndicator)
        try:
            self._sync_events = await collect_sync_events(self._record)
        except rclone.RcloneError as exc:
            changes_widget.update(f"[red]Dry-run error: {exc}[/red]")
            spinner.add_class("done")
            return

        spinner.add_class("done")
        self._render_events()

    def _render_events(self) -> None:
        changes_widget = self.query_one("#changes", Static)
        sync_btn = self.query_one("#btn-sync", Button)

        if not self._sync_events:
            changes_widget.update("[dim]No local changes detected — nothing to sync.[/dim]")
        else:
            lines = [f"  {event.operation}  {event.path}" for event in self._sync_events[:20]]
            if len(self._sync_events) > 20:
                lines.append(f"  … and {len(self._sync_events) - 20} more")
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


# ── Batch sync screen ─────────────────────────────────────────────────────────

class BatchSyncScreen(ModalScreen[None]):
    """Single screen for reviewing and syncing all tracked folders."""

    DEFAULT_CSS = """
    BatchSyncScreen > Vertical {
        width: 82;
        height: auto;
        max-height: 38;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    BatchSyncScreen #title { margin-bottom: 1; }
    BatchSyncScreen DataTable { height: auto; max-height: 14; margin-bottom: 1; }
    BatchSyncScreen #progress-section { display: none; margin-bottom: 1; }
    BatchSyncScreen.syncing #progress-section { display: block; }
    BatchSyncScreen #progress-label { color: $text-muted; height: 1; }
    BatchSyncScreen #status-line { color: $text-muted; height: 1; margin-bottom: 1; }
    BatchSyncScreen #btn-row { layout: horizontal; height: auto; }
    BatchSyncScreen Button { margin-right: 1; }
    """

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("space", "toggle_row", "Toggle"),
        Binding("a", "toggle_all", "Toggle all"),
        Binding("enter", "confirm", "Sync selected"),
        Binding("escape", "skip_all", "Skip all"),
    ]

    def __init__(self, records: list[DownloadRecord]) -> None:
        super().__init__()
        self._rows: dict[str, _RowState] = {
            r.remote_path: _RowState(record=r) for r in records
        }
        self._row_order: list[str] = [r.remote_path for r in records]
        self._dry_run_worker: Worker[None] | None = None
        self._sync_worker: Worker[None] | None = None
        self._checking_done = False
        self._syncing = False
        self._all_done = False
        self._dismissed = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Tracked folders — sync check", id="title")
            yield DataTable(id="folders", cursor_type="row")
            with Vertical(id="progress-section"):
                yield Label("", id="progress-label")
                yield ProgressBar(total=100, show_eta=True, id="progress")
            yield Label("", id="status-line")
            with Center(id="btn-row"):
                yield Button("Sync selected", variant="primary", id="btn-sync", disabled=True)
                yield Button("Skip all", variant="default", id="btn-skip")

    def on_mount(self) -> None:
        table = self.query_one("#folders", DataTable)
        table.add_column("", key="check", width=5)
        table.add_column("Remote path", key="remote", width=44)
        table.add_column("Status", key="status", width=22)
        for state in self._rows.values():
            table.add_row(
                state.check_cell(),
                state.record.remote_path,
                state.status_cell(),
                key=state.record.remote_path,
            )
        self._dry_run_worker = self.run_worker(self._run_dry_runs(), exclusive=True)

    def _update_row(self, state: _RowState) -> None:
        if self._dismissed:
            return
        table = self.query_one("#folders", DataTable)
        key = state.record.remote_path
        table.update_cell(key, "check", state.check_cell())
        table.update_cell(key, "status", state.status_cell())

    async def _run_dry_runs(self) -> None:
        sem = asyncio.Semaphore(4)

        async def check_one(state: _RowState) -> None:
            async with sem:
                try:
                    events = await collect_sync_events(state.record)
                    state.events = events
                    state.status = "clean" if not events else "changed"
                    state.selected = bool(events)
                except rclone.RcloneError as exc:
                    state.status = "error"
                    state.error = str(exc)
            self._update_row(state)

        await asyncio.gather(*(check_one(s) for s in self._rows.values()))
        self._on_dry_runs_complete()

    def _on_dry_runs_complete(self) -> None:
        self._checking_done = True
        has_changes = any(s.status == "changed" for s in self._rows.values())
        if not has_changes:
            self.query_one("#status-line", Label).update("Everything up to date.")
            self.set_timer(0.8, self._safe_dismiss)
            return
        self._refresh_selected_status()

    def _refresh_selected_status(self) -> None:
        selected = sum(1 for s in self._rows.values() if s.selected)
        self.query_one("#btn-sync", Button).disabled = selected == 0
        self.query_one("#status-line", Label).update(
            f"{selected} folder(s) selected for sync."
            if selected > 0
            else "No folders selected — press Space to select."
        )

    # ── Navigation ─────────────────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        table = self.query_one("#folders", DataTable)
        if table.cursor_row < len(self._row_order) - 1:
            table.move_cursor(row=table.cursor_row + 1)

    def action_cursor_up(self) -> None:
        table = self.query_one("#folders", DataTable)
        if table.cursor_row > 0:
            table.move_cursor(row=table.cursor_row - 1)

    # ── Selection ──────────────────────────────────────────────────────────────

    def action_toggle_row(self) -> None:
        if not self._checking_done or self._syncing or self._all_done:
            return
        table = self.query_one("#folders", DataTable)
        idx = table.cursor_row
        if not (0 <= idx < len(self._row_order)):
            return
        state = self._rows[self._row_order[idx]]
        if state.status != "changed":
            return
        state.selected = not state.selected
        self._update_row(state)
        self._refresh_selected_status()

    def action_toggle_all(self) -> None:
        if not self._checking_done or self._syncing or self._all_done:
            return
        changed = [s for s in self._rows.values() if s.status == "changed"]
        if not changed:
            return
        all_selected = all(s.selected for s in changed)
        for s in changed:
            s.selected = not all_selected
            self._update_row(s)
        self._refresh_selected_status()

    # ── Sync ───────────────────────────────────────────────────────────────────

    def action_confirm(self) -> None:
        if not self._checking_done or self._syncing or self._all_done:
            return
        to_sync = [s for s in self._rows.values() if s.selected]
        if not to_sync:
            return
        self._syncing = True
        self.add_class("syncing")
        self.query_one("#btn-sync", Button).disabled = True
        skip_btn = self.query_one("#btn-skip", Button)
        skip_btn.label = "Cancel"
        skip_btn.variant = "warning"
        self._sync_worker = self.run_worker(self._run_syncs(to_sync), exclusive=True)

    async def _run_syncs(self, selected: list[_RowState]) -> None:
        total = len(selected)
        bar = self.query_one("#progress", ProgressBar)
        progress_label = self.query_one("#progress-label", Label)

        for i, state in enumerate(selected):
            state.status = "syncing"
            self._update_row(state)
            progress_label.update(f"Syncing {i + 1}/{total}: {state.record.remote_path}")
            bar.progress = 0

            def on_progress(ev: ProgressEvent, _bar: ProgressBar = bar) -> None:
                _bar.progress = ev.percent()

            try:
                result, err = await _stream_sync(state.record, on_progress)
            except asyncio.CancelledError:
                state.status = "failed"
                state.error = "Cancelled"
                self._update_row(state)
                self._on_sync_done(cancelled=True)
                return

            if result == "error":
                state.status = "failed"
                state.error = err
            else:
                bar.progress = 100
                state.status = "done"
            self._update_row(state)

        self._on_sync_done(cancelled=False)

    def _on_sync_done(self, *, cancelled: bool) -> None:
        self._syncing = False
        self._all_done = True
        self.query_one("#progress-label", Label).update("")
        skip_btn = self.query_one("#btn-skip", Button)
        skip_btn.label = "Close"
        skip_btn.variant = "success"
        self.query_one("#status-line", Label).update(
            "Sync cancelled." if cancelled else "Sync complete."
        )

    # ── Dismiss ────────────────────────────────────────────────────────────────

    def action_skip_all(self) -> None:
        if self._syncing:
            if self._sync_worker is not None:
                self._sync_worker.cancel()
            return
        if self._dry_run_worker is not None and not self._checking_done:
            self._dry_run_worker.cancel()
        self._safe_dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-sync":
            self.action_confirm()
        elif event.button.id == "btn-skip":
            self.action_skip_all()

    def _safe_dismiss(self) -> None:
        if not self._dismissed:
            self._dismissed = True
            self.dismiss(None)
