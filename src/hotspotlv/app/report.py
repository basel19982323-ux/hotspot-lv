"""The station card as a PDF — what a planner attaches to a planning memo (mode B).

    StationResult ──► render_charts()        the three chart widgets drawn off-screen at their printed size
                 ──► station_card_html()    verdict · inputs · charts · notes │ how the numbers were made
                 ──► build_card_document()  QTextDocument on A4, headings kept with their section
                 ──► write_station_card()   printed to a temporary file, then moved over the target

The card repeats what the screen shows — every number goes through the same
formatter as the verdict card, the charts are the app's own chart classes, the
captions and legends are the widgets' own text — and adds what a reader without
the app needs: how S at the transformer was estimated, the thermal constants
used, every rule threshold with its source, and what was NOT checked. Nothing is
recomputed here.

The reviews of 22 Oct 2026 (usability, physics) shaped three choices:
  * charts are rendered at the size they print (680 × 257 px at 96 dpi = the A4 text
    width) with 2× pixels, so their 9-pt fonts print at 9 pt, not shrunk to 6.8 pt;
  * the file is written through QSaveFile (a temporary file renamed over the target on
    commit), so a card that is open in a PDF viewer or read-only gives an error instead
    of a false "saved", and an old card is never half overwritten;
  * every page carries "page i of n" (QTextDocument.print_ skips page numbers for a
    document with a page size, so the pages are drawn one by one);
  * QTextDocument has no "keep with next": `build_card_document` measures the layout,
    writes a page break before the first heading left at the foot of a page, rebuilds, repeats.
"""

from __future__ import annotations

import html
import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from PySide6.QtCore import (QCoreApplication, QDir, QEvent, QEventLoop, QIODevice, QMarginsF, QRectF, QSaveFile, QSize,
                            QSizeF, Qt, QUrl)
from PySide6.QtGui import (QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextBlock,
                           QTextDocument, QTextFormat, QTextFrame)
from PySide6.QtWidgets import QApplication

from hotspotlv import __version__
from hotspotlv.app.theme import ACCENT, MUTED, PANEL, VERDICT_COLORS
from hotspotlv.app.widgets.charts import (TIMELINE_LEGEND, DurationCurve, WorstWeek, YearTimeline, interval_text,
                                         legend_html, without_transformer_temperatures)
from hotspotlv.app.widgets.verdict_card import fmt_card_value
from hotspotlv.station import CABLE_LOSS_SHARE_AT_RATED, NOT_CHECKED_KEYS, StationResult, scale_text
from hotspotlv.thermal import IEC_LIMITS_C, IEC_LIMITS_K, KIOSK_AIR_RISE_K, REF_HOT_SPOT_C, ThermalParams
from hotspotlv.timeseries import NAMEPLATE_RULE_PCT, VMAX_LIMIT_PU, VMIN_LIMIT_PU
from hotspotlv.verdict import (CABLE_SOURCE, DESIGN_LIFE_DAYS_PER_YEAR, HOURS_PER_YEAR, IEC_TABLE_4, OUT_OF_RANGE_RULE,
                               THERMAL_RANGE_K, thermal_ok,
                               VMAX_PLAN_PU, VMIN_PLAN_PU, WATCH_LIFE_DAYS_PER_YEAR)
from hotspotlv.weather import SMHI_STATION, SMHI_STATION_NAME

DPI = 96
PAGE_W = 680                         # A4 text width at 96 dpi with 15 mm margins: 180 mm = 680 px
CHART_W, CHART_H = PAGE_W, 257       # logical px = printed size, so the app's 9-pt chart fonts print at 9 pt
RENDER_DPR = 2.0                     # pixels per logical px in the image: 192 dpi on paper, whatever the screen
CHARTS = ("period", "duration", "worst_week")
NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four"}


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------
def _settle(plot, max_passes: int = 20) -> None:
    """Process events until the plot's layout stops moving. The axes widen once their labels are set, one
    event pass later than the data; a grab taken before that cuts the right-axis label in half (measured)."""
    last = None
    for _ in range(max_passes):
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)   # a click must wait until the export returns
        plot.plotItem.layout.activate()
        geo = tuple(plot.getAxis(side).geometry().getCoords() for side in ("left", "right", "bottom"))
        if geo == last:
            break
        last = geo
    right = plot.getAxis("right")
    if right.isVisible() and right.label.sceneBoundingRect().right() > plot.viewport().width():
        raise RuntimeError("the right-axis label does not fit the chart: it would be cut in the image")


