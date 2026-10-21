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
    assert "kiosk" in panel.card._notes.toPlainText()                            # the enclosure assumption is visible
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


def test_fixes_view_shows_the_comparison_and_recosts_live(panel):
    """Catches: a fix row showing another fix's numbers; swapped bar charts; cost controls not reaching the costing."""
    from hotspotlv.app.theme import VERDICT_COLORS
    from hotspotlv.study import compare_fixes, fixes_table
    cmp = compare_fixes(panel.build_case(), panel.weather, fast=True)
    panel.fixes.set_comparison(cmp)
    shown = panel.fixes.model.df.set_index("fix")
    fx = fixes_table(cmp)
    assert list(shown.index) == list(fx.index)
    assert list(shown.columns[:3]) == ["verdict", "why", "capex low (kSEK)"]        # what the planner came for, first
    assert np.allclose(shown["h > 80 %"].to_numpy(dtype=float), fx["h > 80 %"].to_numpy(dtype=float))
    assert shown.loc["800 kVA", "capex low (kSEK)"] == 209
    assert np.isnan(shown.loc["800 kVA", "peak kVA avoided"])                      # the swap IS the reinforcement
    col = list(shown.columns).index("capex low (kSEK)") + 1                        # +1: the index became a column
    assert panel.fixes.model.data(panel.fixes.model.index(1, col)) == "209"        # whole kSEK, no ".0"
    assert "Energiforsk" in panel.fixes.model.headerData(col + 4, Qt.Horizontal, Qt.ToolTipRole)
    # the bars are the table's numbers, in order, coloured by verdict; the two charts are not swapped
    def bars(plot):
        item = next(i for i in plot.listDataItems() + plot.getPlotItem().items if hasattr(i, "opts") and "height" in i.opts)
        return list(item.opts["height"]), [b.color().name() for b in item.opts["brushes"]]
    real_h, real_c = bars(panel.fixes.bars_real[0])
    assert np.allclose(real_h, fx["life days/yr"]) and real_c == [VERDICT_COLORS[v] for v in fx["verdict"]]
    if float(fx["h > 80 %"].max()) > 0:
        assert np.allclose(bars(panel.fixes.bars_paper[0])[0], fx["h > 80 %"])
    else:
        assert any("no hours above 80 %" in i.toPlainText() for i in panel.fixes.bars_paper[0].getPlotItem().items
                   if hasattr(i, "toPlainText"))
    panel.fixes.new_kiosk.setChecked(True)
    assert panel.fixes.model.df.set_index("fix").loc["800 kVA", "capex low (kSEK)"] == 490
    panel.fixes.bat_lo.setValue(2000)
    bat = [i for i in panel.fixes.model.df["fix"] if str(i).startswith("battery")]
    if bat:
        kwh = cmp.results[bat[0]].case.battery.energy_kwh
        assert panel.fixes.model.df.set_index("fix").loc[bat[0], "capex low (kSEK)"] == pytest.approx(kwh * 2.0, abs=1)
    panel.fixes.timer_hi.setValue(1000)
    n_ev = int(cmp.results["baseline"].neighbourhood.houses.has_ev.sum())
    assert panel.fixes.model.df.set_index("fix").loc["night timer 22–01", "capex high (kSEK)"] == pytest.approx(n_ev * 1.0, abs=1)
    assert "Notes:" in panel.fixes.notes.toPlainText() and "SOURCES.md" not in panel.fixes.notes.toPlainText()
    assert panel.fixes.base_label.text().startswith("Base case: 6 houses / 500 kVA")


