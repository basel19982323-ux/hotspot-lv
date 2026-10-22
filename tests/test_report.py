"""The station card PDF (mode B) and the legend strips. The card must say what the screen card says, carry
the three charts, and list the assumptions the run used; legends sit above the plots, never on the data.
Offscreen (no fonts): the PDF's structure is checked with pypdf, its words through the HTML. Each test is a
mutation trap, like tests/test_app.py; the second group follows the review of 22 Oct 2026 (card-ux,
card-physics): no false "saved", no stale card, no clipped-model number shown as real, headings kept with
their section."""

import copy
import html
import math
import os
import sys
from dataclasses import replace

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
    paper_at, really_at, inputs_at = page.index("ON PAPER (NAMEPLATE RULE)"), page.index("REALLY (THERMAL MODEL"), page.index("<h2>Inputs")
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
    assert "<h2 style='page-break-before: always'>How the numbers were made" in page   # the assumption list opens a page
    assert "IEC 60076-7:2005" in page and "2018" in page                         # the edition the table numbers belong to
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

    def series_pixels(img, colour):
        want = QColor(colour)
        n = 0
        for y in range(0, img.height(), 2):
            for x in range(0, img.width(), 2):
                c = img.pixelColor(x, y)
                n += abs(c.red() - want.red()) < 25 and abs(c.green() - want.green()) < 25 and abs(c.blue() - want.blue()) < 25
        return n
    assert series_pixels(charts["period"]["image"], LOAD_COLOR) > 500
    assert series_pixels(charts["duration"]["image"], LOAD_COLOR) > 300
    assert series_pixels(charts["worst_week"]["image"], HOTSPOT_COLOR) > 300
    v = res.verdict
    assert f"Above 80 %: {v.on_paper['hours above 80 % of nameplate']:,.0f} h" in charts["duration"]["caption"]
    assert charts["period"]["caption"].startswith("At the time of peak loading (black line)")
    assert "hot-spot" in charts["period"]["caption"] and "peaks a little later" in charts["period"]["caption"]
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
    assert page.index("beyond the thermal model's range") < page.index("ON PAPER")           # said first, on page 1
    assert any("beyond the thermal model's range" in n for n in v.notes)


def test_short_files_say_that_life_is_scaled_to_a_year(app):
    """Catches: a winter month reported 'per year' without saying it was multiplied ×12 (card-physics M1)."""
    st = read_station_csv(EXAMPLE)
    st = replace(st, p_kw=st.p_kw.iloc[:24 * 31], q_kvar=st.q_kvar.iloc[:24 * 31])            # January only
    res = assess_station(st, 500, ambient_for(st.index))
    note = next(n for n in res.verdict.notes if "life used per year is scaled" in n)
    assert "×12" in note and "31 days" in note and "winter-only file overstates" in note
    page = station_card_html(res, None)
    assert "744 h of data; life used per year is scaled ×12 to a year" in page


def test_overlay_energy_is_split_from_the_measured_energy(app):
    """Catches: 'energy in the period' quietly including the synthetic overlay (card-physics M3)."""
    res, _ = _assess(EXAMPLE, 500, "meters", "Europe/Stockholm", "start", 96190, True, "kiosk", 3, 2, 7)
    o = res.overlay
    assert o["n_ev"] == 3 and o["n_hp"] == 2 and o["seed"] == 7 and o["charger_kw"] == 11
    measured = read_station_csv(EXAMPLE).p_kw.clip(lower=0).sum() / 1000
    assert o["measured_mwh"] == pytest.approx(measured, rel=1e-9)
    page = station_card_html(res, None)
    assert f"{measured:,.1f} MWh measured; " in page and "with the synthetic overlay" in page
    assert "+3 EV chargers (11 kW each), +2 heat pumps, random draw 7" in page


def test_headings_stay_with_their_section_on_paper(station, charts):
    """Catches: a chart heading alone at the foot of a page, a table's first rows stranded (card-ux 6), also
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
    assert [panel.kva.itemData(i) for i in range(panel.kva.count())] == sorted(TRANSFORMERS)


def test_legends_sit_above_the_plots_not_on_the_data(app):
    """Catches: a pyqtgraph legend box back inside the plot (it sat on the January peaks); an unlabelled rule line;
    a rule label shown although its line is off the axis."""
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
    dc = DurationCurve()
    dc.plot.setFixedSize(600, 400); dc.setAttribute(pg.QtCore.Qt.WA_DontShowOnScreen, True); dc.show()
    dc.plot.setYRange(0, 110, padding=0); app.processEvents()
    shown = {it.label.format: it.label.isVisible() for it in dc.plot.plotItem.items
             if isinstance(it, pg.InfiniteLine) and it.label is not None}
    assert shown == {"80 % rule": True, "nameplate": True, "IEC 1.5 × rated": False}   # 150 % is off a 0–110 axis
    dc.plot.setFixedSize(600, 120); app.processEvents()                          # too short: no labels at all
    assert not any(it.label.isVisible() for it in dc.plot.plotItem.items if isinstance(it, pg.InfiniteLine) and it.label)
    dc.close()


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
    assert calls == [str(EXAMPLE.with_name("station-card-ns-102.pdf"))]          # suggested next to the station's CSV
    assert (tmp_path / "Card.PDF").exists() and not (tmp_path / "Card.PDF.pdf").exists()
    assert during == [(False, False)]                                            # nothing clickable while writing
    assert panel.export_btn.isEnabled() and panel.run_btn.isEnabled()
    assert "saved as Card.PDF" in panel.status.text() and not errors
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
os._exit(0)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="the windows platform plugin gives real fonts and screen scaling")
def test_card_on_a_scaled_windows_screen_has_page_numbers_and_uncut_charts(tmp_path):
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
    doc = fitz.open(str(out))
    n = len(doc)
    assert 4 <= n <= 6
    for i, page in enumerate(doc):
        assert f"page {i + 1} of {n}" in page.get_text(), f"page {i + 1} has no page number"
