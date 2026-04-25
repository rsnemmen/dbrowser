from __future__ import annotations

import argparse
import asyncio
import sys

from . import rclone
from .app import DbrowserApp
from .config import (
    SUPPORTED_BACKENDS,
    discover_remotes,
    pick_remote,
    prompt_backend_choice,
    run_interactive_setup,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dbrowser",
        description="Yazi-style terminal file browser for rclone-backed cloud storage.",
    )
    parser.add_argument(
        "--remote",
        metavar="NAME",
        help="rclone remote name to browse (skips the picker when multiple are configured)",
    )
    args = parser.parse_args()

    # 1. Sanity check
    if not rclone.check_installed():
        print("Error: 'rclone' not found on PATH. Install it and try again.")
        print("  https://rclone.org/install/")
        sys.exit(1)

    # 2. Discover configured remotes with a supported backend
    remotes = asyncio.run(discover_remotes())

    if not remotes:
        # No supported remote configured — run interactive setup
        backend = prompt_backend_choice()
        run_interactive_setup(backend)
        remotes = asyncio.run(discover_remotes())
        if not remotes:
            supported = ", ".join(SUPPORTED_BACKENDS.values())
            print(f"\nNo supported remote found after setup ({supported}).")
            print("Create one via 'rclone config' and try again.")
            sys.exit(1)

    # 3. Resolve which remote to use
    if args.remote is not None:
        # Explicit --remote flag: validate it exists in the discovered list
        remote_names = [name for name, _ in remotes]
        if args.remote not in remote_names:
            all_remotes = asyncio.run(rclone.list_remotes())
            if args.remote in all_remotes:
                btype = next((b for n, b in asyncio.run(rclone.list_remotes_with_types()) if n == args.remote), "?")
                supported = ", ".join(SUPPORTED_BACKENDS.keys())
                print(
                    f"Error: remote '{args.remote}' has backend '{btype}', "
                    f"which is not in the supported set ({supported})."
                )
            else:
                print(f"Error: no rclone remote named '{args.remote}' found.")
            sys.exit(1)
        remote = args.remote
    elif len(remotes) == 1:
        remote = remotes[0][0]
    else:
        remote = pick_remote(remotes)

    # 4. Launch the TUI
    app = DbrowserApp(remote=remote)
    app.run()


if __name__ == "__main__":
    main()
