from __future__ import annotations

from . import rclone

# Maps the rclone backend type to a human-friendly label used in setup guidance.
SUPPORTED_BACKENDS: dict[str, str] = {
    "dropbox": "Dropbox",
    "drive": "Google Drive",
    "s3": "Amazon S3 / S3-compatible",
}


async def discover_remotes() -> list[tuple[str, str]]:
    """Return supported remotes as (name, type) pairs; propagate config read errors."""
    all_remotes = await rclone.list_remotes_with_types()
    return [(name, btype) for name, btype in all_remotes if btype in SUPPORTED_BACKENDS]


def run_interactive_setup(backend: str = "dropbox") -> None:
    """Print per-backend guidance and launch rclone config interactively."""
    label = SUPPORTED_BACKENDS.get(backend, backend)
    print()

    if backend == "dropbox":
        print("  No matching Dropbox remote found in rclone config.")
        print(f"  Launching rclone config to set up {label}:")
        print()
        print("    1. Press n  → New remote")
        print("    2. Name:      dropbox  (or any name you like)")
        print("    3. Storage:   dropbox  (select from list)")
        print("    4. client_id / client_secret: leave blank (press Enter)")
        print("    5. Edit advanced config? n")
        print("    6. Use auto config? n  ← important for headless servers")
        print("    7. Follow the token-paste instructions shown.")
        print("    8. Press q to quit rclone config.")
    elif backend == "drive":
        print("  No matching Google Drive remote found in rclone config.")
        print(f"  Launching rclone config to set up {label}:")
        print()
        print("    1. Press n  → New remote")
        print("    2. Name:      gdrive  (or any name you like)")
        print("    3. Storage:   drive   (select from list)")
        print("    4. client_id / client_secret: leave blank for shared quota")
        print("    5. scope:     drive.readonly  (or drive for full access)")
        print("    6. Edit advanced config? n")
        print("    7. Use auto config? n  ← important for headless servers")
        print("    8. Follow the headless-auth instructions shown.")
        print("    9. Press q to quit rclone config.")
    elif backend == "s3":
        print("  No matching S3 remote found in rclone config.")
        print(f"  Launching rclone config to set up {label}:")
        print()
        print("    1. Press n  → New remote")
        print("    2. Name:      myaws  (or any name you like)")
        print("    3. Storage:   s3")
        print("    4. Provider:  AWS  (for Amazon S3)")
        print("    5. Authentication:")
        print("       env_auth = true: use AWS environment credentials, a profile, or an IAM role;")
        print("                        leave access_key_id / secret_access_key blank.")
        print("       env_auth = false: enter access_key_id / secret_access_key in rclone.")
        print("    6. Region: match the bucket; leave endpoint blank for AWS.")
        print("    7. Use advanced config if you need a named profile or session_token.")
        print("    8. Keep the remaining defaults unless your setup requires otherwise.")
        print("    9. Confirm the remote and press q to quit rclone config.")
    else:
        print(f"  Launching rclone config to set up {label}:")

    print()
    rclone.run_config()
    print()


def prompt_backend_choice() -> str:
    """Ask the user which backend to set up; return the rclone backend type."""
    backends = list(SUPPORTED_BACKENDS.items())
    print()
    print("  Which cloud storage would you like to set up?")
    print()
    for i, (btype, label) in enumerate(backends, 1):
        print(f"    {i}. {label}")
    print()
    while True:
        raw = input("  Enter number [1]: ").strip()
        if raw == "":
            return backends[0][0]
        if raw.isdigit() and 1 <= int(raw) <= len(backends):
            return backends[int(raw) - 1][0]
        print(f"  Please enter a number between 1 and {len(backends)}.")


def pick_remote(remotes: list[tuple[str, str]]) -> str:
    """Interactively ask the user to pick one remote; return its name."""
    print()
    print("  Multiple supported remotes found. Which one would you like to browse?")
    print()
    for i, (name, btype) in enumerate(remotes, 1):
        label = SUPPORTED_BACKENDS.get(btype, btype)
        print(f"    {i}. {name}  ({label})")
    print()
    while True:
        raw = input("  Enter number [1]: ").strip()
        if raw == "":
            return remotes[0][0]
        if raw.isdigit() and 1 <= int(raw) <= len(remotes):
            return remotes[int(raw) - 1][0]
        print(f"  Please enter a number between 1 and {len(remotes)}.")
