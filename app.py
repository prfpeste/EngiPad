import hmac
import io
import multiprocessing
import os
import secrets
import sys
import threading
import zipfile
from urllib.parse import urlsplit

from flask import Flask, Response, abort, render_template, request

from core.engine import DEFAULT_INPUT, evaluate_code
from core.latex_export import build_latex_document
from core.safe_runner import DEFAULT_TIMEOUT_SECONDS, run_with_timeout
from mathlib.units import make_safe_text_latex

DEFAULT_PRECISION = "0.01"
DEFAULT_FONT_SIZE = "14"
# Size of units relative to the surrounding text, in percent (100 = same
# size as numbers/variables). See mathlib/units.py::wrap_unit_latex().
DEFAULT_UNIT_FONT_SIZE = "80"

# User-adjustable computation timeout (Settings), in whole seconds. The
# upper limit keeps a single request from blocking a server worker for
# long -- and must stay BELOW the Gunicorn "--timeout" of the deployment
# (default 30 s, see project_state.txt / server config), otherwise
# Gunicorn kills the worker before our own timeout message can appear.
_TIMEOUT_MIN_SECONDS = 1
_TIMEOUT_MAX_SECONDS = 30

_FONT_SIZE_MIN_PX = 10
_FONT_SIZE_MAX_PX = 40
_UNIT_FONT_SIZE_MIN_PCT = 25
_UNIT_FONT_SIZE_MAX_PCT = 200


