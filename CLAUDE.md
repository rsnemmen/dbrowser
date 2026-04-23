# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

A Textual TUI that wraps `rclone` to give yazi-style interactive browsing of a Dropbox remote. Every cloud operation is an async subprocess — no daemon, no persistent state beyond the current session.

## Stack

- Python 3.11+
- Textual **8.x** (note the major version — APIs differ from 0.x)
- `rclone` on `PATH`, with a remote named exactly `dropbox`

## Dev workflow

```bash
pip install -e .          # editable install, puts `dropbox-cli` on PATH
dropbox-cli                # run the app
python -m dropbox_cli      # equivalent
python -m py_compile src/dropbox_cli/*.py   # quick syntax check
```

No test suite yet. Verification is manual against a real Dropbox remote (see `## Verification` in the original plan at `~/.claude/plans/i-want-to-create-luminous-sketch.md` if still present).

## Textual 8.x gotchas

- `work` decorator: `from textual import work` — **not** `from textual.worker import work` (that was the 0.x path and will ImportError).
- `ModalScreen`, `Screen.run_worker`, `App.push_screen_wait`, `DataTable.RowHighlighted` (with `.cursor_row`, `.row_key`) are all available and used throughout.
- `ProgressBar.progress` is a reactive attribute — assign directly (`bar.progress = 42`).

## Rclone subprocess pattern

All cloud I/O goes through `src/dropbox_cli/rclone.py`. When adding a new op:

- Async one-shot commands (output fits in memory): use the `_run(*args)` helper.
- Streaming commands (copy / sync / anything with `--stats`): use `asyncio.create_subprocess_exec` and `async for line in proc.stderr` with `--use-json-log`. Parse via `progress.parse_log_line`. **Always** wrap the iteration in `try/finally` and kill the subprocess in `finally` — otherwise cancelled workers leak rclone processes.
- Return types: dataclasses, not dicts. See `Entry`, `RemoteSize`, `ProgressEvent`, `SyncEvent`.

## Design decisions (don't undo without asking)

- **One remote, named `dropbox`.** Hardcoded in `config.REMOTE_NAME`. Multi-remote support is an explicit v1 non-goal.
- **Sync is one-way, local → cloud.** Deletions on disk propagate to Dropbox. Bidirectional (`rclone bisync`) is out of scope — don't wire it in without confirming.
- **Download ledger is in-memory only.** If the user quits without confirming sync, nothing is synced. This is intentional: no silent background state.
- **First-run auth is delegated to `rclone config`.** We don't reimplement the OAuth dance. `config.run_interactive_setup()` just prints guidance and shells out.

## File responsibilities

| File | Role |
|------|------|
| `rclone.py` | The only place that shells out to `rclone`. Anything subprocess-y belongs here. |
| `progress.py` | Parse `--use-json-log` stderr. Pure functions, no I/O. |
| `browser.py` | Main `BrowserScreen` — DataTable + preview + filter + navigation. |
| `modals.py` | All `ModalScreen` subclasses (download path, download progress, sync diff, sync progress). |
| `state.py` | `DownloadLedger` — in-memory record of downloaded folders for the exit-sync flow. |
| `config.py` | Detect the `dropbox` remote; launch `rclone config` if missing. |
| `preview.py` | Render an `Entry` as a Rich renderable for the preview pane. Text → syntax, binary → metadata. |
| `app.py` / `__main__.py` | Textual App + CLI entry point. |

## Common pitfalls

- Don't import things from `rclone.py` that create event loops at import time — it's pure async helpers, keep it that way.
- Don't add `print()` calls in the TUI modules — they'll corrupt the Textual display. Use `self.notify()` or `self._set_status()` instead.
- The `Entry.path` from `lsjson` is **relative to the listed directory**, not absolute. When reusing it for a full remote path, prepend the current browser path. See `_fetch_preview` in `browser.py` for the pattern.
