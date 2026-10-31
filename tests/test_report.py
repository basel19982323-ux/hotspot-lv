"""The station card PDF (mode B) and the legend strips. The card must say what the screen card says, carry
the three charts, and list the assumptions the run used; legends sit above the plots, never on the data.
Offscreen (no fonts): the PDF's structure is checked with pypdf, its words through the HTML. Each test is a
mutation trap, like tests/test_app.py; the second group follows the reviews of 22 Oct 2026 (usability,
physics): no false "saved", no stale card, no clipped-model number shown as real, headings kept with
their section."""

import copy
import html
import math
import os
import sys
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from hotspotlv import __version__  # noqa: E402
from hotspotlv.app.report import (CHART_H, CHART_W, CHARTS, build_card_document, page_of_items,  # noqa: E402
                                  render_charts, rule_rows, station_card_html, thermal_rows, write_station_card)
from hotspotlv.app.widgets import station_panel as sp_module  # noqa: E402
from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline  # noqa: E402
from hotspotlv.app.widgets.station_panel import EXAMPLE, StationPanel, _assess  # noqa: E402
from hotspotlv.app.widgets.verdict_card import fmt_card_value  # noqa: E402
from hotspotlv.catalogue import TRANSFORMERS  # noqa: E402
from hotspotlv.station import NOT_CHECKED_KEYS, ambient_for, assess_station, read_station_csv  # noqa: E402
from hotspotlv.thermal import IEC_LIMITS_C  # noqa: E402
from hotspotlv.verdict import OUT_OF_RANGE_RULE, THERMAL_KEYS  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def station(app):
    """The shipped example (synthetic 80-house station), kiosk, default SMHI station — like pressing Assess."""
    return _assess(EXAMPLE, 500, "meters", "Europe/Stockholm", "start", 96190, True, "kiosk", 0, 0, 0)


@pytest.fixture(scope="module")
def charts(station):
    res, amb = station
    return render_charts(res, amb)


@pytest.fixture(scope="module")
def clipped(app):
    """The example × 4: the loading leaves the thermal model's range (K > 3) — a big station left at 500 kVA."""
    st = read_station_csv(EXAMPLE)
    st = replace(st, p_kw=st.p_kw * 4, q_kvar=st.q_kvar * 4)
    amb = ambient_for(st.index)
    return assess_station(st, 500, amb), amb


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
    paper_at, really_at, inputs_at = page.index("ON PAPER (NAMEPLATE)"), page.index("REALLY (THERMAL MODEL"), page.index("<h2>Inputs")
    for k in v.on_paper:                                                         # each row under its own heading
        assert paper_at < page.index(f">{html.escape(k)}</td>") < really_at, k
    for k in v.really:
        assert really_at < page.index(f">{html.escape(k)}</td>") < inputs_at, k
    for n in v.notes:
        assert html.escape(n) in page
    assert "ns-102.csv" in page and "500 kVA" in page and html.escape(res.station.period()) in page
    assert "8,760 h of data" in page and "scaled" not in page.split("Inputs")[1].split("</table>")[0]   # a full year: no scaling
    assert "96190" in page and "the default" in page and "kiosk" in page
    assert "indicative" in page and "simulation" in page and __version__ in page and "Basel Haijar" in page
    p = res.thermal.params
    assert f"{p.d_theta_or:g} K" in page and f"{p.R:.2f}" in page and f"{p.tau_o_min:g} min" in page
    assert f"{p.enclosure_rise_k:g} K for the kiosk" in page
    assert "sum of customer meters" in page and "4.2 % of P at rated load" in page and "fitted" in page
    assert page.count("not checked here") == 2                                   # the voltage and cable rules, in the rules table
    assert "Three rules read" in page
    assert "<h2>How the numbers were made" in page                                # flows; headings kept with sections
    assert "IEC 60076-7:2005" in page and "2018" in page                         # the edition the table numbers belong to
    assert "it was not consulted for the values" in page and "the same in" not in page   # no unbacked 2018 claim
    assert "feeder.py" not in page                                               # no file names on paper
    energy = res.station.p_kw.clip(lower=0).sum() / 1000                         # hourly kW → MWh, by hand
    assert f"{energy:,.1f} MWh (net imports per step)" in page