def _render(plot) -> QImage:
    """The plot's scene painted into an image of its logical size × RENDER_DPR — independent of the screen's
    scaling. The scene, not the widget: QWidget.render into a 2× image scaled the view twice (measured: only the
    top-left quarter of each chart reached the page)."""
    size = plot.size()
    image = QImage(QSize(round(size.width() * RENDER_DPR), round(size.height() * RENDER_DPR)), QImage.Format_RGB32)
    image.setDevicePixelRatio(RENDER_DPR)
    image.fill(QColor("white"))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        plot.scene().render(painter, QRectF(0, 0, size.width(), size.height()), plot.sceneRect())
    finally:
        painter.end()
    return image


def render_charts(res: StationResult, ambient: pd.Series, width: int = CHART_W, height: int = CHART_H) -> dict:
    """Draw the three charts of the Own-station tab off-screen and paint them into images.
    Returns {name: {"image": QImage, "legend": rich text, "caption": str}}. GUI thread only (needs a QApplication)."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        raise RuntimeError("render_charts needs a QApplication (widgets), on the GUI thread")
    step_h = res.station.step_min / 60
    out = {}
    for name, cls in (("period", YearTimeline), ("duration", DurationCurve), ("worst_week", WorstWeek)):
        w = cls()
        try:
            w.setAttribute(Qt.WA_DontShowOnScreen, True)          # laid out and painted, never on the screen
            w.plot.setFixedSize(width, height)
            w.show()
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            ok = thermal_ok(res.verdict)
            if name == "period":
                w.set_data(res.year.hourly, res.thermal.hourly if ok else None, ambient)
                w.scrubber.label.setFormat("peak loading")           # "drag me" means nothing on paper
                items = TIMELINE_LEGEND[:-1] if ok else without_transformer_temperatures(TIMELINE_LEGEND[:-1])
                legend = legend_html(items + (("#000000", "&#9474;", "time of peak loading"),))
                caption = f"At the time of peak loading (black line): {w.readout.text()}."
                if ok:                     # where the hot-spot really peaks — not always after the load peak, nor in
                    th = res.thermal.hourly["theta_h_max"]      # the worst week (numbers audit, 23 Oct 2026)
                    t_max = th.idxmax()
                    in_week = t_max in res.thermal.worst_week().index
                    caption += (f" The highest hot-spot, {th.max():.1f} °C, is reached "
                                f"{interval_text(t_max, pd.Timedelta(minutes=res.station.step_min))}"
                                + (", inside the worst week." if in_week else ", outside the worst week (which is "
                                   "ranked by insulation life used, not by temperature)."))
            elif name == "duration":
                w.set_year(res.year.hourly, step_h)
                caption, legend = w.caption.text(), w.legend.text()
            else:
                w.set_week(res.thermal.worst_week(), res.year.hourly, ambient, step_h, thermal_valid=ok)
                caption, legend = w.caption.text(), w.legend.text()      # after set_week: it filters the legend
            _settle(w.plot)
            image = _render(w.plot)
            dpr = image.devicePixelRatio()
            if (round(image.width() / dpr), round(image.height() / dpr)) != (width, height):
                raise RuntimeError(f"chart {name} rendered {image.width()}×{image.height()} px, wanted {width}×{height} logical")
            out[name] = {"image": image, "legend": legend, "caption": caption}
        finally:
            w.close()
            w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)  # free them now: processEvents does not, and a
    return out                                                      # script without an event loop kept 57 per export


# ---------------------------------------------------------------------------
# the assumption tables (built from the engine's constants, never typed here)
# ---------------------------------------------------------------------------
def thermal_rows(p: ThermalParams, kva: float | None = None) -> list[tuple[str, str, str]]:
    """(name, value, meaning) for every constant the thermal run used."""
    rise = f"{p.d_theta_or:g} K"
    if p.enclosure_rise_k:
        rise += (f" (= {p.d_theta_or - p.enclosure_rise_k:g} K outdoor + {p.enclosure_rise_k:g} K for the kiosk: half the "
                 "kiosk air rise of IEC 60076-7:2005 Table 6, eq. 11; 2018: Table 5")
        sizes = sorted(KIOSK_AIR_RISE_K)
        if kva is not None and kva > sizes[-1]:
            rise += f" — the table stops at {sizes[-1]} kVA, so that row is used"
        elif kva is not None and kva < sizes[0]:
            rise += f" — the table starts at {sizes[0]} kVA, so that row is used"
        rise += ")"
    return [
        ("enclosure", p.enclosure, "kiosk is the Swedish norm; it raises the top-oil rise"),
        ("Δθor", rise, "top-oil rise over ambient at rated load"),
        ("Δθhr", f"{p.d_theta_hr:g} K", "hot-spot rise over top-oil at rated load (H·gr in the standard)"),
        ("R", f"{p.R:.2f}", "load loss / no-load loss at rated load (Ecodesign Tier 2 catalogue values)"),
        ("x", f"{p.x:g}", "oil exponent"),
        ("y", f"{p.y:g}", "winding exponent"),
        ("k11 · k21 · k22", f"{p.k11:g} · {p.k21:g} · {p.k22:g}", "IEC 60076-7 thermal model constants, distribution ONAN"),
        ("τo · τw", f"{p.tau_o_min:g} min · {p.tau_w_min:g} min", "oil and winding time constants"),
        ("paper", p.paper, f"ageing rate V = 2^((θh − {REF_HOT_SPOT_C:g}) / 6): one day of life per day at {REF_HOT_SPOT_C:g} °C"
                            if p.paper == "kraft" else "thermally upgraded paper (Arrhenius, 110 °C reference)"),
    ]


def rule_rows(checked: bool = False) -> list[tuple[str, str, str, str, str]]:
    """(what, Watch if, At risk if, source, status) — the verdict's rules in the order of verdict.py."""
    nc = "not checked here: station data carries no feeder model"
    return [
        ("insulation life used", f"> {WATCH_LIFE_DAYS_PER_YEAR:g} days/yr", f"> {DESIGN_LIFE_DAYS_PER_YEAR:g} days/yr",
         "IEC 60076-7:2005 eq. (2), (4): 98 °C = the design rate; the Watch margin is a chosen allowance for model optimism",
         "checked"),
        ("hot-spot", f"> {IEC_LIMITS_C['normal_cyclic_hot_spot']:g} °C", f"> {IEC_LIMITS_C['long_time_emergency_hot_spot']:g} °C",
         IEC_TABLE_4, "checked"),
        ("top-oil", f"> {IEC_LIMITS_C['normal_cyclic_top_oil']:g} °C", f"> {IEC_LIMITS_C['long_time_emergency_top_oil']:g} °C",
         IEC_TABLE_4, "checked"),
        ("load", f"> {IEC_LIMITS_K['normal_cyclic']:g} × rated", f"> {IEC_LIMITS_K['long_time_emergency']:g} × rated",
         IEC_TABLE_4, "checked"),
        ("house voltage", f"any hour outside {VMIN_PLAN_PU:g}–{VMAX_PLAN_PU:g} pu (planning band)",
         f"any hour outside {VMIN_LIMIT_PU:g}–{VMAX_LIMIT_PU:g} pu", "EIFS 2023:3 6 kap. 3 § (all 10-min values within ±10 %)",
         "checked" if checked else nc),
        ("cable", "> 100 % of its rating", "—", CABLE_SOURCE, "checked" if checked else nc),
        (f"hours above {NAMEPLATE_RULE_PCT:g} % / 100 % of nameplate", "never changes the verdict", "never changes the verdict",
         "echoes the DSO rule of thumb (Energiforsk 2018:552), which applies 80 % to summed meter kW, "
         "about 10 % below the transformer's kVA — shown for contrast", "counted"),
    ]


