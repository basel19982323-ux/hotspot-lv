"""`hotspot-lv --self-test <folder>`: check an installed or frozen build end to end, without anyone at the keyboard.

The window is built as usual but never shown on the screen (WA_DontShowOnScreen); the steps run on the real event
loop, with the real worker threads, and the run ends through the normal close path:

    platform  a frozen build must run on Qt's real Windows platform (not "offscreen", which has no fonts)
    https     the TLS stack a station download needs: ssl, urllib's HTTPS handler, CA certificates — no network used
    station   the shipped example assessed, the card drawn            → station.png
    pdf       Export PDF's writer on that result: pages, lossless chart images, embedded fonts → station_card.pdf
    fleet     the example fleet screened, first row selected; an error row is a failure → fleet.png
    scenario  a small case run in a worker thread to the end         → scenario.png
    sweep     two values of the houses knob, worker thread           → sweep.png
    spread    the fewest random draws, worker thread                 → spread.png
    fixes     the five-run fix comparison, worker thread              → fixes.png
    powerflow one full (not fast) power flow of the app's own feeder, to convergence
    cancel    a full-power-flow case started and cancelled: its thread must end within seconds
    close     an assessment started, the window closed over it ("Stop it and close?" → Yes):
              the process must leave cleanly (the hard exit of the entry point)

Every step in STEPS must log PASS, or the run fails — a step that was skipped is a failure, not a silence. No dialog
can block it: every message box is written to the log as a failure and closed, and a step that is waiting for a
worker ends at once when a failure dialog appears (packaging reviews of 22 Oct 2026). A watchdog ends the process
with exit code 1 if the whole run takes longer than TOTAL_LIMIT_S.

Everything goes to <folder>/self-test.txt — a frozen windowed program has no console. Exit code 0 when every step
passed, 1 otherwise. Written for the packaging review of 22 Oct 2026: a test of the source tree says nothing about
whether a frozen build found its data files, its Qt plugins and its threads.
"""

from __future__ import annotations

import platform
import re
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from hotspotlv import __version__

POLL_MS = 200
STEP_TIMEOUT_S = 240
TOTAL_LIMIT_S = 900                 # the whole run; a healthy build needs about 80 s
STEPS = ("platform", "https", "station", "card layout", "pdf", "fleet", "scenario", "sweep", "spread", "fixes",
         "powerflow", "cancel", "close")


