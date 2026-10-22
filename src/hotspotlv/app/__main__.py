"""`python -m hotspotlv.app` (or the `hotspot-lv` command, or HotspotLV.exe) — start the desktop application.

`--self-test <folder>` runs the end-to-end check of app/selftest.py instead of waiting for a user: for a frozen or
installed build, which the source-tree tests cannot see. Exit code 0 = every step passed; the log is in the folder."""

from __future__ import annotations

import sys
import traceback


def _self_test_folder(argv: list[str]):
    """The folder after --self-test (and argv without the option), or None."""
    if "--self-test" not in argv:
        return None, argv
    i = argv.index("--self-test")
    if i + 1 >= len(argv):
        raise SystemExit("--self-test needs a folder")
    from pathlib import Path
    return Path(argv[i + 1]).resolve(), argv[:i] + argv[i + 2:]


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    self_test, argv = _self_test_folder(sys.argv)
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
            self_test.mkdir(parents=True, exist_ok=True)
            (self_test / "self-test.txt").write_text(f"FAIL start: {type(e).__name__}: {e}\n{traceback.format_exc()}",
                                                     encoding="utf-8")
            return 1
        from pathlib import Path
        folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        box = QMessageBox(QMessageBox.Critical, "Hotspot LV could not start",
                          f"{type(e).__name__}: {e}\n\nProgram folder: {folder}\n"
                          "If the program folder is incomplete (only the .exe copied) or very deep (a path of more than "
                          "about 140 characters), unzip the whole HotspotLV folder again to a short path such as "
                          "C:\\Tools\\HotspotLV. The details below show where it stopped.")
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
