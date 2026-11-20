// EngiPad web version: Python (Pyodide) in a web worker.
//
// Started by static/web/web_shell.js. Start-up (measured in Firefox in
// Moodle: about 1.2 s once the files are in the browser cache):
//   1. restore the memory snapshot (snapshot.bin.gz): Python with SymPy,
//      Jinja2, the engine and web/engipad_web.py already imported and
//      warmed up,
//   2. put pylib.zip (the same packages as precompiled bytecode) back at
//      its path -- the snapshot holds the memory, not the file system,
//   3. "ready": calculations without NumPy/matplotlib/CoolProp work now.
// Normal start instead (about 9 s in Firefox): Python is started without
// the snapshot and imports the same packages from pylib.zip. Used where
// the snapshot cannot be restored: it is made under node (V8) and holds
// indices into WebAssembly's function table; Safari (JavaScriptCore, so
// every browser on iPad/iPhone) fills that table differently and stops
// with "call_indirect to a signature that does not match". The worker
// then reports {type: "fatal", phase: "snapshot"} and the shell starts a
// new worker with snapshot: false.
// Then the package groups numpy, matplotlib and coolprop are installed in
// the background, one small step at a time; requests are served between
// two steps. A calculation that needs a group which is not there yet gets
// it loaded first (web/package_guard.py), the time for that is reported
// so the shell does not count it into the timeout.
//
// Messages in:  {cmd: "init", base, snapshot, skipSnapshotFor}
//               (snapshot false or skipSnapshotFor == the build's date:
//               normal start)
//               {cmd: "calc" | "export" | "timeoutPage", id, form}
// Messages out: {type: "status", state: "ready" | "complete", start}
//               {type: "started", id, timeout}      calculation begins (timer on)
//               {type: "loading", id, group}        loads a group first (timer off)
//               {type: "page", id, html}
//               {type: "file", id, filename, data, mimetype}
//               {type: "failed", id, text}          message for the user
//               {type: "fatal", text, phase, built} Python could not start
//                                                    (phase "snapshot": restoring
//                                                    the snapshot failed)
import { loadPyodide } from "../../pyodide/pyodide_loader.js";
import createPyodideModule from "../../pyodide/pyodide.asm.js";

// Python statements that load a group's modules once its wheels are
// installed (also warms them up, e.g. CoolProp builds its fluid library).
const WARM_UP = {
  numpy: "import numpy",
  matplotlib: "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot, mathlib.plotting",
  coolprop: "import CoolProp.CoolProp, CoolProp.HumidAirProp, mathlib.fluids",
};
const BACKGROUND_ORDER = ["numpy", "matplotlib", "coolprop"];
const BACKGROUND_DELAY_MS = 1500;   // let the first calculation go first

let pyodide = null;
let web = null;           // the Python module web.engipad_web
let guard = null;         // the Python module web.package_guard
let base = "";
let manifest = null;
const installed = new Set();        // wheel names
const groupsDone = new Set();
const queue = [];
let busy = false;

function post(msg, transfer) { self.postMessage(msg, transfer || []); }
const pause = (ms) => new Promise(function (r) { setTimeout(r, ms); });

async function fetchBytes(path) {
  const resp = await fetch(base + path);
  if (!resp.ok) throw new Error(path + ": HTTP " + resp.status);
  return new Uint8Array(await resp.arrayBuffer());
}

