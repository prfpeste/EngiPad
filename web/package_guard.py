"""Import guard of the web version for the packages that are loaded in the
background (NumPy, matplotlib, CoolProp).

The web version is ready to calculate before these packages are there
(they have WebAssembly parts and cannot be part of the start-up memory
snapshot, see WEB_VERSION.md). If a calculation needs one of them
meanwhile, its import must not simply fail -- the user would see
"No module named 'numpy'" in a result line. Instead the guard notes which
package group was asked for and raises the usual ImportError; the caller
(web/engipad_web.py) then reports "need: <group>" instead of a page, the
web worker loads that group first and calculates again.

Only the web version installs the guard; the server and the desktop
version never import this module.
"""
import sys

# Top-level module name -> package group the web worker loads for it
# (a group includes what it depends on, see static/web/worker.js). Only
# packages with WebAssembly parts; the pure-Python helpers of matplotlib
# (cycler, fontTools, pyparsing, ...) are always there.
GROUPS = {
    "numpy": "numpy",
    "matplotlib": "matplotlib",
    "mpl_toolkits": "matplotlib",
    "PIL": "matplotlib",
    "kiwisolver": "matplotlib",
    "contourpy": "matplotlib",
    "CoolProp": "coolprop",
}

_available = set()
_requested = []


class _GuardFinder:
    """Meta path finder that stops imports of groups not loaded yet."""

    def find_spec(self, fullname, path=None, target=None):
        group = GROUPS.get(fullname.partition(".")[0])
        if group is None or group in _available:
            return None
        if group not in _requested:
            _requested.append(group)
        raise ModuleNotFoundError(
            f"No module named '{fullname}' (still loading)", name=fullname
        )

    def invalidate_caches(self):
        pass


_finder = _GuardFinder()


def install():
    """Puts the guard in front of all other finders (once)."""
    if _finder not in sys.meta_path:
        sys.meta_path.insert(0, _finder)


def uninstall():
    if _finder in sys.meta_path:
        sys.meta_path.remove(_finder)


def mark_available(group):
    """The web worker has installed `group`; its imports work from now on."""
    if group not in set(GROUPS.values()):
        raise ValueError(f"unknown package group {group!r}")
    _available.add(group)


def is_available(group):
    return group in _available


def start_request():
    """Forget the groups asked for by an earlier calculation."""
    _requested.clear()


def requested():
    """Groups asked for since start_request(), in the order of the requests."""
    return list(_requested)
