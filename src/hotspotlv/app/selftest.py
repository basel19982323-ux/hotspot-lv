"""`hotspot-lv --self-test <folder>`: check an installed or frozen build end to end, without anyone at the keyboard.

The window is built as usual but never shown on the screen (WA_DontShowOnScreen); the steps run on the real event
loop, with the real worker threads, and the run ends through the normal close path:

    station   the shipped example assessed, the card drawn            → station.png
    pdf       Export PDF's writer on that result                     → station_card.pdf
    fleet     the example fleet screened, first row selected         → fleet.png
    scenario  a small case run in a worker thread to the end         → scenario.png
    cancel    a full-power-flow case started and cancelled: its thread must end within seconds
    close     an assessment started, the window closed over it ("Stop it and close?" → Yes):
              the process must leave cleanly (the hard exit of the entry point)

Everything goes to <folder>/self-test.txt — a frozen windowed program has no console. Exit code 0 when every step
passed, 1 otherwise. Written for the packaging review of 22 Oct 2026: a test of the source tree says nothing about
whether a frozen build found its data files, its Qt plugins and its threads.
"""

from __future__ import annotations

import platform
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from hotspotlv import __version__

POLL_MS = 200


class SelfTest:
    def __init__(self, app: QApplication, win, out: Path):
        self.app, self.win, self.out = app, win, out
        self.lines: list[str] = []
        self.failed = False
        self.t0 = time.perf_counter()
        out.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- log
    def log(self, text: str) -> None:
        self.lines.append(text)
        (self.out / "self-test.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    def result(self, step: str, ok: bool, detail: str) -> None:
        self.failed |= not ok
        self.log(f"{'PASS' if ok else 'FAIL'} {step}: {detail}")

    def guarded(self, step: str, fn) -> bool:
        try:
            fn()
            return True
        except Exception as e:  # every failure is written down, then the run goes on to the end
            self.result(step, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=6)}")
            return False

    def grab(self, name: str) -> None:
        for _ in range(5):                                  # let every layout settle before the picture
            self.app.processEvents()
        self.win.grab().save(str(self.out / name))

    def wait_until(self, cond, timeout_s: float, then, on_timeout) -> None:
        start = time.perf_counter()

        def poll():
            try:
                done = cond()
            except Exception:
                done = False
            if done:
                then(time.perf_counter() - start)
            elif time.perf_counter() - start > timeout_s:
                on_timeout(time.perf_counter() - start)
            else:
                QTimer.singleShot(POLL_MS, poll)
        QTimer.singleShot(POLL_MS, poll)

    # ---------------------------------------------------------------- steps
    def start(self) -> None:
        from hotspotlv.paths import cache_dir, package_data
        self.log(f"Hotspot LV v{__version__} self-test — {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"frozen: {getattr(sys, 'frozen', False)} · executable: {sys.executable}")
        self.log(f"python {platform.python_version()} · {platform.platform()} · Qt platform {self.app.platformName()}")
        self.log(f"package data: {package_data()} (exists: {package_data().exists()}) · cache: {cache_dir()}")
        self.win.resize(1400, 900)
        self.step_station()

    def step_station(self) -> None:
        from hotspotlv.app.report import write_station_card
        from hotspotlv.app.widgets.station_panel import EXAMPLE, _assess
        st = self.win.station

        def run():
            t = time.perf_counter()
            st.path.setText(str(EXAMPLE))
            st._pending_inputs = st._inputs()                  # what run() records before its worker starts
            st._done(_assess(EXAMPLE, 500, "meters", "Europe/Stockholm", "start", 96190, True, "kiosk", 0, 0, 0))
            self.win.tabs.setCurrentIndex(0)
            self.grab("station.png")
            ok = st.result is not None and st.export_btn.isEnabled()
            self.result("station", ok, f"{st.result.verdict.headline() if st.result else 'no result'} "
                                       f"({time.perf_counter() - t:.1f} s)")
            from PySide6.QtWidgets import QLabel                # the card's rows at their full height (21 Oct rule)
            short = [w.text() for col in (st.card._paper[0], st.card._really[0]) for w in col.findChildren(QLabel)
                     if w.isVisible() and w.height() < w.sizeHint().height() - 1]
            self.result("card layout", not short, "every row of the verdict card at full height" if not short
                        else f"{len(short)} squeezed rows, e.g. {short[:3]}")
            t = time.perf_counter()
            pdf = write_station_card(st.result, st._amb, self.out / "station_card.pdf", source_name=EXAMPLE.name)
            size = pdf.stat().st_size
            self.result("pdf", pdf.read_bytes()[:5] == b"%PDF-" and size > 100_000,
                        f"{pdf.name}, {size // 1024} kB ({time.perf_counter() - t:.1f} s)")
        self.guarded("station", run)
        QTimer.singleShot(0, self.step_fleet)

    def step_fleet(self) -> None:
        from hotspotlv.app.widgets.fleet_panel import _screen
        from hotspotlv.paths import package_data
        fl = self.win.fleet

        def run():
            t = time.perf_counter()
            fl._done(_screen(package_data() / "examples" / "fleet_index.csv", progress=None))
            fl.view.selectRow(0)
            self.win.tabs.setCurrentIndex(1)
            self.grab("fleet.png")
            n = 0 if fl.table_df is None else len(fl.table_df)
            self.result("fleet", n == 3, f"{n} stations ranked ({time.perf_counter() - t:.1f} s)")
        self.guarded("fleet", run)
        QTimer.singleShot(0, self.step_scenario)

    def step_scenario(self) -> None:
        sp = self.win.scenario
        self.win.tabs.setCurrentIndex(2)

        def start():
            sp.houses.setValue(6); sp.feeders.setValue(2); sp.fast.setChecked(True)
            sp.run()
        if not self.guarded("scenario", start):
            QTimer.singleShot(0, self.step_cancel); return

        def done(secs):
            self.grab("scenario.png")
            ok = sp.result is not None
            self.result("scenario", ok, (f"{sp.result.verdict.headline()} — a worker thread, {secs:.1f} s"
                                         if ok else "the run ended without a result"))
            QTimer.singleShot(0, self.step_cancel)

        def timeout(secs):
            self.result("scenario", False, f"no result after {secs:.0f} s")
            QTimer.singleShot(0, self.step_cancel)
        self.wait_until(lambda: not sp.is_running() and sp.result is not None, 300, done, timeout)

    def step_cancel(self) -> None:
        from hotspotlv.app.workers import any_running
        sp = self.win.scenario

        def start():
            sp.result = None
            sp.fast.setChecked(False)                          # a full power flow: about a minute if not cancelled
            sp.run()
        if not self.guarded("cancel", start):
            QTimer.singleShot(0, self.step_close); return

        def cancel():
            t = time.perf_counter()
            sp.cancel()

            def stopped(secs):
                self.result("cancel", True, f"Cancel returned in {time.perf_counter() - t - secs:.2f} s, "
                                            f"the thread ended {secs:.1f} s later; the window stayed responsive")
                QTimer.singleShot(0, self.step_close)

            def still(secs):
                self.result("cancel", False, f"the cancelled thread was still running after {secs:.0f} s")
                QTimer.singleShot(0, self.step_close)
            self.wait_until(lambda: not any_running(), 20, stopped, still)
        QTimer.singleShot(1500, cancel)

    def step_close(self) -> None:
        from hotspotlv.app.widgets.station_panel import EXAMPLE
        from hotspotlv.app.workers import any_running
        st = self.win.station

        def start():
            QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)   # "Stop it and close?" — yes
            self.win.tabs.setCurrentIndex(0)
            st.path.setText(str(EXAMPLE))
            st.run()
        if not self.guarded("close", start):
            self.finish(); return

        def close():
            running = any_running()
            self.log(f"{'PASS' if running else 'INFO'} close: closing the window over "
                     f"{'a running assessment' if running else 'no running thread (it finished first)'}; "
                     "the process must now end cleanly")
            self.finish()
        QTimer.singleShot(100, close)

    def finish(self) -> None:
        self.log(f"{'FAILED' if self.failed else 'ALL PASSED'} in {time.perf_counter() - self.t0:.0f} s")
        if self.failed:
            self.app.exit(1)
        else:
            self.win.close()                                    # the normal path: closeEvent → quit → main()


def install(app: QApplication, win, out: Path) -> SelfTest:
    """Hide the window from the screen and schedule the self-test on the event loop."""
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    test = SelfTest(app, win, out)
    QTimer.singleShot(0, test.start)
    return test
