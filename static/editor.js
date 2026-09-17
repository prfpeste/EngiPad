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
    codeTextarea.value = e.target.result;
  };
  reader.readAsText(file, 'utf-8');
});

// --- Save file ---
function saveFile() {
  const text = codeTextarea.value || '';
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

function insertSnippet(snippet, cursorOffsetFromEnd) {
  codeTextarea.focus();

  const start = codeTextarea.selectionStart;
  const end = codeTextarea.selectionEnd;
  const text = codeTextarea.value;

  const before = text.slice(0, start);
  const after = text.slice(end);

  codeTextarea.value = before + snippet + after;

  const newEndPos = start + snippet.length;
  const cursorPos = newEndPos - (cursorOffsetFromEnd || 0);

  codeTextarea.selectionStart = codeTextarea.selectionEnd = cursorPos;
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
  if (saved && isDefaultExampleText(codeTextarea.value)) {
    codeTextarea.value = saved;
  }
}

function saveAutosave() {
  try {
    window.localStorage.setItem(AUTOSAVE_KEY, codeTextarea.value);
  } catch (e) {
    // localStorage nicht verfuegbar oder voll -- Autosave faellt in dem Fall
    // einfach aus, der Rest der App funktioniert unveraendert weiter.
  }
}

function initAutosave() {
  restoreAutosave();

  let debounceTimer = null;
  codeTextarea.addEventListener('input', function () {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(saveAutosave, AUTOSAVE_DEBOUNCE_MS);
  });

  // Zusaetzliches Sicherheitsnetz: falls der Reload ohne "input"-Event
  // dazwischen kommt (z.B. Klick direkt auf Moodles "Absenden und
  // beenden", ohne dass die Debounce-Zeit noch ablaufen konnte).
  window.addEventListener('beforeunload', saveAutosave);
}

loadIcons();
initDropdowns();
initAutosave();
