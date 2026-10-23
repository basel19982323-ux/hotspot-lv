"""What the numbers audit of 23 Oct 2026 found on screen, pinned: the fleet's chart tabs no longer draw on top of each
other, the ← → keys step through the October hour that the Swedish clock shows twice, the Spread chart never
rescales its axis by 1 000, and periods and readouts say which clock they use."""

import io
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from hotspotlv.station import read_station_csv  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_selecting_a_fleet_row_leaves_the_hidden_chart_tabs_hidden(app):
    from hotspotlv.app.widgets.fleet_panel import FleetPanel, _screen
    from hotspotlv.paths import package_data
    fl = FleetPanel()
    fl._done(_screen(package_data() / "examples" / "fleet_index.csv", progress=None))
    fl.view.selectRow(0)
    app.processEvents()
    assert fl.tabs.currentWidget() is fl.timeline and fl.tabs.isVisibleTo(fl)
    assert not fl.duration.isVisibleTo(fl) and not fl.week.isVisibleTo(fl)   # pages forced visible drew on top


def test_arrow_keys_step_through_the_repeated_october_hour(app):
    from hotspotlv.app.widgets.charts import YearTimeline
    idx = pd.date_range("2023-10-28 20:00", "2023-10-29 04:00", freq="h", tz="UTC")   # 22:00 CEST … 05:00 CET
    hourly = pd.DataFrame({"loading_pct": np.linspace(10, 50, len(idx)), "s_kva": 100.0}, index=idx)
    tl = YearTimeline()
    tl.set_data(hourly, None, pd.Series(5.0, index=idx))
    labels = []
    for i in range(len(idx)):
        tl.go_to(i)
        assert tl._i == i                                                    # the keys used to stall at 02:00
        labels.append(tl.label_at(i))
    assert "Sun 29 Oct 2023 02:00 CEST–02:00 CET" in labels and "Sun 29 Oct 2023 02:00–03:00 CET" in labels
    assert len(set(labels)) == len(labels)
    assert np.all(np.diff(tl._x) > 0)                                        # time runs forward on the x axis


def test_the_spread_axis_never_rescales_by_a_thousand(app):
    from hotspotlv.app.widgets.mc_view import MonteCarloView
    from hotspotlv.weather import get_weather
    view = MonteCarloView(lambda: None, get_weather(2023))
    assert view.plot.getAxis("left").autoSIPrefix is False                  # 0.31 days/yr read as "310 (×0.001)"


@pytest.mark.parametrize("text, expected", [
    ("time;kW\n2023-03-01 00:00;1\n2023-03-01 00:15;1\n2023-03-01 00:30;1\n2023-03-01 00:45;1\n",
     "2023-03-01 00:00 – 2023-03-01 01:00 (Swedish time)"),
    ("time;kW\n" + "\n".join(f"2023-03-0{d} {h:02d}:00;1" for d in (1, 2) for h in range(24)) + "\n",
     "2023-03-01 – 2023-03-02 (Swedish time, whole days)"),
])
def test_the_period_names_the_clock_and_ends_where_the_data_end(text, expected):
    assert read_station_csv(io.StringIO(text)).period() == expected
