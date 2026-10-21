"""Offscreen checks of the desktop app's data plumbing: every chart, readout and diagram must show
the engine's number for that hour. Each test is a mutation trap (review of 21 Oct 2026).
Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_app.py` (fonts are missing offscreen — layout and
numbers only). One 6-house / 2-feeder case in fast mode is shared by the module."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from hotspotlv.app.widgets.diagram import FeederDiagram, house_color  # noqa: E402
from hotspotlv.app.widgets.scenario_panel import ScenarioPanel  # noqa: E402
from hotspotlv.app.widgets.tables import DataFrameModel  # noqa: E402
from hotspotlv.feeder import build_feeder  # noqa: E402
from hotspotlv.study import run_case  # noqa: E402
from hotspotlv.weather import get_weather  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def panel(app):
    weather = get_weather(2023)
    p = ScenarioPanel(weather)
    p.houses.setValue(6); p.feeders.setValue(2); p.ev.setValue(50); p.pv.setValue(50)
    p._done(run_case(p.build_case(), weather, fast=True))
    app.processEvents()
    return p


def _scrub_to(panel, i):
    panel.timeline.go_to(i)


def test_readout_numbers_are_the_engines_numbers_for_that_hour(panel):
    """Catches: hot-spot swapped for top-oil, loading_pct/100 shown as kVA, ambient off by one row."""
    res = panel.result
    for i in (0, 100, int(np.argmax(res.year.hourly["loading_pct"].to_numpy())), len(res.year.hourly) - 1):
        _scrub_to(panel, i)
        txt = panel.timeline.readout.text()
        h, t = res.year.hourly.iloc[i], res.thermal.hourly.iloc[i]
        assert f"loading {h['loading_pct']:.0f} % ({h['s_kva']:.0f} kVA)" in txt
        assert f"hot-spot {t['theta_h_max']:.0f} °C" in txt and f"top-oil {t['theta_o']:.0f} °C" in txt
        assert f"outdoor {panel.weather['temp_c'].iloc[i]:.1f} °C" in txt
        assert f"{res.year.hourly.index[i].tz_convert('Europe/Stockholm'):%a %d %b %Y %H:%M}" in txt


def test_scrubber_snaps_to_the_nearest_hour(panel):
    """Catches: searchsorted showing the NEXT hour one second past a tick."""
    x = panel.timeline._x
    panel.timeline.scrubber.setPos(x[500] + 1.0)
    panel.timeline._moved()
    assert panel.timeline._i == 500
    panel.timeline.scrubber.setPos(x[500] + 3599.0)
    panel.timeline._moved()
    assert panel.timeline._i == 501


def test_diagram_colours_follow_the_scrubbed_hour(panel):
    """Catches: diagram coloured for hour i±1, houses × hours transposed, wrong fuse cap."""
    res, d = panel.result, panel.diagram
    p = res.neighbourhood.p_kw.to_numpy()
    for i in (0, 4000, len(p) - 1):
        _scrub_to(panel, i)
        got = [r.brush().color().name() for r in d._items["houses"]]
        want = [house_color(float(p[i, j]), d._cap_kw).name() for j in range(p.shape[1])]
        assert got == want
        assert d._items["trafo_label"].toPlainText().startswith(f"{res.year.hourly['loading_pct'].iloc[i]:.0f} %")
        assert panel.timeline.label_at(i) == d._items["hour_label"].toPlainText()


def test_diagram_houses_per_feeder_match_the_power_flow_model(app):
    """Catches: the drawing putting houses on other feeders than the pandapower net does."""
    for n, f in ((46, 3), (10, 4), (7, 6)):
        net = build_feeder(n_houses=n, n_feeders=f)
        in_net = net.bus.name.str.extract(r"House \d+ \(F(\d+)\)")[0].dropna().astype(int).value_counts().sort_index().tolist()
        houses = pd.DataFrame({"heating": ["none"] * n, "has_ev": False, "has_pv": False}, index=[f"house{j + 1}" for j in range(n)])
        idx = pd.date_range("2023-01-01", periods=3, freq="h", tz="UTC")
        d = FeederDiagram()
        d.set_case(houses, pd.DataFrame(0.0, index=idx, columns=houses.index), pd.Series(50.0, index=idx), 500, n_feeders=f)
        ys = sorted({round(r.rect().y()) for r in d._items["houses"]})          # one y per feeder row
        drawn = [sum(1 for r in d._items["houses"] if round(r.rect().y()) == y) for y in ys]
        assert drawn == in_net, (n, f)


def test_time_axis_labels_agree_with_the_readout(panel):
    """Catches: axis ticks in machine time while the readout is Swedish local time (any machine, any DST)."""
    from pyqtgraph.graphicsItems import DateAxisItem as m
    ax = panel.timeline.plot.getAxis("bottom")
    ax.zoomLevel = m.DAY_HOUR_ZOOM_LEVEL
    spacing = ax.zoomLevel.tickSpecs[-1].spacing
    for i in (12, 5000):                                                         # January and August
        _scrub_to(panel, i)
        tick = ax.tickStrings([panel.timeline._x[i]], 1, spacing)[0]
        assert tick in panel.timeline.readout.text()


def test_captions_and_card_use_the_verdicts_hours_and_days(panel):
    """Catches: duration curve counting rows instead of hours, worst-week days off by step length."""
    res = panel.result
    v = res.verdict
    assert f"Above 80 %: {v.on_paper['hours above 80 % of nameplate']:,.0f} h" in panel.duration.caption.text()
    assert f"above 100 %: {v.on_paper['hours above 100 % of nameplate']:,.0f} h" in panel.duration.caption.text()
    assert f"peak {v.on_paper['peak load (% of nameplate)']:.0f} %" in panel.duration.caption.text()
    ww = res.thermal.worst_week()
    assert f"{ww['V_mean'].sum() / 24:.3f} days" in panel.week.caption.text()
    assert v.level.upper() in panel.card._level.text()
    assert "kiosk" in panel.card._notes.text()                                   # the enclosure assumption is visible
    assert "fast mode" in panel.status.text() or "full power flow" in panel.status.text()


def test_table_model_shows_pandas_bools_and_ints_like_python_ones(app):
    """Catches: numpy.bool_ rendered 'True' instead of 'yes'; numpy ints left-aligned; hours with decimals."""
    m = DataFrameModel()
    m.set_df(pd.DataFrame({"has_ev": np.array([True]), "n": np.array([3], dtype="int64"),
                           "hours_above_80": [17.0], "kVA": [500.0], "life_days_per_year": [0.5634]}))
    assert m.data(m.index(0, 0), Qt.DisplayRole) == "yes"
    assert m.data(m.index(0, 1), Qt.TextAlignmentRole) == int(Qt.AlignRight | Qt.AlignVCenter)
    assert m.data(m.index(0, 2), Qt.DisplayRole) == "17"
    assert m.data(m.index(0, 3), Qt.DisplayRole) == "500"
    assert m.data(m.index(0, 4), Qt.DisplayRole) == "0.563"
    assert m.headerData(2, Qt.Horizontal) == "h > 80 %"


def test_verdict_card_does_not_accumulate_widgets(app, panel):
    """Catches: _fill leaving old rows behind (after the deferred deletes are flushed)."""
    for _ in range(5):
        panel.card.set_verdict(panel.result.verdict, "again")
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    n_rows = len(panel.result.verdict.on_paper)
    assert len(panel.card._paper[0].findChildren(QLabel)) == 1 + 2 * n_rows


def test_heating_shares_are_validated_before_the_engine(panel):
    for sb in panel.mix.values():
        sb.setValue(0)
    assert panel.build_case_error() is not None
    panel.mix["none"].setValue(50); panel.mix["air_water"].setValue(50)
    assert panel.build_case_error() is None and panel.mix_note.text() == ""        # exactly 100 %: nothing to say
    panel.mix["air_water"].setValue(30)                                             # 80 % → shown scaled
    assert "scaled to 100 %" in panel.mix_note.text() and "62 %" in panel.mix_note.text()
    panel.feeders.setValue(6); panel.houses.setValue(3)
    assert "feeders" in (panel.build_case_error() or "")
    panel.houses.setValue(6); panel.feeders.setValue(2)