def test_rule_rows_come_from_the_engines_constants():
    """Catches: a threshold in the report that no longer matches verdict.py / thermal.py. The oracle is typed here
    (literal), not rebuilt from the constants the code uses (code review of 22 Oct 2026)."""
    expect = {
        "insulation life used": ("> 182 days/yr", "> 365 days/yr"),
        "hot-spot": ("> 120 °C", "> 140 °C"),
        "top-oil": ("> 105 °C", "> 115 °C"),
        "load": ("> 1.5 × rated", "> 1.8 × rated"),
        "house voltage": ("any hour outside 0.92–1.05 pu (planning band)", "any hour outside 0.9–1.1 pu"),
        "cable": ("> 100 % of its rating", "—"),
        "hours above 80 % / 100 % of nameplate": ("never changes the verdict", "never changes the verdict"),
    }
    assert {r[0]: (r[1], r[2]) for r in rule_rows(checked=False)} == expect
    rows = {r[0]: r for r in rule_rows(checked=False)}
    assert rows["hot-spot"][1] == f"> {IEC_LIMITS_C['normal_cyclic_hot_spot']:g} °C"
    assert rows["hot-spot"][2] == f"> {IEC_LIMITS_C['long_time_emergency_hot_spot']:g} °C"
    assert rows["top-oil"][1] == "> 105 °C" and rows["top-oil"][2] == "> 115 °C"
    assert rows["insulation life used"][2] == "> 365 days/yr" and rows["insulation life used"][1] == "> 182 days/yr"
    assert rows["load"][1] == "> 1.5 × rated" and rows["load"][2] == "> 1.8 × rated"
    assert rows["house voltage"][4].startswith("not checked") and rows["cable"][4].startswith("not checked")
    assert all(r[4] == "checked" for k, r in rows.items() if k in ("hot-spot", "top-oil", "load", "insulation life used"))
    assert rule_rows(checked=True)[4][4] == "checked"


def test_thermal_rows_follow_the_params(station):
    res, _ = station
    rows = {r[0]: r[1] for r in thermal_rows(res.thermal.params, res.rated_kva)}
    p = res.thermal.params
    assert rows["enclosure"] == "kiosk" and rows["paper"] == "kraft"
    assert rows["Δθor"].startswith(f"{p.d_theta_or:g} K (= {p.d_theta_or - p.enclosure_rise_k:g} K outdoor + "
                                   f"{p.enclosure_rise_k:g} K for the kiosk")
    assert "stops at" not in rows["Δθor"]                                        # 500 kVA is a row of the table
    assert rows["τo · τw"] == f"{p.tau_o_min:g} min · {p.tau_w_min:g} min"
    big = {r[0]: r[1] for r in thermal_rows(type(p).for_transformer(800), 800)}
    assert "the table stops at 750 kVA" in big["Δθor"]                           # 800 kVA uses the 750 kVA row: say so


def test_rendered_charts_are_the_print_size_and_not_blank(station, charts):
    """Catches: a grab at the screen's size or scaling, a chart with no data, a legend lost in the copy."""
    res, _ = station
    assert tuple(charts) == CHARTS
    for name, c in charts.items():
        img = c["image"]
        dpr = img.devicePixelRatio()
        assert dpr == 2.0                                                        # print resolution, not the screen's
        assert (round(img.width() / dpr), round(img.height() / dpr)) == (CHART_W, CHART_H) == (680, 257)
        px = np.array([[img.pixelColor(x, y).lightness() for x in range(0, img.width(), 9)] for y in range(0, img.height(), 9)])
        assert (px < 250).mean() > 0.02, f"{name}: the image is (almost) blank"
        assert c["caption"] and c["legend"]
    # the data, not just axes and grid (axes alone are 8–18 % dark): count pixels of each series' own colour
    from PySide6.QtGui import QColor
    from hotspotlv.app.theme import HOTSPOT_COLOR, LOAD_COLOR

    def series_pixels(img, colour, from_y=0.0):
        want = QColor(colour)
        n = 0
        for y in range(int(img.height() * from_y), img.height(), 2):
            for x in range(0, img.width(), 2):
                c = img.pixelColor(x, y)
                n += abs(c.red() - want.red()) < 25 and abs(c.green() - want.green()) < 25 and abs(c.blue() - want.blue()) < 25
        return n
    assert series_pixels(charts["period"]["image"], LOAD_COLOR) > 500
    assert series_pixels(charts["duration"]["image"], LOAD_COLOR) > 300
    # below the top 40 %: the 120 °C rule line (same red) sits up there and alone passed the old check
    assert series_pixels(charts["worst_week"]["image"], HOTSPOT_COLOR, from_y=0.4) > 150
    v = res.verdict
    assert f"Above 80 %: {v.on_paper['hours above 80 % of nameplate']:,.0f} h" in charts["duration"]["caption"]
    assert charts["period"]["caption"].startswith("At the time of peak loading (black line)")
    assert "hot-spot" in charts["period"]["caption"] and "The highest hot-spot" in charts["period"]["caption"]
    assert f"{res.thermal.worst_week()['V_mean'].sum() / 24:.3f} days" in charts["worst_week"]["caption"]
    assert "loading" in charts["period"]["legend"] and "time of peak loading" in charts["period"]["legend"]
    assert "drag" not in charts["period"]["legend"] and "drag" not in charts["period"]["caption"]      # paper cannot be dragged


