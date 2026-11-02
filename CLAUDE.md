# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Textual TUI that wraps `rclone` to give yazi-style interactive browsing of cloud storage remotes. Every cloud operation is an async subprocess — no daemon, with persistent state limited to a tracked-download ledger.

Supported backends: any rclone remote of type `dropbox`, `drive` (Google Drive), or `s3` (Amazon S3 / S3-compatible storage). The backend registry lives in `config.SUPPORTED_BACKENDS`.

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
python -m unittest discover -s tests -v  # offline regression tests
```

The unittest suite mocks cloud operations and runs Textual headlessly; no cloud credentials are required. Also verify manually against a real remote when available (see `## Manual smoke test` below).

## Startup flow

`__main__.main()` runs these steps in order — useful to know when debugging first-run or auth problems:

1. `rclone.check_installed()` — exits immediately if `rclone` is not on `PATH`.
2. `config.discover_remotes()` — finds configured remotes whose backend type is in `config.SUPPORTED_BACKENDS`. Discovery errors exit with repair guidance, rather than being treated as missing configuration.
3. Remote selection — an explicit `--remote NAME` must exist and be supported, and must match `--backend` when supplied. Otherwise `--backend` filters the discovered remotes. If none remain, run setup for that backend (or ask which backend when omitted), rediscover once, and exit if no matching remote exists. Use the only match silently or show `config.pick_remote()` for multiple matches.
4. `DbrowserApp(remote, initial_path=...)` — receives the remote-relative `--path` (default root; surrounding slashes removed) and loads the tracked-download ledger. An unreadable ledger falls back to an empty one with a notification after mount.
5. `.run()` — `on_mount()` pushes `BrowserScreen` at the initial path and schedules startup sync checks. For S3, `--path bucket/prefix` skips root bucket listing; parent navigation can still reach the remote root.

## Textual 8.x gotchas

- `work` decorator: `from textual import work` — **not** `from textual.worker import work` (that was the 0.x path and will ImportError).
- `ModalScreen`, `Screen.run_worker`, `App.push_screen_wait`, `DataTable.RowHighlighted` (with `.cursor_row`, `.row_key`) are all available and used throughout.
- `ProgressBar.progress` is a reactive attribute — assign directly (`bar.progress = 42`).
- Use `call_after_refresh` to defer startup work; a zero-second `set_timer` raises `ZeroDivisionError` in Textual 8.x.

## Rclone subprocess pattern

All cloud I/O goes through `src/dbrowser/rclone.py`. When adding a new op:

- Async one-shot commands (output fits in memory): use the `_run(*args)` helper.
- Streaming commands (copy / sync / anything with `--stats`): use `asyncio.create_subprocess_exec` and `async for line in proc.stderr` with `--use-json-log`. Parse via `progress.parse_log_line`. **Always** wrap the iteration in `try/finally` and kill the subprocess in `finally` — otherwise cancelled workers leak rclone processes.
- Return types: dataclasses, not dicts. See `Entry`, `RemoteSize`, `ProgressEvent`, `SyncEvent`.

## Design decisions (don't undo without asking)

- **One active remote per session.** `config.discover_remotes()` finds all supported remotes; the user picks one at launch (or passes `--remote NAME`). Switching mid-session is not supported.
- **Backend detection by type, not name.** `rclone listremotes --long` is parsed to match backend type against `config.SUPPORTED_BACKENDS`, ignoring optional descriptions after the type. Users can name their remotes anything.
- **Sync is one-way, local → cloud.** Deletions on disk propagate to the remote. Bidirectional (`rclone bisync`) is out of scope — don't wire it in without confirming.
- **Download ledger persists tracked folders across launches.** Startup and quit both use it to offer one-way local → cloud syncs for previously downloaded folders. The ledger is remote-agnostic — it keys by `remote_name:path` strings, so records from multiple remotes coexist naturally.
- **First-run auth is delegated to `rclone config`.** `config.run_interactive_setup()` prints per-backend guidance and calls `rclone.run_config()`. Reuse rclone's saved credentials, profiles, runtime authentication, and environment; never extract or duplicate secret keys. Access failures on an existing remote show repair guidance rather than opening setup automatically.

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
- Initialize `BrowserScreen.current_path` with `set_reactive` before mount so its watcher does not query unmounted widgets. The first breadcrumb and listing must use the initial path, without requesting the remote root first.

## Manual smoke test

1. `pip install -e .` then `dbrowser` — confirm the browser mounts and the DataTable populates.
2. Navigate with `j`/`k`/`l`; preview pane should show syntax-highlighted content for a text file, a terminal-rendered preview for a supported image or PDF, and a metadata summary for an unsupported binary.
3. Press `d` on a small folder, accept/edit the destination, and verify files land on disk after progress completes.
4. Restart `dbrowser` after editing one downloaded file locally — the sync-diff modal should appear at startup and list exactly that changed file. Choose **Skip** or **Sync** as appropriate.
5. Quit with `q` after editing a tracked folder during the current session — the sync-diff modal should list the changed file. Choose **Sync** and confirm the file is updated on the remote.

### S3 smoke test

1. With an existing S3 remote, run `dbrowser --backend s3` and confirm it reuses the remote without setup. If several S3 remotes exist, confirm the picker contains only those remotes.
2. Run `dbrowser --remote myaws --path my-bucket/disposable-test-prefix`. Confirm the first listing opens that prefix directly, including when bucket-listing permission is unavailable. Check the breadcrumb and navigate children and parents.
3. In the test prefix, preview a text file, download a small folder, and verify the local destination includes the bucket/prefix. Edit a downloaded file and confirm the existing sync flow updates only the intended test data.
4. With read-only access, confirm browsing and downloads work, then skip sync. For an expired session or inaccessible path, confirm visible error/repair guidance and no automatic setup.
5. In an isolated rclone configuration without S3 remotes, run `dbrowser --backend s3`, confirm AWS setup guidance appears even if Drive/Dropbox is configured, and verify cancelling setup without creating a remote exits clearly.
