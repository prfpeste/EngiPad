"""Pictures in the output: validation, limits and the file format.

Syntax in the input (one line):

    picture("name")
    picture("name", width=300, align=left)

The image itself never appears in the input and is never read from a
path. The browser lets the user pick a PNG (button "Insert image"),
shrinks it, keeps it in memory and writes it into the SAVED FILE as an
"image section" at the very end of the text:

    <the sheets, exactly as before>
    ##!image balken
    iVBORw0KGgoAAAANSUhEUgAA...      (Base64, 76 characters per line)
    ##!image anderes
    ...

Rules of that section (static/pictures.js implements the SAME rules --
tests/test_images.py runs both against each other):

* It is only recognised as the END of the text: from its first marker
  line to the end of the text there may only be marker lines, Base64
  lines and blank lines. A marker line somewhere in the middle of the
  code (typed by hand) is therefore just a comment, never an image.
* A block that is damaged (bad Base64, no PNG, too big, ...) is skipped
  with a warning; the rest of the file is opened as usual.
* Images that no sheet refers to (picture("name")) are not written.

Security: only PNG, checked by its bytes (signature, IHDR, IEND), with
hard limits for pixels and bytes BEFORE anything decodes the picture.
The name is restricted to letters, digits and underscore because it ends
up in a file name in the LaTeX export ("img_<name>.png").
"""

from __future__ import annotations

import base64
import binascii
import re

# --- Limits (the only place; the browser gets them via client_config()) ---
MAX_IMAGES = 10
LIMIT_CHOICES_MB = (1, 2, 3)  # setting "Image limit" (total size of all images)
DEFAULT_LIMIT_MB = 1
HARD_TOTAL_BYTES = max(LIMIT_CHOICES_MB) * 1024 * 1024
PER_IMAGE_DIVISOR = 5  # one image may use at most 1/5 of the total limit
MAX_PIXEL_WIDTH = 1200  # stored images are at most this wide
MAX_PIXEL_HEIGHT = 4000
MAX_PIXELS = 3_000_000  # width * height, protects against decompression bombs
DEFAULT_DISPLAY_WIDTH = 500  # px, if picture() has no width= (smaller images stay small)
MAX_DISPLAY_WIDTH = 1200
NAME_MAX_LENGTH = 40
PX_PER_INCH = 96  # CSS pixels; the LaTeX export converts with the same value

ALIGNMENTS = ("left", "center", "right")
DEFAULT_ALIGNMENT = "center"

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,%d}$" % NAME_MAX_LENGTH)
MARKER_RE = re.compile(r"^##!image[ \t]+([A-Za-z0-9_]{1,%d})[ \t]*$" % NAME_MAX_LENGTH)
_BASE64_LINE_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
BASE64_LINE_WIDTH = 76

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def limit_bytes(limit_mb: int) -> int:
    """Total limit in bytes for a value of the setting (1/2/3 MB)."""
    return limit_mb * 1024 * 1024


def per_image_limit(total_bytes: int) -> int:
    return total_bytes // PER_IMAGE_DIVISOR


def parse_limit_mb(raw) -> int:
    """Setting "Image limit": 1, 2 or 3 (MB); anything else -> default."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_LIMIT_MB
    return value if value in LIMIT_CHOICES_MB else DEFAULT_LIMIT_MB


def client_config() -> dict:
    """The limits for static/pictures.js / editor.js (one source of truth)."""
    return {
        "maxImages": MAX_IMAGES,
        "hardTotalBytes": HARD_TOTAL_BYTES,
        "perImageDivisor": PER_IMAGE_DIVISOR,
        "maxPixelWidth": MAX_PIXEL_WIDTH,
        "maxPixelHeight": MAX_PIXEL_HEIGHT,
        "maxPixels": MAX_PIXELS,
        "nameMaxLength": NAME_MAX_LENGTH,
        "defaultDisplayWidth": DEFAULT_DISPLAY_WIDTH,
        "defaultAlignment": DEFAULT_ALIGNMENT,
    }


def is_valid_name(name) -> bool:
    return isinstance(name, str) and bool(NAME_RE.match(name))


# --- PNG check ---------------------------------------------------------------

def png_size(data: bytes, max_bytes: int | None = None) -> tuple[int, int]:
    """Checks `data` as a PNG and returns (width, height).

    Looks only at the bytes (signature, IHDR chunk, IEND at the end) and
    NEVER decodes the picture: size and pixel limits are checked first,
    so a small file that would expand to gigabytes is refused. Raises
    ValueError with a message for the user.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("not an image")
    if max_bytes is None:
        max_bytes = per_image_limit(HARD_TOTAL_BYTES)
    if len(data) > max_bytes:
        raise ValueError(f"image is too large ({len(data) // 1024} KB, limit {max_bytes // 1024} KB)")
    if len(data) < 45 or not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG image")
    if data[12:16] != b"IHDR" or data[8:12] != b"\x00\x00\x00\x0d":
        raise ValueError("damaged PNG image (no header)")
    if not data.endswith(_PNG_IEND):
        raise ValueError("damaged PNG image (incomplete)")

    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width < 1 or height < 1:
        raise ValueError("damaged PNG image (size 0)")
    if width > MAX_PIXEL_WIDTH or height > MAX_PIXEL_HEIGHT or width * height > MAX_PIXELS:
        raise ValueError(
            f"image has too many pixels ({width} x {height}; "
            f"at most {MAX_PIXEL_WIDTH} px wide and {MAX_PIXELS // 1_000_000} million pixels)"
        )
    return width, height


