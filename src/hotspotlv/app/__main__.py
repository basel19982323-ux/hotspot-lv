"""`python -m hotspotlv.app` — start the desktop application."""

from __future__ import annotations

import sys
import traceback


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    app = QApplication(sys.argv)
    app.setApplicationName("Hotspot LV")
    from hotspotlv.app.theme import ACCENT, STYLESHEET
    app.setStyleSheet(STYLESHEET)
    pix = QPixmap(460, 140)
    pix.fill(QColor("white"))
    splash = QSplashScreen(pix)
    splash.showMessage("Hotspot LV\nloading weather and models …", Qt.AlignCenter, QColor(ACCENT))
    splash.show()
    app.processEvents()

    try:
        from hotspotlv.weather import get_weather
        weather = get_weather(2023)
        from hotspotlv.app.main_window import MainWindow
        win = MainWindow(weather)
    except Exception as e:  # a planner must see why the app did not start, not a console trace
        splash.close()
        box = QMessageBox(QMessageBox.Critical, "Hotspot LV could not start",
                          f"{type(e).__name__}: {e}\n\nThe weather file is shipped with the package; if it is missing, "
                          "reinstall or run with network access so it can be rebuilt.")
        box.setDetailedText(traceback.format_exc())
        box.exec()
        return 1
    win.show()
    splash.finish(win)
    code = app.exec()
    from hotspotlv.app.workers import any_running, hard_exit
    if any_running():           # the window closed over a cancelled run that is still computing: a Python thread
        hard_exit(code)         # cannot be stopped from outside, and a QThread destroyed while running aborts —
    return code                 # so leave now; the run's result was already discarded


if __name__ == "__main__":
    sys.exit(main())
