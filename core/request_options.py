"""Reading a calculation request and building the page / export from it.

Shared by the server version (app.py, Flask) and the web version
(web/engipad_web.py, Pyodide in the browser). Nothing here imports Flask,
multiprocessing or anything else that cannot run in a browser: the caller
passes an `evaluate` function -- the server one with the time limit of
core/safe_runner.py, the web one plain (there the web worker is stopped
from the outside on a timeout).

`form` is anything with .get(name, default) and `in`: Flask's
request.form, or a plain dict in the web version.
"""
import json
import re

from core import images as image_rules

# Kept light on purpose (core/safe_runner.py imports this module): the
# engine and SymPy are only imported inside the functions that need them.

DEFAULT_PRECISION = "0.01"
DEFAULT_FONT_SIZE = "14"
# Size of units relative to the surrounding text, in percent (100 = same
# size as numbers/variables). See mathlib/units.py::wrap_unit_latex().
DEFAULT_UNIT_FONT_SIZE = "80"
# Size of the sheet prefix of references to other sheets ("S1::" in
# S1::L), in percent of the surrounding text. See
# mathlib/units.py::wrap_sheet_prefix_latex().
DEFAULT_SHEET_FONT_SIZE = "50"

# Default computation timeout in seconds (Settings field empty/invalid).
# core/safe_runner.py takes its default from here.
DEFAULT_TIMEOUT_SECONDS = 10

# User-adjustable computation timeout (Settings), in whole seconds. The
# upper limit keeps a single request from blocking a server worker for
# long -- and must stay BELOW the Gunicorn "--timeout" of the deployment
# (default 30 s, see project_state.txt / server config), otherwise
# Gunicorn kills the worker before our own timeout message can appear.
TIMEOUT_MIN_SECONDS = 1
TIMEOUT_MAX_SECONDS = 30

FONT_SIZE_MIN_PX = 10
FONT_SIZE_MAX_PX = 40
UNIT_FONT_SIZE_MIN_PCT = 25
UNIT_FONT_SIZE_MAX_PCT = 200
SHEET_FONT_SIZE_MIN_PCT = 20
SHEET_FONT_SIZE_MAX_PCT = 150


# --- Settings fields ---------------------------------------------------------

def parse_precision_to_rel_tol(raw_precision: str) -> float:
    """Converts the text from the rounding-precision field (a percent
    value, e.g. "0.01" for 0.01%) into a rel_tol for
    mathlib.units.format_magnitude_decimal() (e.g. 0.0001).

    Falls back to the previous default (0.01% == 1e-4) on invalid/
    empty/non-positive input -- the RAW input still stays in the field
    (see page_context()), only the actual rounding used for the
    computation falls back.
    """
    try:
        precision_percent = float(raw_precision)
        if precision_percent <= 0:
            raise ValueError("Precision must be positive.")
    except (TypeError, ValueError):
        return 1e-4

    return precision_percent / 100


def parse_font_size_px(raw_font_size: str) -> int:
    """Converts the text from the font-size field into a validated pixel
    value for the result container's inline style.

    Falls back to the default on invalid input, like the rounding
    precision. Also clamped to a sane range (10-40px): this field lands
    directly in a style="..." attribute in the template, so a hard
    number is needed here rather than arbitrary text -- this protects
    both against broken layout (e.g. "9999") and against anything other
    than a number ever reaching the style attribute.
    """
    try:
        font_size = float(raw_font_size)
        if font_size <= 0:
            raise ValueError("Font size must be positive.")
    except (TypeError, ValueError):
        return int(DEFAULT_FONT_SIZE)

    return max(FONT_SIZE_MIN_PX, min(FONT_SIZE_MAX_PX, round(font_size)))


def parse_unit_font_size_pct(raw_unit_font_size: str) -> int:
    """Converts the text from the unit-font-size field (a percentage,
    100 = same size as the surrounding text) into a validated integer
    percentage.

    Same rules as parse_font_size_px(): falls back to the default on
    invalid/empty/non-positive input (the RAW input still stays in the
    field) and is clamped to a sane range (25-200%). The result lands in
    a style="--unit-scale: ...%" attribute in the template and in the
    \\scalebox factor of the .tex export, so it must always be a plain
    number, never arbitrary text.
    """
    try:
        unit_font_size = float(raw_unit_font_size)
        if unit_font_size <= 0:
            raise ValueError("Unit font size must be positive.")
    except (TypeError, ValueError):
        return int(DEFAULT_UNIT_FONT_SIZE)

    return max(UNIT_FONT_SIZE_MIN_PCT, min(UNIT_FONT_SIZE_MAX_PCT, round(unit_font_size)))


def parse_sheet_font_size_pct(raw_sheet_font_size: str) -> int:
    """Like parse_unit_font_size_pct(), for the "Sheet prefix size"
    setting (default 50%, clamped to 20-150%). Lands in a style
    attribute and in the \\scalebox factor of the .tex export, so it
    is always a plain number."""
    try:
        size = float(raw_sheet_font_size)
        if size <= 0:
            raise ValueError("Sheet prefix size must be positive.")
    except (TypeError, ValueError):
        return int(DEFAULT_SHEET_FONT_SIZE)

    return max(SHEET_FONT_SIZE_MIN_PCT, min(SHEET_FONT_SIZE_MAX_PCT, round(size)))


