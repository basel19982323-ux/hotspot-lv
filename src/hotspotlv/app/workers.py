"""Run engine calls off the GUI thread.

    button click ──► Worker(fn, **kwargs).start() ──► fn runs in a QThread
                                                        │ progress(done, total, label)
                                                        ▼
                                              finished(result) / failed(message)

Cancel is cooperative (review of 22 Oct 2026). A Python thread cannot be stopped
safely from outside: the first version called QThread.terminate() (Windows
TerminateThread), which killed the thread while it held the interpreter's lock or
the C heap lock — the whole app froze on its next allocation, measured on the very
first cancel of a numpy/pandas run. Now:

  * cancel() marks the worker cancelled and returns at once; from then on the
    worker reports nothing (no finished_ok, no failed), so the panel is free;
  * a run stops at its next checkpoint: the progress callback (between the runs of a
    sweep, spread, fixes or fleet) and the engine's `checkpoint` (every 50 power flows
    inside a case, ≈ 0.3 s) raise RunCancelled — a cancelled case stops within about a
    second instead of computing for minutes in the background next to the new run
    (round-2 code review: three cancelled runs made the fourth 3.8 × slower);
  * an own-station assessment (about a second) has no checkpoint: it finishes and its
    result is dropped;
  * a started worker stays referenced until its thread ends (a QThread destroyed
    while running aborts the process); if the window closes before that,
    `any_running()` tells the entry point to leave with `hard_exit`.
"""

from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QThread, Signal


class RunCancelled(BaseException):
    """Raised from a progress checkpoint of a cancelled run. A BaseException, not an Exception, so an engine loop
    that shields itself from a broken progress bar (fleet.screen_fleet: `except Exception: pass`) still stops."""


_LIVE: set["Worker"] = set()


def any_running() -> bool:
    """True while a worker thread — cancelled or not — is still computing."""
    return any(w.isRunning() for w in list(_LIVE))


def wait_all(ms: int) -> bool:
    """Wait up to `ms` milliseconds in all for every worker thread to end. True when none is left running."""
    import time
    deadline = time.monotonic() + ms / 1000
    for w in list(_LIVE):
        w.wait(max(0, int((deadline - time.monotonic()) * 1000)))
    return not any_running()


def hard_exit(code: int) -> None:
    """Leave the process now with `code`, running no destructors. On Windows os._exit still runs the DLLs' unload
    routines and Qt's crashed there (access violation, measured); TerminateProcess on the own process does not."""
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.TerminateProcess(ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.c_uint(code & 0xFFFFFFFF))
    os._exit(code)


class Worker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    cancelled = Signal()

    def __init__(self, fn, *args, with_progress: bool = False, with_checkpoint: bool = False, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self._cancel_requested = False
        if with_progress:
            self._kwargs["progress"] = self._checkpoint
        if with_checkpoint:                             # the engine's own checkpoint inside a case (no arguments)
            self._kwargs["checkpoint"] = self._stop_if_cancelled
        self.finished.connect(self._forget)            # QThread.finished: the thread has ended

    def _checkpoint(self, done, total, label):
        self._stop_if_cancelled()
        self.progress.emit(int(done), int(total), str(label))

    def _stop_if_cancelled(self):
        if self._cancel_requested:
            raise RunCancelled()

    def start(self, *args):
        _LIVE.add(self)                                 # kept alive until the thread ends, cancelled or not
        super().start(*args)

    def _forget(self):
        _LIVE.discard(self)

    def is_cancelled(self) -> bool:
        return self._cancel_requested

    def cancel(self):
        """Ask the run to stop and stop reporting. Returns at once; never kills the thread."""
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self.cancelled.emit()

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except RunCancelled:
            return
        except Exception as e:  # shown to the user, never swallowed — unless the user already cancelled the run
            if not self._cancel_requested:
                self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=3)}")
            return
        if not self._cancel_requested:
            self.finished_ok.emit(result)
