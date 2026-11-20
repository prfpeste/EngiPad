"""Builds the web version of EngiPad: a static folder that runs completely
in the browser (Python as WebAssembly, Pyodide) -- for a Moodle course
(upload the ZIP as a file resource and unpack it), GitHub Pages or any
web space. No server computes anything.

    python tools/build_web.py [--cache DIR] [--out DIR]

--cache  folder with Pyodide, the wheels and binaryen (default: the
         environment variable ENGIPAD_WEB_CACHE). They are not in the
         repository; tools/web_packages.json lists every file with its
         SHA-256, and the build says exactly what is missing.
--out    output folder (default: dist_web next to app.py); the ZIP for
         Moodle is written next to it (dist_web.zip).
Needs node (Pyodide is run under node for the bytecode and the memory
snapshot) and Jinja2 in this Python (to compare the first page).

What is built (see WEB_VERSION.md):
  index.html         first page (templates/web.html), rendered in Pyodide
  static/            editor, styles, icons, MathJax, fonts, static/web/*.js
  pyodide/           Pyodide with the two adjustments for Moodle
  pylib.zip          SymPy, mpmath, Jinja2, ... + the EngiPad engine,
                     precompiled bytecode only
  snapshot.bin.gz    memory snapshot: Python with all of that imported
  wheels/            NumPy, matplotlib (+ compiled parts), CoolProp (fixed),
                     loaded in the background after the start
  web_manifest.json  versions, file names, package groups, sizes
  licenses/          licences of all included packages
"""
import argparse
import difflib
import json
import os
import re
import shutil
import sys
import sysconfig
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import web_build_lib as lib  # noqa: E402
from web_build_lib import BuildError  # noqa: E402

PYLIB_PATH = "/lib/engipad/pylib.zip"   # inside Pyodide (written there by worker.js)

ENGINE_DIRS = ["parsing", "mathlib", "core", "rendering", "web"]
ENGINE_EXCLUDE = {os.path.join("core", "safe_runner.py")}     # server only
TEMPLATES = ["base.html", "web.html"]
STATIC_EXCLUDE = {"server_shell.js"}                          # server only

# Folders inside the background wheels that a web page never uses (less
# to download): matplotlib's sample data and the fonts of its PDF and
# PostScript output (plots are drawn with Agg), CoolProp's C++ headers.
WHEEL_DROP = [
    "matplotlib/mpl-data/sample_data",
    "matplotlib/mpl-data/fonts/afm",
    "matplotlib/mpl-data/fonts/pdfcorefonts",
    "CoolProp/include",
]

# Calculations run once before the memory snapshot is taken, so that the
# first real calculation finds SymPy's caches and lazily imported modules
# ready. Only things that need neither NumPy nor CoolProp (no plot, prop,
# nsolve, nintegrate), otherwise the build stops.
WARMUP = [
    "\"Example:\"\na = 5'm/s^2 ; m = 10'kg\nF = m * a\n",
    "u(r) = u_max*(1 - r^2/R^2) ; R = 10'mm ; u_max = 0.2'm/s\n"
    "V__dot = integrate(2*π*r*u(r), (r, 0'm, R)) | L/min\n"
    "Re = 998'kg/m^3*0.1'm/s*0.02'm/1e-3'Pa*s ; Re <= 2300\n",
    "\"Heading\"\n'text' ; a = 5'm/s^2 ; m = 10'kg ; F = m * a | kN\n"
    "sol = solve(Eq(x^2 - 5, 0), x) ; x_1 = sol[1]\n"
    "A =: mat([[1, 2], [3, 4]]) ; det(A) ; v = vec(1, 2, 3)'m ; norm(v)\n"
    "f(x) = x^2 + 1 ; diff(f(x), x) ; f(2)\n"
    "σ = 180'MPa ; σ_zul = 235'MPa ; η = σ/σ_zul | % ; σ <= σ_zul | MPa\n"
    "t = if(σ <= σ_zul, 10'mm, 12'mm) | mm ; round(2.5) ; sum(1/i^2, (i, 1, 10))\n",
    "##Loads\nF = 12'kN\nL = 3'm\n##Beam\nM = Loads::F * Loads::L / 4 | kNm\n",
]


def engipad_version():
    with open(os.path.join(PROJECT, "templates", "base.html"), encoding="utf-8") as f:
        m = re.search(r"<dt>Version</dt><dd>([^<]+)</dd>", f.read())
    return m.group(1) if m else "?"


def build_engine_zip(out_path):
    """Engine + web/ + the two page templates (the server parts left out)."""
    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for d in ENGINE_DIRS:
            for root, dirs, files in os.walk(os.path.join(PROJECT, d)):
                dirs[:] = sorted(x for x in dirs if x != "__pycache__")
                for f in sorted(files):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, PROJECT)
                    if f.endswith(".py") and rel not in ENGINE_EXCLUDE:
                        z.write(full, rel.replace(os.sep, "/"))
                        count += 1
        for name in TEMPLATES:
            z.write(os.path.join(PROJECT, "templates", name), "templates/" + name)
    return count




