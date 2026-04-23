from __future__ import annotations

from rich.syntax import Syntax
from rich.text import Text

from .rclone import Entry, cat
from .progress import _fmt_bytes

TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp",
    ".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".sh", ".bash", ".zsh", ".fish", ".json", ".xml", ".html", ".css", ".scss",
    ".sql", ".r", ".m", ".csv", ".tex", ".bib", ".ipynb",
}

TEXT_MIME_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-sh",
    "application/x-python",
)

# Map file extensions to Rich Syntax lexer names
LEXER_MAP = {
    "py": "python", "js": "javascript", "ts": "typescript",
    "go": "go", "rs": "rust", "java": "java", "c": "c", "cpp": "cpp",
    "h": "c", "hpp": "cpp", "sh": "bash", "bash": "bash", "zsh": "bash",
    "yaml": "yaml", "yml": "yaml", "toml": "toml", "json": "json",
    "xml": "xml", "html": "html", "css": "css", "sql": "sql",
    "md": "markdown", "rst": "rst", "tex": "latex",
}


def _is_text(entry: Entry) -> bool:
    if "." in entry.name:
        ext = "." + entry.name.rsplit(".", 1)[-1].lower()
        if ext in TEXT_EXTENSIONS:
            return True
    for prefix in TEXT_MIME_PREFIXES:
        if entry.mime_type.startswith(prefix):
            return True
    return False


def _metadata(entry: Entry) -> Text:
    t = Text()
    t.append(f"{entry.icon()} {entry.name}\n\n", style="bold")
    if not entry.is_dir:
        t.append(f"Size:      {_fmt_bytes(entry.size)}\n", style="dim")
    t.append(f"Modified:  {entry.mod_time_short()}\n", style="dim")
    if entry.mime_type:
        t.append(f"Type:      {entry.mime_type}\n", style="dim")
    return t


async def render(entry: Entry, remote_prefix: str) -> object:
    """Return a Rich renderable appropriate for the preview pane."""
    if entry.is_dir:
        return _metadata(entry)

    if not _is_text(entry):
        return _metadata(entry)

    # remote_prefix always ends with ":" or "/" (e.g. "dropbox:" or "dropbox:Photos/2024/")
    remote_path = f"{remote_prefix}{entry.path}"
    try:
        data = await cat(remote_path, max_bytes=8192)
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return _metadata(entry)

    ext = entry.name.rsplit(".", 1)[-1].lower() if "." in entry.name else "text"
    lexer = LEXER_MAP.get(ext, "text")
    return Syntax(text, lexer, line_numbers=False, word_wrap=True, theme="monokai")
