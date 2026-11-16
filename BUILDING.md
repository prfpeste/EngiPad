# Building a standalone binary

Ready-made downloads are linked in the [README](README.md#download). This page is for building them yourself with [PyInstaller](https://pyinstaller.org). All commands are run from the project root unless noted.

## Linux / macOS: single executable

```bash
./build.sh
```

Creates a virtual environment, installs `pyinstaller` and the runtime requirements, runs PyInstaller, and leaves a single `EngiPad` executable in the project root.

## Linux: AppImage

```bash
./build-appimage.sh
```

Builds `EngiPad-x86_64.AppImage` (about 51 MB). On first start it adds a menu entry and icon for the current user (disable with `ENGIPAD_NO_INTEGRATION=1`). An AppImage runs on the system it was built on and newer ones (glibc), so build on the oldest system you want to support.

## Windows: executable and installer

```bat
build-windows.bat console
```

Builds `dist\EngiPad\EngiPad.exe` with icon. Use the argument `console` for the first run so errors are visible; without it the exe has no console window. If [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed, `EngiPad.iss` is compiled too and produces `installer\EngiPad-Setup.exe` (per-user installation, no admin rights required). The version number is set in `EngiPad.iss` (`AppVersion`) and in `templates/index.html` – change both for a new release. The files are not code-signed, so Windows SmartScreen shows "unknown publisher".

**Distribute the installer (`EngiPad-Setup.exe`) or the complete `dist\EngiPad` folder, never `EngiPad.exe` alone.** The build is a folder build: the exe needs the `_internal` folder next to it (it contains `python3xx.dll`). A bare exe fails with "Failed to load Python DLL". Antivirus or AppLocker rules can cause the same message; then check whether `_internal\python3xx.dll` still exists.

## Manual build

The build must bundle `templates/` and `static/`, otherwise the UI is unstyled or missing. `static/vendor/` (MathJax and the fonts, about 2 MB) is part of `static/` and is bundled with it automatically; the built program then works without an internet connection.

```bash
pip install pyinstaller
```

Linux / macOS:

```bash
pyinstaller --name EngiPad --onefile \
  --add-data "templates:templates" \
  --add-data "static:static" \
  run.py
```

Windows (separator in `--add-data` is `;`):

```bat
pyinstaller ^
  --name EngiPad ^
  --onefile ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  run.py
```

The result is in `dist/` (`dist/EngiPad` or `dist\EngiPad.exe`).

## CoolProp

`requirements.txt` contains `coolprop==6.8.0`, so all build scripts install and bundle it without further options (PyInstaller picks the module up by itself). It adds about 25 MB to the unpacked program and about 8 MB to a compressed download. The version is pinned on purpose: 6.8.0 loads in about 0.2 s, CoolProp 8.0.0 needs almost 3 s, and the library is loaded for every calculation that uses `prop` or `haprop`. Check that a wheel of this version exists for the Python version you build with (`pip download coolprop==6.8.0 --no-deps`); if not, build with a Python version that has one.

## Updating the bundled MathJax and fonts

`static/vendor/` holds the files the page would otherwise load from a CDN:

- `mathjax/`: from the npm package `mathjax@3` (folder `es5`): `tex-chtml.js`, `input/tex/extensions/html.js`, `textmacros.js`, `unicode.js` and the folder `output/chtml/fonts/woff-v2/`. Keep the folder structure, MathJax looks for extensions and fonts relative to `tex-chtml.js`.
- `fonts/`: the `woff2` files of the subsets latin, latin-ext and greek from the npm packages `@fontsource/inter` (weights 400, 500, 600) and `@fontsource/jetbrains-mono` (400, 500), plus `fonts.css` with the matching `@font-face` rules.

`tests/test_offline_assets.py` checks that the files are complete and that the page loads nothing from another host.
