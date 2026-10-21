"""Offscreen smoke test of the app: build the window, run each mode synchronously,
save screenshots. Run: `python validation/app_smoke.py <out_dir>`."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from hotspotlv.app.theme import STYLESHEET  # noqa: E402
from hotspotlv.app.widgets.fleet_panel import _screen  # noqa: E402
from hotspotlv.app.widgets.station_panel import EXAMPLE as STATION_EXAMPLE, _assess  # noqa: E402
from hotspotlv.app.main_window import MainWindow  # noqa: E402
from hotspotlv.paths import package_data  # noqa: E402
from hotspotlv.study import run_case  # noqa: E402
from hotspotlv.weather import get_weather  # noqa: E402


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    weather = get_weather(2023)
    win = MainWindow(weather)
    win.resize(1400, 900)
    win.show()
    app.processEvents()

    # mode A — synchronous run of the panel's own case
    sp = win.scenario
    sp.houses.setValue(90); sp.ev.setValue(60); sp.pv.setValue(40)
    sp._done(run_case(sp.build_case(), weather, fast=True))
    win.centralWidget().setCurrentIndex(2)
    app.processEvents()
    win.grab().save(str(out / "scenario.png"))
    print("scenario status:", sp.status.text())
    sp.tabs.setCurrentIndex(2); app.processEvents(); win.grab().save(str(out / "scenario_worst_week.png"))
    sp.tabs.setCurrentIndex(1); app.processEvents(); win.grab().save(str(out / "scenario_duration.png"))
    print("scenario:", sp.result.verdict.headline())

    # mode B
    st = win.station
    st.path.setText(str(STATION_EXAMPLE))
    st._done(_assess(STATION_EXAMPLE, 500, "meters", "Europe/Stockholm", "start", 96190, True, "kiosk", 0, 0, 0))
    win.centralWidget().setCurrentIndex(0); app.processEvents()
    win.grab().save(str(out / "station.png"))
    print("station:", st.result.verdict.headline())

    # mode C
    fl = win.fleet
    fl._done(_screen(package_data() / "examples" / "fleet_index.csv", progress=None))
    fl.view.selectRow(0)
    win.centralWidget().setCurrentIndex(1); app.processEvents()
    win.grab().save(str(out / "fleet.png"))
    print("fleet:", fl.table_df[["station_id", "verdict", "peak_loading_pct"]].to_string())

    win.centralWidget().setCurrentIndex(3); app.processEvents()
    win.grab().save(str(out / "assumptions.png"))
    print("screenshots in", out)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("screenshots"))