def s_estimate_text(res: StationResult) -> str:
    st = res.station
    q_loss = "Q_loss = uk·K²·S_rated is the transformer's reactive consumption"
    tr_losses = "the transformer's own losses from the catalogue (no-load loss + load loss × K²)"
    if st.s_kva_measured is not None:
        return ("Apparent power S at the transformer was taken from the file's kVA column as measured at the "
                "transformer; no losses were added. K is therefore S at the LV terminals over the rating — about 3 % "
                "below the MV-side current ratio the other two ways of reading a file give at rated load.")
    if st.measured_at == "meters":
        return (f"The file is the sum of customer meters. S at the transformer = √((P + losses)² + (Q + Q_loss)²), where "
                f"the LV-cable losses are taken as {CABLE_LOSS_SHARE_AT_RATED * 100:.1f} % of P at rated load, growing with "
                f"the loading K (I²R) — a share fitted on this tool's 90-house Al 240 model feeder, an assumption: a shorter "
                f"or more heavily loaded feeder differs — plus {tr_losses}; {q_loss}. Q comes from the file when it has a "
                "kvar column, otherwise it is estimated (see the notes).")
    return (f"The file was measured at the transformer's LV terminals: only {tr_losses} and its reactive consumption "
            "uk·K²·S_rated were added (no cable losses).")