async function fetchGunzip(path) {
  const resp = await fetch(base + path);
  if (!resp.ok) throw new Error(path + ": HTTP " + resp.status);
  const stream = resp.body.pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

// Pyodide sizes the WebAssembly memory for a snapshot too small (the size
// is fixed in pyodide.asm.wasm); grow it before the snapshot is copied in.
function growingModuleFactory(bytesNeeded) {
  return async function (settings) {
    const m = await createPyodideModule(settings);
    if (m.HEAP8.length < bytesNeeded) m._malloc(bytesNeeded);
    return m;
  };
}

class SnapshotError extends Error {}

function putPylib(pylib) {
  const dir = manifest.pylib_path.substring(0, manifest.pylib_path.lastIndexOf("/"));
  pyodide.FS.mkdirTree(dir);
  pyodide.FS.writeFile(manifest.pylib_path, pylib);
}

async function startFromSnapshot(pylibPromise) {
  try {
    const snapshot = await fetchGunzip("snapshot.bin.gz");
    pyodide = await loadPyodide({
      indexURL: base + "pyodide/",
      _loadSnapshot: snapshot,
      createPyodideModule: growingModuleFactory(snapshot.length),
      stdout: function () {},
      stderr: function () {},
    });
    putPylib(await pylibPromise);
    web = pyodide.pyimport("web.engipad_web");
    guard = pyodide.pyimport("web.package_guard");
  } catch (e) {
    throw new SnapshotError(String(e && e.message ? e.message : e));
  }
}

// The same state as in the snapshot, built step by step (no warm-up).
async function startNormally(pylibPromise) {
  pyodide = await loadPyodide({
    indexURL: base + "pyodide/",
    stdout: function () {},
    stderr: function () {},
  });
  putPylib(await pylibPromise);
  pyodide.runPython(
    "import sys\n" +
    "if " + JSON.stringify(manifest.pylib_path) + " not in sys.path:\n" +
    "    sys.path.insert(0, " + JSON.stringify(manifest.pylib_path) + ")\n" +
    "from web import package_guard\n" +
    "package_guard.install()\n" +
    "import web.engipad_web\n");
  web = pyodide.pyimport("web.engipad_web");
  guard = pyodide.pyimport("web.package_guard");
}

async function start(msg) {
  manifest = await (await fetch(base + "web_manifest.json")).json();
  const pylibPromise = fetchBytes("pylib.zip");
  const useSnapshot = msg.snapshot !== false && msg.skipSnapshotFor !== manifest.built;
  if (useSnapshot) await startFromSnapshot(pylibPromise);
  else await startNormally(pylibPromise);
  post({ type: "status", state: "ready", start: useSnapshot ? "snapshot" : "normal" });
  setTimeout(background, BACKGROUND_DELAY_MS);
  await serveQueue();   // requests sent while Python was starting
}

// --- Package groups -------------------------------------------------------------

async function installWheel(name) {
  if (installed.has(name)) return;
  const file = manifest.wheels[name];
  pyodide.FS.writeFile("/tmp/w.whl", await fetchBytes("wheels/" + file));
  pyodide.runPython(
    "import zipfile, site, importlib\n" +
    "zipfile.ZipFile('/tmp/w.whl').extractall(site.getsitepackages()[0])\n" +
    "importlib.invalidate_caches()\n");
  pyodide.FS.unlink("/tmp/w.whl");
  installed.add(name);
}

// The steps of a group (and of the groups it needs), each a function.
function groupSteps(group, steps) {
  steps = steps || [];
  if (groupsDone.has(group)) return steps;
  const g = manifest.groups[group];
  g.needs.forEach(function (dep) { groupSteps(dep, steps); });
  g.wheels.forEach(function (name) {
    steps.push(function () { return installWheel(name); });
  });
  steps.push(function () {
    if (groupsDone.has(group)) return;
    guard.mark_available(group);
    pyodide.runPython(WARM_UP[group]);
    groupsDone.add(group);
  });
  return steps;
}

async function loadGroupNow(group) {
  for (const step of groupSteps(group)) await step();
}

async function background() {
  for (const group of BACKGROUND_ORDER) {
    for (const step of groupSteps(group)) {
      while (busy || queue.length) await pause(50);   // requests first
      busy = true;
      try { await step(); }
      catch (e) { console.error("EngiPad: loading " + group + " failed", e); }
      finally { busy = false; }
      await serveQueue();
    }
  }
  post({ type: "status", state: "complete" });
}

// --- Requests -------------------------------------------------------------------

function toJs(proxy) {
  const value = proxy.toJs({ dict_converter: Object.fromEntries });
  proxy.destroy();
  return value;
}

async function handle(req) {
  const form = pyodide.toPy(req.form);
  try {
    if (req.cmd === "timeoutPage") {
      post({ type: "page", id: req.id, html: web.timeout_page(form) });
      return;
    }
    post({ type: "started", id: req.id, timeout: web.timeout_seconds(form) });
    for (let attempt = 0; attempt < 5; attempt++) {
      const r = toJs(req.cmd === "export" ? web.export(form) : web.calculate(form));
      if (r.need) {
        for (const group of r.need) {
          post({ type: "loading", id: req.id, group: group });
          await loadGroupNow(group);
        }
        post({ type: "started", id: req.id, timeout: web.timeout_seconds(form) });
        continue;
      }
      if (r.html !== undefined) post({ type: "page", id: req.id, html: r.html });
      else if (r.error !== undefined) post({ type: "failed", id: req.id, text: r.error });
      else post({ type: "file", id: req.id, filename: r.filename, data: r.data, mimetype: r.mimetype },
                [r.data.buffer]);
      return;
    }
    post({ type: "failed", id: req.id, text: "The packages for this calculation could not be loaded." });
  } finally {
    form.destroy();
  }
}

async function serveQueue() {
  if (busy) return;
  busy = true;
  try {
    while (queue.length) {
      const req = queue.shift();
      try { await handle(req); }
      catch (e) {
        post({ type: "failed", id: req.id, text: "Computation failed: " + String(e && e.message ? e.message : e).split("\n").slice(-2).join(" ") });
      }
    }
  } finally {
    busy = false;
  }
}

self.onmessage = async function (ev) {
  const msg = ev.data;
  if (msg.cmd === "init") {
    base = msg.base;
    try { await start(msg); }
    catch (e) {
      post({ type: "fatal", text: String(e && e.message ? e.message : e),
             phase: e instanceof SnapshotError ? "snapshot" : "start",
             built: manifest ? manifest.built : "" });
    }
    return;
  }
  queue.push(msg);
  if (web) await serveQueue();
};
