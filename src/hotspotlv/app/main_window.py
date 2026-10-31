"""The main window: three modes as tabs plus the assumptions page.

Tab order follows the planner's workflow (decision of 20 Oct 2026): own station
data first, then the fleet, then the synthetic scenario; the window opens on
"Own station data". Closing while a run is in progress asks first — a QThread
destroyed mid-run would take the whole process down.
"""

from __future__ import annotations


import pandas as pd
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from hotspotlv import __version__
from hotspotlv.app.widgets.fleet_panel import FleetPanel
from hotspotlv.app.widgets.scenario_panel import ScenarioPanel
from hotspotlv.app.widgets.station_panel import StationPanel
from hotspotlv.paths import package_data

TITLE = "Hotspot LV — thermal ageing & loading screening for secondary substations (simulation)"
ABOUT = f"""
# Hotspot LV v{__version__}

**What this is.** An open screening tool for secondary-substation (nätstation) transformers under
electrification. It couples a load model — or your own meter data — to the IEC 60076-7 thermal model
of the transformer and judges the result against IEC 60076-7, EIFS 2023:3 and the cable ratings.

**What it is not.** A simulation with generic constants: results are *indicative*, every limit is stated.
It does not replace a full network study, transformer monitoring, or a DSO's own reserve (N-1), growth
and accessory criteria. Voltage and cable rules are only checked in the scenario mode (a feeder model is
needed); on your own station data they are reported as *not checked*.

**Network use.** The program works offline with what it ships (Västerås / Eskilstuna A weather 2023 and the
example stations). Another SMHI station, or station data outside 2023, needs that station's hourly temperatures
from opendata-download-metobs.smhi.se (HTTPS): the checked archive (about 7 MB) and, for the most recent months,
SMHI's latest-months data — kept in `%USERPROFILE%\\.hotspotlv` and used again from there. SMHI publishes its
checked archive about three months late; the result's notes say when the latest months were used. Nothing else is
sent or fetched.

Author: Basel Haijar · MIT licence · source code and issues: see README.txt next to the program.
Uses Qt for Python / Qt 6 (LGPL-3.0, © The Qt Company Ltd. and other contributors), numpy, pandas, scipy,
pandapower, pyqtgraph and others — licences and source links in THIRD-PARTY-NOTICES.txt next to the program.
The data keep their own licences (below), not MIT.
"""


def _assumptions_markdown() -> str:
    import hotspotlv.catalogue as catalogue
    import hotspotlv.thermal as thermal
    import hotspotlv.verdict as verdict
    sources = (package_data() / "SOURCES.md").read_text(encoding="utf-8")
    code = lambda text: "```\n" + text.strip("\n") + "\n```\n"
    lo, hi = catalogue.CUSTOM_KVA_RANGE
    custom = (f"\n**Custom unit** (Own station data and Fleet screening): a rating from {lo:g} to {hi:g} kVA with the "
              "no-load and load losses of the unit's test report, in watts, and optionally its uk (else "
              f"{catalogue.CUSTOM_UK_PERCENT:g} % is assumed and said so). The losses set the loss ratio R of the "
              "thermal model and the transformer's own losses added to the meters; every other thermal constant "
              "stays the generic IEC 60076-7 distribution value. Values that cannot be right are refused: a "
              "no-load loss outside 0.01–1 % or a load loss outside 0.1–5 % of the rating (kW typed for W), a "
              "load loss not above the no-load loss (swapped), a uk outside 2–10 %.\n")
    return (ABOUT + "\n## Verdict rules (from the engine's own documentation)\n" + code(verdict.__doc__)
            + "\n## Thermal model (from the engine's own documentation)\n" + code(thermal.__doc__)
            + "\n## Transformer catalogue (from the engine's own documentation)\n" + code(catalogue.__doc__)
            + "\n" + catalogue.catalogue_markdown() + custom
            + "\n## Data sources and licences\n" + sources.replace("# Data sources and licences", ""))


class MainWindow(QMainWindow):
    def __init__(self, weather: pd.DataFrame):
        super().__init__()
        self.setWindowTitle(f"{TITLE} — v{__version__}")
        self.resize(1400, 900)
        self.setMinimumSize(900, 570)                    # measured hint 751 × 563 logical at 125 % with results (22 Oct); fits a 1366 × 768 laptop (1093 × 614)
        tabs = QTabWidget()
        self.scenario = ScenarioPanel(weather)
        self.station = StationPanel()
        self.fleet = FleetPanel()
        tabs.addTab(self.station, "Own station data")
        tabs.addTab(self.fleet, "Fleet screening")
        tabs.addTab(self.scenario, "Scenario (synthetic street)")
        about = QWidget(); al = QVBoxLayout(about)
        tb = QTextBrowser(); tb.setOpenExternalLinks(True); tb.setMarkdown(_assumptions_markdown())
        al.addWidget(tb)
        tabs.addTab(about, "Assumptions && sources")
        tabs.setCurrentIndex(0)
        self.tabs = tabs
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Simulation only — indicative results; every number has a stated source and limit. "
                                     "Author: Basel Haijar. MIT licence. Uses Qt for Python (LGPL-3.0) — see "
                                     "THIRD-PARTY-NOTICES.txt.")

    def _panels(self):
        return (self.scenario, self.station, self.fleet)

    def closeEvent(self, event):
        running = [p for p in self._panels() if p.is_running()]
        if not running:
            event.accept(); return
        answer = QMessageBox.question(self, "A run is in progress",
                                      "A calculation is still running. Stop it and close?",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            event.ignore(); return
        for p in running:
            (p.cancel_all if hasattr(p, "cancel_all") else p.cancel)()
        event.accept()