# ---------------------------------------------------------------------------
# the card
# ---------------------------------------------------------------------------
def _esc(x) -> str:
    return html.escape(str(x))


def _kv_rows(items: dict) -> str:
    rows = []
    for k, v in items.items():
        muted = f" style='color:{MUTED}'" if v is None or (isinstance(v, float) and math.isnan(v)) else ""
        rows.append(f"<tr><td style='color:{MUTED}'>{_esc(k)}</td><td align='right'{muted}><b>{_esc(fmt_card_value(k, v))}</b></td></tr>")
    return "".join(rows)


EXAMPLE_CREDIT = ("Hofmann, Bjarghov &amp; Nessa (2023), Zenodo, https://doi.org/10.5281/zenodo.8423312 — CC BY 4.0, "
                  "https://creativecommons.org/licenses/by/4.0/")       # HTML: the card's markup, not data
PVGIS_CREDIT = "PVGIS © European Communities, 2001–2026, 2023 hourly output"   # round 3: the examples contain it


def is_shipped_example(source) -> bool:
    """A station file from the program's own examples folder — a synthetic station, not a measurement (round-2
    review: its card looked like a real station and did not credit the data it is built from)."""
    from hotspotlv.paths import package_data
    try:
        return Path(source).resolve().parent == (package_data() / "examples").resolve()
    except (OSError, TypeError, ValueError):
        return False