def _resource_base_path() -> str:
    """Directory templates/ and static/ live under.

    When running from source, that's simply this file's directory. When
    frozen into a single executable (PyInstaller --onefile), everything
    bundled via --add-data is extracted at startup into a temporary
    directory exposed as sys._MEIPASS -- resources must be looked up
    there instead, or Flask silently fails to find templates/static and
    the packaged app shows a blank/unstyled page.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def _parse_precision_to_rel_tol(raw_precision: str) -> float:
    """Converts the text from the rounding-precision field (a percent
    value, e.g. "0.01" for 0.01%) into a rel_tol for
    mathlib.units.format_magnitude_decimal() (e.g. 0.0001).

    Falls back to the previous default (0.01% == 1e-4) on invalid/
    empty/non-positive input -- the RAW input still stays in the field
    (see index()), only the actual rounding used for the computation
    falls back.
    """
    try:
        precision_percent = float(raw_precision)
        if precision_percent <= 0:
            raise ValueError("Precision must be positive.")
    except (TypeError, ValueError):
        return 1e-4

    return precision_percent / 100


def _parse_font_size_px(raw_font_size: str) -> int:
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

    return max(_FONT_SIZE_MIN_PX, min(_FONT_SIZE_MAX_PX, round(font_size)))


def _parse_unit_font_size_pct(raw_unit_font_size: str) -> int:
    """Converts the text from the unit-font-size field (a percentage,
    100 = same size as the surrounding text) into a validated integer
    percentage.

    Same rules as _parse_font_size_px(): falls back to the default on
    invalid/empty/non-positive input (the RAW input still stays in the
    field, see index()) and is clamped to a sane range (25-200%). The
    result lands in a style="--unit-scale: ...%" attribute in the
    template and in the \\scalebox factor of the .tex export, so it must
    always be a plain number, never arbitrary text.
    """
    try:
        unit_font_size = float(raw_unit_font_size)
        if unit_font_size <= 0:
            raise ValueError("Unit font size must be positive.")
    except (TypeError, ValueError):
        return int(DEFAULT_UNIT_FONT_SIZE)

    return max(_UNIT_FONT_SIZE_MIN_PCT, min(_UNIT_FONT_SIZE_MAX_PCT, round(unit_font_size)))


def _parse_timeout_seconds(raw_timeout) -> int | None:
    """Converts the text from the timeout field into whole seconds,
    clamped to 1..30. Returns None for empty/invalid/non-positive input,
    meaning "use DEFAULT_TIMEOUT_SECONDS" (the RAW text still stays in
    the field, like the other settings, see index()).

    Always validated here on the server -- the browser's input is never
    trusted, otherwise the upper limit would be worthless.
    """
    try:
        timeout = float(raw_timeout)
        if timeout <= 0 or timeout != timeout or timeout == float("inf"):
            raise ValueError("Timeout must be a positive, finite number.")
    except (TypeError, ValueError):
        return None

    return max(_TIMEOUT_MIN_SECONDS, min(_TIMEOUT_MAX_SECONDS, round(timeout)))


# --- Local "Quit" button (POST /shutdown) --------------------------------
# Only active when the server was started locally by run.py, which sets
# app.config["ENGIPAD_LOCAL"] = True. On a real server (Gunicorn imports
# app:app and never runs run.py) the flag stays False: the route answers
# 404 and the button is not rendered -- otherwise any user could stop the
# service for everybody.
_LOCAL_ADDRESSES = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
_LOCAL_HOSTNAMES = ("127.0.0.1", "localhost", "::1")


def _terminate_children() -> None:
    """Kills running computation processes (core/safe_runner.py) --
    otherwise a computation that is still running would be orphaned when
    the server process exits (its timeout watchdog dies with the parent)."""
    for process in multiprocessing.active_children():
        try:
            process.terminate()
        except Exception:  # noqa: BLE001 -- best effort, we are exiting anyway
            pass


def _schedule_exit(delay_seconds: float = 0.4) -> None:
    """Ends the whole process shortly AFTER the HTTP response was sent.
    os._exit() also works when the server runs in a daemon thread (frozen
    build, see run.py), where a normal sys.exit() would not."""
    timer = threading.Timer(delay_seconds, os._exit, args=(0,))
    timer.daemon = True
    timer.start()


def _read_calc_form(form) -> tuple[str, str, str, str, str, float]:
    """Reads code/precision/font_size/unit_font_size/timeout from a POST form,
    with the same fallback rules as index() -- shared by index() and
    export_latex() so e.g. the rounding and the unit size used for the
    export exactly match what's currently shown on screen.
    """
    user_input = form.get("code", "")
    precision = form.get("precision", DEFAULT_PRECISION).strip() or DEFAULT_PRECISION
    font_size = form.get("font_size", DEFAULT_FONT_SIZE).strip() or DEFAULT_FONT_SIZE
    unit_font_size = (
        form.get("unit_font_size", DEFAULT_UNIT_FONT_SIZE).strip() or DEFAULT_UNIT_FONT_SIZE
    )
    timeout = form.get("timeout", "").strip()
    rel_tol = _parse_precision_to_rel_tol(precision)

    return user_input, precision, font_size, unit_font_size, timeout, rel_tol


def _evaluate_code_safely(user_input: str, rel_tol: float, raw_timeout: str = ""):
    """Like core.engine.evaluate_code(), but with a hard time limit
    (see core/safe_runner.py) -- protects the server from an
    intentionally or accidentally very expensive input (a huge symbolic
    integral, a tall power tower, a large matrix, ...) blocking a
    request indefinitely.
    """
    timeout = _parse_timeout_seconds(raw_timeout)
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_SECONDS

    status, value = run_with_timeout(
        evaluate_code, args=(user_input,), kwargs={"rel_tol": rel_tol},
        timeout=timeout,
    )

    if status == "ok":
        return value

    if status == "timeout":
        message = f"Timeout: computation took longer than {timeout:g}s and was aborted."
        if timeout < _TIMEOUT_MAX_SECONDS:
            message += (
                f" You can increase the timeout in Settings "
                f"(max. {_TIMEOUT_MAX_SECONDS}s)."
            )
    else:
        message = f"Computation failed: {value}"

    return [[{"type": "latex", "content": rf"\text{{{make_safe_text_latex(message)}}}"}]]


def create_app():
    base_path = _resource_base_path()
    app = Flask(
        __name__,
        template_folder=os.path.join(base_path, "templates"),
        static_folder=os.path.join(base_path, "static"),
    )

    app.config.setdefault("ENGIPAD_LOCAL", False)
    # Random per start; only pages served by THIS process contain it.
    app.config["SHUTDOWN_TOKEN"] = secrets.token_urlsafe(16)

    @app.route("/", methods=["GET", "POST"])
    def index():
        user_input = DEFAULT_INPUT
        precision = DEFAULT_PRECISION
        font_size = DEFAULT_FONT_SIZE
        unit_font_size = DEFAULT_UNIT_FONT_SIZE
        timeout = f"{DEFAULT_TIMEOUT_SECONDS:g}"
        results = []

        if request.method == "POST":
            user_input, precision, font_size, unit_font_size, timeout_raw, rel_tol = (
                _read_calc_form(request.form)
            )
            # Empty field -> keep showing the default value in it.
            timeout = timeout_raw or timeout
            results = _evaluate_code_safely(user_input, rel_tol, timeout_raw)

        return render_template(
            "index.html",
            code=user_input,
            results=results,
            precision=precision,
            font_size=font_size,
            font_size_px=_parse_font_size_px(font_size),
            unit_font_size=unit_font_size,
            timeout=timeout,
            local_mode=bool(app.config["ENGIPAD_LOCAL"]),
            shutdown_token=app.config["SHUTDOWN_TOKEN"] if app.config["ENGIPAD_LOCAL"] else "",
            unit_font_size_pct=_parse_unit_font_size_pct(unit_font_size),
        )

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        if not app.config["ENGIPAD_LOCAL"]:
            abort(404)

        # Three independent checks: request comes from this machine, the
        # Host header is a local name (DNS rebinding protection) and the
        # per-start token from the page is present (protects against a
        # foreign web page posting to http://127.0.0.1:5000/shutdown).
        if request.remote_addr not in _LOCAL_ADDRESSES:
            abort(403)
        if (urlsplit("//" + request.host).hostname or "").lower() not in _LOCAL_HOSTNAMES:
            abort(403)
        token = request.headers.get("X-Shutdown-Token", "")
        if not hmac.compare_digest(token.encode(), app.config["SHUTDOWN_TOKEN"].encode()):
            abort(403)

        _terminate_children()
        _schedule_exit()
        return Response("EngiPad stopped.", mimetype="text/plain")

    @app.route("/export/latex", methods=["POST"])
    def export_latex():
        user_input, _precision, _font_size, unit_font_size, timeout_raw, rel_tol = (
            _read_calc_form(request.form)
        )
        results = _evaluate_code_safely(user_input, rel_tol, timeout_raw)
        tex, images = build_latex_document(
            results, unit_scale=_parse_unit_font_size_pct(unit_font_size) / 100
        )

        if not images:
            return Response(
                tex,
                mimetype="application/x-tex",
                headers={
                    "Content-Disposition": "attachment; filename=engipad_export.tex"
                },
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("engipad_export.tex", tex)
            for filename, image_bytes in images:
                zf.writestr(filename, image_bytes)
        buf.seek(0)

        return Response(
            buf.getvalue(),
            mimetype="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=engipad_export.zip"
            },
        )

    return app


app = create_app()
