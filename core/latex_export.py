"""Builds a standalone, pdflatex-compilable .tex document from the
results structure produced by core.engine.evaluate_code().

Deliberately NO separate rendering logic: results already contains a
finished LaTeX string per line (the same one shown in the browser via
MathJax's "$$ ... $$" -- see templates/index.html). The export just
wraps these strings in "\\[ ... \\]" (display math) instead of
generating them again -- this guarantees the web view and the export
stay consistent.

Known, deliberately unaddressed limitation: plots are embedded via
\\includegraphics, but only when the document is delivered as a ZIP
(text + PNG files) -- a plain .tex download without image files can't
reference any plots. build_latex_document() therefore always returns
the image files too; the caller (app.py) decides based on that whether
to serve a single .tex or a .zip.
"""

from __future__ import annotations

import base64
import re

from core.images import (
    ALIGNMENTS,
    DEFAULT_DISPLAY_WIDTH,
    PX_PER_INCH,
    png_size,
)
from core.formatter import error_to_latex
from mathlib.units import make_safe_text_latex

_PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[a4paper,margin=2.5cm]{geometry}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{graphicx}

\setlength{\parindent}{0pt}

% Units are emitted as \engiunit{...} (mathlib/units.py::wrap_unit_latex()).
% Size = the "Unit font size" setting of the GUI (see _engiunit_definition()).
%%ENGIUNIT_DEFINITION%%

