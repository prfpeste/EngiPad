// node tools/pyodide_build_helper.mjs <config.json>
//
// Steps of tools/build_web.py that need the exact Python of the web version
// (Pyodide's Python 3.14: its bytecode and its memory layout), run with
// Pyodide under Node:
//
//  1. pylib.zip: the pure-Python packages + the EngiPad engine + web/ +
//     the page templates, as precompiled bytecode only (no .py sources;
//     "unchecked hash" .pyc next to where the .py was). Python imports
//     directly from the ZIP (zipimport), nothing is unpacked in the browser.
//     Test folders of sympy and mpmath and .so files are left out.
//  2. The wheels loaded in the background (NumPy, matplotlib, CoolProp,
//     ...): bytecode only as well, and without the folders listed in
//     "drop" (files a web page never uses, e.g. matplotlib's sample data).
//  3. snapshot.bin.gz: Pyodide memory snapshot after importing SymPy, Jinja2,
//     the engine and web/engipad_web.py and after warm-up calculations.
//     Only pure Python can be in it: loaded WebAssembly libraries (NumPy,
//     CoolProp, matplotlib) do not survive the restore, so this stops if
//     one was loaded. Restoring needs pylib.zip at the same path again.
//  4. index.html: the first page (web/engipad_web.render_page()), rendered
//     in exactly this Python.
//
// config: {"pyodide": dir of the npm package, "pure_wheels": [...],
//          "engine_zip": path, "so_wheels": [...], "out": dir,
//          "pylib_path": path of pylib.zip inside Pyodide,
//          "warmup": [code, ...], "drop": [folder inside a wheel, ...]}
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { pathToFileURL } from "node:url";

// Errors from Python (PythonError) carry the Python traceback in their
// message; print only that (node would otherwise show the huge minified
// line of pyodide.asm.js where the error passed through).
function fail(e) {
  const text = String(e && e.message ? e.message : e);
  console.error(text.length > 4000 ? "..." + text.slice(-4000) : text);
  process.exit(1);
}
process.on("uncaughtException", fail);
process.on("unhandledRejection", fail);

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const { loadPyodide } = await import(pathToFileURL(path.join(cfg.pyodide, "pyodide.mjs")).href);
const T = () => performance.now() / 1000;
const report = {};
fs.mkdirSync(path.join(cfg.out, "wheels"), { recursive: true });
const quiet = { stdout: () => {}, stderr: () => {} };

const BUILD = `
import os, py_compile, zipfile, shutil, warnings
warnings.filterwarnings("ignore")
UNCHECKED = py_compile.PycInvalidationMode.UNCHECKED_HASH

def compile_tree(root, sourceless):
    ok = 0
    for d, dirs, files in os.walk(root):
        for f in files:
            if not f.endswith(".py"):
                continue
            src = os.path.join(d, f)
            rel = os.path.relpath(src, root)
            cfile = src + "c" if sourceless else None
            py_compile.compile(src, cfile=cfile, dfile=rel, doraise=True, invalidation_mode=UNCHECKED)
            if sourceless:
                os.remove(src)
            ok += 1
    return ok

def zip_tree(root, out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d, dirs, files in os.walk(root):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, root))

def build_pylib(inputs, out_zip, drop_tests=("sympy", "mpmath")):
    root = "/build/pylib"
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    for p in inputs:
        zipfile.ZipFile(p).extractall(root)
    for top in drop_tests:
        for d, dirs, files in os.walk(os.path.join(root, top)):
            for x in [x for x in dirs if x in ("tests", "benchmarks")]:
                shutil.rmtree(os.path.join(d, x))
                dirs.remove(x)
    for d, dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".so"):
                os.remove(os.path.join(d, f))
    n = compile_tree(root, sourceless=True)
    zip_tree(root, out_zip)
    return {"compiled": n}

def build_so_wheel(src, out, drop):
    root = "/build/wheel"
    shutil.rmtree(root, ignore_errors=True)
    zipfile.ZipFile(src).extractall(root)
    for rel in drop:
        target = os.path.join(root, rel)
        if os.path.isdir(target):
            shutil.rmtree(target)
    n = compile_tree(root, sourceless=True)
    zip_tree(root, out)
    return {"compiled": n}
`;

