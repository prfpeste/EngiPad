# EngiPad – User Guide

EngiPad is a calculation notebook for engineers. You type formulas in a plain text editor on the left; the typeset result (LaTeX via MathJax) appears on the right. This guide covers everything you can do with it.

**Contents**

1. [The interface](#1-the-interface)
2. [Basics: assignments and calculations](#2-basics-assignments-and-calculations)
3. [Units](#3-units)
4. [Symbolic math](#4-symbolic-math)
5. [Equations and solving](#5-equations-and-solving)
6. [Sums, products, limits](#6-sums-products-limits)
7. [Vectors and matrices](#7-vectors-and-matrices)
8. [Plots](#8-plots)
9. [Pictures](#9-pictures)
10. [Comments and text](#10-comments-and-text)
11. [Variable names: Greek letters, subscripts, accents](#11-variable-names-greek-letters-subscripts-accents)
12. [Worksheets (several sheets in one file)](#12-worksheets-several-sheets-in-one-file)
13. [Calculating: button, Ctrl+Enter, live update](#13-calculating-button-ctrlenter-live-update)
14. [Settings](#14-settings)
15. [Saving, loading, printing, exporting](#15-saving-loading-printing-exporting)
16. [Errors and limits](#16-errors-and-limits)
17. [Quick reference](#17-quick-reference)

---

## 1. The interface

- **Left:** the editor (your input).
- **Right:** the output (formulas and plots).
- **Below the editor:** the sheet tabs (see [Worksheets](#12-worksheets-several-sheets-in-one-file)).

**Toolbar above the editor**

| Button | Function |
|---|---|
| Open | Load a text file into the editor |
| Save | Download the editor content as a text file |
| Undo / Redo | Take back or restore your last changes (also `Ctrl+Z` / `Ctrl+Y`); see below |
| Greek | Insert Greek letters and symbols |
| Functions | Insert snippets for `sqrt`, `sin`, `cos`, `log`, … |
| Equations & calculus | Insert `diff`, `integrate`, `Eq`, `solve`, `sum`, `prod`, `lim`, … |
| Matrices & vectors | Insert matrix and vector snippets |
| Plot | Insert a `plot(...)` snippet |
| Insert image | Add a PNG picture and insert its `picture(...)` line (see [Pictures](#9-pictures)) |
| Settings | Rounding, font sizes, timeout, image limit, live update, … (see [Settings](#14-settings)) |
| Calculate | Evaluate the input |

**Undo and redo.** The two arrow buttons (between *Save* and *Greek*) work on the whole file: typing, inserted snippets and pictures, and also adding, deleting and renaming sheets (including the rewritten `Sheet::var` references). They are greyed out when there is nothing to undo or redo. Typing is grouped into sensible steps: a short pause, Enter, pasting, cutting or switching between typing and deleting starts a new step. Switching sheets is not a step; undo shows the sheet in which the change happened. On the keyboard use `Ctrl+Z` for undo and `Ctrl+Y` or `Ctrl+Shift+Z` for redo (`Cmd` on a Mac). Up to 200 steps are kept. The history is not saved in the file; it starts empty after *Open*. It survives *Calculate* and a page reload within the same browser tab, but not closing the tab. A picture stays loaded when its line is undone, so redo brings it back.

**Toolbar above the output**

| Button | Function |
|---|---|
| Print | Open the browser's print dialog (also used for "save as PDF") |
| LaTeX export | Download a `pdflatex`-ready `.tex` file (a `.zip` if the sheet contains plots or pictures) |
| Info | Version, author, technology |
| Quit | Stops the program (only in the local version) |

---

## 2. Basics: assignments and calculations

Every line is evaluated from top to bottom. Variables keep their value for all following lines.

```text
a = 3
b = 4
c = sqrt(a^2 + b^2)
```

Operators: `+  -  *  /  ^` (power), parentheses `( )`. `**` is not needed; use `^`.

Several commands on one line are separated by `;`:

```text
a = 5'm/s^2 ; m = 10'kg ; F = m * a
```

A line that contains only an expression (no `=`) is calculated and shown:

```text
a^2 + b^2
```

Constants: `pi` (or `π`), `e` (Euler's number), `I` (imaginary unit), `∞` (infinity).

Numbers use a decimal **point**: `0.25`. Scientific notation: `1.5e-3`.

---

## 3. Units

### 3.1 Entering a unit

Put an apostrophe `'` between the number and the unit:

```text
v = 10'm/s
t = 3's
m_a = 10'kg
k = 5'W/(m*K)
p = 2'bar
```

Use `*`, `/`, `^` and parentheses inside the unit. Units are checked: adding `5'm + 3'kg` is an error (*incompatible units*), and exponents and arguments of `sin`, `log`, … must be dimensionless.

Available units include (among many others):

- Length / area / volume: `m mm cm km um nm L mL`
- Mass / force / pressure: `kg g mg to N kN MN Pa kPa MPa GPa bar mbar hPa atm`
- Time / frequency: `s ms us min h d Hz kHz MHz rpm`
- Energy / power: `J kJ MJ Wh kWh W kW MW`
- Electrical: `V mV kV A mA Ohm kOhm F uF H C Ah S T Wb`
- Temperature and angle: `K degC deg rad`
- Amount of substance: `mol mmol`

Micro can be typed as `u`, `µ` or `μ` (`5'uF`, `5'µF`). Ohm is typed `Ohm` and displayed as Ω. A tonne is `to` (displayed as t).

### 3.2 How results are displayed

- A plain number with a unit that you assign (`T = 110'degC`) keeps the unit you entered.
- Results of calculations are shown in a fitting unit automatically (e.g. `N` instead of `kg m/s^2`, `J`, `W`, `Pa`, `C` for `A*s`, …).

### 3.3 Converting with `|`

Append `| unit` to say in which unit the result should be shown:

```text
E = 5'kW * 3'h | kWh
p = 2'bar | Pa
l = 2500'mm | m
```

The target after `|` can be any product, quotient or power of known units. It does not have to be in the unit table:

```text
c = 4.18'kJ/(kg*K) | J/(kg*K)
s = 0.5'J | J/K
```

The conversion is checked: converting a length to seconds is an error.

You can also convert an existing variable without assigning:

```text
F | kN
```

### 3.4 Special cases

- **Zero with a unit** works: `z_1 = 0'm ; z_2 = 15'm ; dz = z_1 + z_2`.
- **Temperatures:** `degC` is handled as a temperature scale (`0'degC` is 273.15 K). Differences of temperatures are best written in `K`.
- A name that was never assigned stays a symbol. `s` or `L` are only units after an apostrophe.

---

## 4. Symbolic math

Variables without a value stay symbolic:

```text
f = x^2 + 3*x
diff(f, x)
integrate(f, (x, 0, 1))
```

**Display mode `:=`** shows the formula but does not evaluate numbers into it:

```text
f := x^2 + 2*x + 1
df := diff(f, x)
```

Available calculus functions include `diff`, `integrate` (indefinite and definite), `lim`, and `Eq`, `solve` (see below).

Other functions: `sqrt`, `exp`, `ln`, `log`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`, `sinh`, `cosh`, `tanh`, `abs`, and for complex numbers `re`, `im`, `conjugate`, `arg`.

---

## 5. Equations and solving

```text
"Quadratic equation"
sol = solve(Eq(x^2 - 5, 0), x)
x_1 = sol[0] ; x_2 = sol[1]
```

- `Eq(left, right)` builds an equation.
- `solve(eq, x)` solves symbolically, `csolve` allows complex solutions, `nsolve(expr, (x), (start))` solves numerically from a start value.
- `sol[0]`, `sol[1]`, … picks one solution out of a list. Indices start at 0. For matrices: `M[1][2]`.

```text
nsolve((x^2 - 2), (x), (1))
```

---

## 6. Sums, products, limits

```text
sum(i, (i, 0, n))
prod(i, (i, 1, n))
lim(sin(x)/x, x, 0)
```

With numbers as limits the sum is evaluated:

```text
sum(1/i^2, (i, 1, 100))
```

---

## 7. Vectors and matrices

```text
v = vec(1, 2, 3)
w = vec(4, 5, 6)
dot(v, w)
cross(v, w)
norm(v)
```

```text
A = mat([[1, 2], [3, 4]])
det(A)
inv(A)
rank(A)
trace(A)
T(A)
```

| Function | Meaning |
|---|---|
| `vec`, `mat`, `Matrix` | create vector / matrix |
| `det`, `inv`, `T`, `trace`, `rank` | determinant, inverse, transpose, trace, rank |
| `dot`, `cross`, `norm` | dot product, cross product, length |
| `eye`, `zeros`, `ones` | identity / zero / one matrices |
| `solve_linear` | solve a linear system |
| `eigenvals`, `eigenvects` | eigenvalues and eigenvectors |

Operators `+ - *` and `^` work on matrices (`A^2`, `A*v`).

---

## 8. Plots

```text
f = sin(x)
plot(f, x, 0, 2*pi)
```

`plot(function, variable, min, max)`. The plot is generated as an image in the output. Functions with units are plotted in SI base units; the axis label says so. Plots are included when you export to LaTeX (as a `.zip`).

---

## 9. Pictures

You can show a picture in the output, for example a system sketch.

1. Click **Insert image** (right of *Plot*) and choose a PNG file.
2. EngiPad inserts a line at the cursor:

```text
picture("beam", width=500, align=center)
```

3. Calculate. The picture appears in the output at the place of the line.

The line shows all options, so you can change the picture by editing it:

| Part | Meaning |
|---|---|
| `"beam"` | Name of the picture. EngiPad derives it from the file name (letters, digits and underscore only; a second picture with the same name becomes `beam_2`). |
| `width=500` | Width in **pixels**, 1–1200. Without `width` the picture is shown in its own width, but at most 500 px. |
| `align=center` | `left`, `center` (default) or `right` |

```text
picture("beam", width=300, align=left)
picture("beam")                          # own size, at most 500 px, centred
```

The same picture can be used several times, for example with different widths. A picture is never wider than the page; in the LaTeX export 96 px correspond to 1 inch, as in the print layout.

**Where the pictures are stored.** Inside the saved text file: the pictures are appended at its end as Base64 blocks that start with `##!image name`. One file therefore contains everything, but it is no longer pleasant to read in a text editor or to compare with a diff tool. Do not edit that part by hand. The pictures belong to the whole file, so every sheet can use any picture by its name.

**What is not saved.** Pictures that no `picture("…")` line refers to are dropped when you save. A commented line (`# picture("beam")`) still counts as a reference, so you can switch a picture off temporarily. If you delete the line, the picture disappears from the file with the next save; until then Ctrl+Z brings the line (and the picture) back.

**Limits.**

- PNG only. Other formats (JPEG, SVG, …) are refused.
- Large pictures are reduced automatically (at most 1200 px wide) until they fit. If a picture is still too big, you get a message; use a smaller picture or raise the limit.
- At most **10 pictures** per file.
- The total size of all pictures is limited by **Settings → Image limit** (1, 2 or 3 MB, default 1 MB); a single picture may use a fifth of it. Sketches and diagrams are small; photos usually are not.

**Good to know.**

- Files with pictures need this version or a newer one. An older version shows the Base64 lines as faulty input.
- If you copy a `picture(...)` line into another file without its picture, the output shows *Image 'beam' is not loaded*.
- The browser keeps pictures in its automatic backup (autosave) as long as the browser storage has room; if it is full you are told once, and you should save the file.

---

## 10. Comments and text

| Input | Result |
|---|---|
| `# note` | Comment, ignored completely (not shown) |
| `"Heading"` | Shown as **bold** text |
| `'Remark'` | Shown as plain text |

Spaces inside a text line are preserved, so you can align text:

```text
L = b*l/b ; '                          [1]'
```

A text line can sit behind a calculation on the same line (after `;`) – useful for equation numbers or notes.

Special characters such as `^ _ ~ \ % # & $` inside text are displayed correctly.

---

## 11. Variable names: Greek letters, subscripts, accents

**Greek letters** can be typed directly or inserted via the Greek menu:

```text
α = 30'deg
ΔT = 20'K
μ = 0.12
ε = 0.003
```

**Subscripts:** `m_a`, `F_max`, or with several characters/commas in braces: `m_{x,1}`.

**Accents:** append `__dot`, `__bar`, `__hat`, `__tilde` (double underscore) to put an accent over the symbol:

```text
Q__dot = 5'W        ; renders as Q̇
P = Q__dot * 2       ; Q̇ is a normal variable
Q__bar = 3 ; a__hat = 4 ; v__tilde = 5'm/s
```

Accents combine with subscripts (`F__dot_max`) and Greek letters (`ω__dot`). A single underscore is always a subscript.

---

## 12. Worksheets (several sheets in one file)

A file can contain several sheets, like the tabs of a spreadsheet. Sheets share nothing automatically, but they can read values from each other.

### 12.1 Working with tabs

Below the editor you find the tabs of the sheets.

- **New sheet:** the *New sheet* button. New sheets are called `S1`, `S2`, … .
- **Switch:** click a tab. The editor shows only that sheet.
- **Rename:** double-click a tab. Names consist of letters, digits and underscore and must start with a letter or underscore (`Loads`, `Beam_1`).
- **Delete:** click the × on the selected tab (you are asked to confirm).

The output on the right always shows the sheet that is selected. Switching a tab, adding, renaming or deleting a sheet updates the output immediately.

### 12.2 Using values of another sheet

Write `SheetName::variable`:

```text
##Loads
F = 12'kN
L = 3'm

##Beam
M = Loads::F * Loads::L / 4
```

- The value is the **final value** of the variable in the other sheet.
- References are **read-only**: `Loads::F = 5` is an error.
- Circular references (A uses B, B uses A) are detected and reported.
- Sheets are evaluated only when needed, in the order that follows from the references.
- Variables and **user-defined functions** are not shared; only variable values via `::`.

In the output, the sheet name is shown small in front of the variable (default 50 % of the size, adjustable with *Sheet prefix size*).

### 12.3 The file format

A workbook is saved as one plain text file. Each sheet starts with a marker line `##Name`:

```text
##Loads
F = 12'kN
L = 3'm
##Beam
M = Loads::F * Loads::L / 4
```

A file is treated as a workbook only if its **first non-blank line** is a marker. Any other text is a single sheet, exactly as in earlier versions. Such old files open without change.

You can edit this text file in any editor; `Save` stores all sheets, `Open` restores them. If the file contains pictures, they follow after the last sheet as `##!image` blocks (see [Pictures](#9-pictures)); they are not part of any sheet.

---

## 13. Calculating: button, Ctrl+Enter, live update

- **Calculate button:** evaluates the whole input.
- **Ctrl+Enter** (Strg+Enter): same as the button, from the keyboard.
- **Live update:** if enabled in *Settings* ("Live update (on Enter)"), the output updates in the background every time you press Enter in the editor. This feels fluid on a local installation. On a shared server the default is off to save computation time; you can switch it on yourself.

Which setting is the default is configurable by the administrator (environment variable `ENGIPAD_LIVE_DEFAULT=1` or `0`). In the local version, live update is on by default.

Your input (including pictures, as far as the browser storage allows) is also kept in the browser (autosave): if the page is reloaded accidentally, the editor content is restored.

---

## 14. Settings

Open the Settings menu (gear icon). The values are applied when the output is updated.

| Setting | Meaning | Default |
|---|---|---|
| Rounding precision (%) | Results are rounded so that the relative error stays below this value. `0.01` means 0.01 %. Smaller value = more digits. | 0.01 |
| Font size (px) | Font size of editor and output | 14 |
| Unit font size (%) | Size of units relative to numbers (also in print and LaTeX export) | 80 |
| Sheet prefix size (%) | Size of the sheet name in `Sheet::var` | 50 |
| Print/export all sheets | Off: print and export only the shown sheet. On: all sheets, each on a new page | off |
| Timeout (s) | Maximum computation time per update, 1–30 s | 10 |
| Image limit (MB) | Total size of all pictures in the file: 1, 2 or 3 MB. One picture may use a fifth of it. Applies when you add pictures. | 1 |
| Live update (on Enter) | See [Section 13](#13-calculating-button-ctrlenter-live-update) | depends |

Invalid values fall back to the default (the field keeps your input). Values are limited to a sensible range.

---

## 15. Saving, loading, printing, exporting

- **Save / Open:** a plain text file with your input. With several sheets it contains the `##Name` markers. Pictures are stored in the same file (see [Pictures](#9-pictures)).
- **Print:** prints only the formatted output (the editor is hidden). To get a PDF, choose "Save as PDF" in the print dialog. With more than one sheet, every page carries a **header with the sheet name**. With *Print/export all sheets* enabled, all sheets are printed, each starting on a new page.
- **LaTeX export:** downloads a standalone `.tex` file that compiles with `pdflatex`. If the sheet contains plots or pictures, you get a `.zip` with the `.tex` and the image files (`plot_1.png`, `img_beam.png`, …); unpack it before running `pdflatex`. With several sheets the sheet name is in the page header; with *Print/export all sheets* every sheet starts on a new page.
- Unit and sheet-prefix sizes from Settings are used in the export, too.

---

## 16. Errors and limits

- An error is shown in the output at the line where it occurred, e.g. *Error in line 2: Cannot add/subtract incompatible units*. The other lines are still evaluated.
- Each calculation has a time limit (*Timeout*, max. 30 s). Very expensive symbolic operations (large integrals, solving big systems) can hit it; simplify the problem or raise the timeout.
- Picture errors are shown in the line of the `picture(...)`: a wrong option (*width must be a whole number of pixels (1-1200)*), an invalid name, or a picture that is not loaded (*Image 'x' is not loaded*).
- For safety, only a fixed set of functions is available. An unknown function name is not executed; it stays a symbolic placeholder.
- Known cosmetic limits: some unusual compound units without a table entry are displayed in SymPy style; `deg/s` is displayed as Hz.

---

## 17. Quick reference

| I want to … | Input |
|---|---|
| Assign with unit | `F = 10'kN` |
| Convert the result | `E = 5'kW*3'h \| kWh` |
| Show a formula without evaluating | `f := x^2 + 1` |
| Derivative / integral | `diff(f, x)`, `integrate(f, (x, 0, 1))` |
| Solve and pick a solution | `s = solve(Eq(x^2-4, 0), x)` then `s[0]` |
| Sum | `sum(i, (i, 1, n))` |
| Vector / matrix | `vec(1,2,3)`, `mat([[1,2],[3,4]])` |
| Plot | `plot(sin(x), x, 0, 2*pi)` |
| Show a picture | button *Insert image*, then `picture("beam", width=300, align=left)` |
| Comment (not shown) | `# text` |
| Bold / plain text line | `"text"` / `'text'` |
| Greek / accent | `α`, `Q__dot` |
| Value from another sheet | `Sheet::var` |
| New sheet in a text file | `##Name` |
| Calculate from keyboard | Ctrl+Enter |
