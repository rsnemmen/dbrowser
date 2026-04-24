from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Optional

from .progress import ProgressEvent, SyncEvent, parse_log_line, _fmt_bytes

RCLONE = "rclone"


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int
    mod_time: str
    mime_type: str

    def size_str(self) -> str:
        if self.is_dir or self.size < 0:
            return "—"
        return _fmt_bytes(self.size)

    def mod_time_short(self) -> str:
        if len(self.mod_time) < 10:
            return "—"
        date = self.mod_time[:10]
        # rclone returns these sentinels when the backend has no mod time (e.g. Dropbox folders)
        if date in ("2000-01-01", "0001-01-01"):
            return "—"
        return date

    def icon(self) -> str:
        if self.is_dir:
            return "📁"
        ext = self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""
        if ext in {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "svg", "heic"}:
            return "🖼 "
        if ext in {"mp4", "mov", "avi", "mkv", "webm", "m4v"}:
            return "🎬"
        if ext in {"mp3", "flac", "aac", "ogg", "wav", "m4a"}:
            return "🎵"
        if ext in {"pdf"}:
            return "📕"
        if ext in {"zip", "tar", "gz", "bz2", "7z", "rar"}:
            return "📦"
        return "📄"


@dataclass
class RemoteSize:
    count: int
    bytes: int

    def __str__(self) -> str:
        return f"{self.count:,} files / {_fmt_bytes(self.bytes)}"


class RcloneError(Exception):
    pass


def check_installed() -> bool:
    return shutil.which(RCLONE) is not None


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        RCLONE, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RcloneError(stderr.decode("utf-8", errors="replace").strip())
    return stdout


async def list_remotes() -> list[str]:
    out = await _run("listremotes")
    return [r.strip().rstrip(":") for r in out.decode().splitlines() if r.strip()]


async def lsjson(remote_path: str) -> list[Entry]:
    """List directory contents as Entry objects, dirs first."""
    out = await _run("lsjson", remote_path)
    items = json.loads(out)
    entries = [
        Entry(
            name=item["Name"],
            path=item["Path"],
            is_dir=item.get("IsDir", False),
            size=item.get("Size", -1),
            mod_time=item.get("ModTime", ""),
            mime_type=item.get("MimeType", ""),
        )
        for item in items
    ]
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


async def cat(remote_path: str, max_bytes: int = 8192) -> bytes:
    """Fetch the first max_bytes of a remote file."""
    proc = await asyncio.create_subprocess_exec(
        RCLONE, "cat", remote_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        data = await asyncio.wait_for(proc.stdout.read(max_bytes), timeout=15.0)
    except asyncio.TimeoutError:
        data = b""
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    return data


async def size(remote_path: str) -> RemoteSize:
    out = await _run("size", remote_path, "--json")
    d = json.loads(out)
    return RemoteSize(count=d["count"], bytes=d["bytes"])


async def copy(
    remote_path: str,
    local_path: str,
    extra_args: list[str] | None = None,
) -> AsyncGenerator[ProgressEvent, None]:
    """Stream progress events while copying remote_path → local_path."""
    args = [
        "copy", remote_path, local_path,
        "--use-json-log", "--stats", "1s", "--log-level", "INFO",
        *(extra_args or []),
    ]
    proc = await asyncio.create_subprocess_exec(
        RCLONE, *args,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async for line in proc.stderr:
            event, _err = parse_log_line(line)
            if isinstance(event, ProgressEvent):
                yield event
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()

    if proc.returncode not in (0, -9, None):
        raise RcloneError(f"rclone copy exited with code {proc.returncode}")


async def sync(
    local_path: str,
    remote_path: str,
    dry_run: bool = False,
    extra_args: list[str] | None = None,
) -> AsyncGenerator[ProgressEvent | SyncEvent, None]:
    """Stream events while syncing local_path → remote_path."""
    args = [
        "sync", str(local_path), remote_path,
        "--use-json-log", "--log-level", "INFO",
        *(["--dry-run"] if dry_run else ["--stats", "1s"]),
        *(extra_args or []),
    ]
    proc = await asyncio.create_subprocess_exec(
        RCLONE, *args,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async for line in proc.stderr:
            event, _err = parse_log_line(line)
            if event is not None:
                yield event
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()

    if proc.returncode not in (0, -9, None):
        raise RcloneError(f"rclone sync exited with code {proc.returncode}")