def decode_png_base64(text: str, max_bytes: int | None = None) -> bytes:
    """Base64 text -> checked PNG bytes (ValueError otherwise)."""
    if not isinstance(text, str):
        raise ValueError("not an image")
    # Refuse huge texts before decoding them.
    limit = max_bytes if max_bytes is not None else per_image_limit(HARD_TOTAL_BYTES)
    if len(text) > limit * 4 // 3 + 16:
        raise ValueError(f"image is too large (limit {limit // 1024} KB)")
    try:
        data = base64.b64decode(re.sub(r"\s+", "", text), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("damaged image data (not Base64)")
    png_size(data, max_bytes)
    return data


def check_image_set(images: dict) -> None:
    """Hard limits for a whole set {name: bytes} (ValueError otherwise)."""
    if len(images) > MAX_IMAGES:
        raise ValueError(f"too many images ({len(images)}, at most {MAX_IMAGES})")
    if sum(len(d) for d in images.values()) > HARD_TOTAL_BYTES:
        raise ValueError("the images are too large altogether")


# --- picture(...) in the input -------------------------------------------------

_CALL_RE = re.compile(r'^picture\(\s*"([^"]*)"\s*((?:,[^,()]*)*)\)$')
_USAGE = 'picture("name", width=300, align=center) expected'


def parse_picture_call(stripped: str) -> dict:
    """Parses one `picture(...)` line into {"name", "width", "align"}.

    width: whole pixels (optionally with "px") or None = default size;
    align: left/center/right. ValueError with a clear message otherwise.
    """
    match = _CALL_RE.match(stripped.strip())
    if not match:
        raise ValueError(_USAGE)

    name = match.group(1)
    if not is_valid_name(name):
        raise ValueError(
            f"Invalid image name '{name}' (letters, digits and underscore only, "
            f"1-{NAME_MAX_LENGTH} characters)."
        )

    width = None
    align = DEFAULT_ALIGNMENT
    seen = set()
    rest = match.group(2)
    for part in rest.split(",")[1:] if rest else []:
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            raise ValueError(_USAGE)
        if key not in ("width", "align"):
            raise ValueError(f"Unknown argument '{key}' (allowed: width, align).")
        if key in seen:
            raise ValueError(f"Argument '{key}' given twice.")
        seen.add(key)

        if key == "width":
            number = re.fullmatch(r"(\d+)\s*(?:px)?", value)
            if not number or not 1 <= int(number.group(1)) <= MAX_DISPLAY_WIDTH:
                raise ValueError(
                    f"width must be a whole number of pixels (1-{MAX_DISPLAY_WIDTH})."
                )
            width = int(number.group(1))
        else:
            if value not in ALIGNMENTS:
                raise ValueError("align must be left, center or right.")
            align = value

    return {"name": name, "width": width, "align": align}


def referenced_names(codes) -> list[str]:
    """Names used in picture("name"...) anywhere in the given codes, in
    order of first use. A commented-out line (# picture("x")) still
    counts: temporarily disabling a line must not lose the image."""
    names: list[str] = []
    for code in codes:
        for match in re.finditer(r'picture\(\s*"([A-Za-z0-9_]{1,%d})"' % NAME_MAX_LENGTH, code):
            if match.group(1) not in names:
                names.append(match.group(1))
    return names


# --- The image section of a saved file -----------------------------------------

def _split_lines(text: str) -> list[str]:
    return re.split(r"\r\n|\r|\n", text)


def split_image_section(text: str) -> tuple[str, dict, list]:
    """Separates the image section from the end of a text.

    Returns (text without the section, {name: PNG bytes}, warnings).
    Without a section the text is returned unchanged.
    """
    lines = _split_lines(text)

    # Longest tail consisting only of marker/Base64/blank lines ...
    tail_start = len(lines)
    while tail_start > 0:
        line = lines[tail_start - 1]
        stripped = line.strip()
        if stripped == "" or MARKER_RE.match(line.rstrip()) or _BASE64_LINE_RE.match(stripped):
            tail_start -= 1
        else:
            break

    # ... and in it the first marker: everything before stays code.
    start = next(
        (k for k in range(tail_start, len(lines)) if MARKER_RE.match(lines[k].rstrip())),
        None,
    )
    if start is None:
        return text, {}, []

    blocks: list[tuple[str, list]] = []
    for line in lines[start:]:
        marker = MARKER_RE.match(line.rstrip())
        if marker:
            blocks.append((marker.group(1), []))
        else:
            blocks[-1][1].append(line.strip())

    images: dict[str, bytes] = {}
    warnings: list[str] = []
    total = 0
    for name, data_lines in blocks:
        try:
            if name in images:
                raise ValueError("name used twice")
            if len(images) >= MAX_IMAGES:
                raise ValueError(f"more than {MAX_IMAGES} images")
            data = decode_png_base64("".join(data_lines))
            if total + len(data) > HARD_TOTAL_BYTES:
                raise ValueError("the images are too large altogether")
            images[name] = data
            total += len(data)
        except ValueError as exc:
            warnings.append(f"Image '{name}' skipped: {exc}.")

    return "\n".join(lines[:start]), images, warnings


def join_image_section(code: str, images: dict) -> str:
    """Inverse of split_image_section(): the text plus the image section
    (without images: the text unchanged)."""
    if not images:
        return code

    parts = []
    for name, data in images.items():
        encoded = base64.b64encode(data).decode("ascii")
        wrapped = [
            encoded[i:i + BASE64_LINE_WIDTH] for i in range(0, len(encoded), BASE64_LINE_WIDTH)
        ]
        parts.append("\n".join([f"##!image {name}"] + wrapped))
    return code + "\n" + "\n".join(parts) + "\n"