def test_sweep_view_draws_the_engines_rows(panel):
    """Catches: x axis not the knob values; life plotted linearly; verdict strip misaligned; h100 fed with h80."""
    from hotspotlv.app.theme import VERDICT_COLORS
    from hotspotlv.study import sweep
    table = sweep(panel.build_case(), panel.weather, "n_houses", [6, 12], fast=True)
    panel.sweep.set_table(table, "n_houses", panel.build_case())
    x, y = panel.sweep.h80.getData()
    assert list(x) == [6, 12] and list(y) == list(table["hours_above_80"])
    assert list(panel.sweep.h100.getData()[1]) == list(table["hours_above_100"])
    lx, ly = panel.sweep.life.getData()
    assert np.allclose(10 ** np.asarray(ly), np.maximum(table["life_days_per_year"], 1e-4))
    assert [s.opts["brush"].color().name() for s in panel.sweep._strip] == [VERDICT_COLORS[v] for v in table["verdict"]]
    assert all(s.getData()[1][0] < 0 for s in panel.sweep._strip)                   # squares below the hours axis
    assert "80 % rule" in panel.sweep.caption.text() and "houses" in panel.sweep.caption.text()
    assert panel.sweep.model.df.iloc[0]["houses"] == 6
    assert "voltage outside ±10 % (h)" in [panel.sweep.model.headerData(c, Qt.Horizontal)
                                            for c in range(panel.sweep.model.columnCount())]
    assert panel.sweep.plot.getAxis("right").logTickStrings([-3, -1, 0, 1, 2], 1, 1) == ["0.001", "0.1", "1", "10", "100"]


def test_sweep_view_scales_share_knobs_to_percent_and_keeps_the_run_knob(panel):
    """Catches: share sweeps drawn as fractions; the combo changed mid-run rescaling the result."""
    table = pd.DataFrame({"verdict": ["OK", "Watch", "At risk", "Watch"], "peak_loading_pct": [50.0, 70.0, 90.0, 85.0],
                          "hours_above_80": [0, 0, 12, 8], "hours_above_100": [0, 0, 1, 0],
                          "life_days_per_year": [0.01, 0.05, 0.5, 0.4], "max_hot_spot_c": [60.0, 70.0, 90.0, 88.0],
                          "voltage_bad_hours": [0, 0, 3, 0], "voltage_plan_hours": [0, 2, 9, 4], "max_cable_pct": [40.0, 60.0, 101.0, 95.0],
                          "exact_hours": [0, 0, 0, 0],
                          "why": ["OK — fine", "Watch — x", "At risk — main cable above its current rating (also: y)", "Watch — z"]},
                         index=pd.Index([0.0, 0.2, 0.6, 1.0], name="ev_share"))
    panel.sweep.knob_box.setCurrentIndex(0)                                        # combo says houses …
    panel.sweep.set_table(table, "ev_share")                                       # … the run was EV share
    assert list(panel.sweep.h80.getData()[0]) == [0, 20, 60, 100]
    assert list(panel.sweep.h100.getData()[1]) == [0, 0, 1, 0]                     # not fed with h80
    assert panel.sweep.flip_line.value() == 60 and panel.sweep.flip_line.isVisible()
    cap = panel.sweep.caption.text()
    assert "first flags the station at 60 %" in cap and "first turns At risk at 60 % because of main cable above its current rating;" in cap
    assert "not monotonic" in cap
    assert list(panel.sweep.model.df.columns)[0] == "share of houses with an EV charger (%)"


