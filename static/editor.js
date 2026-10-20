const codeTextarea = document.getElementById('code-textarea');
const fileInput = document.getElementById('file-input');

// --- Icons: fetch SVG files and embed them INLINE (not as <img>), so
// that stroke="currentColor" follows the button's text color (hover,
// "active" state, a future dark mode -- all via CSS, without having to
// maintain a second icon file per state).
function loadIcons() {
  document.querySelectorAll('[data-icon]').forEach(function (el) {
    const name = el.getAttribute('data-icon');
    fetch('/static/icons/' + name + '.svg')
      .then(function (res) { return res.ok ? res.text() : Promise.reject(res.status); })
      .then(function (svg) { el.innerHTML = svg; })
      .catch(function () { /* icon missing -- button stays empty, not a hard error */ });
  });
}

// --- Dropdowns ---
function closeAllDropdowns(except) {
  document.querySelectorAll('[data-dropdown]').forEach(function (dd) {
    if (dd !== except) dd.removeAttribute('data-open');
  });
}

function positionDropdown(dd) {
  const panel = dd.querySelector('[data-panel]');
  if (!panel) return;

  // On narrow windows, CSS (media query) takes over full bottom-sheet
  // positioning -- don't interfere with that here.
  if (window.matchMedia('(max-width: 760px)').matches) return;

  panel.style.left = '';
  panel.style.right = '';

  requestAnimationFrame(function () {
    const rect = panel.getBoundingClientRect();
    const col = dd.closest('.col');
    const boundary = col ? col.getBoundingClientRect().right : window.innerWidth;
    if (rect.right > boundary - 8) {
      panel.style.left = 'auto';
      panel.style.right = '0';
    }
  });
}

function initDropdowns() {
  document.querySelectorAll('[data-dropdown]').forEach(function (dd) {
    const trigger = dd.querySelector('[data-trigger]');
    if (!trigger) return;

    trigger.addEventListener('click', function (e) {
      e.stopPropagation();
      const isOpen = dd.hasAttribute('data-open');
      closeAllDropdowns(dd);
      if (isOpen) {
        dd.removeAttribute('data-open');
      } else {
        dd.setAttribute('data-open', '');
        positionDropdown(dd);
      }
    });

    // Clicks INSIDE the panel (labels, input fields, text, ...) must not
    // bubble up to the global document click listener below -- otherwise
    // the panel would close itself the instant you click into it (e.g.
    // when focusing the rounding-precision text field). Buttons that
    // should deliberately close the panel (e.g. inserting a symbol)
    // still call closeAllDropdowns() explicitly themselves, which is
    // unaffected by this.
    const panel = dd.querySelector('[data-panel]');
    if (panel) {
      panel.addEventListener('click', function (e) {
        e.stopPropagation();
      });
    }
  });

  document.addEventListener('click', function () {
    closeAllDropdowns(null);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeAllDropdowns(null);
  });
}

// --- Open file ---
fileInput.addEventListener('change', function () {
  const file = fileInput.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = function (e) {
    imageStore.clear(); // a newly opened file brings its own images (if any)
    loadWorkbookText(e.target.result);
    resetHistory(); // an opened file is a new starting point for undo
  };
  reader.readAsText(file, 'utf-8');
});

// --- Save file ---
function saveFile() {
  const text = getFileText() || '';
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });

  let filename = prompt('enter filename:', 'EngiPad.txt');
  if (!filename) {
    return;
  }

  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;

  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  URL.revokeObjectURL(link.href);
}

// --- Insert snippet (symbols, functions, ...) ---
function insertSymbol(sym) {
  insertSnippet(sym, 0);
}

