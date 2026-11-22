// EngiPad shell of the web version (Python in the browser, Pyodide).
//
// Counterpart of static/server_shell.js: static/editor.js is the same in
// both versions and only calls
//
//   EngiPadShell.calculate(form, signal) -> Promise<HTML of the whole page>
//
// Here the page is computed by Python in a web worker (static/web/worker.js),
// nothing is sent to a server. This file also
//   - shows the loading state in the status line (#web-status),
//   - lets the Calculate button (and Ctrl+Enter) update the result
//     without reloading the page,
//   - makes the LaTeX export in the worker and downloads it,
//   - enforces the timeout: the worker is stopped (terminate) and started
//     again from the memory snapshot (about 1 s), and the result shows the
//     same message as the server version.
(function () {
  'use strict';

  // The package root (index.html, pyodide/, pylib.zip, ...) is two levels
  // above this script (static/web/web_shell.js).
  const SCRIPT_URL = document.currentScript.src;
  const BASE = new URL('../../', SCRIPT_URL).href;
  // same ?v= as this script, so a new version is not taken from the cache
  const WORKER_URL = new URL('worker.js' + new URL(SCRIPT_URL).search, SCRIPT_URL).href;

  // Start from the memory snapshot (fast) or normally (see worker.js).
  // Where the snapshot cannot be restored (Safari/WebKit, e.g. every
  // browser on iPad), the build date of the package is remembered in the
  // browser, so later starts (and restarts after a timeout) go the normal
  // way at once; a new build tries the snapshot again.
  // index.html?snapshot=0 always starts normally (for comparisons).
  const NO_SNAPSHOT_KEY = 'engipad_web_no_snapshot';
  const SNAPSHOT_ALLOWED = new URLSearchParams(location.search).get('snapshot') !== '0';

  function rememberedNoSnapshot() {
    try { return localStorage.getItem(NO_SNAPSHOT_KEY) || ''; } catch (e) { return ''; }
  }
  function rememberNoSnapshot(built) {
    try { localStorage.setItem(NO_SNAPSHOT_KEY, built || '?'); } catch (e) { /* storage off */ }
  }

  const GROUP_TEXT = {
    numpy: 'Loading NumPy ...',
    matplotlib: 'Loading the plot library ...',
    coolprop: 'Loading CoolProp (fluid properties) ...',
  };

  let worker = null;
  let ready = false;
  let nextId = 1;
  let current = null;        // request the worker is working on
  const waiting = [];        // requests not sent yet
  let timer = null;
  let backgroundDone = false;
  let snapshotFailed = false;   // in this page
  let firstReady = true;

  // --- Status line -------------------------------------------------------------

  // State of Python as an attribute of <html> (loading, ready, complete,
  // restarting, error): for styling and for the browser tests.
  function setState(state) {
    document.documentElement.setAttribute('data-engipad-python', state);
  }

  function status(text, kind) {
    const el = document.getElementById('web-status');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('is-hidden', !text);
    el.classList.toggle('is-error', kind === 'error');
  }

  function idleStatus() {
    if (!ready) {
      status(snapshotFailed || !SNAPSHOT_ALLOWED
        ? 'Loading Python (without the start-up snapshot, this takes a little longer) ...'
        : 'Loading Python ...');
    }
    else if (!backgroundDone) status('Ready. Plots and fluid properties are still loading in the background ...', 'info');
    else status('');
  }

  // --- Worker ----------------------------------------------------------------------

  function startWorker() {
    ready = false;
    worker = new Worker(WORKER_URL, { type: 'module' });
    worker.onmessage = onMessage;
    worker.onerror = function (ev) {
      setState('error');
      status('Python could not be started: ' + (ev.message || 'unknown error'), 'error');
    };
    worker.postMessage({ cmd: 'init', base: BASE, snapshot: SNAPSHOT_ALLOWED && !snapshotFailed,
                         skipSnapshotFor: rememberedNoSnapshot() });
    if (document.documentElement.getAttribute('data-engipad-python') !== 'restarting') setState('loading');
    idleStatus();
  }

  function stopTimer() {
    if (timer) clearTimeout(timer);
    timer = null;
  }

  function onMessage(ev) {
    const m = ev.data;
    if (m.type === 'status') {
      if (m.state === 'ready') {
        ready = true;
        document.documentElement.setAttribute('data-engipad-start', m.start || '');
        setState('ready');
        idleStatus();
        dispatch();
        if (firstReady) { firstReady = false; calculateRestored(); }
      }
      if (m.state === 'complete') { backgroundDone = true; setState('complete'); if (!current) idleStatus(); }
      return;
    }
    if (m.type === 'fatal' && m.phase === 'snapshot' && !snapshotFailed) {
      // e.g. Safari: start again without the snapshot (requests stay queued)
      console.warn('EngiPad: memory snapshot not usable here, normal start:', m.text);
      snapshotFailed = true;
      rememberNoSnapshot(m.built);
      worker.terminate();
      startWorker();
      return;
    }
    if (m.type === 'fatal') {
      setState('error');
      status('Python could not be started: ' + m.text, 'error');
      return;
    }
    if (!current || m.id !== current.id) return;   // answer to a request given up
    if (m.type === 'started') {
      stopTimer();
      const req = current;
      timer = setTimeout(function () { onTimeout(req, m.timeout); }, m.timeout * 1000);
      if (!backgroundDone) idleStatus();
      return;
    }
    if (m.type === 'loading') {
      stopTimer();                         // loading does not count as computing time
      status(GROUP_TEXT[m.group] || 'Loading ...', 'info');
      return;
    }
    stopTimer();
    const req = current;
    current = null;
    if (m.type === 'page') req.resolve(m.html);
    else if (m.type === 'file') req.resolve({ filename: m.filename, data: m.data, mimetype: m.mimetype });
    else req.reject(new Error(m.text));
    idleStatus();
    dispatch();
  }

  function onTimeout(req, seconds) {
    if (current !== req) return;
    worker.terminate();
    current = null;
    setState('restarting');
    status('The calculation took longer than ' + seconds + ' s and was stopped. Restarting Python ...', 'info');
    if (req.cmd === 'calc') {
      // the page with the timeout message, made by the new worker
      waiting.unshift({ id: nextId++, cmd: 'timeoutPage', form: req.form,
                        resolve: req.resolve, reject: req.reject });
    } else {
      req.reject(new Error('Timeout: the export took longer than ' + seconds +
                           ' s and was aborted. You can increase the timeout in Settings.'));
    }
    backgroundDone = false;
    startWorker();
  }

  function dispatch() {
    if (!ready || current || !waiting.length) return;
    current = waiting.shift();
    worker.postMessage({ cmd: current.cmd, id: current.id, form: current.form });
  }

  function request(cmd, form) {
    return new Promise(function (resolve, reject) {
      if (cmd === 'calc') {
        // only the newest calculation that is not sent yet matters
        for (let i = waiting.length - 1; i >= 0; i--) {
          if (waiting[i].cmd === 'calc') {
            waiting[i].reject(new DOMException('superseded', 'AbortError'));
            waiting.splice(i, 1);
          }
        }
      }
      waiting.push({ id: nextId++, cmd: cmd, form: form, resolve: resolve, reject: reject });
      dispatch();
    });
  }

  // A calculation brought back by the editor's backup (e.g. after "Check"
  // in a Moodle question reloaded the page) or an example (?example=name)
  // is calculated once, so its result is visible without pressing Calculate.
  function calculateRestored() {
    const form = document.getElementById('calc-form');
    const code = document.getElementById('code-textarea');
    const out = document.querySelector('.output-container');
    if (!form || !code || !out) return;
    if (code.value.trim() === '' || out.children.length || current || waiting.length) return;
    form.requestSubmit();
  }

  function formObject(form) {
    const data = {};
    new FormData(form).forEach(function (value, key) {
      if (typeof value === 'string') data[key] = value;
    });
    return data;
  }

  // --- The shell interface ------------------------------------------------------

  window.EngiPadShell = {
    calculate: function (form, signal) {
      const promise = request('calc', formObject(form));
      if (!signal) return promise;
      return promise.then(function (html) {
        if (signal.aborted) throw new DOMException('superseded', 'AbortError');
        return html;
      });
    },
  };

  // Calculate button and Ctrl+Enter: the editor submits the form; here it
  // is computed in the worker and only the result area is replaced (no
  // page reload, nothing is sent anywhere).
  function initSubmit() {
    const form = document.getElementById('calc-form');
    if (!form) return;
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      window.EngiPadShell.calculate(form)
        .then(window.EngiPadEditor.applyResultPage)
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;
          window.alert(err && err.message ? err.message : String(err));
        });
    });
  }

  function download(filename, data, mimetype) {
    const url = URL.createObjectURL(new Blob([data], { type: mimetype }));
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
  }

  function initExport() {
    const btn = document.getElementById('export-btn');
    const form = document.getElementById('calc-form');
    if (!btn || !form) return;
    btn.addEventListener('click', function () {
      window.EngiPadEditor.updateHiddenFields();
      const data = formObject(form);
      data.images = window.EngiPadEditor.exportImagesJson();
      request('export', data)
        .then(function (file) { download(file.filename, file.data, file.mimetype); })
        .catch(function (err) {
          window.alert(err && err.message ? err.message : String(err));
        });
    });
  }

  // This file is loaded before editor.js; DOMContentLoaded comes after
  // both have run (window.EngiPadEditor exists then, and the editor's own
  // submit handler, which fills the hidden fields, comes first).
  document.addEventListener('DOMContentLoaded', function () {
    initSubmit();
    initExport();
  });
  // An example opened with ?example=name (static/editor.js) is calculated
  // once; if Python is not ready yet, that happens when it is.
  document.addEventListener('engipad:example-loaded', function () {
    if (ready) calculateRestored();
  });
  startWorker();
})();