def test_spread_view_draws_the_engines_draws(panel):
    """Catches: dots not the draws' numbers; band/median not the 10/50/90 of the draws; tally miscounted; limits not the verdict's."""
    from hotspotlv.app.theme import VERDICT_COLORS
    from hotspotlv.app.widgets.mc_view import METRICS
    from hotspotlv.study import monte_carlo, summarise_monte_carlo  # noqa: F401
    from hotspotlv.verdict import DESIGN_LIFE_DAYS_PER_YEAR, WATCH_LIFE_DAYS_PER_YEAR
    from hotspotlv.app.widgets.mc_view import fmt_value
    mc = monte_carlo(panel.build_case(), panel.weather, seeds=range(3), fast=True)
    panel.mc.set_table(mc, panel.build_case())
    key = panel.mc.metric_box.currentData()
    x, y = panel.mc.dots.getData()
    assert list(x) == [0, 1, 2] and np.allclose(y, mc[key])
    lo, hi = panel.mc.band.getRegion()                                             # 3 draws: the band is the full range
    assert lo == pytest.approx(mc[key].min()) and hi == pytest.approx(mc[key].max())
    assert panel.mc.median_line.value() == pytest.approx(np.median(mc[key]))
    assert [s.opts["brush"].color().name() for s in panel.mc._strip] == [VERDICT_COLORS[v] for v in mc["verdict"]]
    # switching the metric redraws band, median and dots from THAT metric; far-off limits are named, not drawn
    panel.mc.metric_box.setCurrentIndex(list(METRICS).index("life_days_per_year"))
    assert np.allclose(panel.mc.dots.getData()[1], mc["life_days_per_year"])
    assert panel.mc.band.getRegion()[1] == pytest.approx(mc["life_days_per_year"].max())
    assert panel.mc.median_line.value() == pytest.approx(np.median(mc["life_days_per_year"]))
    assert panel.mc._limit_lines == [] and f"at {WATCH_LIFE_DAYS_PER_YEAR:g}" in panel.mc.limits_note.text() \
        and f"at {DESIGN_LIFE_DAYS_PER_YEAR:g}" in panel.mc.limits_note.text() and "off the chart" in panel.mc.limits_note.text()
    panel.mc.metric_box.setCurrentIndex(list(METRICS).index("peak_loading_pct"))
    assert sorted(l.value() for l in panel.mc._limit_lines) == [80.0, 100.0] or "off the chart" in panel.mc.limits_note.text()
    # tally and tables
    counts = mc["verdict"].value_counts()
    for lvl, n in counts.items():
        assert f"{lvl} {n}" in panel.mc.tally_label.text() and f"{lvl} {n}" in panel.mc.caption.text()
    assert "same verdict in all" in panel.mc.caption.text() and "Same verdict in all 3 draws" in panel.mc.tally_label.text()
    summ = summarise_monte_carlo(mc)
    shown = panel.mc.summary_model.df.set_index("quantity")
    assert shown.loc["life used (days/yr)", "50%"] == fmt_value("life_days_per_year", float(summ.loc["life_days_per_year", "50%"]))
    assert shown.loc["peak load (% of nameplate)", "max"] == f"{summ.loc['peak_loading_pct', 'max']:.1f}"
    assert shown.loc["hours with voltage outside ±10 %", "mean"] == f"{summ.loc['voltage_bad_hours', 'mean']:,.0f}"
    assert set(shown.index) == {METRICS[k][0] for k in summ.index}                 # every engine row, by name
    assert len(panel.mc.draws_model.df) == 3 and "seed" in panel.mc.draws_model.df.columns
    assert {"peak_loading_pct", "life_days_per_year", "max_load_pu", "why"} <= set(panel.mc.draws_model.df.columns)
    assert panel.mc.base_label.text().startswith("Base case: 6 houses / 500 kVA") and "draw 0" not in panel.mc.base_label.text()


def test_spread_view_tally_and_caption_on_a_mixed_result(panel):
    """Catches: 'robust' claimed when the verdict flips; a constant number printed as a range."""
    from hotspotlv.app.theme import VERDICT_COLORS
    from hotspotlv.app.widgets.mc_view import METRICS
    idx = pd.Index([3, 4, 5, 6], name="seed")
    table = pd.DataFrame({"verdict": ["OK", "Watch", "At risk", "OK"], "peak_loading_pct": [60.0, 75.0, 95.0, 62.0],
                          "hours_above_80": [0, 0, 30, 0], "hours_above_100": [0, 0, 0, 0],
                          "life_days_per_year": [0.1, 0.2, 0.5, 0.1], "max_hot_spot_c": [70.0, 70.0, 70.0, 70.0],
                          "hours_hot_spot_above_98": [0, 0, 0, 0], "max_load_pu": [0.6, 0.75, 0.95, 0.62],
                          "voltage_bad_hours": [0, 0, 4, 0], "voltage_plan_hours": [0, 3, 9, 0], "max_cable_pct": [50.0, 60.0, 101.0, 52.0],
                          "exact_hours": [0, 0, 0, 0], "why": ["a", "b", "c", "d"]}, index=idx)
    panel.mc.set_table(table)
    cap = panel.mc.caption.text()
    assert cap.startswith("4 draws (draws 3–6): the verdict changed between draws: OK 2 / Watch 1 / At risk 1")
    assert "max hot-spot 70.0 °C in every draw" in cap and "peak load 60.0–95.0 % (median 68.5)" in cap
    assert "The verdict changed between draws" in panel.mc.tally_label.text()
    assert list(panel.mc.dots.getData()[0]) == [3, 4, 5, 6]
    assert [s.opts["brush"].color().name() for s in panel.mc._strip] == [VERDICT_COLORS[v] for v in table["verdict"]]
    assert [s.getData()[0][0] for s in panel.mc._strip] == [3, 4, 5, 6]            # squares under their own draw
    panel.mc.metric_box.setCurrentIndex(list(METRICS).index("voltage_bad_hours"))
    assert [l.value() for l in panel.mc._limit_lines] == [0.0] and "none allowed" in panel.mc.limits_note.text()
    assert panel.mc.minimumSizeHint().height() <= 360                              # the laptop case (fonts aside)


