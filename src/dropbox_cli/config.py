from __future__ import annotations

import subprocess
import sys

from . import rclone

REMOTE_NAME = "dropbox"


async def find_remote() -> str | None:
    """Return the remote name if a dropbox remote is configured, else None."""
    try:
        remotes = await rclone.list_remotes()
    except rclone.RcloneError:
        return None
    return REMOTE_NAME if REMOTE_NAME in remotes else None


def run_interactive_setup() -> None:
    """Print guidance and launch rclone config interactively."""
    print()
    print("  No 'dropbox' remote found in rclone config.")
    print("  Launching rclone config — follow the prompts:")
    print()
    print("    1. Press n  → New remote")
    print("    2. Name:      dropbox")
    print("    3. Storage:   dropbox  (select from list)")
    print("    4. client_id / client_secret: leave blank (press Enter)")
    print("    5. Edit advanced config? n")
    print("    6. Use auto config? n  ← important for headless servers")
    print("    7. Follow the token-paste instructions shown.")
    print()
    subprocess.run(["rclone", "config"], check=False)
    print()
