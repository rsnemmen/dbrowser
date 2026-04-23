from __future__ import annotations

import asyncio
import sys

from . import rclone
from .app import DropboxApp
from .config import REMOTE_NAME, find_remote, run_interactive_setup


def main() -> None:
    # 1. Sanity check
    if not rclone.check_installed():
        print("Error: 'rclone' not found on PATH. Install it and try again.")
        print("  https://rclone.org/install/")
        sys.exit(1)

    # 2. Ensure the dropbox remote is configured
    remote = asyncio.run(find_remote())
    if remote is None:
        run_interactive_setup()
        # Re-check after setup
        remote = asyncio.run(find_remote())
        if remote is None:
            print(
                f"\nNo '{REMOTE_NAME}' remote found after setup.\n"
                "Create one named exactly 'dropbox' and try again."
            )
            sys.exit(1)

    # 3. Launch the TUI
    app = DropboxApp(remote=remote)
    app.run()


if __name__ == "__main__":
    main()