def parse_timeout_seconds(raw_timeout, max_seconds: int = TIMEOUT_MAX_SECONDS) -> int | None:
    """Converts the text from the timeout field into whole seconds,
    clamped to 1..max_seconds (30). Returns None for empty/invalid/
    non-positive input, meaning "use the default timeout" (the RAW text
    still stays in the field, like the other settings).

    Always validated here -- the browser's input is never trusted,
    otherwise the upper limit would be worthless.
    """
    try:
        timeout = float(raw_timeout)
        if timeout <= 0 or timeout != timeout or timeout == float("inf"):
            raise ValueError("Timeout must be a positive, finite number.")
    except (TypeError, ValueError):
        return None

    return max(TIMEOUT_MIN_SECONDS, min(max_seconds, round(timeout)))


def read_calc_form(form) -> tuple[str, str, str, str, str, float]:
    """Reads code/precision/font_size/unit_font_size/timeout from a POST form,
    with the same fallback rules for the page and the export -- so e.g.
    the rounding and the unit size used for the export exactly match
    what's currently shown on screen.
    """
    user_input = form.get("code", "")
    precision = form.get("precision", DEFAULT_PRECISION).strip() or DEFAULT_PRECISION
    font_size = form.get("font_size", DEFAULT_FONT_SIZE).strip() or DEFAULT_FONT_SIZE
    unit_font_size = (
        form.get("unit_font_size", DEFAULT_UNIT_FONT_SIZE).strip() or DEFAULT_UNIT_FONT_SIZE
    )
    timeout = form.get("timeout", "").strip()
    rel_tol = parse_precision_to_rel_tol(precision)

    return user_input, precision, font_size, unit_font_size, timeout, rel_tol


def read_sheet_options(form) -> tuple[str | None, bool, str]:
    """Reads the workbook options of a POST form: the sheet currently
    shown (None = first), the "print/export all sheets" checkbox and
    the raw "sheet prefix size" text. Kept apart from read_calc_form()
    so that one stays as it is."""
    active_sheet = form.get("active_sheet", "").strip() or None
    all_sheets = form.get("all_sheets", "") not in ("", "0", "false", "off")
    sheet_font_size = (
        form.get("sheet_font_size", DEFAULT_SHEET_FONT_SIZE).strip()
        or DEFAULT_SHEET_FONT_SIZE
    )
    return active_sheet, all_sheets, sheet_font_size


# --- Pictures (picture("name") lines, see core/images.py) ------------------

def read_image_options(form) -> tuple[int, frozenset | None]:
    """Reads the image fields of a POST form: the setting "Image limit"
    (1/2/3 MB, only used to show it again) and the NAMES of the images
    the browser holds ("image_names", comma separated; images
    themselves never travel with a calculation). None = the field is
    missing, then picture() lines are not checked against a list."""
    limit_mb = image_rules.parse_limit_mb(form.get("image_limit", ""))

    if "image_names" not in form:
        return limit_mb, None

    names = frozenset(
        name
        for name in (part.strip() for part in form.get("image_names", "").split(","))
        if image_rules.is_valid_name(name)
    )
    return limit_mb, names


def read_export_images(form) -> dict:
    """The images of a LaTeX export request: JSON {"name": "<Base64 PNG>"}
    in the field "images". Everything is checked again here (name, PNG
    bytes, sizes, count) -- the browser's checks are never trusted.
    Raises ValueError with a message for the user."""
    raw = form.get("images", "").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("the image data could not be read")
    if not isinstance(data, dict):
        raise ValueError("the image data could not be read")
    if len(data) > image_rules.MAX_IMAGES:
        raise ValueError(f"too many images (at most {image_rules.MAX_IMAGES})")

    pictures = {}
    for name, encoded in data.items():
        if not image_rules.is_valid_name(name):
            raise ValueError(f"invalid image name '{str(name)[:40]}'")
        try:
            pictures[name] = image_rules.decode_png_base64(encoded)
        except ValueError as exc:
            raise ValueError(f"image '{name}': {exc}")
    image_rules.check_image_set(pictures)
    return pictures


# --- Results when a computation did not finish -------------------------------

def timeout_message(timeout: float, max_seconds: int = TIMEOUT_MAX_SECONDS) -> str:
    """Text shown when a computation was stopped after `timeout` seconds;
    the hint to raise the limit is left out at the maximum."""
    message = f"Timeout: computation took longer than {timeout:g}s and was aborted."
    if timeout < max_seconds:
        message += (
            f" You can increase the timeout in Settings "
            f"(max. {max_seconds}s)."
        )
    return message


