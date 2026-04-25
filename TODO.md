# TODO — candidate improvements for dbrowser

Grouped by theme and sized roughly. Items flagged when they bump against the explicit non-goals in `CLAUDE.md` (multi-remote, bidirectional sync, on-disk cache, remote rename/delete).

---

## Tier 1 — High user-visible value, small-to-medium effort

### ~~0. Support for Google Drive and Box~~ ✓ done — Google Drive added; Box follows trivially by adding `"box": "box"` to `config.SUPPORTED_BACKENDS`

### 1. Copy remote path to clipboard (`y`)
**Why:** Yazi's `y` is muscle memory — users want to grab a path to paste into `rclone copy`, a shell script, or a share sheet. Low cost, high frequency.
**Design:** Bind `y` → copy `"{remote}:{current_path}/{entry.name}"` via `pyperclip` (new dep) or `xclip` / `pbcopy` subprocess (no new dep).
**Files:** `src/dbrowser/browser.py` (new action), optionally `pyproject.toml`.
**Effort:** ~1 hour.

### 2. Sort toggle (`s` cycles name / size / mtime)
**Why:** `lsjson` always sorts dirs-first + alphabetical (`rclone.py:98`). Finding the biggest or newest file in a folder is currently a manual scan.
**Design:** Track a `sort_key` reactive on `BrowserScreen`; re-sort `_all_entries` locally, no extra API call.
**Files:** `src/dbrowser/browser.py` + possibly `Entry` helpers on `rclone.py`.
**Effort:** ~1.5 hours.

### 3. Typed path navigation (`:` to jump)
**Why:** Navigating to a deep path is one keypress per directory level. A `:` modal that jumps directly would save dozens of keystrokes.
**Design:** New `PathInputModal` reusing the existing `ModalScreen` pattern. On accept, set `self.current_path` — reactive watchers handle the rest.
**Files:** `src/dbrowser/modals.py`, `src/dbrowser/browser.py`.
**Effort:** ~2 hours.

---

## Tier 2 — Correctness and robustness

### 4. Visible handling of `cat` timeouts in previews
**Why:** `rclone.cat` has a 15 s timeout that silently returns an empty preview (`rclone.py:110-112`). On a slow link the user sees nothing and doesn't know why.
**Design:** Distinguish timeout from empty-file; show `[dim]preview timed out — press r to retry[/dim]` via `_metadata(entry)`.
**Files:** `src/dbrowser/rclone.py`, `src/dbrowser/preview.py`.
**Effort:** ~1 hour.

### 5. Unit tests for `progress.parse_log_line`
**Why:** The one pure function with non-trivial logic — parses rclone JSON log lines into dataclasses, plus a hand-rolled dry-run parser (`progress.py:81-87`). Breaking it silently breaks progress bars and sync diffs. Zero mocks: just feed it fixture strings.
**Design:** Add `pytest` to `[project.optional-dependencies]`, a `tests/` dir, fixtures for: stats line, error line, dry-run "Would copy", malformed JSON.
**Files:** `pyproject.toml`, new `tests/test_progress.py`.
**Effort:** ~2 hours.

---

## Tier 3 — Features that may exceed v1 scope (confirm before building)

### 6. Recursive search across the current subtree
**Why:** `/` filter is local-only. Finding a file by partial name in a deep tree is currently impossible without shelling out to `rclone ls`.
**Cost:** Needs `rclone lsjson --recursive`, which can take seconds-to-minutes on large trees. Needs async "searching…" state and cancel.
**Non-goal check:** Not ruled out by CLAUDE.md, but a noticeable scope bump.

### 7. Open file in `$EDITOR` / view in `$PAGER`
**Why:** Current flow for "just let me look at this" is: download folder → open in editor. Pressing `o` on a text file to stream-to-tempfile-and-exec-$EDITOR would be yazi-like.
**Cost:** Harder than it sounds — Textual needs to suspend and resume cleanly. Possible via `App.suspend()` but edge cases exist.

---

## Tier 4 — Project infrastructure

### 8. Add `ruff` config + pre-commit
**Effort:** ~30 min.

### 9. GitHub Actions CI for `py_compile` + (if added) pytest + ruff
**Effort:** ~30 min, gated on item 5.

### 10. PyPI release
Currently only `pip install -e .`. Publishing would allow `pipx install dbrowser`.
**Note:** Package was renamed from `dropbox-cli` to `dbrowser` to avoid the "Dropbox" trademark on PyPI.

---

## Recommendation

Biggest perceived-quality jump for ~one afternoon: **1 + 2 + 3** together (yank-path, sort, jump-to-path). The gap between "a demo" and "daily driver."

**Item 5** (tests for `progress.parse_log_line`) is the most defensible single piece of engineering to add — the parser is where silent breakage would hurt most.