def test_pdf_is_written_with_the_three_charts(station, tmp_path):
    res, amb = station
    out = write_station_card(res, amb, tmp_path / "card.pdf", source_name="ns-102.csv")
    assert out.exists() and out.read_bytes()[:5] == b"%PDF-"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["card.pdf"]            # no temporary file left behind
    pypdf = pytest.importorskip("pypdf")
    r = pypdf.PdfReader(str(out))
    assert len(r.pages) >= 2                                                     # card, then the assumption list
    images = [im for pg in r.pages for im in pg.images]
    assert len(images) == 3                                                      # not a 16-px broken-image icon …
    assert all(im.image.size == (2 * CHART_W, 2 * CHART_H) for im in images)     # … each a full chart at 2×
    assert len({im.data for im in images}) == 3                                  # three different charts
    filters = [x.get_object().get("/Filter") for pg in r.pages
               for x in (pg["/Resources"].get("/XObject") or {}).values() if x.get_object()["/Subtype"] == "/Image"]
    assert filters and all(f == "/FlateDecode" for f in filters), filters        # lossless, not JPEG (/DCTDecode)
    assert "Hotspot LV" in (r.metadata.title or "") and "ns-102.csv" in (r.metadata.title or "")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file locking and read-only semantics")
def test_a_locked_or_read_only_target_is_an_error_not_a_saved_card(station, tmp_path):
    """Catches: 'Station card saved' while a PDF viewer holds the old file (Windows denies the write) — the old
    card stayed on disk and the planner attached it (review of 22 Oct 2026)."""
    import ctypes
    import stat
    res, amb = station
    target = tmp_path / "card.pdf"
    target.write_bytes(b"OLD CARD")
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    handle = k32.CreateFileW(str(target), 0x80000000, 0, None, 3, 0x80, None)    # GENERIC_READ, no sharing
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(OSError, match="open in another program or read-only"):
            write_station_card(res, amb, target)
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))
    assert target.read_bytes() == b"OLD CARD" and sorted(x.name for x in tmp_path.iterdir()) == ["card.pdf"]
    os.chmod(target, stat.S_IREAD)                                               # read-only attribute
    try:
        with pytest.raises(OSError, match="read-only"):
            write_station_card(res, amb, target)
        assert target.read_bytes() == b"OLD CARD"
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
    with pytest.raises(OSError, match="does not exist"):
        write_station_card(res, amb, tmp_path / "no-such-folder" / "card.pdf")


def test_a_clipped_thermal_run_shows_no_number_that_looks_real(clipped):
    """Catches: '1.66e+16 days of life', '440 °C' printed as the verdict of a station whose loading left the
    thermal model's range, with the caveat four pages later."""
    res, _ = clipped
    v = res.verdict
    assert v.level == "At risk" and v.at_risk[0].rule == OUT_OF_RANGE_RULE
    assert not any(r.rule.startswith(("ageing", "hot-spot", "top-oil")) for r in v.reasons)
    assert all(math.isnan(v.really[k]) for k in THERMAL_KEYS)
    page = station_card_html(res, None)
    assert "e+" not in page
    for k in THERMAL_KEYS:
        assert f"{html.escape(k)}</td><td align='right' style='color:#6b6b6b'><b>–</b>" in page
    assert page.index("the IEC short-time emergency limit.</b>") < page.index("ON PAPER")  # said first, on page 1
    assert any("thermal run was clipped" in n for n in v.notes)