def station_card_html(res: StationResult, charts: dict | None = None, source_name: str | None = None,
                      generated: datetime | None = None) -> str:
    """The whole card as HTML for QTextDocument (a subset of HTML 4 / CSS 2: tables, colours, images).
    `charts` as from render_charts(); images are referenced as chart://<name> (resources added by the caller).
    Every string that comes from data (file name, notes, captions) is escaped; the legends are the app's own markup."""
    v = res.verdict
    st = res.station
    generated = generated or datetime.now()
    source_name = source_name or st.source or "station data"
    color = VERDICT_COLORS.get(v.level, MUTED)
    reasons = "".join(f"<li>{_esc(r)}</li>" for r in v.reasons)
    reasons_html = f"<ul>{reasons}</ul>" if reasons else f"<p style='color:{MUTED}'>No rule is violated.</p>"
    clipped = any(r.rule == OUT_OF_RANGE_RULE for r in v.at_risk)
    clip_html = (f"<p style='color:{VERDICT_COLORS['At risk']}'><b>The loading went above {THERMAL_RANGE_K:g} × rated, "
                 "the IEC short-time emergency limit.</b> Life used, hot-spot and top-oil are not computed (shown as –) and "
                 "not drawn. Check the transformer rating first.</p>") if clipped else ""
    not_checked = [k for k in NOT_CHECKED_KEYS if v.really.get(k, 0) is None]

    hours = len(st.index) * st.step_min / 60
    scale = HOURS_PER_YEAR / hours
    period = f"{st.period()} — {hours:,.0f} h of data"
    if abs(scale - 1) > 0.1:
        period += f"; life used per year is scaled ×{scale_text(scale)} to a year (see the notes)"
    example = is_shipped_example(st.source)
    example_html = (f"<p style='color:{ACCENT}'><b>Synthetic example</b> — not a measured station. Built from the "
                    f"Norwegian household data of {EXAMPLE_CREDIT}, with SMHI temperatures and PVGIS solar output "
                    f"({PVGIS_CREDIT}).</p>") if example else ""
    example_footer = (f" Base loads of this synthetic example: {EXAMPLE_CREDIT}; its rooftop solar: {PVGIS_CREDIT}."
                      if example else "")
    smhi = f"{res.smhi_station}"
    if res.smhi_station == SMHI_STATION:
        smhi += f" {SMHI_STATION_NAME} (the default — choose the station nearest the place)"
    energy_now = res.metrics()["energy_mwh"]                 # the fleet table's formula: one number, one place
    if res.overlay and "measured_mwh" in res.overlay:
        energy = (f"{res.overlay['measured_mwh']:,.1f} MWh measured; {energy_now:,.1f} MWh with the synthetic overlay "
                  "(net imports per step)")
    else:
        energy = f"{energy_now:,.1f} MWh (net imports per step)"
    inputs = [
        ("file", source_name + (" (a synthetic example shipped with the program)" if example else "")),
        ("period", period),
        ("resolution", f"{st.step_min:g}-min values, {st.gaps_filled} missing steps filled"),
        ("values are", "the sum of customer meters" if st.measured_at == "meters" else "measured at the transformer"),
        ("transformer rating", f"{res.rated_kva:g} kVA"),
        ("outdoor temperature", f"SMHI station {smhi}"),
        ("enclosure", res.thermal.params.enclosure),
        ("energy in the period", energy),
    ]
    if res.overlay:
        o = res.overlay
        inputs.append(("synthetic overlay",
                       f"+{o.get('n_ev', 0)} EV chargers ({o.get('charger_kw', 11):g} kW each), +{o.get('n_hp', 0)} heat pumps, "
                       f"random draw {o.get('seed', 0)} — an upper bound added on top of the measured profile "
                       "(no base load, each synthetic house capped at its main fuse)"))
    inputs_html = "".join(f"<tr><td style='color:{MUTED}' width='28%'>{_esc(k)}</td><td>{_esc(val)}</td></tr>" for k, val in inputs)

    charts_html = ""
    if charts:
        titles = {"period": "The whole period — loading and temperatures",
                  "duration": "Loading duration curve — for how many hours is the station above x %",
                  "worst_week": "The worst week — the seven days that used the most insulation life"}
        for name in CHARTS:
            c = charts[name]
            title = titles[name]
            if clipped and name == "worst_week":
                title = "The heaviest week by loading — no transformer temperatures"
            charts_html += f"<h3>{title}</h3>"
            if clipped and name != "duration":
                charts_html += (f"<p style='color:{VERDICT_COLORS['At risk']}'>No hot-spot or top-oil is drawn: the loading "
                                f"went above {THERMAL_RANGE_K:g} × rated, the IEC short-time emergency limit.</p>")
            charts_html += (f"<p><img src='chart://{name}' width='{PAGE_W}'></p>"
                            f"<p style='color:{MUTED}; font-size:8.5pt'>{c['legend']}</p>"
                            f"<p style='font-size:9pt'>{_esc(c['caption'])}</p>")

    notes_html = "".join(f"<li>{_esc(n)}</li>" for n in v.notes)
    thermal_html = "".join(f"<tr><td><b>{_esc(n)}</b></td><td>{_esc(val)}</td><td style='color:{MUTED}'>{_esc(m)}</td></tr>"
                           for n, val, m in thermal_rows(res.thermal.params, res.rated_kva))
    rules_html = "".join(
        f"<tr><td><b>{_esc(w)}</b></td><td>{_esc(wa)}</td><td>{_esc(ar)}</td><td style='color:{MUTED}'>{_esc(src)}</td>"
        + (f"<td style='color:{MUTED}'><i>{_esc(status)}</i></td></tr>" if status.startswith("not checked")
           else f"<td style='color:{MUTED}'>{_esc(status)}</td></tr>")
        for w, wa, ar, src, status in rule_rows(checked=not not_checked))
    not_checked_html = ""
    if not_checked:
        not_checked_html = (f"{NUMBER_WORDS.get(len(not_checked), str(len(not_checked)))} "
                            f"{'rule reads' if len(not_checked) == 1 else 'rules read'} <i>not checked</i>: "
                            f"{_esc(', '.join(not_checked))}.")
    clip_method = (f" Above {THERMAL_RANGE_K:g} × rated (the IEC short-time emergency current limit) the thermal model "
                   "is not used: life and temperatures were not computed." if clipped else "")

    return f"""
<html><head><style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; color: #1e1e1e; }}
  h1 {{ font-size: 18pt; color: {ACCENT}; margin-bottom: 0; }}
  h2 {{ font-size: 13pt; color: {ACCENT}; margin-top: 16px; }}
  h3 {{ font-size: 11pt; color: {ACCENT}; margin-top: 14px; }}
  td {{ padding: 3px 6px; vertical-align: top; }}
  th {{ padding: 3px 6px; text-align: left; background: {PANEL}; }}
</style></head><body>
<h1>Hotspot LV — station card</h1>
<p style='color:{MUTED}'>{_esc(source_name)} · generated {generated:%d %b %Y %H:%M} · Hotspot LV v{__version__} ·
simulation (IEC 60076-7 model with generic constants) — results are indicative</p>
{example_html}

<table width='100%' cellpadding='8' bgcolor='{PANEL}'><tr><td width='10' bgcolor='{color}'></td><td>
<p style='font-size:20pt; font-weight:bold; color:{color}; margin:0'>{_esc(v.level.upper())} &nbsp;·&nbsp; {res.rated_kva:g} kVA</p>
<p style='font-size:11pt; margin-top:4px'>{_esc(v.headline())}</p>
{clip_html}
{reasons_html}
</td></tr></table>

<table width='100%' cellpadding='4'><tr>
<td width='50%' valign='top'>
  <p style='color:{ACCENT}; font-weight:bold; font-size:9pt; letter-spacing:1px'>ON PAPER (NAMEPLATE)</p>
  <table width='100%'>{_kv_rows(v.on_paper)}</table>
</td>
<td width='50%' valign='top'>
  <p style='color:{ACCENT}; font-weight:bold; font-size:9pt; letter-spacing:1px'>REALLY (THERMAL MODEL + LIMITS)</p>
  <table width='100%'>{_kv_rows(v.really)}</table>
</td></tr></table>
<p style='color:{MUTED}; font-size:9pt'>The verdict is decided by the right-hand column only; the nameplate counters are
shown for contrast. {not_checked_html}</p>

<h2>Inputs</h2>
<table width='100%' cellpadding='3'>{inputs_html}</table>

{charts_html}

<h2>Notes written during the run</h2>
<ul>{notes_html}</ul>

<h2>How the numbers were made</h2>
<p>{_esc(s_estimate_text(res))}</p>
<p>The transformer's loading K = S / {res.rated_kva:g} kVA drives the IEC 60076-7 difference equations with the outdoor
temperature from SMHI as the ambient, at sub-steps of at most half the winding time constant, holding the load constant
within each {st.step_min:g}-min value. The hot-spot of each step is its maximum within the step. Life used is the ageing
rate integrated over the period (Σ V·Δt, IEC 60076-7:2005 eq. 4) and scaled to 8,760 h as if the period repeated all
year: a winter-only file therefore overstates the yearly figure and a summer-only file understates it.{clip_method}</p>

<h3>Thermal constants used</h3>
<table width='100%' cellpadding='3' border='0'>
<tr><th>constant</th><th>value</th><th>meaning</th></tr>{thermal_html}</table>

<h3>Rules behind the verdict</h3>
<table width='100%' cellpadding='3' border='0'>
<tr><th width='17%'>what</th><th width='18%'>Watch if</th><th width='18%'>At risk if</th><th width='32%'>source</th><th width='15%'>here</th></tr>{rules_html}</table>
<p style='color:{MUTED}; font-size:9pt'>Table and equation numbers and all limit values are those of IEC 60076-7:2005. The
2018 edition renumbers the tables and reviewed the temperature limits; it was not consulted for the values.</p>

<h3>What this card is not</h3>
<p>A screening with a simulation model and generic constants: the thermal constants are IEC 60076-7 defaults for a
distribution ONAN unit, not this transformer's test-report values; the simplified model has no cold-oil viscosity
correction, which makes it optimistic in a Swedish winter (the Watch margin on life is there for that); the load is held
constant within each step. The ambient is the SMHI station's air; the extra heating inside the kiosk is approximated by the
IEC enclosure correction above, not measured. It does not replace a network study, transformer monitoring, or the DSO's
own reserve (N-1), growth and accessory criteria. Sources and licences: the app's <i>Assumptions &amp; sources</i> page.</p>
<p style='color:{MUTED}; font-size:9pt'>Outdoor temperature: SMHI open data (Source: SMHI, CC BY 4.0,
https://creativecommons.org/licenses/by/4.0/), re-indexed to whole hours with short gaps interpolated.{example_footer}</p>
<p style='color:{MUTED}; font-size:9pt'>Made with Hotspot LV v{__version__} — an open screening tool by Basel Haijar, MIT
licence · simulation, indicative</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# pagination and printing
# ---------------------------------------------------------------------------
def _top_level_items(doc: QTextDocument) -> list:
    """The root frame's children in order: QTextBlock for paragraphs / headings / list items, QTextFrame for tables."""
    out = []
    it = doc.rootFrame().begin()
    while not it.atEnd():
        frame = it.currentFrame()
        out.append(frame if frame is not None else it.currentBlock())
        it += 1
    return out


