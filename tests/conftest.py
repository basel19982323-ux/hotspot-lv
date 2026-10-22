"""Shared Qt plumbing for the app tests (code review of 22 Oct 2026: green summaries followed by a crash at exit
or a hang).

* No modal dialog can block a run: a file dialog or a message box opened by a test raises instead of waiting
  for a click that never comes. A test that expects a dialog patches it itself (its patch wins).
* Every top-level widget a test creates is deleted when the test ends; module-scope fixtures delete theirs
  in their own teardown.
* After pytest has written its whole report, a run that imported Qt leaves with os._exit(<pytest's own exit
  status>). Interpreter shutdown with ~100 of pyqtgraph's parentless helper widgets still alive crashed
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

    def refuse(*args, **kwargs):
        raise AssertionError("a test opened a modal dialog — it would block the run; patch it in the test")

    for name in ("getSaveFileName", "getOpenFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, refuse)
    for name in ("exec", "warning", "question", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, refuse)
    yield


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


_exit_status = {"code": 0}


def pytest_sessionfinish(session, exitstatus):
    _exit_status["code"] = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    if "PySide6.QtWidgets" in sys.modules and os.environ.get("HOTSPOTLV_TEST_SOFT_EXIT") != "1":
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_exit_status["code"])