def check_first_page(html):
    """The page rendered in Pyodide must equal the one of this Python
    (same templates, same Jinja2 version): otherwise the first page and
    the pages computed later would not fit together."""
    sys.path.insert(0, PROJECT)
    from web import engipad_web
    here = engipad_web.render_page(None)
    if here != html:
        diff = "\n".join(list(difflib.unified_diff(here.splitlines(), html.splitlines(),
                                                   "CPython", "Pyodide", lineterm=""))[:30])
        raise BuildError("first page differs between CPython and Pyodide "
                         "(different Jinja2 version?):\n" + diff)


def copy_static(out_dir):
    def ignore(folder, names):
        return [n for n in names if n == "__pycache__" or n in STATIC_EXCLUDE]
    shutil.copytree(os.path.join(PROJECT, "static"), out_dir, ignore=ignore)


def _metadata_licence(wheel):
    with zipfile.ZipFile(wheel) as z:
        name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        meta = z.read(name).decode("utf-8", "replace").split("\n\n")[0]
    for key in ("License-Expression: ", "License: "):
        for line in meta.splitlines():
            if line.startswith(key) and len(line) < len(key) + 60:
                return line[len(key):].strip()
    found = [line.split("::")[-1].strip() for line in meta.splitlines()
             if line.startswith("Classifier: License ::")]
    return ", ".join(found) or "see the licence files"


PYODIDE_NOTICE = """Pyodide {version}
https://pyodide.org -- source code: https://github.com/pyodide/pyodide

Pyodide is distributed under the Mozilla Public License, version 2.0
(MPL-2.0), full text: https://www.mozilla.org/en-US/MPL/2.0/

Included unchanged except for two adjustments for Moodle, made by
tools/build_web.py of EngiPad: pyodide.mjs and pyodide.asm.mjs are
renamed to pyodide_loader.js and pyodide.asm.js, and the loader reads
pyodide.asm.wasm as bytes instead of with WebAssembly.instantiateStreaming.

Pyodide contains CPython {python} (Python Software Foundation License,
see Python-LICENSE.txt) and other libraries compiled to WebAssembly; see
the Pyodide repository for their licences.
"""

COOLPROP_NOTE = """
Note: the file CoolProp/CoolProp.so of this package was changed by
tools/wasm_bsymbolic.mjs of EngiPad (calls of the library to its own
functions are linked directly, as with the linker option -Bsymbolic;
nothing else is changed). Without it, loading the fluid library takes
about 70 s in a browser instead of a few seconds.
"""


