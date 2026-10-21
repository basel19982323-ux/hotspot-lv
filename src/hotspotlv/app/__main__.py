"""`python -m hotspotlv.app` — start the desktop application."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication, QSplashScreen
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import Qt

    app = QApplication(sys.argv)
    app.setApplicationName("Hotspot LV")
    from hotspotlv.app.theme import STYLESHEET
    app.setStyleSheet(STYLESHEET)
    splash = QSplashScreen(QPixmap(420, 120))
    splash.showMessage("Hotspot LV — loading weather and models …", Qt.AlignCenter)
    splash.show()
    app.processEvents()

    from hotspotlv.weather import get_weather
    weather = get_weather(2023)
    from hotspotlv.app.main_window import MainWindow
    win = MainWindow(weather)
    win.show()
    splash.finish(win)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