// The text is inserted the way typing would insert it (execCommand), NOT
// by assigning codeTextarea.value: an assignment throws away the
// browser's undo history of the textarea, so Ctrl+Z (and the undo of
// the on-screen keyboard) would stop working after every inserted
// snippet. The insertion also fires the normal "input" event (sheet
// text and autosave stay in step). Fallback for a browser without
// execCommand: setRangeText + our own "input" event (no undo there).
function insertSnippet(snippet, cursorOffsetFromEnd) {
  codeTextarea.focus();
  flushHistory(); // what was typed so far is its own undo step

  const start = codeTextarea.selectionStart;
  const end = codeTextarea.selectionEnd;

  let inserted = false;
  try {
    inserted = document.execCommand('insertText', false, snippet);
  } catch (e) {
    inserted = false;
  }
  if (!inserted) {
    codeTextarea.setRangeText(snippet, start, end, 'end');
    codeTextarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  const cursorPos = start + snippet.length - (cursorOffsetFromEnd || 0);
  codeTextarea.selectionStart = codeTextarea.selectionEnd = cursorPos;
  commitHistory(); // ... and so is the snippet
  closeAllDropdowns(null);
}

// --- Autosave / Recovery (localStorage) ---
//
// Problem: wird EngiPad in einem iframe eingebettet (z.B. in Moodle) und
// die umgebende Seite laedt neu (z.B. nach "Absenden und beenden"), laedt
// der Browser auch den iframe komplett neu -- der Server liefert dann
// wieder die leere/Beispiel-Startseite aus, der bisherige Lösungsweg ist
// weg. Das hier merkt sich den zuletzt eingegebenen Text im Browser
// selbst (nicht auf dem Server) und stellt ihn beim naechsten Laden
// automatisch wieder her -- solange es derselbe Browser/dasselbe Geraet
// bleibt.
//
// Bewusst NICHT serverseitig geloest: das braeuchte eine verlaessliche
// Nutzer-Identitaet (z.B. per LTI), die es aktuell nicht gibt. Diese
// rein lokale Loesung deckt den haeufigsten Fall (Reload derselben
// Moodle-Sitzung im selben Browser) bereits gut ab.

const AUTOSAVE_KEY = 'engipad_autosave_code';
const AUTOSAVE_DEBOUNCE_MS = 1000;

// Erkennt, ob das Textfeld noch den unveraenderten Beispieltext zeigt
// (= frischer Seitenaufruf ohne eigene Eingabe). Nur DANN wird ein
// gespeicherter Stand automatisch wiederhergestellt -- ein Ergebnis,
// das der Server gerade erst frisch berechnet und zurueckgeschickt hat
// (z.B. nach Klick auf "Calculate"), darf NIE ueberschrieben werden.
function isDefaultExampleText(text) {
  return text.trim().indexOf('"Example:"') === 0;
}

function restoreAutosave() {
  let saved;
  try {
    saved = window.localStorage.getItem(AUTOSAVE_KEY);
  } catch (e) {
    return; // localStorage nicht verfuegbar (z.B. Privacy-Mode) -- kein Problem, einfach nichts wiederherstellen
  }
  if (!saved) return;
  if (isDefaultExampleText(codeTextarea.value)) {
    codeTextarea.value = saved; // loadWorkbookText() takes its images from it
  } else {
    // A fresh calculation of the server must not be overwritten (see
    // above), but its images are not in the page: take them from here.
    mergeImagesFromText(saved);
  }
}

let autosaveWarned = false;
let scheduleAutosave = function () {}; // replaced by initAutosave()

function saveAutosave(notify) {
  try {
    window.localStorage.setItem(AUTOSAVE_KEY, getFileText());
  } catch (e) {
    // localStorage nicht verfuegbar oder voll -- Autosave faellt in dem Fall
    // einfach aus, der Rest der App funktioniert unveraendert weiter.
    // Mit Bildern ist "voll" realistisch: dann einmal deutlich sagen.
    if (notify === true && imageStore.size > 0 && !autosaveWarned) {
      autosaveWarned = true;
      window.alert('The browser storage for the automatic backup is full (images need a lot of space). ' +
        'Please save your work to a file.');
    }
  }
}

function initAutosave() {
  restoreAutosave();

  let debounceTimer = null;
  scheduleAutosave = function () {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () { saveAutosave(true); }, AUTOSAVE_DEBOUNCE_MS);
  };
  codeTextarea.addEventListener('input', function () { scheduleAutosave(); });

  // Zusaetzliches Sicherheitsnetz: falls der Reload ohne "input"-Event
  // dazwischen kommt (z.B. Klick direkt auf Moodles "Absenden und
  // beenden", ohne dass die Debounce-Zeit noch ablaufen konnte).
  window.addEventListener('beforeunload', function () { saveAutosave(false); });
}

// --- Live update (optional) + Ctrl+Enter ---
//
// Default (checkbox off): results are only computed by the Calculate
// button (normal form POST, full page reload) or Ctrl+Enter.
// Checkbox on: after every Enter in the editor the same POST is sent in
// the BACKGROUND (fetch), and only the result area is swapped. The
// editor (cursor, focus) is never touched and the page does not reload.
// No server change is needed: the response is the full page, of which
// only .output-container is used.
//
// A still running request is aborted when a newer one starts, so only
// the latest state is ever shown; on errors/timeouts the last good
// result simply stays.

const LIVE_KEY = 'engipad_live_update';
let liveController = null;

// The user's own choice (stored) wins; without one the server-provided
// default applies (on locally, off on a server, see app.py).
function readLivePreference(serverDefault) {
  try {
    const stored = window.localStorage.getItem(LIVE_KEY);
    if (stored === '1') return true;
    if (stored === '0') return false;
  } catch (e) {
    // storage blocked (e.g. embedded) -- fall through to the default
  }
  return serverDefault;
}

function writeLivePreference(on) {
  try {
    window.localStorage.setItem(LIVE_KEY, on ? '1' : '0');
  } catch (e) {
    // not persisted -- the toggle still works for this page view
  }
}

function runLiveUpdate() {
  const form = document.getElementById('calc-form');
  const current = document.querySelector('.output-container');
  if (!form || !current) return;

  updateHiddenFields();

  if (liveController) liveController.abort();
  liveController = new AbortController();
  const controller = liveController;

  fetch('/', {
    method: 'POST',
    body: new URLSearchParams(new FormData(form)),
    signal: controller.signal,
  })
    .then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.text();
    })
    .then(function (html) {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const fresh = doc.querySelector('.output-container');
      if (!fresh) throw new Error('no result area in response');

      const wrap = current.closest('.output-wrap');
      const scrollTop = wrap ? wrap.scrollTop : 0;

      if (window.MathJax && MathJax.typesetClear) MathJax.typesetClear([current]);
      current.innerHTML = fresh.innerHTML;
      current.setAttribute('style', fresh.getAttribute('style') || '');
      fillPictures(current);

      const freshForm = doc.getElementById('calc-form');
      if (freshForm) form.setAttribute('style', freshForm.getAttribute('style') || '');

      // Results of ALL sheets for printing (only filled when "Print/export
      // all sheets" is on). Typeset on demand, see ensurePrintTypeset().
      const printBox = document.getElementById('print-sheets');
      const freshPrint = doc.getElementById('print-sheets');
      if (printBox && freshPrint) {
        if (window.MathJax && MathJax.typesetClear) MathJax.typesetClear([printBox]);
        printBox.innerHTML = freshPrint.innerHTML;
        printBox.setAttribute('style', freshPrint.getAttribute('style') || '');
        printBox.classList.add('tex2jax_ignore');
        printBox.dataset.typeset = '0';
        fillPictures(printBox);
      }

      if (wrap) wrap.scrollTop = scrollTop;
      if (window.MathJax && MathJax.typesetPromise) {
        MathJax.typesetPromise([current]).catch(function () { /* keep going */ });
      }
    })
    .catch(function (err) {
      if (err && err.name === 'AbortError') return; // superseded by a newer request
      // keep the last good result (visible in the browser console for debugging)
      console.error('EngiPad live update failed:', err);
    });
}