def write_licenses(out_dir, wheels, packages, pyodide_version, python_version):
    os.makedirs(out_dir)
    lines = ["Third-party software included in the EngiPad web version", "",
             f"{'Package':<20} {'Version':<14} Licence", f"{'-' * 20} {'-' * 14} {'-' * 30}"]
    lines.append(f"{'Pyodide':<20} {pyodide_version:<14} MPL-2.0 (see Pyodide/NOTICE.txt)")
    lines.append(f"{'CPython':<20} {python_version:<14} PSF-2.0 (part of Pyodide)")
    os.makedirs(os.path.join(out_dir, "Pyodide"))
    with open(os.path.join(out_dir, "Pyodide", "NOTICE.txt"), "w", encoding="utf-8") as f:
        f.write(PYODIDE_NOTICE.format(version=pyodide_version, python=python_version))
    psf = os.path.join(sysconfig.get_paths()["stdlib"], "LICENSE.txt")
    if os.path.isfile(psf):
        shutil.copy2(psf, os.path.join(out_dir, "Pyodide", "Python-LICENSE.txt"))
    for w in packages["wheels"]:
        path = wheels[w["name"]]
        lic = _metadata_licence(path)
        lines.append(f"{w['name']:<20} {w['version']:<14} {lic}")
        target = os.path.join(out_dir, w["name"])
        os.makedirs(target)
        files = lib.wheel_license_files(path)
        for name, data in files:
            rel = name.split("/", 1)[1]
            dest = os.path.join(target, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
        if not files:
            with open(os.path.join(target, "LICENSE-NOTE.txt"), "w", encoding="utf-8") as f:
                f.write(f"{w['name']} {w['version']}: {lic}\n")
        if w["name"] == "coolprop":
            with open(os.path.join(target, "CHANGED.txt"), "w", encoding="utf-8") as f:
                f.write(COOLPROP_NOTE)
    lines += ["", "MathJax and the fonts Inter and JetBrains Mono: see static/vendor/ "
                  "(each with its licence file).",
              "EngiPad itself: GNU General Public License v3.0 (GPL-3.0), "
              "https://github.com/prfpeste/EngiPad"]
    with open(os.path.join(out_dir, "THIRD_PARTY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def build(cache, out):
    t_start = time.time()
    report = []
    packages = lib.load_packages()
    found = lib.check_cache(cache, packages)
    by_name = {w["name"]: w for w in packages["wheels"]}

    with tempfile.TemporaryDirectory() as tmp:
        npm = lib.unpack_pyodide(found["pyodide"], os.path.join(tmp, "npm"))
        with open(os.path.join(npm, "package.json"), encoding="utf-8") as f:
            version = json.load(f)["version"]
        if version != packages["pyodide"]["version"]:
            raise BuildError(f"Pyodide {version} in the tarball, "
                             f"{packages['pyodide']['version']} expected")
        with open(os.path.join(npm, "pyodide-lock.json"), encoding="utf-8") as f:
            python_version = json.load(f)["info"]["python"]

        if os.path.exists(out):
            shutil.rmtree(out)
        os.makedirs(out)
        lib.copy_pyodide(npm, os.path.join(out, "pyodide"))

        engine_zip = os.path.join(tmp, "engine.zip")
        n = build_engine_zip(engine_zip)
        report.append(f"engine: {n} Python files + {len(TEMPLATES)} templates")

        coolprop = os.path.join(tmp, by_name["coolprop"]["file"])
        stats = lib.patch_coolprop_wheel(found["wheels"]["coolprop"], coolprop, found["binaryen"])
        report.append(f"CoolProp.so fixed: {stats['self_imports_redirected']} of "
                      f"{stats['imported_functions']} imported functions were its own")

        background = [name for g in packages["groups"].values() for name in g["wheels"]]
        cfg = {
            "pyodide": npm,
            "pure_wheels": [found["wheels"][w["name"]] for w in packages["wheels"] if w["kind"] == "pure"],
            "engine_zip": engine_zip,
            "so_wheels": [coolprop if name == "coolprop" else found["wheels"][name] for name in background],
            "out": os.path.abspath(out),
            "pylib_path": PYLIB_PATH,
            "warmup": WARMUP,
            "drop": WHEEL_DROP,
        }
        cfg_path = os.path.join(tmp, "cfg.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        helper = lib.run_node([os.path.join(HERE, "pyodide_build_helper.mjs"), cfg_path],
                              "pyodide_build_helper.mjs")
        report.append(f"pylib.zip: {helper['pylib']['compiled']} modules as bytecode; "
                      f"snapshot: imports {helper['snapshot_python']['imports']:.1f} s, "
                      f"warm-up {helper['snapshot_python']['warmup_seconds']} s (in node)")

    with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
        check_first_page(f.read())
    copy_static(os.path.join(out, "static"))
    write_licenses(os.path.join(out, "licenses"), found["wheels"], packages, version, python_version)

    wheels_dir = os.path.join(out, "wheels")
    sizes = {
        "pyodide": lib.folder_size(os.path.join(out, "pyodide")),
        "pylib": os.path.getsize(os.path.join(out, "pylib.zip")),
        "snapshot": os.path.getsize(os.path.join(out, "snapshot.bin.gz")),
        "snapshot_in_memory": helper["snapshot"]["raw"],
        "wheels": lib.folder_size(wheels_dir),
        "static": lib.folder_size(os.path.join(out, "static")),
    }
    manifest = {
        "engipad": engipad_version(),
        "built": time.strftime("%Y-%m-%d %H:%M"),
        "pyodide": version,
        "python": python_version,
        "pylib_path": PYLIB_PATH,
        "wheels": {name: by_name[name]["file"] for name in background},
        "groups": packages["groups"],
        "versions": {w["name"]: w["version"] for w in packages["wheels"]},
        "coolprop_fix": stats,
        "sizes": sizes,
    }
    with open(os.path.join(out, "web_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    total = lib.folder_size(out)

    zip_path = out.rstrip("/\\") + ".zip"
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(out):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, out))
    report.append(
        f"unpacked {total / 1e6:.1f} MB (Pyodide {sizes['pyodide'] / 1e6:.1f}, "
        f"pylib {sizes['pylib'] / 1e6:.1f}, snapshot {sizes['snapshot'] / 1e6:.1f} "
        f"[{sizes['snapshot_in_memory'] / 1e6:.0f} MB in memory], wheels {sizes['wheels'] / 1e6:.1f}, "
        f"static {sizes['static'] / 1e6:.1f}), ZIP {os.path.getsize(zip_path) / 1e6:.1f} MB")
    report.append(f"EngiPad {manifest['engipad']}, built in {time.time() - t_start:.0f} s")
    return report, zip_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", default=os.environ.get("ENGIPAD_WEB_CACHE"))
    ap.add_argument("--out", default=os.path.join(PROJECT, "dist_web"))
    args = ap.parse_args(argv)
    if not args.cache:
        print("BUILD FAILED: no package cache (--cache DIR or ENGIPAD_WEB_CACHE); "
              "see tools/web_packages.json", file=sys.stderr)
        return 1
    try:
        report, zip_path = build(os.path.abspath(args.cache), os.path.abspath(args.out))
    except BuildError as exc:
        print("BUILD FAILED:", exc, file=sys.stderr)
        return 1
    for line in report:
        print(line)
    print("->", zip_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
