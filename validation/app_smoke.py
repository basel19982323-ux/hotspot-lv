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
    from hotspotlv.study import compare_fixes, sweep
    sp.houses.setValue(60)
    sp.fixes.set_comparison(compare_fixes(sp.build_case(), weather, fast=True))
    sp.tabs.setCurrentIndex(4); app.processEvents(); win.grab().save(str(out / "scenario_fixes.png"))
    sp.sweep.set_table(sweep(sp.build_case(), weather, "n_houses", [45, 60, 75, 90, 105], fast=True))
    sp.tabs.setCurrentIndex(5); app.processEvents(); win.grab().save(str(out / "scenario_sweep.png"))
    print("sweep caption:", sp.sweep.caption.text())

    # mode B
    import time
    st = win.station
    st.path.setText(str(STATION_EXAMPLE))
    st._t0 = time.perf_counter()
    st._done(_assess(STATION_EXAMPLE, 500, "meters", "Europe/Stockholm", "start", 96190, True, "kiosk", 0, 0, 0))
    win.centralWidget().setCurrentIndex(0); app.processEvents()
    win.grab().save(str(out / "station.png"))
    print("station:", st.result.verdict.headline())
    from PySide6.QtWidgets import QLabel
    short = [w for w in st.card._really[0].findChildren(QLabel) if w.height() < w.sizeHint().height() - 1]
    print("card labels squeezed:", len(short))
    assert not short, "the verdict card's REALLY column is squeezed — regression of 21 Oct"


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