function initLiveUpdate() {
  const toggle = document.getElementById('live-update-toggle');
  const form = document.getElementById('calc-form');
  if (!form) return;

  if (toggle) {
    toggle.checked = readLivePreference(toggle.getAttribute('data-default') === '1');
    toggle.addEventListener('change', function () {
      writeLivePreference(toggle.checked);
      if (toggle.checked) runLiveUpdate(); // bring the result up to date once
    });
  }

  codeTextarea.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.isComposing) return;

    if (e.ctrlKey || e.metaKey) {
      e.preventDefault(); // no newline for the shortcut
      if (toggle && toggle.checked) runLiveUpdate();
      else form.requestSubmit();
      return;
    }

    if (toggle && toggle.checked && !e.shiftKey && !e.altKey) {
      // Wait until the browser has inserted the line break.
      setTimeout(runLiveUpdate, 0);
    }
  });
}

// --- Sheets (workbook) ---
//
// A workbook is ONE text: every sheet starts with a marker line
// "##Name" (see core/workbook.py, which parses it the same way). The
// textarea only shows the active sheet; the whole text is assembled
// when it is needed (calculate, save, autosave) and travels in the
// hidden field "code" of the form. A text whose first non-blank line is
// no marker is a single unnamed sheet -- no tabs, exactly as before.

const SHEET_MARKER_RE = /^##[ \t]*([\p{L}_][\p{L}\p{N}_]*)[ \t]*$/u;
const SHEET_NAME_RE = /^[\p{L}_][\p{L}\p{N}_]*$/u;

let sheets = [{ name: null, code: '' }];
let activeSheet = 0;

