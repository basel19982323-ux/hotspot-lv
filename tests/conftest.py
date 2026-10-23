"""Shared Qt plumbing for the app tests (code review of 22 Oct 2026: green summaries followed by a crash at exit
or a hang).

* No modal dialog can block a run: a file dialog or a message box opened by a test raises instead of waiting
  for a click that never comes. A test that expects a dialog patches it itself (its patch wins).
* Every top-level widget a test creates is deleted when the test ends; module-scope fixtures delete theirs
  in their own teardown.
* After pytest has written its whole report, a run that imported Qt leaves with hard_exit(<pytest's own exit
  status>) — TerminateProcess on Windows, because even os._exit ran Qt's DLL unload code and crashed there. Interpreter shutdown with ~100 of pyqtgraph's parentless helper widgets still alive crashed
  (0xC0000409) or hung in about half of the runs, after a green summary — the problem pyqtgraph documents
  ("PyQt and PySide both are prone to crashing on exit") and works around the same way in pyqtgraph.exit().
  The exit code is always pytest's verdict; set HOTSPOTLV_TEST_SOFT_EXIT=1 to see the plain shutdown.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def _qt():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return None
    return QApplication


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    if _qt() is None:
        yield
        return
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    opened = []

    def refuse(*args, **kwargs):
        opened.append(args[1:3] if len(args) > 2 else args)
        raise AssertionError("a test opened a modal dialog — it would block the run; patch it in the test")

    for name in ("getSaveFileName", "getOpenFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, refuse)
    for name in ("exec", "warning", "question", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, refuse)
    yield
    # inside a Qt slot (or an app-level except) the AssertionError is swallowed: fail the test here instead
    assert not opened, f"a modal dialog was opened during the test: {opened}"


@pytest.fixture(autouse=True)
def _delete_new_widgets():
    QApplication = _qt()
    if QApplication is None or QApplication.instance() is None:
        before = None
    else:
        before = {id(w) for w in QApplication.topLevelWidgets()}
    yield
    if QApplication is None or QApplication.instance() is None:
        return
    delete_widgets([w for w in QApplication.topLevelWidgets() if before is None or id(w) not in before])


def delete_widgets(widgets) -> None:
    """Close, schedule deletion and flush the deferred deletes now (without an event loop they never run).
    deleteLater, not shiboken6.delete: a widget may already have a delete pending (render_charts), and an
    immediate delete followed by that event crashed the process (measured)."""
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    for w in widgets:
        # only this project's own parentless windows. topLevelWidgets() also lists child popups (a combo box's
        # drop-down, deleted with its parent) and pyqtgraph's hidden helpers (each plot's menus and options
        # panel, still used when the plot is destroyed); deleting those here crashed the process (measured).
        if (shiboken6.isValid(w) and w.parentWidget() is None
                and type(w).__module__.startswith("hotspotlv.")):
            w.close()
            w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    # The Python side of the deleted widgets (pyqtgraph's helpers, their plots) is collected now, at a fixed point,
    # and whatever deletes that posts is flushed — not at a random later moment inside another test's event loop
    # (23 Oct 2026: an access violation in the self-test's unit tests, only in full-suite runs on Python 3.14.9,
    # depending on when the garbage collector happened to run).
    import gc
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


_exit_status = {"code": 0}


def pytest_sessionfinish(session, exitstatus):
    _exit_status["code"] = int(exitstatus)


@pytest.hookimpl(tryfirst=True)                   # the report is complete; leave before the teardown
def pytest_unconfigure(config):
    if "PySide6.QtWidgets" in sys.modules and os.environ.get("HOTSPOTLV_TEST_SOFT_EXIT") != "1":
        from hotspotlv.app.workers import hard_exit
        hard_exit(_exit_status["code"])