def test_charts_of_a_withdrawn_run_print_no_temperature_or_life(clipped):
    """Catches: '1583043024046439680.000 days of insulation life used, max hot-spot 458 °C' in the worst-week caption
    and '452 °C' in the period readout of a clipped run (round 2 of 22 Oct 2026) — on paper and on screen."""
    import re
    res, amb = clipped
    charts = render_charts(res, amb)
    for name, c in charts.items():
        text = c["caption"] + " " + c["legend"]
        assert not re.search(r"\d{7,}", text.replace(",", "")), f"{name}: an absurd number in {text!r}"
        assert not re.search(r"hot-spot \d|top-oil \d|days of insulation", text), f"{name}: {text!r}"
    assert "not computed" in charts["worst_week"]["caption"] and "IEC short-time emergency limit" in charts["worst_week"]["caption"]
    from hotspotlv.app.widgets.charts import WorstWeek
    w = WorstWeek()
    w.set_week(res.thermal.worst_week(), res.year.hourly, amb, thermal_valid=False)
    assert not any(line.isVisible() for line in w._rules)                         # no °C rule lines without temperatures
    page = station_card_html(res, charts)
    assert "The heaviest week by loading — no transformer temperatures" in page and "No hot-spot or top-oil is drawn" in page
    assert all("hot-spot °C" not in c["legend"] and "top-oil °C" not in c["legend"] for c in charts.values())


def test_short_files_say_that_life_is_scaled_to_a_year(app):
    """Catches: a winter month reported 'per year' without saying it was multiplied ×12 (physics review)."""
    st = read_station_csv(EXAMPLE)
    st = replace(st, p_kw=st.p_kw.iloc[:24 * 31], q_kvar=st.q_kvar.iloc[:24 * 31])            # January only
    res = assess_station(st, 500, ambient_for(st.index))
    note = next(n for n in res.verdict.notes if "life used per year is scaled" in n)
    assert "×11.8" in note and "cover 31 days" in note and "winter-only file overstates" in note
    full = read_station_csv(EXAMPLE)
    part = replace(full, p_kw=full.p_kw.iloc[:7900], q_kvar=full.q_kvar.iloc[:7900])  # whole days, never "329.167"
    assert "cover 329 days:" in next(n for n in assess_station(part, 500, ambient_for(part.index)).verdict.notes if "scaled" in n)
    page = station_card_html(res, None)
    assert "744 h of data; life used per year is scaled ×11.8 to a year" in page
    day = replace(st, p_kw=st.p_kw.iloc[:24], q_kvar=st.q_kvar.iloc[:24])     # one day: no '1 days', no 3.6e+02
    note = next(n for n in assess_station(day, 500, ambient_for(day.index)).verdict.notes if "scaled" in n)
    assert "cover 24 hours:" in note and "×365" in note and "e+" not in note


def test_overlay_energy_is_split_from_the_measured_energy(app):
    """Catches: 'energy in the period' quietly including the synthetic overlay (physics review)."""
    res, _ = _assess(EXAMPLE, 500, "meters", "Europe/Stockholm", "start", 96190, True, "kiosk", 3, 2, 7)
    o = res.overlay
    assert o["n_ev"] == 3 and o["n_hp"] == 2 and o["seed"] == 7 and o["charger_kw"] == 11
    measured = read_station_csv(EXAMPLE).p_kw.clip(lower=0).sum() / 1000
    assert o["measured_mwh"] == pytest.approx(measured, rel=1e-9)
    page = station_card_html(res, None)
    assert f"{measured:,.1f} MWh measured; " in page and "with the synthetic overlay" in page
    assert "+3 EV chargers (11 kW each), +2 heat pumps, random draw 7" in page


def test_headings_stay_with_their_section_on_paper(station, charts):
    """Catches: a chart heading alone at the foot of a page, a table's first rows stranded (usability review), also
    with a long list of notes."""
    res, _ = station
    many = copy.deepcopy(res)
    many.verdict.notes.extend(f"note {i}: a long line of text that a real file can produce about its columns" for i in range(40))
    for r in (res, many):
        doc = build_card_document(r, None, charts=charts)
        items = page_of_items(doc)
        for i, (kind, first, last) in enumerate(items[:-1]):
            if kind == "heading":
                assert items[i + 1][1] == first, f"heading {i} on page {first}, its first item on page {items[i + 1][1]}"