def _is_heading(x) -> bool:
    return isinstance(x, QTextBlock) and x.blockFormat().headingLevel() > 0


def _first_line_y(layout, x) -> float:
    """Where an item's first line starts. A block pushed to the next page keeps a bounding rect that starts on the
    previous one; its first line does not (round-2 code review: the test read two stranded headings as fine)."""
    if isinstance(x, QTextFrame):
        cell = x.cellAt(0, 0) if hasattr(x, "cellAt") else None
        if cell is not None and cell.isValid():
            return _first_line_y(layout, cell.firstCursorPosition().block())
        return layout.frameBoundingRect(x).top()
    r = layout.blockBoundingRect(x)
    lay = x.layout()
    return r.top() + (lay.lineAt(0).y() if lay is not None and lay.lineCount() else 0.0)


def _first_orphan(doc: QTextDocument) -> int | None:
    """Index (in document order, counting every heading) of the first heading that does not start on the same page
    as its whole section (up to the next heading) although the section fits on a page — or, for a section longer
    than a page, not on the same page as its first item. None when every heading is placed well."""
    layout = doc.documentLayout()
    page_h = doc.pageSize().height()

    def rect(x):
        return layout.frameBoundingRect(x) if isinstance(x, QTextFrame) else layout.blockBoundingRect(x)

    seq = _top_level_items(doc)
    heading_no = -1
    for i, x in enumerate(seq):
        if not _is_heading(x):
            continue
        heading_no += 1
        if x.blockFormat().pageBreakPolicy() & QTextFormat.PageBreak_AlwaysBefore:
            continue                                            # already opens a page
        j = i + 1
        while j < len(seq) and not _is_heading(seq[j]):
            j += 1
        if j == i + 1:
            continue
        top = _first_line_y(layout, x)
        end = rect(seq[j - 1]).bottom()
        if end - top > page_h * 0.95:                           # longer than a page: keep the heading with its first item
            end = rect(seq[i + 1]).bottom()
        if int(top // page_h) != int((end - 1) // page_h):
            return heading_no
    return None


def _with_breaks(page_html: str, breaks: set[int]) -> str:
    """The card's HTML with 'page-break-before' on the headings numbered in `breaks` (document order)."""
    count = -1

    def add(m):
        nonlocal count
        count += 1
        tag = m.group(0)
        if count not in breaks or "page-break-before" in tag:
            return tag
        for q in ("'", '"'):                                     # a heading that already has a style keeps it
            if f"style={q}" in tag:
                return tag.replace(f"style={q}", f"style={q}page-break-before: always; ", 1)
        return tag[:-1] + " style='page-break-before: always'>"

    return re.sub(r"<h[1-6](?:\s[^>]*)?>", add, page_html)


def page_of_items(doc: QTextDocument) -> list[tuple[str, int, int]]:
    """(kind, first page, last page) of every top-level item — for tests and probes."""
    layout = doc.documentLayout()
    page_h = doc.pageSize().height()
    out = []
    for x in _top_level_items(doc):
        r = layout.frameBoundingRect(x) if isinstance(x, QTextFrame) else layout.blockBoundingRect(x)
        kind = "table" if isinstance(x, QTextFrame) else ("heading" if _is_heading(x) else "block")
        out.append((kind, int(_first_line_y(layout, x) // page_h), int((r.bottom() - 1) // page_h)))
    return out


def _page_layout() -> QPageLayout:
    return QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)


def build_card_document(res: StationResult, ambient: pd.Series | None, source_name: str | None = None,
                        generated: datetime | None = None, charts: dict | None = None) -> QTextDocument:
    """The paginated card. Charts are rendered here unless given (ambient=None and charts=None → no charts)."""
    if charts is None and ambient is not None:
        charts = render_charts(res, ambient)
    page_html = station_card_html(res, charts, source_name, generated)
    breaks: set[int] = set()
    for _ in range(20):                       # QTextDocument has no 'keep with next': measure, break, rebuild
        doc = QTextDocument()                 # a fresh document each pass — a block format changed after layout
        doc.setDefaultFont(QFont("Segoe UI", 10))   # left a stale layout and the printer dropped whole sections
        for name in CHARTS if charts else ():
            doc.addResource(QTextDocument.ImageResource, QUrl(f"chart://{name}"), charts[name]["image"])
        doc.setHtml(_with_breaks(page_html, breaks))
        doc.setPageSize(QSizeF(_page_layout().paintRectPixels(DPI).size()))
        k = _first_orphan(doc)
        if k is None or k in breaks:
            break
        breaks.add(k)
    return doc


def _print(doc: QTextDocument, device, title: str, footer: str) -> int:
    """Draw the paginated document page by page onto a PDF on `device`, with "<footer> · page i of n" in the
    bottom margin. Returns the number of pages."""
    writer = QPdfWriter(device)
    writer.setPageLayout(_page_layout())
    writer.setResolution(DPI)
    writer.setTitle(title)
    writer.setCreator(f"Hotspot LV v{__version__}")
    page = doc.pageSize()
    n = doc.pageCount()
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.LosslessImageRendering)   # charts as Flate, not JPEG: thin lines stay sharp
    try:
        for i in range(n):
            if i:
                writer.newPage()
            painter.save()
            painter.translate(0, -i * page.height())
            doc.drawContents(painter, QRectF(0, i * page.height(), page.width(), page.height()))
            painter.restore()
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QColor(MUTED))
            tail = f" · page {i + 1} of {n}"
            room = page.width() - painter.fontMetrics().horizontalAdvance(tail)
            text = painter.fontMetrics().elidedText(footer, Qt.ElideMiddle, int(room)) + tail   # a long file name
            painter.drawText(QRectF(0, page.height() + 10, page.width(), 24), int(Qt.AlignRight | Qt.AlignTop), text)
    finally:
        painter.end()
    return n


def write_station_card(res: StationResult, ambient: pd.Series, path, source_name: str | None = None,
                       generated: datetime | None = None) -> Path:
    """Render the charts, build the card and print it to `path` through a QSaveFile: the PDF goes to a temporary
    file that replaces the target only on commit, so the target is either the new card or untouched — and an
    error says which, with Windows' own reason. Qt's file engine also handles paths longer than 260 characters,
    which pathlib's checks did not (a written card was reported as missing). Returns the path. GUI thread only."""
    path = Path(path)
    if not QDir(str(path.parent)).exists():
        raise OSError(f"The folder {path.parent} does not exist — choose another place.")
    name = source_name or Path(res.station.source).name or "station"
    doc = build_card_document(res, ambient, name, generated)
    f = QSaveFile(str(path))
    if not f.open(QIODevice.WriteOnly):
        raise OSError(f"{path.name} cannot be written ({f.errorString()}) — it may be read-only; choose another name "
                      "or place. The file on disk was not changed.")
    try:
        _print(doc, f, f"Hotspot LV station card — {name}", f"Hotspot LV station card · {name}")
        if f.size() == 0:
            raise OSError(f"Nothing was written to {path.name}.")
    except BaseException:
        f.cancelWriting()
        f.commit()                                            # with cancelWriting: discards the temporary file
        raise
    if not f.commit():
        raise OSError(f"{path.name} could not be replaced ({f.errorString()}) — it is probably open in another program "
                      "or read-only; close it or choose another name. The file on disk was not changed.")
    return path
