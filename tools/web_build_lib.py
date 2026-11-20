"""Building blocks of tools/build_web.py (the web version of EngiPad).

Kept apart from the build script itself so that each step can be read
(and tested) on its own:
  - the package cache and its check against tools/web_packages.json,
  - the Pyodide core with the two adjustments for Moodle,
  - the fix of CoolProp.so (tools/wasm_bsymbolic.mjs),
  - running node scripts.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGES_FILE = os.path.join(HERE, "web_packages.json")


class BuildError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def folder_size(path):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs)


def load_packages():
    with open(PACKAGES_FILE, encoding="utf-8") as f:
        return json.load(f)


# --- Cache ------------------------------------------------------------------

def _wheel_path(cache, file_name):
    """Also accepts the ending .wasm: browsers name the download from
    jsDelivr that way (content type), the content is the wheel."""
    for candidate in (file_name, file_name[:-4] + ".wasm"):
        p = os.path.join(cache, "wheels", candidate)
        if os.path.isfile(p):
            return p
    return None


def check_cache(cache, packages):
    """Checks the cache folder against web_packages.json. Returns
    {"pyodide": tgz path, "binaryen": dir, "wheels": {name: path}}.
    Raises BuildError that lists exactly what is missing or wrong."""
    problems = []
    found = {"wheels": {}}

    p = packages["pyodide"]
    tgz = os.path.join(cache, p["file"])
    if not os.path.isfile(tgz):
        problems.append(f"missing {p['file']}: {p['get']}")
    elif sha256(tgz) != p["sha256"]:
        problems.append(f"{p['file']}: SHA-256 differs from tools/web_packages.json")
    found["pyodide"] = tgz

    b = packages["binaryen"]
    bdir = os.path.join(cache, b["folder"])
    try:
        with open(os.path.join(bdir, "package.json"), encoding="utf-8") as f:
            version = json.load(f)["version"]
        if version != b["version"]:
            problems.append(f"binaryen {version} found, {b['version']} expected: {b['get']}")
    except OSError:
        problems.append(f"missing binaryen: {b['get']}")
    found["binaryen"] = bdir

    for w in packages["wheels"]:
        path = _wheel_path(cache, w["file"])
        how = packages["wheel_sources"][w["source"]].format(**w)
        if path is None:
            problems.append(f"missing wheels/{w['file']}: {how}")
        elif sha256(path) != w["sha256"]:
            problems.append(f"wheels/{w['file']}: SHA-256 differs from tools/web_packages.json "
                            f"(another version or another Pyodide build?): {how}")
        found["wheels"][w["name"]] = path

    if problems:
        raise BuildError(f"package cache {cache} is incomplete:\n  " + "\n  ".join(problems))
    return found


def unpack_pyodide(tgz, dest):
    """The npm tarball unpacked; returns the folder with pyodide.mjs."""
    with tarfile.open(tgz) as t:
        t.extractall(dest, filter="data")
    folder = os.path.join(dest, "package")
    if not os.path.isfile(os.path.join(folder, "pyodide.mjs")):
        raise BuildError("pyodide.mjs not found in the npm tarball")
    return folder


# --- Pyodide core for Moodle ---------------------------------------------------
# Moodle serves .mjs as text/plain (module scripts then fail) and .wasm as
# application/octet-stream (WebAssembly.instantiateStreaming fails, the
# loader has no fallback). Checked on the HTWG Moodle, 17.11.2026.

PATCH_OLD = ("n?l=await WebAssembly.instantiateStreaming(n,s):"
             "l=await WebAssembly.instantiate(await t,s)")
PATCH_NEW = ("n?l=await WebAssembly.instantiate(await (await n).arrayBuffer(),s):"
             "l=await WebAssembly.instantiate(await t,s)")


def _strip_source_map(text):
    return "\n".join(line for line in text.split("\n")
                     if not line.startswith("//# sourceMappingURL="))


def copy_pyodide(npm_dir, out_dir):
    """Copies the Pyodide core: .mjs renamed to .js, the wasm loaded as
    bytes. The patch site must occur exactly once."""
    os.makedirs(out_dir)
    for name in ("pyodide.asm.wasm", "python_stdlib.zip", "package.json", "pyodide-lock.json"):
        shutil.copy2(os.path.join(npm_dir, name), os.path.join(out_dir, name))

    with open(os.path.join(npm_dir, "pyodide.mjs"), encoding="utf-8") as f:
        loader = f.read()
    count = loader.count(PATCH_OLD)
    if count != 1:
        raise BuildError(f"wasm patch site found {count} times in pyodide.mjs (expected exactly 1)")
    loader = loader.replace(PATCH_OLD, PATCH_NEW)
    if "pyodide.asm.mjs" not in loader:
        raise BuildError("reference to pyodide.asm.mjs not found in pyodide.mjs")
    loader = loader.replace("pyodide.asm.mjs", "pyodide.asm.js")
    with open(os.path.join(out_dir, "pyodide_loader.js"), "w", encoding="utf-8") as f:
        f.write(_strip_source_map(loader))

    with open(os.path.join(npm_dir, "pyodide.asm.mjs"), encoding="utf-8") as f:
        asm = f.read()
    with open(os.path.join(out_dir, "pyodide.asm.js"), "w", encoding="utf-8") as f:
        f.write(_strip_source_map(asm))


# --- node -------------------------------------------------------------------

def run_node(args, what, timeout=1800):
    """Runs a node script; its last output line is JSON (the result)."""
    try:
        proc = subprocess.run(["node", "--max-old-space-size=6000"] + args,
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise BuildError("node not found (needed for the web build)")
    if proc.returncode != 0:
        raise BuildError(f"{what} failed (node {_node_version()}):\n" + _readable(proc.stderr or proc.stdout))
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _node_version():
    try:
        return subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return "?"


def _readable(output, lines=40, width=300):
    """The end of node's output without the huge minified source lines
    node prints next to an error (they hid the actual message)."""
    out = []
    for line in output.splitlines():
        if len(line) > width:
            line = line[:width] + f" ... [{len(line)} characters]"
        out.append(line)
    return "\n".join(out[-lines:])


# --- CoolProp -----------------------------------------------------------------

def _record_hash(data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return "sha256=" + digest


def patch_coolprop_wheel(src, dst, binaryen_dir):
    """Copy of the CoolProp wheel with CoolProp/CoolProp.so fixed by
    tools/wasm_bsymbolic.mjs (RECORD updated). The Pyodide build of
    CoolProp 7.2.0 was linked without -Bsymbolic: every internal call went
    through JavaScript and loading the fluid library took about 70 s in
    Chrome instead of about 6 s. Returns the statistics of the fix."""
    so_name = "CoolProp/CoolProp.so"
    with zipfile.ZipFile(src) as zin, tempfile.TemporaryDirectory() as tmp:
        names = zin.namelist()
        if so_name not in names:
            raise BuildError(f"{os.path.basename(src)}: {so_name} not found")
        orig, fixed = os.path.join(tmp, "in.so"), os.path.join(tmp, "out.so")
        with open(orig, "wb") as f:
            f.write(zin.read(so_name))
        stats = run_node([os.path.join(HERE, "wasm_bsymbolic.mjs"), os.path.abspath(binaryen_dir),
                          orig, fixed], "wasm_bsymbolic.mjs")
        if stats["self_imports_redirected"] < 1000:
            raise BuildError(f"CoolProp.so: only {stats['self_imports_redirected']} self-imports "
                             "found -- another build of CoolProp? Check the load time.")
        with open(fixed, "rb") as f:
            so_data = f.read()
        record_name = next((n for n in names if n.endswith(".dist-info/RECORD")), None)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if info.filename == so_name:
                    zout.writestr(info, so_data)
                elif info.filename == record_name:
                    lines = [f"{so_name},{_record_hash(so_data)},{len(so_data)}"
                             if line.startswith(so_name + ",") else line
                             for line in zin.read(record_name).decode().splitlines()]
                    zout.writestr(info, "\n".join(lines) + "\n")
                else:
                    zout.writestr(info, zin.read(info.filename))
    return stats


# --- Licences -----------------------------------------------------------------

def wheel_license_files(wheel):
    """(name in the wheel, bytes) of its licence files (dist-info)."""
    out = []
    with zipfile.ZipFile(wheel) as z:
        for n in z.namelist():
            parts = n.split("/")
            if not parts[0].endswith(".dist-info") or n.endswith("/"):
                continue
            base = parts[-1].upper()
            if "licenses" in parts[1:-1] or base.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")):
                out.append((n, z.read(n)))
    return out