def test_export_follows_the_result_the_run_and_the_inputs(app, station, monkeypatch):
    """Catches: Export enabled before there is anything to export, during a run, or for a card whose inputs the
    planner has since changed (a 500 kVA card written while the panel said 800 kVA)."""

    class StubWorker:                              # a run that never finishes until cancelled
        def __init__(self, *a, **k): self._running = False
        def start(self): self._running = True
        def isRunning(self): return self._running
        def cancel(self): self._running = False
        finished_ok = failed = type("S", (), {"connect": lambda self, *a: None})()

    monkeypatch.setattr(sp_module, "Worker", StubWorker)
    monkeypatch.setattr(sp_module.QFileDialog, "getSaveFileName",
                        lambda *a, **k: pytest.fail("export_pdf must not open a dialog here"))
    panel = StationPanel()
    assert not panel.export_btn.isEnabled() and "Assess a station first" in panel.export_btn.toolTip()
    panel.path.setText(str(EXAMPLE))
    panel.run()
    assert not panel.export_btn.isEnabled() and not panel.run_btn.isEnabled()
    panel._done(station)                                                         # the run's result arrives …
    panel._worker._running = False                                               # … and its thread ends
    assert panel.export_btn.isEnabled() and panel.result is station[0] and panel._amb is station[1]
    panel.kva.setCurrentIndex(panel.kva.findData(800))                           # the planner changes an input …
    assert not panel.export_btn.isEnabled() and "inputs on the left changed" in panel.export_btn.toolTip()
    panel.export_pdf()                                                           # … and presses Export anyway: nothing
    panel.kva.setCurrentIndex(panel.kva.findData(500))                           # set back: the card matches again
    assert panel.export_btn.isEnabled()
    panel.n_ev.setValue(5)
    assert not panel.export_btn.isEnabled()
    panel.n_ev.setValue(0)
    panel.run()                                                                  # a second run: disabled while it runs
    assert panel.is_running() and not panel.export_btn.isEnabled()
    panel.export_pdf()                                                           # ignored while running: no dialog
    panel.cancel()
    assert panel.export_btn.isEnabled() and panel.run_btn.isEnabled()           # the card on screen still matches


def test_station_panel_layout_keeps_buttons_and_charts_usable(app):
    """Catches: Assess / Export inside the scrolling inputs (below the fold on a laptop), a tabs pane the card
    can squeeze to nothing, ratings offered that the catalogue cannot run."""
    panel = StationPanel()
    w = panel.export_btn
    while w is not None:
        assert not isinstance(w, QScrollArea), "the buttons must not scroll away with the inputs"
        w = w.parentWidget()
    assert panel.tabs.minimumHeight() >= 300
    assert [panel.kva.itemData(i) for i in range(panel.kva.count())] == sorted(TRANSFORMERS) + ["custom"]


def test_legends_sit_above_the_plots_not_on_the_data(app):
    """Catches: a pyqtgraph legend box back inside the plot (it sat on the January peaks); an unlabelled rule line;
    a rule label shown although its line is off the axis."""
    import pyqtgraph as pg
    for cls, words in ((YearTimeline, ("loading", "hot-spot", "outdoor", "80 %", "scrubber")),
                       (DurationCurve, ("80 %", "nameplate", "1.5 × rated")),
                       (WorstWeek, ("hot-spot", "top-oil", "outdoor", "98 °C", "loading"))):
        w = cls()
        assert w.plot.plotItem.legend is None, f"{cls.__name__}: legend box inside the plot"
        assert w.layout().indexOf(w.legend) == 0 and w.layout().indexOf(w.plot) == 1
        for word in words:
            assert word in w.legend.text(), f"{cls.__name__}: legend does not name {word!r}"
    tl = YearTimeline()
    labels = {it.label.format for it in tl.plot.plotItem.items if isinstance(it, pg.InfiniteLine) and it.label is not None}
    assert {"80 %", "nameplate"} <= labels
    dc = DurationCurve()
    dc.plot.setFixedSize(600, 400); dc.setAttribute(pg.QtCore.Qt.WA_DontShowOnScreen, True); dc.show()
    dc.plot.setYRange(0, 110, padding=0); app.processEvents()
    shown = {it.label.format: it.label.isVisible() for it in dc.plot.plotItem.items
             if isinstance(it, pg.InfiniteLine) and it.label is not None}
    assert shown == {"80 %": True, "nameplate": True, "IEC 1.5 × rated": False}   # 150 % is off a 0–110 axis
    dc.plot.setYRange(0, 600, padding=0); app.processEvents()                    # an At-risk station: 80 and 100 crowd
    vis = [it.label for it in dc.plot.plotItem.items if isinstance(it, pg.InfiniteLine) and it.label and it.label.isVisible()]
    rects = [lab.sceneBoundingRect().adjusted(0, 5, 0, -5) for lab in vis]       # the text, without its padding
    assert all(not a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:]), "two labels on each other"
    assert "IEC 1.5 × rated" in {lab.format for lab in vis}                      # the IEC limit wins the space
    dc.plot.setFixedSize(600, 120); app.processEvents()                          # too short: no labels at all
    assert not any(it.label.isVisible() for it in dc.plot.plotItem.items if isinstance(it, pg.InfiniteLine) and it.label)
    dc.close()