class SelfTest:
    def __init__(self, app: QApplication, win, out: Path):
        self.app, self.win, self.out = app, win, out
        self.lines: list[str] = []
        self.failed = False
        self.passed: set[str] = set()
        self.dialogs = 0                                    # failure dialogs so far (a waiting step watches this)
        self.t0 = time.perf_counter()
        out.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- log
    def log(self, text: str) -> None:
        self.lines.append(text)
        (self.out / "self-test.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    def result(self, step: str, ok: bool, detail: str) -> None:
        self.failed |= not ok
        if ok:
            self.passed.add(step)
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

    def go(self, fn, delay_ms: int = 0) -> None:
        """Run fn on the event loop. An exception in a step's own code (outside its guarded part) fails the run
        and ends it at once, instead of stopping the chain until the watchdog fires."""
        def run():
            try:
                fn()
            except Exception as e:
                self.result(getattr(fn, "__name__", "step").removeprefix("step_"), False,
                            f"{type(e).__name__}: {e}" + chr(10) + traceback.format_exc(limit=6))
                if fn != self.finish:
                    self.finish()
                else:
                    self.app.exit(1)
        QTimer.singleShot(delay_ms, run)

    def wait_until(self, step: str, cond, timeout_s: float, then, on_timeout, next_step) -> None:
        """Poll cond() on the event loop; then(secs) when it holds, on_timeout(secs) after timeout_s. A failure
        dialog during the wait ends it at once (a failed worker never sets its result). If a callback itself
        raises, the step fails and the run goes on with next_step — a broken callback must not end the chain."""
        start, dialogs = time.perf_counter(), self.dialogs

        def call(fn, *args):
            try:
                fn(*args)
            except Exception as e:
                self.result(step, False, f"{type(e).__name__} after the step: {e}\n{traceback.format_exc(limit=6)}")
                self.go(next_step)

        def poll():
            secs = time.perf_counter() - start
            try:
                done = cond()
            except Exception:
                done = False
            if done:
                call(then, secs)
            elif self.dialogs > dialogs:
                self.result(step, False, f"ended after {secs:.1f} s by the failure dialog above")
                self.go(next_step)
            elif secs > timeout_s:
                call(on_timeout, secs)
            else:
                QTimer.singleShot(POLL_MS, poll)
        QTimer.singleShot(POLL_MS, poll)

    def no_dialogs(self) -> None:
        """Every message box becomes a logged failure; 'Stop it and close?' is answered Yes (the close step)."""
        test = self

        def record(box_or_parent=None, title="", text="", *a, **k):
            if isinstance(box_or_parent, QMessageBox):
                title, text = box_or_parent.windowTitle(), box_or_parent.text()
            test.dialogs += 1
            test.result("dialog", False, f"a message box appeared — {title}: {text}")
            return QMessageBox.Ok
        QMessageBox.exec = record
        for name in ("warning", "critical", "information"):
            setattr(QMessageBox, name, staticmethod(lambda parent=None, title="", text="", *a, **k:
                                                    record(None, title, text)))
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    def watchdog(self) -> None:
        """A last resort on its own thread: if the event loop never reaches the end, write why and leave."""
        def fire():
            with open(self.out / "self-test.txt", "a", encoding="utf-8") as f:
                f.write(f"FAIL watchdog: the self-test did not finish within {TOTAL_LIMIT_S} s\nFAILED\n")
            from hotspotlv.app.workers import hard_exit
            hard_exit(1)
        self._watchdog = threading.Timer(TOTAL_LIMIT_S, fire)
        self._watchdog.daemon = True
        self._watchdog.start()

    # ---------------------------------------------------------------- steps
    def start(self) -> None:
        from hotspotlv.paths import cache_dir, package_data
        self.watchdog()
        frozen = getattr(sys, "frozen", False)
        self.log(f"Hotspot LV v{__version__} self-test — {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"frozen: {frozen} · executable: {sys.executable}")
        self.log(f"python {platform.python_version()} · {platform.platform()} · Qt platform {self.app.platformName()}")
        self.log(f"package data: {package_data()} (exists: {package_data().exists()}) · cache: {cache_dir(create=False)}")
        name = self.app.platformName()
        if frozen:                       # QT_QPA_PLATFORM=offscreen would hide a missing qwindows.dll (round 2)
            self.result("platform", name == "windows", f"Qt platform {name!r} (must be 'windows')")
        else:
            self.result("platform", True, f"Qt platform {name!r} (source run: any platform)")
        self.win.resize(1400, 900)
        self.no_dialogs()
        self.step_https()

    def step_https(self) -> None:
        def run():
            import ssl
            import urllib.request
            ctx = ssl.create_default_context()                  # loads the Windows certificate store
            cas = ctx.cert_store_stats().get("x509_ca", 0)
            ok = hasattr(urllib.request, "HTTPSHandler") and ctx.verify_mode == ssl.CERT_REQUIRED and cas > 0
            self.result("https", ok, f"{ssl.OPENSSL_VERSION}; {cas} CA certificates; urllib can open https:// "
                                     "(not used now)")
        self.guarded("https", run)
        self.go(self.step_station)

    def check_pdf(self, pdf: Path) -> tuple[bool, str]:
        """Pages, lossless images, and (on a real platform) embedded fonts — a PDF without text is not a card."""
        data = pdf.read_bytes()
        pages = len(re.findall(rb"/Type\s*/Page(?!s)", data))
        images = len(re.findall(rb"/Subtype\s*/Image", data))
        lossless = images >= 3 and b"/DCTDecode" not in data             # the three charts, none as JPEG
        fonts = data.count(b"/FontFile2")
        need_fonts = self.app.platformName() not in ("offscreen", "minimal")
        ok = data[:5] == b"%PDF-" and pages >= 2 and lossless and (fonts > 0 or not need_fonts)
        return ok, (f"{pdf.name}, {len(data) // 1024} kB, {pages} pages, {fonts} embedded fonts"
                    f"{'' if need_fonts else ' (not required on this platform)'}, {images} images, "
                    f"{'none as JPEG' if b'/DCTDecode' not in data else 'JPEG images'}")

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
            ok, detail = self.check_pdf(pdf)
            self.result("pdf", ok, f"{detail} ({time.perf_counter() - t:.1f} s)")
        self.guarded("station", run)
        self.go(self.step_fleet)

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
            errors = [] if fl.table_df is None else list(fl.table_df.loc[fl.table_df["verdict"] == "error", "error"])
            self.result("fleet", n == 3 and not errors,          # an error row is a failure, not a ranked station
                        f"{n} stations ranked ({time.perf_counter() - t:.1f} s)" if not errors
                        else f"{len(errors)} of {n} stations failed: {errors[0][:200]}")
        self.guarded("fleet", run)
        self.go(self.step_scenario)

    def step_scenario(self) -> None:
        sp = self.win.scenario
        self.win.tabs.setCurrentIndex(2)

        def start():
            sp.houses.setValue(6); sp.feeders.setValue(2); sp.fast.setChecked(True)
            sp.run()
        if not self.guarded("scenario", start):
            self.go(self.step_sweep); return

        def done(secs):
            self.grab("scenario.png")
            ok = sp.result is not None
            self.result("scenario", ok, (f"{sp.result.verdict.headline()} — a worker thread, {secs:.1f} s"
                                         if ok else "the run ended without a result"))
            self.go(self.step_sweep)

        def timeout(secs):
            self.result("scenario", False, f"no result after {secs:.0f} s")
            self.go(self.step_sweep)
        self.wait_until("scenario", lambda: not sp.is_running() and sp.result is not None, STEP_TIMEOUT_S, done,
                        timeout, self.step_sweep)

    def _view_step(self, name: str, tab: int, view, setup, finished, describe, then) -> None:
        """Run one of the scenario panel's views (Sweep, Spread, Fixes) with its own Run button logic."""
        sp = self.win.scenario

        def start():
            sp.tabs.setCurrentIndex(tab)
            setup()
            view.run()
        if not self.guarded(name, start):
            self.go(then); return

        def done(secs):
            self.grab(f"{name}.png")
            self.result(name, True, f"{describe()} — {secs:.1f} s")
            self.go(then)

        def timeout(secs):
            self.result(name, False, f"no result after {secs:.0f} s (status: {view.status.text()[:200]})")
            self.go(then)
        self.wait_until(name, lambda: not view.is_running() and finished(), STEP_TIMEOUT_S, done, timeout, then)

    def step_sweep(self) -> None:
        sw = self.win.scenario.sweep

        def setup():
            sw.table = None
            sw.knob_box.setCurrentIndex(sw.knob_box.findData("n_houses"))
            sw.start.setValue(6); sw.stop.setValue(12); sw.step.setValue(6); sw.fast.setChecked(True)
        self._view_step("sweep", 5, sw, setup, lambda: sw.table is not None,
                        lambda: f"{len(sw.table)} values, verdicts {', '.join(sw.table['verdict'])}", self.step_spread)

    def step_spread(self) -> None:
        mc = self.win.scenario.mc

        def setup():
            mc.table = None
            mc.n_draws.setValue(mc.n_draws.minimum()); mc.first.setValue(0); mc.fast.setChecked(True)
        self._view_step("spread", 6, mc, setup, lambda: mc.table is not None,
                        lambda: f"{len(mc.table)} draws", self.step_fixes)

    def step_fixes(self) -> None:
        fx = self.win.scenario.fixes

        def setup():
            fx.comparison = None
            fx.fast.setChecked(True)
        self._view_step("fixes", 4, fx, setup, lambda: fx.comparison is not None,
                        lambda: f"{len(fx.comparison.results)} cases compared", self.step_powerflow)

    def step_powerflow(self) -> None:
        """The other steps run in fast mode and the cancel step stops its power flows early, so one full power flow
        of the app's own feeder runs here to convergence (pandapower's Newton–Raphson in the frozen build)."""
        def run():
            from hotspotlv.feeder import build_feeder, run_snapshot, set_house_loads
            t = time.perf_counter()
            net = build_feeder()
            set_house_loads(net, 3.0)
            r = run_snapshot(net)
            ok = bool(net.converged) and 0 < r["trafo_loading_percent"] < 200 and 0.8 < r["vmin_pu"] < 1.05
            self.result("powerflow", ok, f"converged: {bool(net.converged)}; transformer "
                                         f"{r['trafo_loading_percent']:.1f} %, lowest voltage {r['vmin_pu']:.3f} p.u. "
                                         f"({time.perf_counter() - t:.1f} s)")
        self.guarded("powerflow", run)
        self.go(self.step_cancel)

    def step_cancel(self) -> None:
        from hotspotlv.app.workers import any_running
        sp = self.win.scenario

        def start():
            sp.result = None
            sp.fast.setChecked(False)                          # a full power flow: about a minute if not cancelled
            sp.run()
        if not self.guarded("cancel", start):
            self.go(self.step_close); return

        def cancel():
            if not any_running():                              # nothing to cancel: the run failed or never started
                self.result("cancel", False, "no run was in progress 1.5 s after Run — nothing could be cancelled")
                self.go(self.step_close)
                return
            t = time.perf_counter()
            sp.cancel()

            def stopped(secs):
                self.result("cancel", True, f"Cancel returned in {time.perf_counter() - t - secs:.2f} s, "
                                            f"the thread ended {secs:.1f} s later; the window stayed responsive")
                self.go(self.step_close)

            def still(secs):
                self.result("cancel", False, f"the cancelled thread was still running after {secs:.0f} s")
                self.go(self.step_close)
            self.wait_until("cancel", lambda: not any_running(), 20, stopped, still, self.step_close)
        self.go(cancel, 1500)

    def step_close(self) -> None:
        from hotspotlv.app.widgets.station_panel import EXAMPLE
        from hotspotlv.app.workers import any_running
        st = self.win.station

        def start():
            self.win.tabs.setCurrentIndex(0)
            st.path.setText(str(EXAMPLE))
            st.run()
        if not self.guarded("close", start):
            self.finish(); return
        running = any_running()                                # checked at once: the assessment has just started
        self.result("close", running, "closing the window over a running assessment; the process must now end "
                    "cleanly" if running else "the assessment did not start, so closing over a run is not tested")
        self.go(self.finish)

    def finish(self) -> None:
        if getattr(self, "_watchdog", None) is not None:
            self._watchdog.cancel()                             # the run reached its end
        missing = [s for s in STEPS if s not in self.passed]
        if missing and not self.failed:
            self.result("steps", False, f"no PASS for: {', '.join(missing)}")
        elif missing:
            self.log(f"INFO steps: no PASS for: {', '.join(missing)}")
        self.log(f"{'FAILED' if self.failed else 'ALL PASSED'} in {time.perf_counter() - self.t0:.0f} s")
        if self.failed:
            self.app.exit(1)
        else:
            self.win.close()                                    # the normal path: closeEvent → quit → main()


def install(app: QApplication, win, out: Path) -> SelfTest:
    """Hide the window from the screen and schedule the self-test on the event loop."""
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    test = SelfTest(app, win, out)
    test.go(test.start)
    return test
