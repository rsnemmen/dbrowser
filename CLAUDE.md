# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Textual TUI that wraps `rclone` to give yazi-style interactive browsing of cloud storage remotes. Every cloud operation is an async subprocess — no daemon, with persistent state limited to a tracked-download ledger.

Supported backends: any rclone remote of type `dropbox` or `drive` (Google Drive). The backend registry lives in `config.SUPPORTED_BACKENDS`.

## Stack

- Python 3.11+
- Textual **8.x** (note the major version — APIs differ from 0.x). `pyproject.toml` pins `textual>=8.0`.
- `rclone` on `PATH`, with at least one remote of a supported backend type

## Dev workflow

```bash
pip install -e .          # editable install, puts `dbrowser` on PATH
dbrowser                   # run the app
python -m dbrowser         # equivalent
python -m py_compile src/dbrowser/*.py   # quick syntax check
```

No test suite yet. Verification is manual against a real remote (see `## Manual smoke test` below).

## Startup flow

`__main__.main()` runs these steps in order — useful to know when debugging first-run or auth problems:

1. `rclone.check_installed()` — exits immediately if `rclone` is not on `PATH`.
2. `config.discover_remotes()` — finds all configured remotes whose backend type is in `config.SUPPORTED_BACKENDS`. If none are found, calls `config.prompt_backend_choice()` + `config.run_interactive_setup()` and re-checks; exits if still none found.
3. Remote selection — if exactly one supported remote is configured, use it silently. If multiple are configured, check `--remote NAME` CLI flag; otherwise show `config.pick_remote()` picker.
4. `DbrowserApp(remote)` — loads the tracked-download ledger during app init; if the ledger is unreadable, the app falls back to an empty one and surfaces the error as a notification after mount.
5. `.run()` — TUI starts only after the above pass; `on_mount()` pushes `BrowserScreen` and schedules startup sync checks for any previously downloaded folders that still exist locally.

## Textual 8.x gotchas

- `work` decorator: `from textual import work` — **not** `from textual.worker import work` (that was the 0.x path and will ImportError).
- `ModalScreen`, `Screen.run_worker`, `App.push_screen_wait`, `DataTable.RowHighlighted` (with `.cursor_row`, `.row_key`) are all available and used throughout.
- `ProgressBar.progress` is a reactive attribute — assign directly (`bar.progress = 42`).

## Rclone subprocess pattern

All cloud I/O goes through `src/dbrowser/rclone.py`. When adding a new op:

- Async one-shot commands (output fits in memory): use the `_run(*args)` helper.
- Streaming commands (copy / sync / anything with `--stats`): use `asyncio.create_subprocess_exec` and `async for line in proc.stderr` with `--use-json-log`. Parse via `progress.parse_log_line`. **Always** wrap the iteration in `try/finally` and kill the subprocess in `finally` — otherwise cancelled workers leak rclone processes.
- Return types: dataclasses, not dicts. See `Entry`, `RemoteSize`, `ProgressEvent`, `SyncEvent`.

## Design decisions (don't undo without asking)

- **One active remote per session.** `config.discover_remotes()` finds all supported remotes; the user picks one at launch (or passes `--remote NAME`). Switching mid-session is not supported.
- **Backend detection by type, not name.** `rclone listremotes --long` is parsed to match backend type against `config.SUPPORTED_BACKENDS`. Users can name their remotes anything.
- **Sync is one-way, local → cloud.** Deletions on disk propagate to the remote. Bidirectional (`rclone bisync`) is out of scope — don't wire it in without confirming.
- **Download ledger persists tracked folders across launches.** Startup and quit both use it to offer one-way local → cloud syncs for previously downloaded folders. The ledger is remote-agnostic — it keys by `remote_name:path` strings, so records from multiple remotes coexist naturally.
- **First-run auth is delegated to `rclone config`.** We don't reimplement the OAuth dance. `config.run_interactive_setup()` just prints per-backend guidance and shells out.

## Cross-cutting state

`DownloadLedger` (in `state.py`) is the only cross-session state. It's loaded in `DbrowserApp.__init__`, passed into `BrowserScreen`, updated after each successful download, pruned when local paths disappear, and reused by both startup and quit-time sync prompts. If a new feature needs persistent tracking, extend the ledger rather than creating a parallel store.

## File responsibilities

| File | Role |
|------|------|
| `rclone.py` | The only place that shells out to `rclone`. Anything subprocess-y belongs here. |
| `progress.py` | Parse `--use-json-log` stderr. Pure functions, no I/O. |
| `browser.py` | Main `BrowserScreen` — DataTable + preview + filter + navigation. |
| `modals.py` | All `ModalScreen` subclasses (download path, download progress, sync diff, sync progress). |
| `state.py` | `DownloadLedger` — persisted record of tracked downloads for startup + exit sync checks. |
| `config.py` | Discover supported remotes by backend type; launch `rclone config` if none found; multi-remote picker. |
| `preview.py` | Render an `Entry` as a Rich renderable for the preview pane. Text → syntax, raster images/PDFs → terminal preview, unsupported binaries → metadata. |
| `app.py` / `__main__.py` | Textual App + CLI entry point. |

See also `README.md` for install, first-run headless auth, keybindings, and user-facing behavior.

## Common pitfalls

- Don't import things from `rclone.py` that create event loops at import time — it's pure async helpers, keep it that way.
- Don't add `print()` calls in the TUI modules — they'll corrupt the Textual display. Use `self.notify()` or `self._set_status()` instead.
- The `Entry.path` from `lsjson` is **relative to the listed directory**, not absolute. When reusing it for a full remote path, prepend the current browser path. See `_fetch_preview` in `browser.py` for the pattern.

## Manual smoke test

1. `pip install -e .` then `dbrowser` — confirm the browser mounts and the DataTable populates.
2. Navigate with `j`/`k`/`l`; preview pane should show syntax-highlighted content for a text file, a terminal-rendered preview for a supported image or PDF, and a metadata summary for an unsupported binary.
3. Press `d` on a small folder, accept/edit the destination, and verify files land on disk after progress completes.
4. Restart `dbrowser` after editing one downloaded file locally — the sync-diff modal should appear at startup and list exactly that changed file. Choose **Skip** or **Sync** as appropriate.
5. Quit with `q` after editing a tracked folder during the current session — the sync-diff modal should list the changed file. Choose **Sync** and confirm the file is updated on the remote.