def test_a_stacked_card_shows_the_deciding_column_first(app, station):
    """Catches: REALLY (which decides the verdict) below ON PAPER when the columns stack — below the fold on a laptop."""
    from hotspotlv.app.widgets.verdict_card import VerdictCard
    card = VerdictCard()
    card.set_verdict(station[0].verdict, "500 kVA")
    card.resize(300, 600); card._place(stacked=True)
    assert card._cols.itemAtPosition(0, 0).widget() is card._really[0]
    assert card._cols.itemAtPosition(1, 0).widget() is card._paper[0]
    card._place(stacked=False)
    assert card._cols.itemAtPosition(0, 0).widget() is card._paper[0]          # side by side: paper left, really right


def test_export_pdf_writes_the_result_on_screen_and_reports_failures(app, station, monkeypatch, tmp_path):
    """Catches: Card.PDF renamed, a missing suffix, the card naming the path box instead of the result's file,
    'saved' after a failure, buttons live during the write (a second export or a run started meanwhile)."""
    calls, errors, during = [], [], []
    target = {"name": str(tmp_path / "Card.PDF")}

    def fake_dialog(parent, title, suggested, flt):
        calls.append(suggested)
        return target["name"], flt
    monkeypatch.setattr(sp_module.QFileDialog, "getSaveFileName", fake_dialog)
    monkeypatch.setattr(sp_module, "show_error", lambda parent, title, msg: errors.append(msg))
    real_write = sp_module.write_station_card

    def spy_write(*a, **k):
        during.append((panel.export_btn.isEnabled(), panel.run_btn.isEnabled()))
        return real_write(*a, **k)
    monkeypatch.setattr(sp_module, "write_station_card", spy_write)

    panel = StationPanel()
    panel.path.setText(str(EXAMPLE))
    panel._pending_inputs = panel._inputs()                                      # what run() records
    panel._done(station)
    panel.path.setText(str(tmp_path / "other.csv"))                              # a different file in the box …
    panel.path.setText(str(EXAMPLE))                                             # … and back: the card matches again
    panel.export_pdf()
    from PySide6.QtCore import QStandardPaths                                    # the shipped example lives inside
    docs = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))  # the program: suggest Documents
    assert calls == [str(docs / "station-card-ns-102.pdf")]
    assert (tmp_path / "Card.PDF").exists() and not (tmp_path / "Card.PDF.pdf").exists()
    assert during == [(False, False)]                                            # nothing clickable while writing
    assert panel.export_btn.isEnabled() and panel.run_btn.isEnabled()
    assert f"saved as Card.PDF in the folder {tmp_path.name}" in panel.status.text() and not errors
    assert panel.status.toolTip() == str(tmp_path / "Card.PDF")
    pypdf = pytest.importorskip("pypdf")
    assert "ns-102.csv" in pypdf.PdfReader(str(tmp_path / "Card.PDF")).metadata.title     # the result's file
    target["name"] = str(tmp_path / "plain")                                     # no suffix → .pdf appended
    panel.export_pdf()
    assert (tmp_path / "plain.pdf").exists()
    target["name"] = str(tmp_path / "no-such-folder" / "card.pdf")               # a failure: one error, never "saved"
    panel.export_pdf()
    assert len(errors) == 1 and "does not exist" in errors[0]
    assert panel.status.text() == "The station card was not saved."
    assert panel.export_btn.isEnabled() and panel.run_btn.isEnabled()


def test_card_text_escapes_file_names_and_notes(station):
    """Catches: a file name or a note with < or & vanishing into markup (or injecting it)."""
    res, _ = station
    tricky = copy.deepcopy(res)
    tricky.verdict.notes.append("K < 1 & θ > 98 °C <i>not italic</i>")
    page = station_card_html(tricky, None, source_name="Ställverk <b>7</b> & 'Nord'.csv")
    assert "Ställverk &lt;b&gt;7&lt;/b&gt; &amp; &#x27;Nord&#x27;.csv" in page and "<b>7</b>" not in page
    assert "K &lt; 1 &amp; θ &gt; 98 °C &lt;i&gt;not italic&lt;/i&gt;" in page


