"""Run engine calls off the GUI thread.

    button click ──► Worker(fn, **kwargs).start() ──► fn runs in a QThread
                                                        │ progress(done, total, label)
                                                        ▼
                                              finished(result) / failed(message)
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, fn, *args, with_progress: bool = False, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs
        if with_progress:
            self._kwargs["progress"] = lambda done, total, label: self.progress.emit(int(done), int(total), str(label))

    def run(self):
        try:
            self.finished_ok.emit(self._fn(*self._args, **self._kwargs))
        except Exception as e:  # shown to the user, never swallowed
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=3)}")
