from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Input, Label, Static

from . import preview as preview_mod
from . import rclone
from .modals import DownloadModal, DownloadProgressModal, TransferOutcome
from .rclone import Entry
from .state import DownloadLedger


class BrowserScreen(Screen):
    LISTING_CACHE_SIZE = 32

    DEFAULT_CSS = """
    BrowserScreen {
        layout: vertical;
    }
    #breadcrumb {
        height: 1;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
        text-style: bold;
    }
    #content {
        height: 1fr;
        layout: horizontal;
    }
    DataTable {
        width: 3fr;
    }
    #preview-panel {
        width: 2fr;
        border-left: solid $panel-darken-1;
        padding: 0 1;
        overflow-y: auto;
        overflow-x: hidden;
    }
    #filter-row {
        height: 1;
        layout: horizontal;
        display: none;
        background: $panel;
    }
    #filter-label {
        width: auto;
        padding: 0 1;
        color: $accent;
    }
    #filter-input {
        height: 1;
        border: none;
        background: $panel;
    }
    #status {
        height: 1;
        background: $panel-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "", show=False),
        Binding("down", "cursor_down", "", show=False),
        Binding("k", "cursor_up", "", show=False),
        Binding("up", "cursor_up", "", show=False),
        Binding("g", "cursor_top", "", show=False),
        Binding("G", "cursor_bottom", "", show=False),
        Binding("h", "go_parent", "Back", show=True),
        Binding("left", "go_parent", "", show=False),
        Binding("l", "enter_item", "Enter", show=True),
        Binding("right", "enter_item", "", show=False),
        Binding("enter", "enter_item", "", show=False),
        Binding("d", "download", "Download", show=True),
        Binding("slash", "toggle_filter", "Filter", show=True),
        Binding("escape", "clear_filter", "", show=False),
        Binding("r", "refresh_listing", "Refresh", show=True),
        Binding("q", "quit_app", "Quit", show=True),
    ]

    current_path: reactive[str] = reactive("", init=False)
    filter_text: reactive[str] = reactive("", init=False)

    def __init__(self, remote: str, ledger: DownloadLedger) -> None:
        super().__init__()
        self._remote = remote
        self._ledger = ledger
        self._all_entries: list[Entry] = []
        self._filtered_entries: list[Entry] = []
        self._listing_cache: OrderedDict[str, list[Entry]] = OrderedDict()
        self._filter_active: bool = False
        self._preview_timer: Optional[Timer] = None

    # ── Layout ─────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Label(self._breadcrumb(), id="breadcrumb")
        with Horizontal(id="content"):
            yield DataTable(id="file-list", cursor_type="row", zebra_stripes=True)
            with ScrollableContainer(id="preview-panel"):
                yield Static("", id="preview")
        with Horizontal(id="filter-row"):
            yield Label("/ ", id="filter-label")
            yield Input(placeholder="type to filter…", id="filter-input")
        yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#file-list", DataTable)
        table.add_column("", width=2, key="icon")
        table.add_column("Name", width=40, key="name")
        table.add_column("Size", width=10, key="size")
        table.add_column("Modified", width=12, key="mtime")
        self._load_listing(self.current_path)

    # ── Reactive watchers ──────────────────────────────────────────────────────

    def watch_current_path(self, path: str) -> None:
        self.query_one("#breadcrumb", Label).update(self._breadcrumb(path))
        self._load_listing(path)

    def watch_filter_text(self, text: str) -> None:
        self._apply_filter(text)

    # ── Listing ────────────────────────────────────────────────────────────────

    def _breadcrumb(self, path: str = "") -> str:
        display = f"{self._remote}:/{path}" if path else f"{self._remote}:/"
        return f"📦 {display}"

    def _remote_path(self, rel_path: str = "") -> str:
        base = self.current_path
        if rel_path:
            full = f"{base}/{rel_path}" if base else rel_path
        else:
            full = base
        return f"{self._remote}:{full}" if full else f"{self._remote}:"

    def _cache_listing(self, path: str, entries: list[Entry]) -> None:
        self._listing_cache[path] = list(entries)
        self._listing_cache.move_to_end(path)
        while len(self._listing_cache) > self.LISTING_CACHE_SIZE:
            self._listing_cache.popitem(last=False)

    def _get_cached_listing(self, path: str) -> list[Entry] | None:
        entries = self._listing_cache.get(path)
        if entries is None:
            return None
        self._listing_cache.move_to_end(path)
        return list(entries)

    def _show_listing(self, entries: list[Entry]) -> None:
        self._all_entries = list(entries)
        self._apply_filter(self.filter_text)
        self._schedule_preview()

    def _load_listing(self, path: str) -> None:
        cached_entries = self._get_cached_listing(path)
        if cached_entries is not None:
            self._show_listing(cached_entries)
            return

        self._set_status("listing…")
        remote_path = f"{self._remote}:{path}" if path else f"{self._remote}:"
        self.run_worker(self._fetch_listing(path, remote_path), exclusive=True)

    async def _fetch_listing(self, path: str, remote_path: str) -> None:
        try:
            entries = await rclone.lsjson(remote_path)
        except rclone.RcloneError as exc:
            if path == self.current_path:
                self._set_status(f"[red]Error: {exc}[/red]")
            return
        self._cache_listing(path, entries)
        if path != self.current_path:
            return
        self._show_listing(entries)

    def _populate_table(self, entries: list[Entry]) -> None:
        table = self.query_one("#file-list", DataTable)
        table.clear()
        for entry in entries:
            table.add_row(
                entry.icon(),
                entry.name + ("/" if entry.is_dir else ""),
                entry.size_str(),
                entry.mod_time_short(),
            )
        if entries:
            table.move_cursor(row=0)

    def _apply_filter(self, text: str) -> None:
        if text:
            low = text.lower()
            self._filtered_entries = [
                e for e in self._all_entries if low in e.name.lower()
            ]
        else:
            self._filtered_entries = list(self._all_entries)
        self._populate_table(self._filtered_entries)
        count = len(self._filtered_entries)
        total = len(self._all_entries)
        if text:
            self._set_status(f"{count}/{total} items  (filter: {text!r})")
        else:
            self._set_status(f"{total} items")

    def _set_status(self, msg: str) -> None:
        self.query_one("#status", Label).update(msg)

    # ── Preview ────────────────────────────────────────────────────────────────

    def _schedule_preview(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.15, self._trigger_preview)

    def _trigger_preview(self) -> None:
        table = self.query_one("#file-list", DataTable)
        row = table.cursor_row
        if 0 <= row < len(self._filtered_entries):
            entry = self._filtered_entries[row]
            self.run_worker(self._fetch_preview(entry), exclusive=True)

    async def _fetch_preview(self, entry: Entry) -> None:
        base = self.current_path
        remote_prefix = f"{self._remote}:{base}/" if base else f"{self._remote}:"
        preview_panel = self.query_one("#preview-panel", ScrollableContainer)
        renderable = await preview_mod.render(
            entry,
            remote_prefix,
            max_width=preview_panel.content_size.width,
            max_height=preview_panel.content_size.height,
        )

        table = self.query_one("#file-list", DataTable)
        row = table.cursor_row
        if not (0 <= row < len(self._filtered_entries)):
            return
        current_entry = self._filtered_entries[row]
        if current_entry.path != entry.path or current_entry.name != entry.name:
            return
        self.query_one("#preview", Static).update(renderable)

    # ── DataTable events ───────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, _: DataTable.RowHighlighted) -> None:
        self._schedule_preview()

    # ── Navigation actions ─────────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        if self._filter_active:
            return
        table = self.query_one("#file-list", DataTable)
        if table.cursor_row < len(self._filtered_entries) - 1:
            table.move_cursor(row=table.cursor_row + 1)

    def action_cursor_up(self) -> None:
        if self._filter_active:
            return
        table = self.query_one("#file-list", DataTable)
        if table.cursor_row > 0:
            table.move_cursor(row=table.cursor_row - 1)

    def action_cursor_top(self) -> None:
        if not self._filter_active:
            self.query_one("#file-list", DataTable).move_cursor(row=0)

    def action_cursor_bottom(self) -> None:
        if not self._filter_active:
            table = self.query_one("#file-list", DataTable)
            if self._filtered_entries:
                table.move_cursor(row=len(self._filtered_entries) - 1)

    def action_go_parent(self) -> None:
        if self._filter_active:
            self.action_clear_filter()
            return
        if not self.current_path:
            return  # already at root
        parts = self.current_path.rsplit("/", 1)
        self.current_path = parts[0] if len(parts) > 1 else ""

    def action_enter_item(self) -> None:
        if self._filter_active:
            return
        table = self.query_one("#file-list", DataTable)
        row = table.cursor_row
        if not (0 <= row < len(self._filtered_entries)):
            return
        entry = self._filtered_entries[row]
        if entry.is_dir:
            new_path = f"{self.current_path}/{entry.name}" if self.current_path else entry.name
            self.current_path = new_path
        # For files, preview is already shown; do nothing extra

    def action_refresh_listing(self) -> None:
        self._listing_cache.pop(self.current_path, None)
        self._load_listing(self.current_path)

    # ── Filter ─────────────────────────────────────────────────────────────────

    def action_toggle_filter(self) -> None:
        filter_row = self.query_one("#filter-row")
        if self._filter_active:
            self.action_clear_filter()
        else:
            self._filter_active = True
            filter_row.display = True
            self.query_one("#filter-input", Input).focus()

    def action_clear_filter(self) -> None:
        self._filter_active = False
        filter_input = self.query_one("#filter-input", Input)
        filter_input.clear()
        self.query_one("#filter-row").display = False
        self.query_one("#file-list", DataTable).focus()
        self.filter_text = ""

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.filter_text = event.value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            # Keep filter active, move focus back to table
            self.query_one("#file-list", DataTable).focus()

    # ── Download ───────────────────────────────────────────────────────────────

    def action_download(self) -> None:
        self.run_worker(self._do_download())

    async def _do_download(self) -> None:
        table = self.query_one("#file-list", DataTable)
        row = table.cursor_row

        if 0 <= row < len(self._filtered_entries):
            entry = self._filtered_entries[row]
            if entry.is_dir:
                rel = f"{self.current_path}/{entry.name}" if self.current_path else entry.name
                remote_path = f"{self._remote}:{rel}"
                default_local = Path.home() / "Dropbox-downloads" / Path(rel)
            else:
                rel = f"{self.current_path}/{entry.name}" if self.current_path else entry.name
                remote_path = f"{self._remote}:{rel}"
                default_local = Path.home() / "Dropbox-downloads" / Path(rel).parent
        else:
            # Download current directory
            rel = self.current_path
            remote_path = self._remote_path()
            default_local = Path.home() / "Dropbox-downloads" / (rel or "")

        is_root = remote_path == f"{self._remote}:"

        remote_size = None
        if is_root:
            self._set_status("calculating size…")
            try:
                remote_size = await rclone.size(remote_path)
            except rclone.RcloneError:
                pass
            self._set_status(f"{len(self._all_entries)} items")

        local_path: Path | None = await self.app.push_screen_wait(
            DownloadModal(
                remote_path=remote_path,
                default_local=default_local,
                is_root=is_root,
                remote_size=remote_size,
            )
        )
        if local_path is None:
            return

        local_path.mkdir(parents=True, exist_ok=True)
        outcome = await self.app.push_screen_wait(
            DownloadProgressModal(remote_path=remote_path, local_path=local_path)
        )
        if outcome == TransferOutcome.SUCCESS:
            self._ledger.record(remote_path, local_path)
            self.notify(f"Downloaded → {local_path}", title="Download complete")
        elif outcome == TransferOutcome.CANCELLED:
            self.notify(f"Cancelled download → {local_path}")

    # ── Quit ───────────────────────────────────────────────────────────────────

    def action_quit_app(self) -> None:
        self.run_worker(self._quit_with_sync())

    async def _quit_with_sync(self) -> None:
        from .modals import SyncModal, SyncProgressModal

        pending = self._ledger.pending_syncs()
        for record in pending:
            should_sync: bool = await self.app.push_screen_wait(SyncModal(record))
            if should_sync:
                outcome = await self.app.push_screen_wait(SyncProgressModal(record))
                if outcome == TransferOutcome.CANCELLED:
                    self.notify(f"Cancelled sync → {record.remote_path}")
        self.app.exit()
