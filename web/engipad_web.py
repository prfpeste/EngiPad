"""Entry point of the EngiPad web version: what the web worker
(static/web/worker.js) calls in Python (Pyodide).

It is the counterpart of the Flask routes in app.py and uses the same
shared code (core/request_options.py) and the same page
(templates/base.html, here filled by templates/web.html), so that both
versions produce the same results for the same input
(tests/test_web_render.py checks this).

Differences to the server version, all on purpose:
- no Flask: the template is rendered with Jinja2 directly, set up like
  Flask does it (autoescaping for .html, sorted JSON), with an url_for()
  that gives RELATIVE paths ("static/main.css"), so the page works from
  any folder (Moodle, GitHub Pages, ...);
- no time limit here: the web worker is stopped from the outside
  (worker.terminate()) and started again; the page with the timeout
  message is then made by timeout_page();
- packages that are loaded in the background (NumPy, matplotlib,
  CoolProp) are guarded (web/package_guard.py): a calculation that needs
  one of them before it is there answers {"need": [...]} instead of a
  page, and the worker calculates again once the package is loaded.

`form` is a dict of the form fields, as the browser sends them.
"""
import os

import jinja2

from core import request_options as options
from web import package_guard

TEMPLATE = "web.html"

# The templates are next to this package in the source tree; in the web
# build all of this lives in pylib.zip, where they are read through the
# zip importer (see _load_template).
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

_environment = None


def _load_template(name):
    path = os.path.join(_TEMPLATE_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        pass
    loader = globals().get("__loader__")
    if loader is not None and hasattr(loader, "get_data"):
        try:
            return loader.get_data(path).decode("utf-8")
        except OSError:
            pass
    return None   # Jinja2 reports TemplateNotFound


def url_for(endpoint, filename):
    """Like Flask's url_for('static', filename=...), but relative."""
    if endpoint != "static":
        raise ValueError(f"no endpoint {endpoint!r} in the web version")
    return "static/" + filename


def environment():
    """The Jinja2 environment, set up like Flask's (created once)."""
    global _environment
    if _environment is None:
        env = jinja2.Environment(
            loader=jinja2.FunctionLoader(_load_template),
            autoescape=jinja2.select_autoescape(
                enabled_extensions=("html", "htm", "xml", "xhtml", "svg"),
                default_for_string=True, default=False,
            ),
        )
        env.globals["url_for"] = url_for
        _environment = env
    return _environment


def _evaluate(user_input, rel_tol, raw_timeout, active_sheet, all_sheets, image_names):
    """core.workbook.evaluate_workbook() without a time limit (see module doc)."""
    from core.workbook import evaluate_workbook

    return evaluate_workbook(
        user_input, rel_tol=rel_tol, active=active_sheet,
        all_sheets=all_sheets, image_names=image_names,
    )


def _render(context):
    return environment().get_template(TEMPLATE).render(**context)


def _context(form, evaluate=_evaluate):
    return options.page_context(
        form, evaluate,
        default_timeout=options.DEFAULT_TIMEOUT_SECONDS,
        local_mode=False,
        live_default=True,    # no server load in the web version
    )


def render_page(form=None):
    """The whole page for the form (None: first view with the example
    and an empty result), as the Flask route "/" would render it."""
    return _render(_context(form))


def export_latex(form):
    """The LaTeX export: (file name, bytes, MIME type), as /export/latex.
    Raises core.request_options.ExportError for an invalid request."""
    return options.latex_export(form, _evaluate)


def timeout_seconds(form):
    """The time limit the web worker has to enforce for this form."""
    timeout = options.parse_timeout_seconds(form.get("timeout", "").strip())
    return options.DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout


def timeout_page(form):
    """The page shown after a calculation was stopped: the same message
    as on the server, the sheet tabs stay."""
    seconds = timeout_seconds(form)
    message = options.timeout_message(seconds)

    def stopped(user_input, rel_tol, raw_timeout, active_sheet, all_sheets, image_names):
        return options.failure_outcome(user_input, active_sheet, message)

    return _render(_context(form, stopped))


# --- What the web worker calls (plain dicts, easy to hand to JavaScript) -----

def calculate(form):
    """{"html": page} or {"need": [package groups]} (see package_guard)."""
    package_guard.start_request()
    html = render_page(form)
    need = package_guard.requested()
    if need:
        return {"need": need}
    return {"html": html}


def export(form):
    """{"filename", "data" (bytes), "mimetype"}, {"error": text} or
    {"need": [package groups]}."""
    package_guard.start_request()
    try:
        filename, data, mimetype = export_latex(form)
    except options.ExportError as exc:
        return {"error": f"Export failed: {exc}."}
    need = package_guard.requested()
    if need:
        return {"need": need}
    return {"filename": filename, "data": data, "mimetype": mimetype}
