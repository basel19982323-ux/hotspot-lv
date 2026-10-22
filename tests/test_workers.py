"""Cancel must never freeze the app (review of 22 Oct 2026): the first Worker stopped a running thread with
QThread.terminate(), and the whole process froze on the next allocation after cancelling a numpy/pandas run.
Cancel is now cooperative; these tests run real threads."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hotspotlv.app.workers import Worker, any_running, wait_all  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _busy(seconds):
    t0 = time.perf_counter()
    acc = 0.0
    while time.perf_counter() - t0 < seconds:
        acc += float(pd.DataFrame(np.random.rand(1000, 20)).rolling(24).mean().sum().sum())
    return acc


def test_cancel_returns_at_once_and_the_main_thread_keeps_working(app):
    """Catches: terminate() back in cancel() — this test then freezes instead of finishing in about 2 s."""
    got = []
    w = Worker(_busy, 1.5)
    w.finished_ok.connect(got.append)
    w.failed.connect(got.append)
    w.start()
    time.sleep(0.3)
    t = time.perf_counter()
    w.cancel()
    assert time.perf_counter() - t < 0.5
    assert pd.DataFrame(np.random.rand(300, 30)).describe().shape == (8, 30)       # the main thread allocates freely
    assert w.isRunning() and any_running()                                         # the run finishes on its own …
    assert wait_all(10_000) and not any_running()
    QCoreApplication.processEvents()
    assert got == []                                                               # … and reports nothing


def test_a_run_with_checkpoints_stops_at_the_next_one(app):
    """Catches: progress checkpoints that ignore cancel, or a cancelled run reported as failed. The loop shields
    itself from a broken progress bar like fleet.screen_fleet does — RunCancelled must get through anyway."""
    seen, failed = [], []

    def looped(progress):
        for i in range(1000):
            try:
                progress(i, 1000, f"step {i}")
            except Exception:                    # the fleet's guard: a broken progress bar must not stop it
                pass
            seen.append(i)
            time.sleep(0.01)
        return "finished"

    w = Worker(looped, with_progress=True)
    w.failed.connect(failed.append)
    w.start()
    time.sleep(0.2)
    w.cancel()
    t = time.perf_counter()
    assert w.wait(2000), "the run did not stop at its next checkpoint"
    assert time.perf_counter() - t < 0.5 and len(seen) < 100
    QCoreApplication.processEvents()
    assert failed == []
