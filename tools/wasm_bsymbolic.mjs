// node tools/wasm_bsymbolic.mjs <binaryen package dir> <in.so> <out.so>
//
// Post-link fix for an Emscripten side module (Pyodide wheel .so) that was
// linked without -Wl,-Bsymbolic: such a module IMPORTS the functions it
// also exports itself, and the Emscripten loader connects each of those
// imports through a JavaScript stub. Every internal C++ call then goes
// wasm -> JS -> wasm. For CoolProp 7.2.0 (Pyodide 314.0.8) that are 4159
// of 4490 imported functions, and loading the fluid library took about
// 70 s instead of about 9 s.
//
// The fix does what -Bsymbolic does at link time: each such import is
// replaced by a local function that calls the module's own definition
// directly. Nothing else changes (dylink.0 section, data, exports).
// binaryen comes from npm ("npm install binaryen"); its folder is passed
// as the first argument so that no node_modules is needed in the project.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [binaryenDir, src, dst] = process.argv.slice(2);
if (!dst) {
  console.error("usage: node wasm_bsymbolic.mjs <binaryen dir> <in.so> <out.so>");
  process.exit(2);
}
const pkg = JSON.parse(fs.readFileSync(path.join(binaryenDir, "package.json"), "utf8"));
const entry = (pkg.exports && (pkg.exports["."]?.import || pkg.exports["."])) || pkg.module || pkg.main || "index.js";
const binaryen = (await import(pathToFileURL(path.join(binaryenDir, typeof entry === "string" ? entry : "index.js")).href)).default;

const mod = binaryen.readBinary(fs.readFileSync(src));
// Only the features an Emscripten/Pyodide side module uses; "All" would
// let binaryen write newer encodings that browsers do not accept yet.
const F = binaryen.Features;
mod.setFeatures(["MutableGlobals", "NontrappingFPToInt", "SignExt", "BulkMemory", "BulkMemoryOpt",
                 "ExceptionHandling", "ReferenceTypes", "Multivalue", "ExtendedConst",
                 "CallIndirectOverlong"].reduce((a, k) => a | (F[k] || 0), 0));

const exported = new Map();
for (let i = 0; i < mod.getNumExports(); i++) {
  const e = binaryen.getExportInfo(mod.getExportByIndex(i));
  if (e.kind === binaryen.ExternalFunction) exported.set(e.name, e.value);
}
const selfImports = [];
let importedFunctions = 0;
for (let i = 0; i < mod.getNumFunctions(); i++) {
  const f = binaryen.getFunctionInfo(mod.getFunctionByIndex(i));
  if (!f.module) continue;
  importedFunctions++;
  if (f.module === "env" && exported.has(f.base) && exported.get(f.base) !== f.name) selfImports.push(f);
}
for (const f of selfImports) {
  mod.removeFunction(f.name);
  const params = binaryen.expandType(f.params);
  const args = params.map((t, k) => mod.local.get(k, t));
  mod.addFunction(f.name, f.params, f.results, [], mod.call(exported.get(f.base), args, f.results));
}
if (!mod.validate()) {
  console.error("validation of the patched module failed");
  process.exit(1);
}
binaryen.setOptimizeLevel(2);
mod.runPasses(["inlining-optimizing"]);
fs.writeFileSync(dst, mod.emitBinary());
console.log(JSON.stringify({ imported_functions: importedFunctions, self_imports_redirected: selfImports.length }));