% Sheet prefix of references to other sheets ("S1::" in S1::L), emitted as
% \engisheet{...} (mathlib/units.py::wrap_sheet_prefix_latex()). Size = the
% "Sheet prefix size" setting of the GUI.
%%ENGISHEET_DEFINITION%%
%%ENGIPICTURE_DEFINITION%%%%SHEET_HEADER%%
\begin{document}
"""

# Only added when the document shows sheet names (more than one sheet):
# the name of the sheet is printed in the page header (\leftmark, set per
# sheet with \markboth).
_SHEET_HEADER = r"""\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\textbf{\leftmark}}
\fancyhead[R]{\thepage}
\renewcommand{\headrulewidth}{0.4pt}
\setlength{\headheight}{14pt}
"""


def _engiunit_definition(unit_scale: float) -> str:
    r"""The \newcommand for the \engiunit{...} marker (see
    mathlib/units.py::wrap_unit_latex()), for the given unit size
    relative to the surrounding text (1.0 = same size).

    At 1.0 the macro is a plain pass-through: no needless \scalebox
    box around every unit. Otherwise \scalebox (graphicx, already in
    the preamble) scales the unit; \displaystyle keeps fractions
    inside a unit (kN/m^2) at the size they'd have in display math.
    """
    if not unit_scale > 0:
        raise ValueError(f"unit_scale must be positive, got {unit_scale!r}")

    if unit_scale == 1:
        return r"\newcommand{\engiunit}[1]{#1}"

    return rf"\newcommand{{\engiunit}}[1]{{\scalebox{{{unit_scale:g}}}{{$\displaystyle #1$}}}}"


def _engisheet_definition(sheet_scale: float) -> str:
    r"""The \newcommand for the \engisheet{...} marker (sheet prefix of a
    reference, see mathlib/units.py::wrap_sheet_prefix_latex()), for
    the given size relative to the surrounding text (0.5 = 50%)."""
    if not sheet_scale > 0:
        raise ValueError(f"sheet_scale must be positive, got {sheet_scale!r}")

    if sheet_scale == 1:
        return r"\newcommand{\engisheet}[1]{#1}"

    return rf"\newcommand{{\engisheet}}[1]{{\scalebox{{{sheet_scale:g}}}{{$#1$}}}}"


_CLOSING = r"""
\end{document}
"""

_DATA_URI_RE = re.compile(r"^data:image/(?P<ext>[a-zA-Z0-9.+-]+);base64,(?P<data>.*)$", re.DOTALL)


def _decode_plot_src(src: str) -> tuple[bytes, str]:
    """Splits a "data:image/png;base64,..." string (see
    mathlib.plotting.create_plot()) into raw image bytes + file
    extension.
    """
    match = _DATA_URI_RE.match(src)
    if not match:
        raise ValueError("Unexpected plot image format (not a data: URI).")

    ext = match.group("ext").split("+")[0]  # e.g. "svg+xml" -> "svg" (unused here, always png)
    return base64.b64decode(match.group("data")), ext


# \engipicture{<width>}{<file>}: the picture with the given width, but
# never wider than the text (a 1200 px picture is 317 mm wide).
_PICTURE_DEFINITION = r"""% Pictures (picture("name") in the input): given width, at most the text width.
\newlength{\engipicwidth}
\newcommand{\engipicture}[2]{%
  \setlength{\engipicwidth}{#1}%
  \ifdim\engipicwidth>\linewidth\setlength{\engipicwidth}{\linewidth}\fi
  \includegraphics[width=\engipicwidth]{#2}%
}
"""

_PICTURE_ENVIRONMENTS = {"left": "flushleft", "center": "center", "right": "flushright"}


def _picture_to_latex(item: dict, pictures: dict, images: list) -> str:
    """LaTeX for one {"type": "image"} result item. The PNG goes to
    `images` once per name ("img_<name>.png"; the name only consists of
    letters, digits and underscore, see core.images)."""
    name = item["name"]
    if name not in pictures:
        raise ValueError(f"Image '{name}' is missing.")

    filename = f"img_{name}.png"
    if not any(existing == filename for existing, _data in images):
        images.append((filename, pictures[name]))

    width_px = item.get("width")
    if not width_px:
        width_px = min(png_size(pictures[name])[0], DEFAULT_DISPLAY_WIDTH)
    width_mm = round(width_px * 25.4 / PX_PER_INCH, 1)

    align = item.get("align") if item.get("align") in ALIGNMENTS else "center"
    env = _PICTURE_ENVIRONMENTS[align]
    return (
        f"\\begin{{{env}}}\n"
        f"\\engipicture{{{width_mm:g}mm}}{{{filename}}}\n"
        f"\\end{{{env}}}"
    )


def _results_to_latex(
    results, images: list, plot_counter: int, pictures: dict | None = None
) -> tuple[str, int]:
    """LaTeX body of one result list; plots are appended to `images`
    (numbered continuously across sheets via plot_counter), pictures
    too (by name, see _picture_to_latex())."""
    body_parts: list[str] = []

    for block in results:
        block_parts: list[str] = []

        for item in block:
            item_type = item.get("type")

            if item_type == "latex":
                block_parts.append(rf"\[ {item['content']} \]")

            elif item_type == "plot":
                plot_counter += 1
                image_bytes, ext = _decode_plot_src(item["src"])
                filename = f"plot_{plot_counter}.{ext}"
                images.append((filename, image_bytes))
                block_parts.append(
                    "\\begin{center}\n"
                    rf"\includegraphics[width=0.7\linewidth]{{{filename}}}"
                    "\n\\end{center}"
                )

            elif item_type == "image":
                try:
                    block_parts.append(_picture_to_latex(item, pictures or {}, images))
                except ValueError as exc:
                    block_parts.append(rf"\[ {error_to_latex('Picture error', exc)} \]")

            elif item_type == "spacer":
                block_parts.append(r"\vspace{0.8em}")

        if block_parts:
            body_parts.append("\n".join(block_parts))

    return "\n\n".join(body_parts), plot_counter


def build_latex_document(
    results,
    title: str = "EngiPad Export",
    unit_scale: float = 1.0,
    sheet_name: str | None = None,
    sheets: list | None = None,
    sheet_scale: float = 0.5,
    pictures: dict | None = None,
) -> tuple[str, list[tuple[str, bytes]]]:
    """Builds the .tex source + a list of (filename, image bytes) for
    every plot contained in the results.

    results: the same structure returned by core.engine.evaluate_code()
    -- a list of "blocks" (one per input line), each a list of
    {"type": "latex"|"plot"|"spacer", ...} dicts.

    unit_scale: size of units relative to the surrounding text (1.0 =
    same size, 0.75 = 75%), the "Unit font size" setting of the GUI.

    sheet_name: name of the sheet the results belong to. Only given for
    workbooks with more than one sheet: the name is then printed in the
    page header. Without it the document looks exactly as for a single
    sheet.

    sheets: [(name, results), ...] to export SEVERAL sheets into one
    document (each starts on a new page, with its own name in the page
    header); takes the place of results/sheet_name.

    sheet_scale: size of the "S1::" prefix of sheet references relative
    to the surrounding text, the "Sheet prefix size" setting.

    pictures: {name: PNG bytes} for the picture("name") lines (already
    checked, see core.images). Each used picture is added to the
    returned image files as "img_<name>.png".
    """
    images: list[tuple[str, bytes]] = []
    plot_counter = 0

    if sheets is not None:
        sections = []
        for index, (name, sheet_results) in enumerate(sheets):
            part, plot_counter = _results_to_latex(sheet_results, images, plot_counter, pictures)
            safe = make_safe_text_latex(name)
            lead = "\\clearpage\n" if index else ""
            sections.append(rf"{lead}\markboth{{{safe}}}{{{safe}}}" + "\n" + part)
        body = "\n\n".join(sections)
        with_header = True
    else:
        body, plot_counter = _results_to_latex(results, images, plot_counter, pictures)
        with_header = sheet_name is not None
        if with_header:
            safe = make_safe_text_latex(sheet_name)
            body = rf"\markboth{{{safe}}}{{{safe}}}" + "\n" + body

    tex = (
        _PREAMBLE
        .replace("%%ENGIUNIT_DEFINITION%%", _engiunit_definition(unit_scale))
        .replace("%%ENGISHEET_DEFINITION%%", _engisheet_definition(sheet_scale))
        .replace("%%ENGIPICTURE_DEFINITION%%", _PICTURE_DEFINITION if r"\engipicture{" in body else "")
        .replace("%%SHEET_HEADER%%\n", _SHEET_HEADER if with_header else "")
        .replace(r"\begin{document}", f"% {title}\n\\begin{{document}}")
    ) + body + _CLOSING

    return tex, images
