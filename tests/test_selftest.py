"""`hotspot-lv --self-test <folder>`: the end-to-end check a frozen build runs on itself. Here from the source tree, so
the suite fails when the self-test itself breaks (it is the only test a packaged build gets) — and, since packaging
review round 2, also when it would pass something broken: a skipped step, a failure dialog, a broken callback."""

import os
import subprocess
import sys
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from hotspotlv.app import selftest  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _env(tmp_path):
    """Like a clean PC: an empty cache and no downloads (round 2: the test used the developer's cache and network)."""
    return {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            "HOTSPOTLV_CACHE": str(tmp_path / "cache"), "HOTSPOTLV_OFFLINE": "1"}


def test_self_test_option_is_parsed_and_removed_from_argv(tmp_path):
    from hotspotlv.app.__main__ import _self_test_folder
    folder, argv = _self_test_folder(["HotspotLV.exe", "--self-test", str(tmp_path), "-platform", "windows"])
    assert folder == tmp_path.resolve() and argv == ["HotspotLV.exe", "-platform", "windows"]
    assert _self_test_folder(["HotspotLV.exe"]) == (None, ["HotspotLV.exe"])
    with pytest.raises(SystemExit):
        _self_test_folder(["HotspotLV.exe", "--self-test"])


def test_the_self_test_runs_every_step_and_exits_cleanly(tmp_path):
    """Every step of selftest.STEPS logs PASS (a mutant that skipped sweep, spread and fixes passed the old test),
    offline with an empty cache, through the real entry point, exit code 0."""
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "hotspotlv.app", "--self-test", str(out)],
                       capture_output=True, text=True, timeout=600, env=_env(tmp_path))
    log = (out / "self-test.txt").read_text(encoding="utf-8")
    assert r.returncode == 0, f"exit {r.returncode:#x}\n{log}\n{r.stderr[-1500:]}"
    for step in selftest.STEPS:
        assert f"PASS {step}:" in log, f"no PASS for {step}\n{log}"
    assert "FAIL" not in log and log.rstrip().splitlines()[-1].startswith("ALL PASSED")
    assert (out / "station_card.pdf").stat().st_size > 100_000
    assert all((out / f).exists() for f in ("station.png", "fleet.png", "scenario.png", "sweep.png"))
    assert not (tmp_path / "cache").exists()                     # the shipped data were enough


def test_a_failing_step_fails_the_run_and_the_rest_still_runs(tmp_path):
    """The exit code a build relies on: one broken feature → FAIL line, FAILED, exit 1 — and the steps after it
    still run and report."""
    out = tmp_path / "out"
    code = ("import sys; import hotspotlv.app.widgets.fleet_panel as fp\n"
            "def broken(*a, **k):\n    raise ValueError('the fleet is broken on purpose')\n"
            "fp._screen = broken\n"
            "from hotspotlv.app.__main__ import main\n"
            f"sys.argv = ['hotspot-lv', '--self-test', {str(out)!r}]\n"
            "sys.exit(main())\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600, env=_env(tmp_path))
    log = (out / "self-test.txt").read_text(encoding="utf-8")
    assert r.returncode == 1, f"exit {r.returncode:#x}\n{log}\n{r.stderr[-1500:]}"
    assert "FAIL fleet: ValueError: the fleet is broken on purpose" in log
    assert "PASS scenario:" in log and "PASS cancel:" in log                # the run went on after the failure
    assert log.rstrip().splitlines()[-1].startswith("FAILED")


class _Win:
    def close(self):
        pass


class _App:
    def __init__(self):
        self.code = None

    def exit(self, code):
        self.code = code

    def platformName(self):
        return "offscreen"

    def processEvents(self):
        pass


def _spin(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def test_dialogs_and_missing_steps_are_failures(app, tmp_path, monkeypatch):
    for name in ("exec", "warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, getattr(QMessageBox, name))    # restored after the test
    fake_app = _App()
    test = selftest.SelfTest(fake_app, _Win(), tmp_path)
    test.no_dialogs()
    QMessageBox.warning(None, "Run failed", "FileNotFoundError: base load")
    assert test.failed and test.dialogs == 1
    assert "FAIL dialog: a message box appeared — Run failed: FileNotFoundError: base load" in test.lines
    assert QMessageBox.question(None, "A run is in progress", "Stop it and close?") == QMessageBox.Yes
    test.failed = False                                          # only the step check from here on
    test.result("https", True, "ok")
    test.finish()
    assert any(line.startswith("FAIL steps: no PASS for: platform, station") for line in test.lines)
    assert test.lines[-1].startswith("FAILED") and fake_app.code == 1


def test_a_wait_ends_at_a_failure_dialog_and_a_broken_callback_fails_the_step(app, tmp_path):
    """Round 2: a failed worker made each step wait its full 240 s, and an exception in a step's callback hung the
    run with no FAIL line."""
    test = selftest.SelfTest(_App(), _Win(), tmp_path)
    went_on = []
    test.wait_until("scenario", lambda: False, 30, lambda s: None, lambda s: None, lambda: went_on.append("next"))
    test.dialogs += 1                                            # what no_dialogs() does when a box appears
    _spin(0.8)
    assert went_on == ["next"] and any(line.startswith("FAIL scenario: ended after") for line in test.lines)

    def broken_then(secs):
        raise RuntimeError("the picture could not be saved")
    test.wait_until("sweep", lambda: True, 30, broken_then, lambda s: None, lambda: went_on.append("after sweep"))
    _spin(0.8)
    assert went_on[-1] == "after sweep"
    assert any(line.startswith("FAIL sweep: RuntimeError after the step: the picture could not be saved")
               for line in test.lines)


def test_an_error_in_a_steps_own_code_ends_the_run_with_a_log(app, tmp_path):
    fake_app = _App()
    test = selftest.SelfTest(fake_app, _Win(), tmp_path)

    def step_fleet():
        raise KeyError("verdict")
    test.go(step_fleet)
    _spin(0.5)
    assert any(line.startswith("FAIL fleet: KeyError") for line in test.lines)
    assert test.lines[-1].startswith("FAILED") and fake_app.code == 1


def test_a_frozen_build_must_run_on_the_windows_platform(app, tmp_path, monkeypatch):
    """Round 2: with QT_QPA_PLATFORM=offscreen inherited, a build without qwindows.dll passed the gate."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_app = _App()
    test = selftest.SelfTest(fake_app, _Win(), tmp_path)
    monkeypatch.setattr(test, "step_https", lambda: None)
    monkeypatch.setattr(test, "watchdog", lambda: None)
    test.win.resize = lambda *a: None
    for name in ("exec", "warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, getattr(QMessageBox, name))
    test.start()
    assert "FAIL platform: Qt platform 'offscreen' (must be 'windows')" in test.lines and test.failed


def test_too_deep_a_folder_is_named_before_qt_starts(tmp_path, monkeypatch):
    from hotspotlv.app import __main__ as entry
    deep = "C:\\" + "\\".join(["a" * 20] * 9) + "\\HotspotLV"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", deep + "\\HotspotLV.exe")
    monkeypatch.setattr(sys, "argv", ["HotspotLV.exe", "--self-test", str(tmp_path)])
    assert len(deep) > entry.FOLDER_LIMIT
    assert entry.main() == 1
    log = (tmp_path / "self-test.txt").read_text(encoding="utf-8")
    assert log.startswith("FAIL start: The program folder is") and "C:\\Tools\\HotspotLV" in log
