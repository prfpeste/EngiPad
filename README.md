# EngiPad

EngiPad is a browser-based calculation notebook for engineers using Python, SymPy, and Matplotlib.

It lets you:

- Perform calculations with physical units (`m`, `s`, `kg`, `N`, `J`, `W`, `Pa`, `bar`, `atm`, `V`, `A`, `Ohm`, `K`, `degC`, `Hz`, `rpm`, `L`, …)
- Convert results explicitly to target units using `|` syntax
- Do symbolic math (derivatives, integrals, equations, sums, products, limits)
- Work with vectors and matrices, and pick individual values out of a result (e.g. `solve(...)[0]`)
- Generate plots directly in the browser
- View results as nicely formatted LaTeX via MathJax
- Insert Greek symbols and common function snippets from the UI
- Load and save calculation files via the web UI
- Print the output in a print-optimized layout
- Export the current sheet as a standalone, `pdflatex`-compilable `.tex` document (or a `.zip` if it contains plots)
- Adjust rounding precision and font size from a settings panel

The interface consists of a text editor on the left and a LaTeX/plot output area on the right.

# Download
[Download for Windows (.exe)](https://github.com/prfpeste/EngiPad/releases/download/v1.1.2/EngiPad-1.1.2-windows-x86_64.exe)

[Download for Linux (.bin)](https://github.com/prfpeste/EngiPad/releases/download/v1.1.2/EngiPad-1.1.2-linux-x86_64.bin)

## Screenshot

![EngiPad screenshot](images/SC1.png)
![EngiPad screenshot](images/SC2.png)
![EngiPad screenshot](images/SC3.png)
![EngiPad screenshot](images/SC4.png)

## Features

- Web UI using Flask, HTML/CSS/JavaScript and MathJax
- Expressions are parsed by a small, purpose-built parser and evaluated through a fixed function whitelist — no `eval()`/`sympify()` is ever run on user input (see [Security](#security) below)
- SymPy integration for
  - symbolic expressions (`diff`, `integrate`, `Eq`, `solve`, `csolve`, `nsolve`, `sum`, `prod`, `lim`)
  - numeric evaluation
  - symbolic display mode via `:=`
- Unit system with convenient input:
  - apostrophe syntax for units: `10'J`, `3's`, `5'm`, `10'kg`, `5'W/(m*K)`, …
  - automatic unit simplification for output
  - explicit target units using the `|` syntax (for example `| kWh`, `| bar`, `| Pa`, `| m`)
  - a bare numeric literal (e.g. `110'degC`) keeps the entered unit instead of converting to base SI
- Plotting function: `plot(f, x, xmin, xmax)` generates a PNG plot with Matplotlib and shows it in the browser
- Matrix and vector support:
  - `vec`, `mat`, `Matrix`
  - `det`, `inv`, `T`, `trace`, `rank`
  - `dot`, `cross`, `norm`
  - `eye`, `zeros`, `ones`
  - `solve_linear`, `eigenvals`, `eigenvects`
  - indexing into a result with `[...]`, e.g. `sol[0]`, `M[1][2]`
- Text lines in double quotes (`"..."`) are rendered as bold LaTeX text; single quotes (`'...'`) render as plain (non-bold) text
- Lines starting with `#` are comments and are ignored
- Greek variables are automatically rendered as LaTeX symbols
- Accent notation (`Q__dot`, `Q__bar`, `Q__hat`, `Q__tilde`) puts a dot, bar, hat, or tilde over a variable's base symbol (e.g. for a time derivative `Q̇`), and works as a normal, reusable variable
- Toolbar buttons: open file, save file, insert Greek letters/symbols, insert function snippets, insert equation/calculus snippets, insert matrix/vector snippets, insert a plot snippet, settings (rounding precision, font size), calculate, print, LaTeX export, info
- Print stylesheet that hides the editor and prints only the formatted output
- A configurable computation timeout (10s by default) protects the server against accidentally or intentionally very expensive input

## Security

Expressions are evaluated through a custom recursive-descent parser (`parsing/`) and a small AST-to-SymPy bridge (`mathlib/sympy_bridge.py`) that only ever calls a fixed whitelist of functions (`mathlib/functions.py`). Unknown function names become inert symbolic placeholders rather than being executed. No user input is ever passed to Python's `eval()` or SymPy's `sympify()`/`parse_expr()`.

Each computation additionally runs in a separate process with a hard timeout (`core/safe_runner.py`, default 10s) so a single expensive or hanging input can't tie up the server.

The bundled `run.py` binds to `127.0.0.1` by default — if you expose this on a network beyond localhost, review `app.py`/`run.py` first.

## Requirements

- Python 3
- Python packages: see `requirements.txt` (`Flask`, `Jinja2`, `sympy`, `matplotlib`, `numpy`)
- For running the test suite: see `requirements-dev.txt` (`pytest`)

Install the runtime dependencies:

```bash
pip install -r requirements.txt
```

## Running from source

The main script is `run.py`.

Start the application with:

```bash
python run.py
```

By default, the Flask application runs on:

```text
http://127.0.0.1:5000
```

When running as a normal Python script, it starts the Flask development server. When running as a packaged/frozen executable, it starts the server and opens the browser automatically.

## Running the tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

## Building a standalone binary (PyInstaller)

You can build a single executable using PyInstaller. A ready-made build script is provided as `build.sh` (Linux/macOS):

```bash
./build.sh
```

It creates a virtual environment, installs `pyinstaller` and the runtime requirements, runs PyInstaller, and leaves a single `EngiPad` executable in the project root.

### Manual build

#### 1. Install PyInstaller

```bash
pip install pyinstaller
```

#### 2. Project structure

The build bundles both `templates/` and `static/` (CSS, JavaScript, icons) — both are required for the UI to render correctly:

```text
run.py
app.py
core/
mathlib/
parsing/
rendering/
templates/
  index.html
static/
  main.css
  editor.js
  icons/
```

#### 3. Build the executable on Linux / macOS

```bash
pyinstaller \
  --name EngiPad \
  --onefile \
  --add-data "templates:templates" \
  --add-data "static:static" \
  run.py
```

After a successful build, the executable will be in the `dist/` folder.

Run it with:

```bash
./dist/EngiPad
```

#### 4. Build the executable on Windows

On Windows, the separator in `--add-data` is `;` instead of `:`:

```bash
pyinstaller ^
  --name EngiPad ^
  --onefile ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  run.py
```

The executable will be created in:

```text
dist\EngiPad.exe
```

## Usage

### User interface

- Left side: textarea for the calculation code
- Right side: output area for LaTeX formulas and plots

- Top toolbar (input side):
  - Open file: load a local text file into the editor
  - Save file: download the current editor content as a text file
  - Greek symbols: insert Greek letters and symbols into the editor
  - Functions: insert common math function snippets
  - Equations & calculus: insert `diff`, `integrate`, `Eq`, `solve`, `sum`, `prod`, `lim`, … snippets
  - Matrices / vectors: insert matrix and vector snippets
  - Plot: insert a `plot(...)` snippet
  - Settings: rounding precision (%) and font size (px), applied to both panels
  - Calculate: submit the current code to the server and update the output

- Top toolbar (result side):
  - Print: open the browser's print dialog with a print-optimized layout
  - LaTeX export: download the sheet as a `.tex` file, or a `.zip` (containing the `.tex` and any plot images) if it contains at least one plot
  - Info: version, author, and technology info

### Input syntax

- Basic assignments and calculations

```text
v = 10'm/s
t = 3's
m_a = 10'kg
F = m_a * v / t
```

- Multiple commands on one line

```text
a = 5'm/s^2 ; m = 10'kg ; F = m * a
```

- Units and target units

```text
E = 5'kW * 3'h | kWh
p = 2'bar | Pa
l = 2500'mm | m
```

- Symbolic math

```text
"Symbolic"
f = x^2
diff(f, x)
integrate(f, (x, 0, 1))
solve(Eq(x^2 - 4, 0), x)
```

- Symbolic display mode

```text
f := x^2 + 2*x + 1
df := diff(f, x)
```

- Equation solver, and picking one solution out of several

```text
sol = solve(Eq(x^2 - 5, 0), x)
x_1 = sol[0] ; x_2 = sol[1]
nsolve((x^2 - 2), (x), (1))
```

- Sums, products and limits

```text
sum(i, (i, 0, n))
prod(i, (i, 1, n))
lim(sin(x)/x, x, 0)
```

- Matrix and vector operations

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
```

- Plots

```text
f = sin(x)
plot(f, x, 0, 2*pi)
```

- Comments / text

```text
# this line is ignored
"Bold heading or comment"
'Plain (non-bold) comment'
```

### Greek variables

Greek letters can be used directly in variable names, for example:

```text
α = 30'deg
β = 45'deg
ΔT = 20'K
μ = 0.12
```

### Accent notation

Append `__dot`, `__bar`, `__hat`, or `__tilde` (double underscore) directly
after a variable's base symbol to put an accent over it — useful for
things like a time derivative (`Q̇`), an average value (`Q̄`), an
estimated value (`Q̂`), or an approximate value (`Q̃`):

```text
Q__dot = 5'W        ; renders as Q̇ = 5 W, and works as a normal variable
P = Q__dot * 2       ; Q̇ can be reused in later formulas, e.g. P = Q̇ · 2 = 10 W

Q__bar = 3           ; Q̄
a__hat = 4           ; â
v__tilde = 5'm/s     ; ṽ
```

A double underscore is used specifically because a *single* underscore
already means a subscript (`Q_dot` renders as `Q` with the literal text
subscript "dot" — unrelated, and unaffected by this feature). The accent
combines with everything a plain variable already supports: a subscript
after the accent (`F__dot_max` → Ḟ with subscript "max"), the `name_{a,b}`
comma-subscript syntax (`Q__dot_{1,x}` → Q̇ with subscript "1,x"), and
Greek base symbols (`ω__dot` → ω̇).

## Project structure

```text
run.py                 # entry point: starts the Flask dev server, opens the browser when frozen
app.py                 # Flask app/routes, settings parsing, LaTeX export route
build.sh                # PyInstaller build script
requirements.txt        # runtime dependencies
requirements-dev.txt     # test-only dependencies (pytest)
pytest.ini
conftest.py

parsing/                # lexer + recursive-descent parser -> AST (no eval())
  lexer.py
  parser.py
  ast_nodes.py

rendering/
  latex_input.py        # AST -> LaTeX renderer for the input/formula side

mathlib/
  sympy_bridge.py        # safe AST -> SymPy translation (whitelist-only function calls)
  units.py               # unit table, unit conversion/formatting, LaTeX text escaping
  functions.py           # whitelisted numeric/display/plot functions
  solving.py              # solve()/csolve() wrappers
  plotting.py             # matplotlib plot generation

core/
  context.py             # EvaluationContext: parses + evaluates a line
  engine.py               # evaluate_code(): splits input into lines/blocks
  formatter.py            # builds the final LaTeX for a computed result
  latex_export.py          # builds a standalone .tex document from results
  safe_runner.py           # process-based timeout wrapper

templates/
  index.html             # HTML template with editor, toolbar, panels and MathJax

static/
  main.css
  editor.js
  icons/

tests/                   # pytest test suite
```

## Credits

- Author: Prof. Dr. Peter Stein
- Coding: Claude Sonnet 5 (Anthropic) — the implementation, architecture, tests, and documentation in this repository were written with Claude Sonnet 5 as an AI coding assistant.

## License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0).
See the `LICENSE` file for details.

