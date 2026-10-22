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


def test_a_cancelled_case_stops_inside_its_power_flows(app):
    """Catches: a cancelled single case computing on in the background next to the new run (round-2 code review:
    three cancelled runs made the fourth 3.8 × slower). The engine's checkpoint stops it within seconds."""
    from hotspotlv.neighbourhood import Scenario
    from hotspotlv.study import Case, run_case
    from hotspotlv.weather import get_weather
    case = Case(name="t", scenario=Scenario(n_houses=6, ev_share=0.5, pv_share=0.5), n_feeders=2)
    got = []
    w = Worker(run_case, case, get_weather(2023), fast=False, with_checkpoint=True)    # full power flow: ~a minute
    w.finished_ok.connect(got.append)
    w.failed.connect(got.append)
    w.start()
    time.sleep(2.0)
    assert w.isRunning()
    w.cancel()
    t = time.perf_counter()
    assert w.wait(5000), "the cancelled case did not stop at a checkpoint"
    assert time.perf_counter() - t < 3
    QCoreApplication.processEvents()
    assert got == []


def test_a_cancelled_run_that_raises_reports_nothing_and_is_released(app):
    """Catches: an error reported after Cancel, and finished workers that never leave the live set."""
    from hotspotlv.app import workers

    def fails_late():
        time.sleep(0.3)
        raise ValueError("boom")

    failed = []
    w = Worker(fails_late)
    w.failed.connect(failed.append)
    w.start()
    w.cancel()
    assert w.wait(3000)
    QCoreApplication.processEvents()
    assert failed == []
    assert w not in workers._LIVE                                    # released once its thread ended


CANCEL_SCRIPT = """
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import numpy as np, pandas as pd
from PySide6.QtWidgets import QApplication
app = QApplication([])
from hotspotlv.app.workers import Worker, wait_all

def busy():
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 3:
        pd.DataFrame(np.random.rand(1000, 20)).rolling(24).mean()

for i in range(4):
    w = Worker(busy); w.start(); time.sleep(0.2 + 0.1 * i); w.cancel()
    pd.DataFrame(np.random.rand(300, 30)).describe()
print("ALIVE", flush=True)
wait_all(10000)
from hotspotlv.app.workers import hard_exit
hard_exit(0)
"""


def test_cancel_cannot_freeze_the_process(tmp_path):
    """The freeze itself, in a separate process with a timeout: QThread.terminate() back in cancel() hung the whole
    test run before; now it fails here within a minute."""
    import subprocess
    import sys
    script = tmp_path / "cancel.py"
    script.write_text(CANCEL_SCRIPT, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0 and "ALIVE" in r.stdout, r.stderr[-1500:]


APP_SCRIPT = """
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
import hotspotlv.app.__main__ as entry
from hotspotlv.app.main_window import MainWindow
mode = sys.argv[1]
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)     # "Stop it and close?" — yes
show = MainWindow.show

def driven_show(self):
    show(self)
    def start():
        if mode == "station":                      # an assessment has no checkpoint: the thread is alive at exit
            st = self.station
            import time
            import hotspotlv.app.widgets.station_panel as spm
            real_assess = spm._assess
            spm._assess = lambda *a: (time.sleep(5), real_assess(*a))[1]   # certainly still running at shutdown
            from hotspotlv.app.widgets.station_panel import EXAMPLE
            st.path.setText(str(EXAMPLE)); st.run()
            def finish_station():
                print("CLOSING", flush=True)
                self.close()
            QTimer.singleShot(200, finish_station)
            return
        sp = self.scenario
        sp.houses.setValue(6); sp.feeders.setValue(2); sp.fast.setChecked(False)   # a full run: ~a minute
        sp.run()
        def finish():
            assert sp.is_running(), "the run should still be going"
            if mode == "cancel":
                sp.cancel()
            print("CLOSING", flush=True)
            self.close()
        QTimer.singleShot(1500, finish)
    QTimer.singleShot(300, start)

import hotspotlv.app.workers as workers_module
real_any_running = workers_module.any_running
def reporting_any_running():
    alive = real_any_running()
    print(f"ANY_RUNNING {alive}", flush=True)
    return alive
workers_module.any_running = reporting_any_running

MainWindow.show = driven_show
sys.exit(entry.main())
"""


@pytest.mark.parametrize("mode", ["cancel", "close", "station"])
def test_the_app_closes_cleanly_over_a_running_case(tmp_path, mode):
    """The real entry point: start a full-power-flow case, then Cancel and close, or close at once ("Stop it and
    close?" → Yes). Before: os._exit crashed in Windows' DLL unload in 11 of 14 closes; without a hard exit a
    QThread destroyed while running aborts. Exit code 0, within seconds, every time."""
    import subprocess
    import sys
    script = tmp_path / "app.py"
    script.write_text(APP_SCRIPT, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    r = subprocess.run([sys.executable, str(script), mode], capture_output=True, text=True, timeout=120, env=env)
    assert "CLOSING" in r.stdout, r.stderr[-1500:]
    assert r.returncode == 0, f"exit {r.returncode:#x}: {r.stderr[-1500:]}"
    if mode == "station":                          # the hard exit was really reached (round 3, R3)
        assert "ANY_RUNNING True" in r.stdout, r.stdout[-500:]