def failure_outcome(user_input: str, active_sheet: str | None, message: str) -> dict:
    """The dict shape of core.workbook.evaluate_workbook() for a
    computation that did not finish: the sheet tabs stay, "results" holds
    only the message."""
    from core.workbook import parse_workbook
    from mathlib.units import text_to_latex

    try:
        names = [sh.name for sh in parse_workbook(user_input) if sh.name]
    except ValueError:
        names = []
    return {
        "names": names,
        "active": active_sheet if active_sheet in names else (names[0] if names else None),
        "results": [[{"type": "latex", "content": text_to_latex(message)}]],
        "all": None,
    }


# --- Page and export -------------------------------------------------------

def page_context(form, evaluate, *, default_timeout, local_mode, live_default,
                 shutdown_token="") -> dict:
    """All values the page template needs.

    form: the POST form, or None for the first page view (empty result,
    default values). evaluate(user_input, rel_tol, raw_timeout,
    active_sheet, all_sheets, image_names) returns the dict of
    core.workbook.evaluate_workbook().
    """
    from core.engine import DEFAULT_INPUT

    user_input = DEFAULT_INPUT
    precision = DEFAULT_PRECISION
    font_size = DEFAULT_FONT_SIZE
    unit_font_size = DEFAULT_UNIT_FONT_SIZE
    sheet_font_size = DEFAULT_SHEET_FONT_SIZE
    timeout = f"{default_timeout:g}"
    all_sheets = False
    image_limit_mb = image_rules.DEFAULT_LIMIT_MB
    outcome = {"names": [], "active": None, "results": [], "all": None}

    if form is not None:
        user_input, precision, font_size, unit_font_size, timeout_raw, rel_tol = (
            read_calc_form(form)
        )
        active_sheet, all_sheets, sheet_font_size = read_sheet_options(form)
        image_limit_mb, image_names = read_image_options(form)
        # Empty field -> keep showing the default value in it.
        timeout = timeout_raw or timeout
        outcome = evaluate(
            user_input, rel_tol, timeout_raw, active_sheet, all_sheets, image_names
        )

    return dict(
        code=user_input,
        results=outcome["results"],
        sheet_names=outcome["names"],
        active_sheet=outcome["active"] or "",
        all_results=outcome["all"] or [],
        all_sheets=all_sheets,
        sheet_font_size=sheet_font_size,
        sheet_font_size_pct=parse_sheet_font_size_pct(sheet_font_size),
        precision=precision,
        font_size=font_size,
        font_size_px=parse_font_size_px(font_size),
        unit_font_size=unit_font_size,
        timeout=timeout,
        image_limit_mb=image_limit_mb,
        image_limit_choices=image_rules.LIMIT_CHOICES_MB,
        image_config=image_rules.client_config(),
        local_mode=bool(local_mode),
        live_default=bool(live_default),
        shutdown_token=shutdown_token if local_mode else "",
        unit_font_size_pct=parse_unit_font_size_pct(unit_font_size),
    )


class ExportError(ValueError):
    """The export request itself is invalid (e.g. a broken image); the
    message is meant for the user."""


def latex_export(form, evaluate) -> tuple[str, bytes, str]:
    """The LaTeX export: (file name, file content, MIME type).

    A plain .tex file, or a .zip with the .tex and the image files when
    the sheet contains plots or pictures. evaluate: as in page_context().
    Raises ExportError for an invalid request.
    """
    import io
    import zipfile

    from core.latex_export import build_latex_document

    user_input, _precision, _font_size, unit_font_size, timeout_raw, rel_tol = (
        read_calc_form(form)
    )
    active_sheet, all_sheets, sheet_font_size = read_sheet_options(form)
    try:
        pictures = read_export_images(form)
    except ValueError as exc:
        raise ExportError(str(exc))
    outcome = evaluate(
        user_input, rel_tol, timeout_raw, active_sheet, all_sheets,
        frozenset(pictures),
    )

    export_kwargs = {
        "unit_scale": parse_unit_font_size_pct(unit_font_size) / 100,
        "sheet_scale": parse_sheet_font_size_pct(sheet_font_size) / 100,
        "pictures": pictures,
    }
    # More than one sheet: the sheet name goes into the page header
    # (one sheet: the document looks exactly as it always did).
    if outcome["names"] and all_sheets and outcome["all"]:
        tex, images = build_latex_document(None, sheets=outcome["all"], **export_kwargs)
        basename = "engipad_workbook"
    elif outcome["names"]:
        tex, images = build_latex_document(
            outcome["results"], sheet_name=outcome["active"], **export_kwargs
        )
        safe_sheet = re.sub(r"[^A-Za-z0-9_]", "_", outcome["active"])
        basename = f"engipad_{safe_sheet}"
    else:
        tex, images = build_latex_document(outcome["results"], **export_kwargs)
        basename = "engipad_export"

    if not images:
        return f"{basename}.tex", tex.encode("utf-8"), "application/x-tex"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{basename}.tex", tex)
        for filename, image_bytes in images:
            zf.writestr(filename, image_bytes)
    return f"{basename}.zip", buf.getvalue(), "application/zip"
