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

## Manual build

The build must bundle `templates/` and `static/`, otherwise the UI is unstyled or missing.

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
