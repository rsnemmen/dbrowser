# dbrowser

A yazi-style terminal file browser for a Dropbox remote configured in [rclone](https://rclone.org/).

Browse your Dropbox files interactively, preview files without downloading them, pull any folder to disk with a keystroke, and push local edits back on exit.

Target audience: Those who need to browser their Dropbox files in a headless Linux server.

> **Disclaimer**
> This is an unofficial community project. It is not affiliated with, endorsed by, or sponsored by Dropbox, Inc. References to Dropbox in this repository are purely descriptive and refer to compatibility with the Dropbox backend exposed through `rclone`.

## Requirements

- Python 3.11+
- [rclone](https://rclone.org/install/) on `PATH`
- A terminal capable of rendering Unicode (most modern terminals)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/rsnemmen/dbrowser/main/install.sh | bash
```

The installer sets up [uv](https://docs.astral.sh/uv/) if needed, installs `dbrowser` in an isolated environment, and offers to install `rclone` if it is not already on your `PATH`.

## First run

The first time you launch `dbrowser`, it will detect that no rclone remote named `dropbox` exists and drop you into `rclone config`. Follow the prompts exactly as shown:

1. `n` — new remote
2. Name: `dropbox` (must be this exact name)
3. Storage: pick `dropbox` from the list
4. `client_id` / `client_secret`: leave blank (press Enter)
5. Edit advanced config? `n`
6. **Use auto config?** `n` — important for headless servers
7. Follow the headless-auth instructions: on a machine with a browser, run the `rclone authorize "dropbox"` command rclone prints, then paste the token back.
8. Confirm and quit config (`q`).

`dbrowser` will then launch its TUI.

## Usage

```bash
dbrowser
```

### Keybindings

| Key             | Action                            |
|-----------------|-----------------------------------|
| `j` / `↓`       | next entry                        |
| `k` / `↑`       | previous entry                    |
| `g` / `G`       | jump to top / bottom              |
| `h` / `←`       | parent directory                  |
| `l` / `→` / `⏎` | enter directory                   |
| `d`             | download current folder (or file) |
| `/`             | filter current folder by name     |
| `Esc`           | clear filter                      |
| `r`             | refresh current listing           |
| `q`             | quit (triggers sync-back prompt)  |

### Preview

Text files render with syntax highlighting in the right-hand pane. Supported raster images (`png`, `jpg`, `jpeg`, `gif`, `webp`, `bmp`, `tiff`) render as a low-resolution terminal preview in the same pane; unsupported image types and oversized images fall back to metadata.

### Download

Pressing `d` opens a confirmation modal showing the remote path and a default local destination (`~/Dropbox-downloads/<remote-path>`, editable). Downloading the root of the configured Dropbox remote prints a size estimate and requires explicit confirmation — it won't happen by accident.

Progress is shown live; files stream to disk as they transfer. Press `Esc` in the progress modal to cancel an in-flight download.

### Sync back on exit

If you downloaded any folders during a session and edited them in another terminal (or the same one, after exiting), quitting with `q` runs a `rclone sync --dry-run` for each downloaded folder and shows you exactly which files would change. You then choose **Sync** or **Skip** per folder, and can press `Esc` while a sync is running to cancel it.

Sync is **one-way: local → Dropbox remote**. Deletions on disk propagate to the configured Dropbox remote. If you want bidirectional reconciliation, use `rclone bisync` manually — it's not wired into v1.

## Architecture

- Single-process Python app using [Textual](https://textual.textualize.io/) for the TUI.
- Every cloud operation is an async `rclone` subprocess. No daemon, no persistent state.
- Listings stream in as background workers — the UI stays responsive even on slow connections.
- The download ledger is in-memory only; closing the app without confirming sync means no sync.

Module layout:

```
src/dbrowser/
├── __main__.py     entry point
├── app.py          Textual App
├── browser.py      main browser screen (list + preview)
├── modals.py       download/sync modals
├── rclone.py       async rclone wrappers
├── config.py       first-run remote bootstrap
├── state.py        download ledger
├── preview.py      file preview rendering
└── progress.py     parse rclone --use-json-log output
```

## Limitations (v1)

- One remote only, hardcoded as `dropbox`.
- Read-only on the remote: no rename, delete, or move. Edit locally and sync back instead.
- No on-disk cache — re-opening the tool re-fetches listings.
- Exit-sync is one-way (local → cloud); use `rclone bisync` manually for bidirectional.
- Unsupported binary files still show metadata only.