function splitLines(text) {
  const lines = text.split(/\r\n|\r|\n/);
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

function parseWorkbookText(text) {
  const lines = splitLines(text);
  const first = lines.find(function (l) { return l.trim() !== ''; });
  if (first === undefined || !SHEET_MARKER_RE.test(first.replace(/\s+$/, ''))) {
    return [{ name: null, code: text }];
  }

  const result = [];
  let current = null;
  lines.forEach(function (line) {
    const match = SHEET_MARKER_RE.exec(line.replace(/\s+$/, ''));
    if (match) {
      let name = match[1];
      let n = 2;
      while (result.some(function (sh) { return sh.name === name; })) {
        name = match[1] + '_' + n++; // duplicate name in the file: keep both
      }
      current = { name: name, lines: [] };
      result.push(current);
    } else if (current) {
      current.lines.push(line);
    }
  });

  return result.map(function (sh) { return { name: sh.name, code: sh.lines.join('\n') }; });
}

function serializeWorkbook() {
  if (sheets.length === 1 && sheets[0].name === null) return sheets[0].code;
  return sheets.map(function (sh) { return '##' + sh.name + '\n' + sh.code; }).join('\n');
}

function syncActiveSheet() {
  sheets[activeSheet].code = codeTextarea.value;
}

function getWorkbookText() {
  syncActiveSheet();
  return serializeWorkbook();
}

// The text of a SAVED file: the workbook plus the images that are still
// used (picture("name") somewhere). getWorkbookText() stays the code
// alone -- that is what goes to the server with every calculation.
function getFileText() {
  const code = getWorkbookText();
  return EngiPictures.joinImageSection(code, usedImageEntries());
}

function isNamedWorkbook() {
  return !(sheets.length === 1 && sheets[0].name === null);
}

function updateHiddenFields() {
  const workbookField = document.getElementById('workbook-field');
  const activeField = document.getElementById('active-sheet-field');
  if (workbookField) workbookField.value = getWorkbookText();
  if (activeField) activeField.value = sheets[activeSheet].name || '';
  // Only the NAMES of the images go with a calculation (the server checks
  // picture("...") lines against them); the images stay in the browser.
  const namesField = document.getElementById('image-names-field');
  if (namesField) namesField.value = Array.from(imageStore.keys()).join(',');
}

function updatePrintTitle() {
  const th = document.getElementById('print-sheet-title');
  if (!th) return;
  const named = isNamedWorkbook();
  th.textContent = named ? sheets[activeSheet].name : '';
  const head = th.closest('thead');
  if (head) head.classList.toggle('is-empty', !named);
}

function showSheet(index) {
  activeSheet = index;
  codeTextarea.value = sheets[index].code;
  renderTabs();
  updateHiddenFields();
  updatePrintTitle();
}

// Loads a whole workbook text (open file, restored autosave, page load).
function loadWorkbookText(text, preferredActiveName) {
  text = takeImagesFromText(text, true);
  sheets = parseWorkbookText(text);
  let index = 0;
  if (preferredActiveName) {
    const found = sheets.findIndex(function (sh) { return sh.name === preferredActiveName; });
    if (found >= 0) index = found;
  }
  showSheet(index);
  updateImageStatus();
  fillPictures(document);
}

function freeSheetName() {
  let n = 1;
  while (sheets.some(function (sh) { return sh.name === 'S' + n; })) n++;
  return 'S' + n;
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function sheetReferenceRegExp(name) {
  return new RegExp('(^|[^\\p{L}\\p{N}_])' + escapeRegExp(name) + '::', 'gu');
}

function switchToSheet(index) {
  if (index === activeSheet) return;
  flushHistory(); // switching the view is not an edit and gets no undo step of its own
  syncActiveSheet();
  showSheet(index);
  runLiveUpdate(); // the right side must show the sheet you switched to
}

function addSheet() {
  flushHistory();
  syncActiveSheet();
  if (sheets.length === 1 && sheets[0].name === null) sheets[0].name = freeSheetName();
  sheets.push({ name: freeSheetName(), code: '' });
  showSheet(sheets.length - 1);
  commitHistory();
  runLiveUpdate();
  codeTextarea.focus();
}

function deleteSheet(index) {
  flushHistory();
  syncActiveSheet();
  const name = sheets[index].name;
  const pattern = sheetReferenceRegExp(name);
  const users = sheets
    .filter(function (sh, j) { pattern.lastIndex = 0; return j !== index && pattern.test(sh.code); })
    .map(function (sh) { return sh.name; });

  let message = "Delete sheet '" + name + "'?";
  if (users.length) {
    message += '\n\nIt is referenced from: ' + users.join(', ') + '. Those references will fail.';
  }
  if (!window.confirm(message)) return;

  sheets.splice(index, 1);
  if (sheets.length === 1) sheets[0].name = null; // back to a plain single sheet
  showSheet(Math.min(activeSheet, sheets.length - 1));
  commitHistory();
  runLiveUpdate();
}

// Renames a sheet and rewrites "Old::" references in ALL sheets.
function renameSheet(index, newName) {
  flushHistory();
  syncActiveSheet();
  const oldName = sheets[index].name;
  const selection = codeTextarea.selectionStart;
  const pattern = sheetReferenceRegExp(oldName);

  sheets.forEach(function (sh) {
    sh.code = sh.code.replace(pattern, function (all, lead) { return lead + newName + '::'; });
  });
  sheets[index].name = newName;

  codeTextarea.value = sheets[activeSheet].code;
  try { codeTextarea.setSelectionRange(selection, selection); } catch (e) { /* ignore */ }
  renderTabs();
  updateHiddenFields();
  updatePrintTitle();
  commitHistory();
  runLiveUpdate();
}

function startRename(index, tabEl) {
  const oldName = sheets[index].name;
  const input = document.createElement('input');
  input.type = 'text';
  input.value = oldName;
  input.setAttribute('aria-label', 'Sheet name');
  tabEl.textContent = '';
  tabEl.appendChild(input);
  input.focus();
  input.select();

  let finished = false;

  function problem(value) {
    if (!SHEET_NAME_RE.test(value)) {
      return 'Letters, digits and _ only; must start with a letter (no spaces).';
    }
    if (sheets.some(function (sh, j) { return j !== index && sh.name === value; })) {
      return 'A sheet with this name already exists.';
    }
    return '';
  }

  function finish(commit) {
    if (finished) return;
    const value = input.value.trim();
    if (commit && value !== oldName) {
      const why = problem(value);
      if (why) {
        input.classList.add('is-invalid');
        input.title = why;
        return false; // stay in the field
      }
      finished = true;
      renameSheet(index, value);
      return true;
    }
    finished = true;
    renderTabs();
    return true;
  }

  input.addEventListener('keydown', function (e) {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  input.addEventListener('input', function () {
    input.classList.remove('is-invalid');
    input.title = '';
  });
  input.addEventListener('blur', function () {
    if (!finished && finish(true) === false) finish(false); // invalid on blur: revert
  });
  input.addEventListener('click', function (e) { e.stopPropagation(); });
  input.addEventListener('dblclick', function (e) { e.stopPropagation(); });
}

function renderTabs() {
  const box = document.getElementById('sheet-tabs');
  if (!box) return;
  box.textContent = '';
  if (!isNamedWorkbook()) return;

  sheets.forEach(function (sh, index) {
    const tab = document.createElement('span');
    tab.className = 'sheet-tab' + (index === activeSheet ? ' is-active' : '');
    tab.setAttribute('role', 'tab');
    tab.title = 'Double-click to rename';

    const label = document.createElement('span');
    label.textContent = sh.name;
    tab.appendChild(label);

    if (index === activeSheet) {
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'sheet-tab-close';
      close.textContent = '×';
      close.title = 'Delete sheet';
      close.addEventListener('click', function (e) { e.stopPropagation(); deleteSheet(index); });
      close.addEventListener('dblclick', function (e) { e.stopPropagation(); });
      tab.appendChild(close);
    }

    tab.addEventListener('click', function () { switchToSheet(index); });
    tab.addEventListener('dblclick', function (e) {
      e.preventDefault();
      if (index !== activeSheet) { syncActiveSheet(); showSheet(index); runLiveUpdate(); }
      startRename(index, box.children[index]);
    });
    box.appendChild(tab);
  });
}

// --- Printing ---
// The sheet name goes into the printed header (CSS, @media print). With
// "Print/export all sheets" the results of all sheets (already in the
// page, see runLiveUpdate()/the server) are printed instead; they are
// typeset only now, so normal work doesn't pay for them.

function ensurePrintTypeset(box) {
  if (box.dataset.typeset === '1') return;
  box.classList.remove('tex2jax_ignore');
  if (window.MathJax && MathJax.typeset) {
    try { MathJax.typeset([box]); } catch (e) { /* print what we have */ }
  }
  box.dataset.typeset = '1';
}

let titleBeforePrint = null;

function initPrinting() {
  window.addEventListener('beforeprint', function () {
    titleBeforePrint = document.title;
    const allToggle = document.getElementById('all-sheets-toggle');
    const box = document.getElementById('print-sheets');

    if (isNamedWorkbook() && allToggle && allToggle.checked && box && box.querySelector('.print-sheet')) {
      ensurePrintTypeset(box);
      document.body.classList.add('print-all');
      document.title = 'EngiPad – all sheets';
    } else if (isNamedWorkbook()) {
      // Also shown in the browser's own print header and used as the PDF file name.
      document.title = 'EngiPad – ' + sheets[activeSheet].name;
    }
  });

  window.addEventListener('afterprint', function () {
    document.body.classList.remove('print-all');
    if (titleBeforePrint !== null) document.title = titleBeforePrint;
    titleBeforePrint = null;
  });
}

const ALL_SHEETS_KEY = 'engipad_all_sheets';

function initSheets() {
  const form = document.getElementById('calc-form');

  loadWorkbookText(codeTextarea.value, codeTextarea.getAttribute('data-active') || '');

  codeTextarea.addEventListener('input', syncActiveSheet);
  if (form) form.addEventListener('submit', updateHiddenFields);

  const newBtn = document.getElementById('new-sheet-btn');
  if (newBtn) newBtn.addEventListener('click', addSheet);

  const allToggle = document.getElementById('all-sheets-toggle');
  if (allToggle) {
    try {
      const stored = window.localStorage.getItem(ALL_SHEETS_KEY);
      if (stored === '1') allToggle.checked = true;
      else if (stored === '0') allToggle.checked = false;
    } catch (e) { /* storage blocked: keep what the page says */ }

    allToggle.addEventListener('change', function () {
      try { window.localStorage.setItem(ALL_SHEETS_KEY, allToggle.checked ? '1' : '0'); } catch (e) { /* ignore */ }
      runLiveUpdate(); // fetch (or drop) the results of all sheets
    });
  }

  initPrinting();
}

// --- Pictures: picture("name") lines (see core/images.py, static/pictures.js) ---
//
// The images live HERE, in the browser: in imageStore (name -> {b64,
// bytes, width, height}), in the saved file (image section at its end)
// and in the autosave. A calculation only sends their NAMES to the
// server (hidden field "image_names"); the LaTeX export sends the used
// ones once more. The result shows an <img data-name="..."> placeholder
// whose picture is put in by fillPictures() after every update.
//
// An image that no sheet refers to any more is not saved (see
// usedImageEntries()) -- but it stays in memory until the next upload, so
// Ctrl+Z after deleting a picture line brings the picture back too.

const imageStore = new Map();

function allSheetCodes() {
  syncActiveSheet();
  return sheets.map(function (sh) { return sh.code; });
}

// Images that are still used, in order of first use.
function usedImageEntries() {
  return EngiPictures.referencedNames(allSheetCodes())
    .filter(function (name) { return imageStore.has(name); })
    .map(function (name) { return { name: name, b64: imageStore.get(name).b64 }; });
}

function usedImageBytes() {
  return usedImageEntries().reduce(function (sum, entry) {
    return sum + imageStore.get(entry.name).bytes;
  }, 0);
}

function imageLimitBytes() {
  const select = document.getElementById('image-limit-select');
  const mb = select ? parseInt(select.value, 10) : 1;
  return (mb > 0 ? mb : 1) * 1024 * 1024;
}

function updateImageStatus() {
  const btn = document.getElementById('image-btn');
  if (!btn) return;
  const used = usedImageEntries().length;
  const kb = Math.round(usedImageBytes() / 1024);
  btn.title = 'Insert image (PNG) – ' + used + '/' + window.ENGIPAD_IMAGES.maxImages +
    ' images, ' + kb + ' KB of ' + Math.round(imageLimitBytes() / 1024 / 1024) + ' MB';
}

// Puts the pictures of the store into the <img> placeholders of a result.
function fillPictures(root) {
  (root || document).querySelectorAll('img.engipad-picture').forEach(function (img) {
    const entry = imageStore.get(img.getAttribute('data-name'));
    if (!entry) {
      img.title = 'Image not loaded';
      return;
    }
    const src = 'data:image/png;base64,' + entry.b64;
    if (img.getAttribute('src') !== src) img.setAttribute('src', src);
  });
}

function addImagesToStore(images) {
  images.forEach(function (image) {
    imageStore.set(image.name, {
      b64: image.b64, bytes: image.bytes, width: image.width, height: image.height,
    });
  });
}

// Opening a file / restoring: takes the image section off the text,
// loads its images and returns the text without it.
function takeImagesFromText(text, announce) {
  const split = EngiPictures.splitImageSection(text);
  addImagesToStore(split.images);
  if (announce && split.warnings.length) window.alert(split.warnings.join('\n'));
  return split.code;
}

// Page reload after a calculation: the code comes from the server, the
// images from the last autosave (only those not loaded yet).
function mergeImagesFromText(text) {
  const split = EngiPictures.splitImageSection(text);
  addImagesToStore(split.images.filter(function (image) { return !imageStore.has(image.name); }));
}

function pruneUnreferencedImages() {
  const used = EngiPictures.referencedNames(allSheetCodes());
  Array.from(imageStore.keys()).forEach(function (name) {
    if (used.indexOf(name) < 0) imageStore.delete(name);
  });
}

function uniqueImageName(base) {
  const max = window.ENGIPAD_IMAGES.nameMaxLength;
  let name = base.slice(0, max);
  let n = 2;
  while (imageStore.has(name)) {
    const suffix = '_' + n++;
    name = base.slice(0, max - suffix.length) + suffix;
  }
  return name;
}

function bytesToBase64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function loadImageElement(file) {
  return new Promise(function (resolve, reject) {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      reject(new Error('The file could not be read as an image.'));
    };
    img.src = url;
  });
}

// Shrinks the picture step by step (1200, 900, 700, ... px wide) until it
// fits the limits; the canvas re-encodes it as a plain PNG.
async function encodeWithinLimits(img, perImageLimit, bytesLeft) {
  const c = window.ENGIPAD_IMAGES;
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  const widths = [];
  [c.maxPixelWidth, 900, 700, 500, 350, 250].forEach(function (step) {
    const tw = Math.min(w, step);
    if (widths.indexOf(tw) < 0) widths.push(tw);
  });

  for (let i = 0; i < widths.length; i++) {
    const tw = widths[i];
    const th = Math.max(1, Math.round(h * tw / w));
    if (th > c.maxPixelHeight || tw * th > c.maxPixels) continue;

    const canvas = document.createElement('canvas');
    canvas.width = tw;
    canvas.height = th;
    canvas.getContext('2d').drawImage(img, 0, 0, tw, th);
    const blob = await new Promise(function (resolve) { canvas.toBlob(resolve, 'image/png'); });
    if (!blob) continue;
    if (blob.size <= perImageLimit && blob.size <= bytesLeft) {
      return new Uint8Array(await blob.arrayBuffer());
    }
  }
  return null;
}

async function addImageFromFile(file) {
  const c = window.ENGIPAD_IMAGES;
  try {
    const head = new Uint8Array(await file.slice(0, 8).arrayBuffer());
    const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    if (head.length < 8 || signature.some(function (b, i) { return head[i] !== b; })) {
      throw new Error('Only PNG images are supported.');
    }

    // Images that nothing refers to any more make room first.
    pruneUnreferencedImages();
    if (imageStore.size >= c.maxImages) {
      throw new Error('At most ' + c.maxImages + ' images per file. Delete a picture(...) line first.');
    }

    const total = imageLimitBytes();
    const perImage = EngiPictures.perImageLimit(total);
    const bytesLeft = total - usedImageBytes();
    const img = await loadImageElement(file);
    const bytes = await encodeWithinLimits(img, perImage, bytesLeft);
    if (!bytes) {
      throw new Error('The image is too large for the image limit (' +
        Math.floor(Math.min(perImage, bytesLeft) / 1024) + ' KB are available for it). ' +
        'Use a smaller picture or raise "Image limit" in the settings.');
    }

    const b64 = bytesToBase64(bytes);
    const info = EngiPictures.pngInfo(b64, perImage);
    const base = file.name.replace(/\.[^.]*$/, '').replace(/[^A-Za-z0-9_]/g, '_') || 'image';
    const name = uniqueImageName(base);
    addImagesToStore([{ name: name, b64: b64, bytes: info.bytes, width: info.width, height: info.height }]);

    // The line shows all options with their defaults, so the user can see
    // how to change the picture without opening the guide.
    const shown = Math.min(info.width, c.defaultDisplayWidth);
    insertSnippet('picture("' + name + '", width=' + shown + ', align=' + c.defaultAlignment + ')', 0);
    updateImageStatus();
    fillPictures(document);
    const live = document.getElementById('live-update-toggle');
    if (live && live.checked) runLiveUpdate();
  } catch (err) {
    window.alert(err && err.message ? err.message : 'The image could not be added.');
  }
}

function initPictures() {
  const btn = document.getElementById('image-btn');
  const input = document.getElementById('image-input');
  const form = document.getElementById('calc-form');

  if (btn && input) {
    btn.addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () {
      const file = input.files[0];
      input.value = ''; // the same file may be chosen again
      if (file) addImageFromFile(file);
    });
  }

  const select = document.getElementById('image-limit-select');
  if (select) select.addEventListener('change', updateImageStatus);

  // The LaTeX export needs the picture data; every other submit and every
  // live update carries only the names (field stays empty).
  if (form) {
    form.addEventListener('submit', function (e) {
      const field = document.getElementById('export-images-field');
      if (!field) return;
      const submitter = e.submitter;
      if (submitter && submitter.getAttribute('formaction') === '/export/latex') {
        const data = {};
        usedImageEntries().forEach(function (entry) { data[entry.name] = entry.b64; });
        field.value = Object.keys(data).length ? JSON.stringify(data) : '';
        setTimeout(function () { field.value = ''; }, 0);
      } else {
        field.value = '';
      }
    });
  }

  codeTextarea.addEventListener('input', updateImageStatus);
  updateImageStatus();
  fillPictures(document);
}

// --- Undo / Redo (own history of the whole workbook) ---
//
// The browser's own undo of a textarea cannot be used here: it does not
// survive a switch to another sheet, a rename (which rewrites the
// references in ALL sheets), opening a file, and it is unreliable with
// the auto-correction of tablet keyboards. So EngiPad keeps its own
// history: a list of snapshots of the whole workbook (all sheets, the
// sheet that was shown, the cursor). Undo/Redo step through it, the
// buttons and Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z use the same history, and
// the browser's own undo is switched off in the editor.
//
// What becomes one step:  typing in one go (a pause of HISTORY_PAUSE_MS,
// the change from typing to deleting, or Enter ends a step); a paste, a
// cut or a drop; an inserted snippet or picture line; adding, deleting
// and renaming a sheet. Switching sheets is not an edit and no step; undo
// shows the sheet in which the undone change happened. The history is
// not saved (neither in the file nor in the autosave) and starts anew
// when a file is opened. Pictures are not part of it: a picture whose
// line is undone away stays in memory until the next upload.

const HISTORY_LIMIT = 200;
const HISTORY_PAUSE_MS = 700;
const HISTORY_OWN_STEP_TYPES = [
  'insertFromPaste', 'insertFromDrop', 'insertFromYank', 'deleteByCut', 'deleteByDrag',
  'insertLineBreak', 'insertParagraph',
];

let historySteps = [];
let historyIndex = -1;
let historyTimer = null;
let historyPending = false; // typed since the last step
let historyKind = null; // 'insert' or 'delete': the last kind of typing
let historyOwnStep = false;
let historyComposing = false;

function takeHistorySnapshot() {
  syncActiveSheet();
  return {
    sheets: sheets.map(function (sh) { return { name: sh.name, code: sh.code }; }),
    active: activeSheet,
    selStart: codeTextarea.selectionStart,
    selEnd: codeTextarea.selectionEnd,
  };
}

function sameWorkbook(a, b) {
  return a.sheets.length === b.sheets.length && a.sheets.every(function (sh, i) {
    return sh.name === b.sheets[i].name && sh.code === b.sheets[i].code;
  });
}

function updateHistoryButtons() {
  const undoBtn = document.getElementById('undo-btn');
  const redoBtn = document.getElementById('redo-btn');
  if (undoBtn) undoBtn.disabled = !(historyIndex > 0 || historyPending);
  if (redoBtn) redoBtn.disabled = !(historyIndex < historySteps.length - 1);
}

// Records the current state as a step (nothing if nothing changed).
function commitHistory() {
  clearTimeout(historyTimer);
  historyTimer = null;
  historyPending = false;

  const snap = takeHistorySnapshot();
  const current = historySteps[historyIndex];
  if (current && sameWorkbook(current, snap)) {
    current.selStart = snap.selStart;
    current.selEnd = snap.selEnd;
    updateHistoryButtons();
    return;
  }

  historySteps.length = historyIndex + 1; // a new change ends the redo branch
  historySteps.push(snap);
  if (historySteps.length > HISTORY_LIMIT) historySteps.shift();
  historyIndex = historySteps.length - 1;
  updateHistoryButtons();
}

// Ends the step that typing has begun (if any).
function flushHistory() {
  if (historyPending) commitHistory();
}

function resetHistory() {
  clearTimeout(historyTimer);
  historyTimer = null;
  historyPending = false;
  historyKind = null;
  historySteps = [takeHistorySnapshot()];
  historyIndex = 0;
  updateHistoryButtons();
}

function scheduleHistoryStep() {
  clearTimeout(historyTimer);
  historyTimer = setTimeout(function () {
    if (historyComposing) { scheduleHistoryStep(); return; } // wait until the composition is finished
    commitHistory();
  }, HISTORY_PAUSE_MS);
  updateHistoryButtons();
}

function applyHistorySnapshot(snap) {
  const before = sheets.map(function (sh) { return sh.name; }).join('\u0000') + '\u0001' + activeSheet;

  syncActiveSheet();
  const old = sheets;
  sheets = snap.sheets.map(function (sh) { return { name: sh.name, code: sh.code }; });

  // Show the sheet that changes (stay if it is among them); with another number
  // of sheets the sheet of the snapshot.
  let target = Math.min(snap.active, sheets.length - 1);
  let exact = true;
  if (old.length === sheets.length) {
    const changed = [];
    sheets.forEach(function (sh, i) {
      if (sh.name !== old[i].name || sh.code !== old[i].code) changed.push(i);
    });
    if (changed.length > 0 && changed.indexOf(activeSheet) < 0) target = changed[0];
    else if (changed.length > 0) target = activeSheet;
    exact = target === snap.active;
  }
  showSheet(target);
  try {
    if (exact) codeTextarea.setSelectionRange(snap.selStart, snap.selEnd);
    else codeTextarea.setSelectionRange(codeTextarea.value.length, codeTextarea.value.length);
  } catch (e) { /* ignore */ }

  updateImageStatus();
  scheduleAutosave();

  // The result is only refreshed if it would otherwise show another sheet
  // (or sheet set) than the editor; else like for typing: live update or Calculate.
  const after = sheets.map(function (sh) { return sh.name; }).join('\u0000') + '\u0001' + activeSheet;
  const live = document.getElementById('live-update-toggle');
  if (before !== after || (live && live.checked)) runLiveUpdate();
}

function undoHistory() {
  flushHistory();
  if (historyIndex <= 0) return;
  historyIndex--;
  applyHistorySnapshot(historySteps[historyIndex]);
  updateHistoryButtons();
}

function redoHistory() {
  flushHistory();
  if (historyIndex >= historySteps.length - 1) return;
  historyIndex++;
  applyHistorySnapshot(historySteps[historyIndex]);
  updateHistoryButtons();
}

// "Calculate" reloads the page; the history must survive that. It is kept in
// sessionStorage (this tab only) and only taken back if its current step is
// exactly the workbook that is in the editor now.
const HISTORY_STORAGE_KEY = 'engipad_history';

function saveHistoryForReload() {
  try {
    flushHistory();
    let steps = historySteps;
    let index = historyIndex;
    for (;;) {
      try {
        sessionStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify({ steps: steps, index: index }));
        return;
      } catch (e) {
        if (steps.length <= 1) throw e;
        const cut = Math.max(1, Math.floor(steps.length / 4)); // storage full: drop the oldest steps
        steps = steps.slice(cut);
        index = Math.max(0, index - cut);
      }
    }
  } catch (e) {
    try { sessionStorage.removeItem(HISTORY_STORAGE_KEY); } catch (e2) { /* ignore */ }
  }
}

function restoreHistoryAfterReload() {
  try {
    const raw = sessionStorage.getItem(HISTORY_STORAGE_KEY);
    sessionStorage.removeItem(HISTORY_STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (!data || !Array.isArray(data.steps) || typeof data.index !== 'number') return;
    if (data.index < 0 || data.index >= data.steps.length) return;
    if (!sameWorkbook(data.steps[data.index], takeHistorySnapshot())) return;
    historySteps = data.steps.slice(-HISTORY_LIMIT);
    historyIndex = data.index - (data.steps.length - historySteps.length);
    updateHistoryButtons();
  } catch (e) { /* unusable: start with an empty history */ }
}

function initHistory() {
  resetHistory();
  restoreHistoryAfterReload();
  window.addEventListener('pagehide', saveHistoryForReload);
  window.addEventListener('beforeunload', saveHistoryForReload);

  codeTextarea.addEventListener('beforeinput', function (e) {
    const type = e.inputType || '';
    // Undo/redo of the system (keyboard of a tablet, shake, context menu): ours instead.
    if (type === 'historyUndo') { e.preventDefault(); undoHistory(); return; }
    if (type === 'historyRedo') { e.preventDefault(); redoHistory(); return; }

    const kind = type.indexOf('delete') === 0 ? 'delete' : 'insert';
    historyOwnStep = HISTORY_OWN_STEP_TYPES.indexOf(type) >= 0;
    if (historyOwnStep || (historyKind !== null && kind !== historyKind)) flushHistory();
    historyKind = kind;
  });

  codeTextarea.addEventListener('input', function () {
    syncActiveSheet();
    historyPending = true;
    if (historyOwnStep) {
      historyOwnStep = false;
      commitHistory();
      historyKind = null;
    } else {
      scheduleHistoryStep();
    }
  });

  codeTextarea.addEventListener('compositionstart', function () { historyComposing = true; });
  codeTextarea.addEventListener('compositionend', function () { historyComposing = false; });

  codeTextarea.addEventListener('keydown', function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.isComposing) return;
    const key = (e.key || '').toLowerCase();
    if (key === 'z' && !e.shiftKey) { e.preventDefault(); undoHistory(); }
    else if ((key === 'z' && e.shiftKey) || key === 'y') { e.preventDefault(); redoHistory(); }
  });

  [['undo-btn', undoHistory], ['redo-btn', redoHistory]].forEach(function (entry) {
    const btn = document.getElementById(entry[0]);
    if (!btn) return;
    // A tap on the button must not take the focus (and the on-screen keyboard) from the editor.
    btn.addEventListener('mousedown', function (e) { e.preventDefault(); });
    btn.addEventListener('click', entry[1]);
  });
}

// --- Quit button (only rendered when the server runs locally, see
// app.py /shutdown): stops the server process, then replaces the page by
// a short notice (a script can't reliably close a tab it didn't open).
function initExitButton() {
  const btn = document.getElementById('exit-btn');
  if (!btn) return;

  btn.addEventListener('click', function () {
    if (!window.confirm('Quit EngiPad? The local server will be stopped.')) return;
    saveAutosave(false);

    fetch('/shutdown', {
      method: 'POST',
      headers: { 'X-Shutdown-Token': btn.getAttribute('data-token') || '' },
    })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        document.body.innerHTML =
          '<div style="font-family: sans-serif; text-align: center; margin-top: 20vh;">' +
          '<h2>EngiPad has been stopped.</h2>' +
          '<p>You can close this tab now.</p></div>';
        window.close(); // only works for tabs opened by a script, harmless otherwise
      })
      .catch(function () {
        window.alert('Could not stop EngiPad (the server may already be stopped).');
      });
  });
}

loadIcons();
initDropdowns();
initAutosave();
initSheets();
initPictures();
initHistory();
initLiveUpdate();
initExitButton();
