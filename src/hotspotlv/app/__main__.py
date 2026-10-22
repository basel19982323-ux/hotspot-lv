"""`python -m hotspotlv.app` (or the `hotspot-lv` command, or HotspotLV.exe) — start the desktop application.

`--self-test <folder>` runs the end-to-end check of app/selftest.py instead of waiting for a user: for a frozen or
installed build, which the source-tree tests cannot see. Exit code 0 = every step passed; the log is in the folder."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Deeper than this, Windows' 260-character path limit stops Qt's plugins and DLLs from loading, and Qt's own box
# ("no Qt platform plugin could be initialized … reinstalling may fix this") hides the cause (packaging review
# round 2: a 157-character extraction folder started, 198 did not; the longest file the program loads at run time
# lies 88 characters below the extraction folder). The program folder is the extraction folder + "\HotspotLV".
FOLDER_LIMIT = 165


def _self_test_folder(argv: list[str]):
    """The folder after --self-test (and argv without the option), or None."""
    if "--self-test" not in argv:
        return None, argv
    i = argv.index("--self-test")
    if i + 1 >= len(argv):
        raise SystemExit("--self-test needs a folder")
    return Path(argv[i + 1]).resolve(), argv[:i] + argv[i + 2:]


def _too_deep() -> str | None:
    """The frozen program's folder when it is too deep to start from, else None."""
    if not getattr(sys, "frozen", False):
        return None
    folder = str(Path(sys.executable).parent)
    return folder if len(folder) > FOLDER_LIMIT else None


def _fail_start(self_test: Path, text: str) -> int:
    """A self-test that cannot even start still leaves its log, written without Qt."""
    self_test.mkdir(parents=True, exist_ok=True)
    (self_test / "self-test.txt").write_text(f"FAIL start: {text}\nFAILED\n", encoding="utf-8")
    return 1


def main() -> int:
    self_test, argv = _self_test_folder(sys.argv)
    deep = _too_deep()
    if deep is not None:
        text = (f"The program folder is {len(deep)} characters deep:\n{deep}\n\nWindows cannot load the program's "
                "parts from a folder that deep. Unzip the whole HotspotLV folder to a short path such as "
                "C:\\Tools\\HotspotLV and start it from there.")
        if self_test is not None:
            return _fail_start(self_test, text)
        import ctypes                                     # no Qt yet: Windows' own message box
        ctypes.windll.user32.MessageBoxW(None, text, "Hotspot LV could not start", 0x10)
        return 1
    if self_test is None:
        return _run(argv, None)
    try:                                                  # from the first Qt import on: a log, never a modal box
        return _run(argv, self_test)
    except Exception as e:
        return _fail_start(self_test, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def _run(argv: list[str], self_test: Path | None) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    app = QApplication(argv)
    app.setApplicationName("Hotspot LV")
    from PySide6.QtGui import QIcon
    from hotspotlv.paths import package_data
    app.setWindowIcon(QIcon(str(package_data() / "icon.png")))     # title bar and taskbar (the exe has the .ico)
    from hotspotlv.app.theme import ACCENT, STYLESHEET
    app.setStyleSheet(STYLESHEET)
    pix = QPixmap(460, 140)
    pix.fill(QColor("white"))
    splash = QSplashScreen(pix)
    splash.showMessage("Hotspot LV\nloading weather and models …", Qt.AlignCenter, QColor(ACCENT))
    if self_test is None:                       # a self-test runs without anything on the screen
        splash.show()
        app.processEvents()

    try:
        from hotspotlv.weather import get_weather
        weather = get_weather(2023)
        from hotspotlv.app.main_window import MainWindow
        win = MainWindow(weather)
    except Exception as e:  # a planner must see why the app did not start, not a console trace
        splash.close()
        if self_test is not None:
            return _fail_start(self_test, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        box = QMessageBox(QMessageBox.Critical, "Hotspot LV could not start",
                          f"{e}\n\nProgram folder: {folder}\n"
                          "If the program folder is incomplete (only the .exe copied), unzip the whole HotspotLV "
                          "folder again to a short path such as C:\\Tools\\HotspotLV. The details below show where "
                          "it stopped.")
        box.setDetailedText(traceback.format_exc())
        box.exec()
        return 1
    if self_test is not None:
        from hotspotlv.app import selftest
        selftest.install(app, win, self_test)
    win.show()
    splash.finish(win)
    code = app.exec()
    from hotspotlv.app.workers import any_running, hard_exit
    if any_running():           # the window closed over a cancelled run that is still computing: a Python thread
        hard_exit(code)         # cannot be stopped from outside, and a QThread destroyed while running aborts —
    return code                 # so leave now; the run's result was already discarded


if __name__ == "__main__":
    sys.exit(main())
