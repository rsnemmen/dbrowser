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


def _resolve_remote(requested_remote: str | None, backend: str | None) -> str:
    """Select a supported remote, running first-time setup only for auto-selection."""
    remotes = asyncio.run(discover_remotes())

    if requested_remote is not None:
        remote_type = next((b for name, b in remotes if name == requested_remote), None)
        if remote_type is None:
            all_remotes = asyncio.run(rclone.list_remotes_with_types())
            remote_type = next((b for name, b in all_remotes if name == requested_remote), None)
            if remote_type is None:
                raise ValueError(f"No rclone remote named '{requested_remote}' found.")
            supported = ", ".join(SUPPORTED_BACKENDS)
            raise ValueError(
                f"Remote '{requested_remote}' has unsupported backend '{remote_type}' "
                f"(supported: {supported})."
            )
        if backend is not None and remote_type != backend:
            raise ValueError(
                f"Remote '{requested_remote}' has backend '{remote_type}', "
                f"which does not match '--backend {backend}'."
            )
        return requested_remote

    if backend is not None:
        remotes = [(name, b) for name, b in remotes if b == backend]
    if not remotes:
        setup_backend = backend or prompt_backend_choice()
        run_interactive_setup(setup_backend)
        remotes = asyncio.run(discover_remotes())
        if backend is not None:
            remotes = [(name, b) for name, b in remotes if b == backend]
        if not remotes:
            supported = SUPPORTED_BACKENDS[backend] if backend else ", ".join(SUPPORTED_BACKENDS.values())
            raise ValueError(
                f"No matching remote found after setup ({supported}). "
                "Create one via 'rclone config' and try again."
            )

    return remotes[0][0] if len(remotes) == 1 else pick_remote(remotes)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="dbrowser",
        description="Yazi-style terminal file browser for rclone-backed cloud storage.",
    )
    parser.add_argument(
        "--remote",
        metavar="NAME",
        help="rclone remote name to browse (skips the picker when multiple are configured)",
    )
    parser.add_argument(
        "--backend",
        choices=SUPPORTED_BACKENDS,
        help="browse this backend; configure it with rclone if no matching remote exists",
    )
    parser.add_argument(
        "--path",
        metavar="PATH",
        default="",
        help="initial directory relative to the remote (for S3: bucket or bucket/prefix)",
    )
    args = parser.parse_args(argv)

    # 1. Sanity check
    if not rclone.check_installed():
        print("Error: 'rclone' not found on PATH. Install it and try again.")
        print("  https://rclone.org/install/")
        sys.exit(1)

    # 2. Discover, optionally configure, and select a remote.
    try:
        remote = _resolve_remote(args.remote, args.backend)
    except rclone.RcloneError as exc:
        print(f"Error: could not discover rclone remotes: {exc}")
        print("Check access to your rclone configuration; use 'rclone config' to repair it.")
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    # 3. Launch directly at the requested directory, without listing the root first.
    app = DbrowserApp(remote=remote, initial_path=args.path.strip("/"))
    app.run()


if __name__ == "__main__":
    main()