// ---- 1 + 2: bytecode ------------------------------------------------------
let t = T();
const py = await loadPyodide(quiet);
py.FS.mkdirTree("/in");
py.runPython(BUILD);
const inputs = [];
for (const p of cfg.pure_wheels.concat([cfg.engine_zip])) {
  const dest = "/in/" + path.basename(p);
  py.FS.writeFile(dest, fs.readFileSync(p));
  inputs.push(dest);
}
py.globals.set("inputs", py.toPy(inputs));
report.pylib = py.runPython("build_pylib(inputs, '/build/pylib.zip')").toJs({ dict_converter: Object.fromEntries });
fs.writeFileSync(path.join(cfg.out, "pylib.zip"), py.FS.readFile("/build/pylib.zip"));
report.so_wheels = {};
py.globals.set("drop", py.toPy(cfg.drop));
for (const p of cfg.so_wheels) {
  py.FS.writeFile("/in/w.whl", fs.readFileSync(p));
  const r = py.runPython("build_so_wheel('/in/w.whl', '/build/out.whl', drop)").toJs({ dict_converter: Object.fromEntries });
  fs.writeFileSync(path.join(cfg.out, "wheels", path.basename(p)), py.FS.readFile("/build/out.whl"));
  report.so_wheels[path.basename(p)] = r.compiled;
}
report.bytecode_seconds = T() - t;

// ---- 3 + 4: snapshot and first page ----------------------------------------
t = T();
const sp = await loadPyodide(Object.assign({ _makeSnapshot: true }, quiet));
sp.FS.mkdirTree(path.dirname(cfg.pylib_path));
sp.FS.writeFile(cfg.pylib_path, fs.readFileSync(path.join(cfg.out, "pylib.zip")));
sp.globals.set("warmup_codes", sp.toPy(cfg.warmup));
const info = JSON.parse(sp.runPython(`
import sys, time, gc, json
sys.path.insert(0, ${JSON.stringify(cfg.pylib_path)})
from web import package_guard
package_guard.install()
t0 = time.perf_counter()
import sympy, jinja2, core.workbook, core.engine
from web import engipad_web
imports = time.perf_counter() - t0
first_page = engipad_web.render_page(None)
# Warm-up: typical calculations that need neither NumPy nor CoolProp, so
# that the first real calculation finds SymPy's caches and the lazily
# imported modules ready. The guard must not have been asked for anything.
warm = []
for code in warmup_codes:
    t1 = time.perf_counter()
    r = engipad_web.calculate({"code": code})
    if "html" not in r:
        raise SystemExit("warm-up needs a package that cannot be in the snapshot: " + str(r) + "\\n" + code)
    warm.append(round(time.perf_counter() - t1, 2))
engipad_web.timeout_page({"code": "a = 1"})
so = sorted(n for n, m in list(sys.modules.items())
            if str(getattr(m, "__file__", "") or "").endswith(".so"))
gc.collect()
json.dumps({"imports": imports, "warmup_seconds": warm, "extension_modules": so,
            "first_page": first_page})
`));
if (info.extension_modules.length) {
  console.error("WebAssembly modules loaded before the snapshot: " + info.extension_modules);
  process.exit(1);
}
fs.writeFileSync(path.join(cfg.out, "index.html"), info.first_page);
delete info.first_page;
report.snapshot_python = info;
const snap = sp.makeMemorySnapshot();
const gz = zlib.gzipSync(snap, { level: 9 });
fs.writeFileSync(path.join(cfg.out, "snapshot.bin.gz"), gz);
report.snapshot = { raw: snap.length, gzip: gz.length, seconds: T() - t };
console.log(JSON.stringify(report));
