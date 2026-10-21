"""The main window: three modes as tabs plus the assumptions page."""

from __future__ import annotations

import pandas as pd
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from hotspotlv import __version__
from hotspotlv.app.widgets.fleet_panel import FleetPanel
from hotspotlv.app.widgets.scenario_panel import ScenarioPanel
from hotspotlv.app.widgets.station_panel import StationPanel
from hotspotlv.paths import package_data

TITLE = "Hotspot LV — thermal ageing & loading screening for secondary substations (simulation)"


def _assumptions_html() -> str:
    import hotspotlv.thermal as thermal
    import hotspotlv.verdict as verdict
    sources = (package_data() / "SOURCES.md").read_text(encoding="utf-8")
    body = "<h2>What this tool is</h2><p>An open screening tool. It couples a load model (or your own meter data) to the " \
           "IEC 60076-7 thermal model of the transformer and judges the result against IEC 60076-7, EIFS 2023:3 and the " \
           "cable ratings. It is a simulation with generic constants: results are indicative and every limit is stated. " \
           "It is not a replacement for a full network study, for monitoring, or for a DSO's reserve and growth criteria.</p>"
    body += "<h2>Verdict rules</h2><pre>" + verdict.__doc__ + "</pre>"
    body += "<h2>Thermal model</h2><pre>" + thermal.__doc__ + "</pre>"
    body += "<h2>Data sources and licences</h2><pre>" + sources + "</pre>"
    return body


class MainWindow(QMainWindow):
    def __init__(self, weather: pd.DataFrame):
        super().__init__()
        self.setWindowTitle(f"{TITLE} — v{__version__}")
        self.resize(1400, 900)
        tabs = QTabWidget()
        self.scenario = ScenarioPanel(weather)
        self.station = StationPanel()
        self.fleet = FleetPanel()
        tabs.addTab(self.fleet, "Fleet screening")
        tabs.addTab(self.station, "Own station data")
        tabs.addTab(self.scenario, "Scenario (synthetic street)")
        about = QWidget(); al = QVBoxLayout(about)
        tb = QTextBrowser(); tb.setOpenExternalLinks(True); tb.setHtml(_assumptions_html())
        al.addWidget(tb)
        tabs.addTab(about, "Assumptions && sources")
        tabs.setCurrentIndex(2)
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Simulation only — indicative results; every number has a stated source and limit. "
                                     "Author: Basel Haijar. MIT licence.")
