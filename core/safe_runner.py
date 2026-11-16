"""Runs a function in a separate process and hard-kills it if it takes
too long -- protects the server from overload caused by (intentionally
or accidentally) very expensive input, e.g. huge symbolic integrals,
tall power towers, or large matrices.

Deliberately a separate PROCESS rather than a thread: a hanging/CPU-
heavy SymPy call can't be cleanly killed out of a thread in Python --
the thread would keep running in the background and consuming CPU even
after the HTTP request has long since been answered with an error. A
child process, on the other hand, can actually be terminated via
terminate()/kill() and gives its resources back -- this protects the
server itself, not just the individual response.
"""

from __future__ import annotations

import multiprocessing as mp
from multiprocessing.connection import wait

DEFAULT_TIMEOUT_SECONDS = 10


def _worker(conn, func, args: tuple, kwargs: dict) -> None:
    try:
        try:
            message = ("ok", func(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 -- error text is passed through
            message = ("error", str(exc))
        try:
            conn.send(message)
        except Exception as exc:  # noqa: BLE001 -- e.g. result not picklable
            conn.send(("error", f"Result could not be returned: {exc}"))
    finally:
        conn.close()


def run_with_timeout(func, args: tuple = (), kwargs: dict | None = None,
                      timeout: float = DEFAULT_TIMEOUT_SECONDS):
    """Runs func(*args, **kwargs) in a child process.

    Returns a tuple (status, value):
        ("ok", result)     -- ran normally
        ("error", message) -- an exception was raised in the child process
        ("timeout", None)  -- ran longer than `timeout` seconds, process
                               was killed

    func AND its arguments/return value must be picklable (true for
    core.engine.evaluate_code(): only strings/numbers/lists/dicts, no
    SymPy objects in the return value).

    The result is READ while the child is still running, and only then
    the child is joined. The other order (join first, read afterwards)
    blocks for any result larger than the pipe buffer (about 64 KB on
    Linux): the child cannot finish writing before somebody reads, so it
    never exits and the caller reports a timeout although the
    computation was done long ago -- that is what happened with three or
    more plots in one sheet.
    """
    kwargs = kwargs or {}
    receiver, sender = mp.Pipe(duplex=False)
    process = mp.Process(target=_worker, args=(sender, func, args, kwargs))
    process.start()
    # Only the child writes; without closing our copy, a dead child would
    # not be noticed as "end of data".
    sender.close()

    try:
        # Wakes up when a result is there, when the child died, or after
        # the timeout -- whichever comes first.
        wait([receiver, process.sentinel], timeout)

        if receiver.poll():
            try:
                result = receiver.recv()
            except (EOFError, OSError):
                result = None
            except Exception as exc:  # noqa: BLE001 -- broken pickle etc.
                result = ("error", f"Result could not be read: {exc}")
            if result is not None:
                process.join(2)
                return result

        if process.is_alive():
            return "timeout", None

        # Process ended without sending anything (e.g. an OOM-kill or a
        # crash inside a C extension) -- treat this as a clear error
        # instead of surfacing a cryptic exception.
        return "error", "Computation ended unexpectedly (e.g. memory limit)."
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join()
        receiver.close()
