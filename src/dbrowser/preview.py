from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError
from rich.console import Group
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

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
}

IMAGE_MIME_PREFIXES = (
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
)

TEXT_PREVIEW_BYTES = 8192
MAX_IMAGE_PREVIEW_BYTES = 8 * 1024 * 1024
DEFAULT_IMAGE_WIDTH = 48
DEFAULT_IMAGE_HEIGHT = 14
IMAGE_METADATA_ROWS = 7

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


def _is_image(entry: Entry) -> bool:
    if "." in entry.name:
        ext = "." + entry.name.rsplit(".", 1)[-1].lower()
        if ext in IMAGE_EXTENSIONS:
            return True
    mime_type = entry.mime_type.lower()
    return any(mime_type.startswith(prefix) for prefix in IMAGE_MIME_PREFIXES)


def _metadata(
    entry: Entry,
    *,
    dimensions: tuple[int, int] | None = None,
    note: str | None = None,
) -> Text:
    t = Text()
    t.append(f"{entry.icon()} {entry.name}\n\n", style="bold")
    if not entry.is_dir:
        t.append(f"Size:      {_fmt_bytes(entry.size)}\n", style="dim")
    if dimensions is not None:
        t.append(f"Dimensions: {dimensions[0]}x{dimensions[1]}\n", style="dim")
    t.append(f"Modified:  {entry.mod_time_short()}\n", style="dim")
    if entry.mime_type:
        t.append(f"Type:      {entry.mime_type}\n", style="dim")
    if note is not None:
        t.append(f"\n{note}", style="yellow")
    return t


def _image_bounds(max_width: int, max_height: int) -> tuple[int, int]:
    width = max_width if max_width > 0 else DEFAULT_IMAGE_WIDTH
    height = max_height if max_height > 0 else DEFAULT_IMAGE_HEIGHT
    width = max(8, width - 2)
    height = max(4, height - IMAGE_METADATA_ROWS)
    return width, height * 2


def _pixel_style(foreground: tuple[int, int, int], background: tuple[int, int, int]) -> str:
    fg_r, fg_g, fg_b = foreground
    bg_r, bg_g, bg_b = background
    return f"rgb({fg_r},{fg_g},{fg_b}) on rgb({bg_r},{bg_g},{bg_b})"


def _render_image(data: bytes, max_width: int, max_height: int) -> tuple[Text, tuple[int, int]]:
    with Image.open(BytesIO(data)) as img:
        rgba = img.convert("RGBA")
        original_dimensions = rgba.size
        background = Image.new("RGBA", rgba.size, (24, 24, 24, 255))
        image = Image.alpha_composite(background, rgba).convert("RGB")

    image.thumbnail(_image_bounds(max_width, max_height), Image.Resampling.LANCZOS)
    if image.height % 2:
        padded = Image.new("RGB", (image.width, image.height + 1), (24, 24, 24))
        padded.paste(image, (0, 0))
        image = padded

    pixels = image.load()
    art = Text(no_wrap=True, end="")
    for y in range(0, image.height, 2):
        for x in range(image.width):
            top = pixels[x, y]
            bottom = pixels[x, y + 1]
            art.append("▀", style=_pixel_style(top, bottom))
        if y + 2 < image.height:
            art.append("\n")
    art.append("\n\n")
    return art, original_dimensions


async def _render_image_preview(
    entry: Entry,
    remote_path: str,
    max_width: int,
    max_height: int,
) -> object:
    if entry.size > MAX_IMAGE_PREVIEW_BYTES:
        return _metadata(
            entry,
            note=f"Preview unavailable: image is larger than {_fmt_bytes(MAX_IMAGE_PREVIEW_BYTES)}.",
        )

    data = await cat(remote_path, max_bytes=MAX_IMAGE_PREVIEW_BYTES + 1)
    if len(data) > MAX_IMAGE_PREVIEW_BYTES:
        return _metadata(
            entry,
            note=f"Preview unavailable: image is larger than {_fmt_bytes(MAX_IMAGE_PREVIEW_BYTES)}.",
        )

    try:
        art, dimensions = _render_image(data, max_width=max_width, max_height=max_height)
    except UnidentifiedImageError:
        return _metadata(
            entry,
            note="Preview unavailable: unsupported raster format.",
        )
    except OSError:
        return _metadata(
            entry,
            note="Preview unavailable: image data could not be decoded.",
        )

    return Group(art, _metadata(entry, dimensions=dimensions))


async def render(
    entry: Entry,
    remote_prefix: str,
    *,
    max_width: int = 0,
    max_height: int = 0,
) -> object:
    """Return a Rich renderable appropriate for the preview pane."""
    if entry.is_dir:
        return _metadata(entry)

    # remote_prefix always ends with ":" or "/" (e.g. "dropbox:" or "dropbox:Photos/2024/")
    remote_path = f"{remote_prefix}{entry.path}"

    if _is_image(entry):
        return await _render_image_preview(
            entry,
            remote_path,
            max_width=max_width,
            max_height=max_height,
        )

    if not _is_text(entry):
        return _metadata(entry)

    data = await cat(remote_path, max_bytes=TEXT_PREVIEW_BYTES)
    text = data.decode("utf-8", errors="replace")
    ext = entry.name.rsplit(".", 1)[-1].lower() if "." in entry.name else "text"
    lexer = LEXER_MAP.get(ext, "text")
    return Syntax(text, lexer, line_numbers=False, word_wrap=True, theme="monokai")
