"""Workbooks: several named sheets in ONE text.

File/text format (the "variant A" of the design discussion): a workbook
is a plain text in which every sheet starts with a marker line

    ##NameA
    L = 5'm
    ...
    ##NameB
    t = NameA::L / NameA::c

A text is a workbook only if its FIRST non-blank line is a marker --
this way an old single-sheet file that merely contains a comment line
like "##Results" somewhere is never misread. Everything else is one
unnamed sheet (name None), exactly as before.

Sheets reference each other read-only as "Sheet::variable" and are
evaluated lazily: a sheet is evaluated (once) the first time another
sheet asks for one of its variables, so the order follows from the
references, and a circular reference is detected on the way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.context import EvaluationContext
from core.engine import evaluate_lines
from core.formatter import error_to_latex
from core.images import split_image_section

# Letters/digits/underscore, starting with a letter or underscore --
# the same characters the lexer accepts for identifiers.
_NAME_PATTERN = r"[^\W\d]\w*"
SHEET_MARKER_RE = re.compile(rf"^##[ \t]*({_NAME_PATTERN})[ \t]*$")
SHEET_NAME_RE = re.compile(rf"^{_NAME_PATTERN}$")


@dataclass
class Sheet:
    name: str | None
    code: str


def is_valid_sheet_name(name: str) -> bool:
    return bool(SHEET_NAME_RE.match(name)) and "::" not in name


def parse_workbook(text: str) -> list[Sheet]:
    """Splits a text into sheets (see module docstring). Raises
    ValueError for duplicate sheet names.

    An image section at the end of the text ("##!image name" + Base64,
    see core/images.py) is not part of any sheet and is dropped here."""
    text, _images, _warnings = split_image_section(text)
    lines = text.splitlines()

    first = next((ln for ln in lines if ln.strip()), None)
    if first is None or not SHEET_MARKER_RE.match(first.rstrip()):
        return [Sheet(None, text)]

    sheets: list[Sheet] = []
    current_name: str | None = None
    current_lines: list[str] = []
    started = False

    for line in lines:
        match = SHEET_MARKER_RE.match(line.rstrip())
        if match:
            if started:
                sheets.append(Sheet(current_name, "\n".join(current_lines)))
            current_name = match.group(1)
            current_lines = []
            started = True
        elif started:
            current_lines.append(line)
        # (blank lines before the first marker are dropped)

    sheets.append(Sheet(current_name, "\n".join(current_lines)))

    seen = set()
    for sheet in sheets:
        if sheet.name in seen:
            raise ValueError(f"Duplicate sheet name '{sheet.name}'.")
        seen.add(sheet.name)

    return sheets


def serialize_workbook(sheets: list[Sheet]) -> str:
    """Inverse of parse_workbook(). A single unnamed sheet is just its
    code (no marker)."""
    if len(sheets) == 1 and sheets[0].name is None:
        return sheets[0].code
    return "\n".join(f"##{sh.name}\n{sh.code}" for sh in sheets)


class Workbook:
    """Lazy evaluation of all sheets of a workbook."""

    def __init__(self, sheets: list[Sheet], rel_tol: float, image_names=None):
        self._image_names = image_names
        self._codes = {sh.name: sh.code for sh in sheets}
        self._rel_tol = rel_tol
        self._contexts: dict[str, EvaluationContext] = {}
        self._results: dict = {}
        self._stack: list[str] = []
        self._cycle: str | None = None

    @property
    def names(self) -> list:
        return list(self._codes)

    def evaluate(self, name):
        """Results (list of blocks, see core.engine) of one sheet."""
        if name in self._results:
            return self._results[name]

        self._stack.append(name)
        try:
            ctx = EvaluationContext(
                sheet_lookup=lambda sheet, var, _me=name: self._lookup(_me, sheet, var)
            )
            self._contexts[name] = ctx
            results = evaluate_lines(
                self._codes[name], self._rel_tol, ctx, self._image_names
            )
        finally:
            self._stack.pop()

        self._results[name] = results
        return results

    def _lookup(self, from_sheet, sheet, var):
        if sheet not in self._codes:
            raise ValueError(f"Sheet '{sheet}' does not exist.")

        if sheet == from_sheet:
            ctx = self._contexts[sheet]  # own variable, current value
        else:
            if sheet in self._stack:
                path = " -> ".join(self._stack[self._stack.index(sheet):] + [sheet])
                self._cycle = f"Circular reference between sheets: {path}."
                raise ValueError(self._cycle)
            self.evaluate(sheet)
            ctx = self._contexts[sheet]

        if var not in ctx.user_vars:
            if self._cycle:
                # The variable is most likely missing because its own
                # line failed on the circular reference: say that.
                raise ValueError(self._cycle)
            raise ValueError(f"Variable '{var}' is not defined in sheet '{sheet}'.")
        return ctx.var_ns[var]


def evaluate_workbook(
    text: str,
    rel_tol: float = 1e-4,
    active: str | None = None,
    all_sheets: bool = False,
    image_names=None,
) -> dict:
    """Evaluates a whole workbook text.

    image_names: names of the loaded images (see core.engine); None =
    do not check picture() lines against a list.

    Returns {"names": [...], "active": name, "results": blocks of the
    active sheet, "all": [(name, blocks), ...] or None}. For a single
    unnamed sheet "names" is empty and "results" is exactly what
    core.engine.evaluate_code() returns. Sheets are only evaluated as
    far as needed: the active one (plus whatever it references), or all
    of them if all_sheets is set.
    """
    try:
        sheets = parse_workbook(text)
    except ValueError as exc:
        return {
            "names": [],
            "active": None,
            "results": [[{"type": "latex", "content": error_to_latex("Workbook error", exc)}]],
            "all": None,
        }

    if len(sheets) == 1 and sheets[0].name is None:
        book = Workbook(sheets, rel_tol, image_names)
        return {"names": [], "active": None, "results": book.evaluate(None), "all": None}

    book = Workbook(sheets, rel_tol, image_names)
    names = book.names
    if active not in names:
        active = names[0]

    results = book.evaluate(active)
    every = [(n, book.evaluate(n)) for n in names] if all_sheets else None

    return {"names": names, "active": active, "results": results, "all": every}