PROBE = """
import os, sys
os.environ["QT_QPA_PLATFORM"] = "windows"; os.environ["QT_SCALE_FACTOR"] = "1.25"
from PySide6.QtWidgets import QApplication
app = QApplication([])
from hotspotlv.app.widgets.station_panel import EXAMPLE, _assess
from hotspotlv.app import report
res, amb = _assess(EXAMPLE, 500, "meters", "Europe/Stockholm", "start", 96190, True, "kiosk", 0, 0, 0)
charts = report.render_charts(res, amb)
img = charts["period"]["image"]
dark = sum(img.pixelColor(x, y).lightness() < 140 for x in range(img.width() - 6, img.width()) for y in range(img.height()))
print("DPR", img.devicePixelRatio(), img.width(), img.height(), "EDGE", dark)
report.write_station_card(res, amb, sys.argv[1], source_name="ns-102.csv")
report.write_station_card(res, amb, sys.argv[1] + ".long.pdf", source_name="NS-102-" + "Kopparlunden-" * 10 + ".csv")
os._exit(0)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="the windows platform plugin gives real fonts and screen scaling")
def test_card_on_a_scaled_windows_screen_has_page_numbers_and_uncut_charts(tmp_path, station, charts):
    """Real fonts at 125 % scaling in a separate process (the suite itself runs offscreen, without fonts):
    every page says 'page i of n', the charts are 2x print images whatever the screen, and the right edge of the
    period chart carries no text (the right-axis label was cut in half before 22 Oct 2026)."""
    import subprocess
    script = tmp_path / "probe.py"
    out = tmp_path / "card.pdf"
    script.write_text(PROBE, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    r = subprocess.run([sys.executable, str(script), str(out)], capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    line = next(x for x in r.stdout.splitlines() if x.startswith("DPR"))
    assert line.split()[1:4] == ["2.0", str(2 * CHART_W), str(2 * CHART_H)]
    assert int(line.split()[-1]) == 0, "text pixels at the right edge of the period chart: a label is cut"
    fitz = pytest.importorskip("fitz")
    import re
    heads = {h.strip() for h in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", station_card_html(station[0], charts))}
    doc = fitz.open(str(out))
    n = len(doc)
    assert 4 <= n <= 6
    for i, page in enumerate(doc):
        lines = [x.strip() for x in page.get_text().splitlines() if x.strip()]
        assert lines and lines[-1].endswith(f"page {i + 1} of {n}"), f"page {i + 1} has no page number"
        body = lines[:-1]
        assert len(body) >= (3 if i == n - 1 else 8), f"page {i + 1} is (nearly) empty: {body}"   # content, not a footer
        assert body[-1] not in heads, f"page {i + 1} ends with the heading {body[-1]!r}"           # no stranded heading
        for info in page.get_image_info():
            assert page.rect.contains(fitz.Rect(info["bbox"])), f"an image lies outside page {i + 1}"
    long_doc = fitz.open(str(out) + ".long.pdf")                                  # a 140-character file name
    for i, page in enumerate(long_doc):
        foot = [b for b in page.get_text("blocks") if "page " in b[4] and " of " in b[4]][-1]
        assert foot[4].strip().startswith("Hotspot LV station card") and "…" in foot[4]
        assert foot[0] >= page.rect.x0 and foot[2] <= page.rect.x1, "the footer runs off the page"



def test_export_guards_hold(app, station, monkeypatch, tmp_path):
    """The fix pass's protections, each pinned (round-2 code review: they were right but every one of them slipped
    its mutant): a failure while printing keeps the old card; the inputs that count are those of Assess, not of the
    moment the result arrives; a result that failed to draw is not exportable; an unexpected error says 'not saved';
    no wait cursor over the error dialog."""
    from hotspotlv.app import report as report_module
    errors, cursors = [], []

    def record(parent, title, msg):
        errors.append(msg)
        cursors.append(QApplication.overrideCursor())
    monkeypatch.setattr(sp_module, "show_error", record)

    # a failure in the middle of printing: the old card stays, no temporary file is left
    target = tmp_path / "card.pdf"
    target.write_bytes(b"OLD CARD")

    def half_print(doc, device, title, footer):
        device.write(b"%PDF-1.4 half a card")
        raise RuntimeError("printer fell over")
    monkeypatch.setattr(report_module, "_print", half_print)
    with pytest.raises(RuntimeError):
        report_module.write_station_card(station[0], station[1], target)
    assert target.read_bytes() == b"OLD CARD" and sorted(x.name for x in tmp_path.iterdir()) == ["card.pdf"]
    monkeypatch.undo()
    monkeypatch.setattr(sp_module, "show_error", record)

    class StubWorker:
        def __init__(self, *a, **k): self._running = False
        def start(self): self._running = True
        def isRunning(self): return self._running
        def cancel(self): self._running = False
        finished_ok = failed = type("S", (), {"connect": lambda self, *a: None})()
    monkeypatch.setattr(sp_module, "Worker", StubWorker)

    panel = StationPanel()
    panel.path.setText(str(EXAMPLE))
    panel.run()
    panel.kva.setCurrentIndex(panel.kva.findData(800))                          # changed while the run computes
    panel._done(station); panel._worker._running = False
    assert not panel.export_btn.isEnabled()                                      # the card is for 500 kVA, the box says 800
    panel.kva.setCurrentIndex(panel.kva.findData(500))
    assert panel.export_btn.isEnabled()

    monkeypatch.setattr(panel.timeline, "set_data", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("draw")))
    panel.run(); panel._done(station); panel._worker._running = False           # a result that failed to draw
    assert len(errors) == 1 and "draw" in errors[0] and not panel.export_btn.isEnabled() and panel.result is None
    monkeypatch.undo()
    monkeypatch.setattr(sp_module, "show_error", record)

    panel.run(); panel._done(station)
    panel._worker._running = False
    assert panel.export_btn.isEnabled()
    monkeypatch.setattr(sp_module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "x.pdf"), ""))
    monkeypatch.setattr(sp_module, "write_station_card", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("odd")))
    panel.export_pdf()                                                           # an unexpected error: said, not raised
    assert errors[-1] == "RuntimeError: odd" and panel.status.text() == "The station card was not saved."
    assert cursors[-1] is None                                                    # the wait cursor is gone first


def test_closing_fixes_are_pinned(app, station, monkeypatch, tmp_path):
    """Round 3 (R5): footer elision, a styled heading still gets its break, the random draw matters only with an
    overlay, the worst-week caption at the card's precision, and the Fleet tab after Cancel."""
    import pandas as pd
    from hotspotlv.app import report as report_module
    from hotspotlv.app.widgets.charts import WorstWeek
    out = report_module._with_breaks("<h2 style='color:red'>A</h2><h3>B</h3>", {0, 1})
    assert "<h2 style='page-break-before: always; color:red'>" in out and "<h3 style='page-break-before: always'>" in out
    panel = StationPanel()
    panel.path.setText(str(EXAMPLE)); panel._pending_inputs = panel._inputs(); panel._done(station)
    assert panel.export_btn.isEnabled()
    panel.seed.setValue(7)                                                       # no overlay: the draw changes nothing
    assert panel.export_btn.isEnabled()
    panel.n_ev.setValue(3)
    assert not panel.export_btn.isEnabled()
    res, amb = station
    w = WorstWeek()
    week = res.thermal.worst_week()
    w.set_week(week, res.year.hourly, amb)
    assert f"max hot-spot {week['theta_h_max'].max():.1f} °C" in w.caption.text()
    from hotspotlv.app.widgets import fleet_panel as fp

    from PySide6.QtCore import QObject, Signal

    class StubWorker(QObject):                                                   # real signals: the slot sees its sender
        progress = Signal(int, int, str)
        finished_ok = Signal(object)
        failed = Signal(str)
        def __init__(self, *a, **k): super().__init__(); self._running = False
        def start(self): self._running = True
        def isRunning(self): return self._running
        def cancel(self): self._running = False
    monkeypatch.setattr(fp, "Worker", StubWorker)
    fleet = fp.FleetPanel()
    fleet.table_df = pd.DataFrame({"station_id": ["A"]})                         # a table already on screen
    fleet.path.setText(str(EXAMPLE.parent / "fleet_index.csv"))
    fleet.run()
    retired = fleet._worker
    fleet.cancel()
    assert fleet.status.text() == "Cancelled." and fleet.export_btn.isEnabled()
    retired.progress.emit(1, 3, "NS-101")                                        # a tick from before the Cancel
    assert fleet.status.text() == "Cancelled."
