"""The station card PDF (mode B) and the legend strips. The card must say what the screen card says, carry
the three charts, and list the assumptions the run used; legends sit above the plots, never on the data.
Offscreen (no fonts): the PDF's structure is checked with pypdf, its words through the HTML. Each test is a
mutation trap, like tests/test_app.py."""

import html
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from hotspotlv import __version__  # noqa: E402
from hotspotlv.app.report import (CHART_H, CHART_W, CHARTS, render_charts, rule_rows, station_card_html,  # noqa: E402
                                  thermal_rows, write_station_card)
from hotspotlv.app.widgets import station_panel as sp_module  # noqa: E402
from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline  # noqa: E402
from hotspotlv.app.widgets.station_panel import EXAMPLE, StationPanel, _assess  # noqa: E402
from hotspotlv.app.widgets.verdict_card import fmt_card_value  # noqa: E402
from hotspotlv.station import NOT_CHECKED_KEYS  # noqa: E402
from hotspotlv.thermal import IEC_LIMITS_C  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def station(app):
    """The shipped example (synthetic 80-house station), kiosk, default SMHI station — like pressing Assess."""
    return _assess(EXAMPLE, 500, "meters", "Europe/Stockholm", "start", 96190, True, "kiosk", 0, 0, 0)


def test_card_html_says_what_the_screen_card_says(station):
    """Catches: a column left out, a number formatted differently from the screen, a note dropped,
    'not checked' turned into a 0, typed-in constants that drift from the run's own."""
    res, _ = station
    v = res.verdict
    page = station_card_html(res, None, source_name="ns-102.csv")
    assert v.level.upper() in page and html.escape(v.headline()) in page
    for k, val in {**v.on_paper, **v.really}.items():                          # every row of both columns, same text as the card
        assert f"{html.escape(k)}</td><td align='right'" in page
        assert f"<b>{html.escape(fmt_card_value(k, val))}</b>" in page
    hours = v.on_paper["hours above 80 % of nameplate"]
    assert f"<b>{hours:,.0f}</b>" in page                                        # whole hours (literal oracle, not the formatter)
    assert f"<b>{v.really['days of life used per year']:.3f}</b>" in page
    assert f"<b>{v.really['max hot-spot (°C)']:,.1f}</b>" in page
    for k in NOT_CHECKED_KEYS:
        assert f"{html.escape(k)}</td><td align='right' style='color:#6b6b6b'><b>not checked</b>" in page
    for n in v.notes:
        assert html.escape(n) in page
    assert "ns-102.csv" in page and "500 kVA" in page and html.escape(res.station.period()) in page
    assert "96190" in page and "the default" in page and "kiosk" in page
    assert "indicative" in page and "simulation" in page and __version__ in page and "Basel Haijar" in page
    p = res.thermal.params
    assert f"{p.d_theta_or:g} K" in page and f"{p.R:.2f}" in page and f"{p.tau_o_min:g} min" in page
    assert f"{p.enclosure_rise_k:g} K kiosk" in page
    assert "sum of customer meters" in page and "4.2 % of P at rated load" in page
    assert page.count("not checked here") == 2                                   # the voltage and cable rules, in the rules table
    assert "page-break-before" in page                                           # the assumption list starts a new page


def test_rule_rows_come_from_the_engines_constants():
    """Catches: a threshold typed into the report that no longer matches verdict.py / thermal.py."""
    rows = {r[0]: r for r in rule_rows(checked=False)}
    assert rows["hot-spot"][1] == f"> {IEC_LIMITS_C['normal_cyclic_hot_spot']:g} °C"
    assert rows["hot-spot"][2] == f"> {IEC_LIMITS_C['long_time_emergency_hot_spot']:g} °C"
    assert rows["insulation life used"][2] == "> 365 days/yr" and rows["insulation life used"][1] == "> 182 days/yr"
    assert rows["load"][1] == "> 1.5 × rated" and rows["load"][2] == "> 1.8 × rated"
    assert rows["house voltage"][4].startswith("not checked") and rows["cable"][4].startswith("not checked")
    assert all(r[4] == "checked" for k, r in rows.items() if k in ("hot-spot", "top-oil", "load", "insulation life used"))
    assert rule_rows(checked=True)[4][4] == "checked"


def test_thermal_rows_follow_the_params(station):
    res, _ = station
    rows = {r[0]: r[1] for r in thermal_rows(res.thermal.params)}
    p = res.thermal.params
    assert rows["enclosure"] == "kiosk" and rows["paper"] == "kraft"
    assert rows["Δθor"].startswith(f"{p.d_theta_or:g} K (= {p.d_theta_or - p.enclosure_rise_k:g} K outdoor + {p.enclosure_rise_k:g} K kiosk")
    assert rows["τo · τw"] == f"{p.tau_o_min:g} min · {p.tau_w_min:g} min"


