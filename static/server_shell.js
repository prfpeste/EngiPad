// EngiPad shell of the server and desktop version (Flask, app.py).
//
// The shell is the part of the page that differs between the versions;
// static/editor.js is the same everywhere and only calls
//
//   EngiPadShell.calculate(form, signal) -> Promise<HTML of the whole page>
//
// (live update). The web version has its own shell (static/web/web_shell.js).
// Here: calculations are POSTed to the server, the Calculate button posts
// the form normally (whole page reloads), the LaTeX export posts it to
// /export/latex (the browser downloads the answer), and the local version
// has a Quit button.
(function () {
  'use strict';

  window.EngiPadShell = {
    calculate: function (form, signal) {
      return fetch('/', {
        method: 'POST',
        body: new URLSearchParams(new FormData(form)),
        signal: signal,
      }).then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.text();
      });
    },
  };

  // The LaTeX export needs the picture data; every other submit carries
  // only the names (field stays empty).
  function initExportImages() {
    const form = document.getElementById('calc-form');
    if (!form) return;
    form.addEventListener('submit', function (e) {
      const field = document.getElementById('export-images-field');
      if (!field) return;
      const submitter = e.submitter;
      if (submitter && submitter.getAttribute('formaction') === '/export/latex') {
        field.value = window.EngiPadEditor.exportImagesJson();
        setTimeout(function () { field.value = ''; }, 0);
      } else {
        field.value = '';
      }
    });
  }

  // Quit button (only rendered when the server runs locally, see app.py
  // /shutdown): stops the server process, then replaces the page by a
  // short notice (a script can't reliably close a tab it didn't open).
  function initExitButton() {
    const btn = document.getElementById('exit-btn');
    if (!btn) return;

    btn.addEventListener('click', function () {
      if (!window.confirm('Quit EngiPad? The local server will be stopped.')) return;
      window.EngiPadEditor.saveAutosave(false);

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

  // This file is loaded before editor.js; DOMContentLoaded comes after
  // both have run, so window.EngiPadEditor exists then.
  document.addEventListener('DOMContentLoaded', function () {
    initExportImages();
    initExitButton();
  });
})();