def test_spread_tab_ignores_a_seed_only_change(panel):
    """Catches: the banner and the stale line raised by the Random-draw box, which this tab replaces."""
    from hotspotlv.app.widgets.mc_view import VERDICT_COLORS as _  # noqa: F401  (import check)
    panel._inputs_changed()
    seed = panel.seed.value()
    panel.seed.setValue(seed + 1)
    assert not panel.fixes.banner.isHidden() and panel.mc.banner.isHidden()
    panel.seed.setValue(seed)
    assert panel.fixes.banner.isHidden() and panel.mc.banner.isHidden()


def test_sweep_ranges_follow_the_knob(panel):
    """Catches: a share sweep that cannot start at 0 % or reach 100 %; To silently not reached."""
    panel.sweep.knob_box.setCurrentIndex(1)
    assert panel.sweep.start.minimum() == 0 and panel.sweep.stop.maximum() == 100
    assert panel.sweep.values() == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert "values: 0, 20, 40, 60, 80, 100 %" in panel.sweep.values_label.text()
    panel.sweep.step.setValue(30)
    assert "To 100 is not reached" in panel.sweep.values_label.text()
    panel.sweep.knob_box.setCurrentIndex(0)
    assert panel.sweep.start.minimum() == 3 and panel.sweep.stop.maximum() == 200
    assert panel.sweep.values() == [45, 60, 75, 90, 105, 120] and panel.sweep.run_btn.text() == "Run sweep (6 runs)"


def test_tabs_warn_when_inputs_leave_the_cards_case_and_cancel_is_per_run(panel):
    """Catches: the Fixes/Sweep tabs silently answering a different case than the card; the main Cancel killing a tab run."""
    houses = panel.houses.value()
    panel._inputs_changed()
    assert panel.fixes.banner.isHidden() and panel.sweep.banner.isHidden()      # (the panel itself is never shown offscreen)
    panel.houses.setValue(houses + 1)
    assert not panel.fixes.banner.isHidden() and f"{houses} houses" in panel.fixes.banner.text() and "differ" in panel.sweep.banner.text()
    assert f"{houses + 1} houses" in panel.fixes.banner.toolTip() and f"Card: {houses} houses" in panel.sweep.banner.toolTip()
    assert "Inputs changed" in panel.fixes.status.text()
    panel.houses.setValue(houses)
    assert panel.fixes.banner.isHidden()
    before = panel.sweep.status.text()
    panel.cancel()                                                                 # nothing of its own to cancel …
    assert panel.sweep.status.text() == before and panel.status.text() != "Cancelled."   # … and it leaves the tabs alone


def test_one_run_at_a_time_and_the_block_message_goes_away(panel):
    """Catches: a second worker started from another tab; the 'run in progress' line left behind after the run."""
    from hotspotlv.app.workers import Worker
    import time as _t
    own_status = panel.status.text(); sweep_status = panel.sweep.status.text()
    panel.fixes._worker = Worker(_t.sleep, 2.0); panel.fixes._worker.start(); panel.fixes.run_started.emit()
    try:
        assert not panel.run_btn.isEnabled() and not panel.sweep.run_btn.isEnabled()
        assert "Fixes tab" in panel.status.text() and "Fixes tab" in panel.sweep.status.text()
        panel.sweep.step.setValue(panel.sweep.step.value() + 1)                   # the trap of round 2
        assert not panel.sweep.run_btn.isEnabled()
        n_before = panel.sweep._worker
        panel.sweep.run(); panel.run()
        assert panel.sweep._worker is n_before and panel._worker is None          # nothing started
    finally:
        panel.fixes.cancel()
    assert panel.run_btn.isEnabled() and panel.sweep.run_btn.isEnabled()
    assert panel.status.text() == own_status and panel.sweep.status.text() == sweep_status


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