def test_rendered_charts_are_the_right_size_and_not_blank(station):
    """Catches: a hidden widget grabbed at its stale geometry, a chart with no data, a legend lost in the copy."""
    res, amb = station
    charts = render_charts(res, amb)
    assert tuple(charts) == CHARTS
    for name, c in charts.items():
        img = c["image"]
        dpr = img.devicePixelRatio()                                             # 1 offscreen, 1.25 on a scaled Windows screen
        assert (round(img.width() / dpr), round(img.height() / dpr)) == (CHART_W, CHART_H)
        px = np.array([[img.pixelColor(x, y).lightness() for x in range(0, img.width(), 7)] for y in range(0, img.height(), 7)])
        assert (px < 250).mean() > 0.02, f"{name}: the grab is (almost) blank"
        assert c["caption"] and c["legend"]
    v = res.verdict
    assert f"Above 80 %: {v.on_paper['hours above 80 % of nameplate']:,.0f} h" in charts["duration"]["caption"]
    assert charts["period"]["caption"].startswith("Peak hour (the black line)") and "hot-spot" in charts["period"]["caption"]
    assert f"{res.thermal.worst_week()['V_mean'].sum() / 24:.3f} days" in charts["worst_week"]["caption"]
    assert "loading" in charts["period"]["legend"] and "the peak hour" in charts["period"]["legend"]
    assert "drag" not in charts["period"]["legend"] and "drag" not in charts["period"]["caption"]      # paper cannot be dragged


def test_pdf_is_written_with_the_three_charts(station, tmp_path):
    res, amb = station
    out = write_station_card(res, amb, tmp_path / "card.pdf", source_name="ns-102.csv")
    assert out.exists() and out.read_bytes()[:5] == b"%PDF-"
    pypdf = pytest.importorskip("pypdf")
    r = pypdf.PdfReader(str(out))
    assert len(r.pages) >= 2                                                     # card, then the assumption list
    assert sum(len(pg.images) for pg in r.pages) == 3
    assert "Hotspot LV" in (r.metadata.title or "") and "ns-102.csv" in (r.metadata.title or "")


def test_export_button_follows_the_result_and_the_run(app, station, monkeypatch):
    """Catches: Export enabled before there is anything to export, or left enabled during a run."""
    panel = StationPanel()
    assert not panel.export_btn.isEnabled()
    panel._done(station)
    assert panel.export_btn.isEnabled() and panel.result is station[0] and panel._amb is station[1]

    class StubWorker:                              # a run that never finishes until cancelled
        def __init__(self, *a, **k): self._running = False
        def start(self): self._running = True
        def isRunning(self): return self._running
        def cancel(self): self._running = False
        finished_ok = failed = type("S", (), {"connect": lambda self, *a: None})()
    monkeypatch.setattr(sp_module, "Worker", StubWorker)
    panel.path.setText(str(EXAMPLE))
    panel.run()
    assert panel.is_running() and not panel.export_btn.isEnabled() and not panel.run_btn.isEnabled()
    panel.export_pdf()                                                           # ignored while running: no dialog, no file
    panel.cancel()
    assert panel.export_btn.isEnabled() and panel.run_btn.isEnabled()           # the last result is still exportable


def test_legends_sit_above_the_plots_not_on_the_data(app):
    """Catches: a pyqtgraph legend box back inside the plot (it sat on the January peaks); an unlabelled rule line."""
    import pyqtgraph as pg
    for cls, words in ((YearTimeline, ("loading", "hot-spot", "outdoor", "80 % rule", "scrubber")),
                       (DurationCurve, ("80 % rule", "nameplate", "1.5 × rated")),
                       (WorstWeek, ("hot-spot", "top-oil", "outdoor", "98 °C", "loading"))):
        w = cls()
        assert w.plot.plotItem.legend is None, f"{cls.__name__}: legend box inside the plot"
        assert w.layout().indexOf(w.legend) == 0 and w.layout().indexOf(w.plot) == 1
        for word in words:
            assert word in w.legend.text(), f"{cls.__name__}: legend does not name {word!r}"
    tl = YearTimeline()
    labels = {it.label.format for it in tl.plot.plotItem.items if isinstance(it, pg.InfiniteLine) and it.label is not None}
    assert {"80 % rule", "nameplate"} <= labels
