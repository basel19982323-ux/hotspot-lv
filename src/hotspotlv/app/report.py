"""The station card as a PDF — what a planner attaches to a planning memo (mode B).

    StationResult ──► render_charts()      three fresh chart widgets drawn off-screen at a fixed size
                 ──► station_card_html()  verdict · inputs · the two columns · charts · notes · assumptions
                 ──► write_station_card() QTextDocument → QPdfWriter, A4

The card repeats what the screen shows — every number comes through the same
formatter as the verdict card, the charts are the app's own chart classes, the
captions and legends are the widgets' own text — and adds what a reader without
the app needs: how S at the transformer was estimated, the thermal constants
used, every rule threshold with its source, and what was NOT checked. Nothing is
recomputed here.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QMarginsF, QSizeF, Qt, QUrl
from PySide6.QtGui import QFont, QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument
from PySide6.QtWidgets import QApplication

from hotspotlv import __version__
from hotspotlv.app.theme import ACCENT, BORDER, MUTED, PANEL, VERDICT_COLORS
from hotspotlv.app.widgets.charts import TIMELINE_LEGEND, DurationCurve, WorstWeek, YearTimeline, legend_html
from hotspotlv.app.widgets.verdict_card import SOURCE_WORDING, fmt_card_value
from hotspotlv.station import CABLE_LOSS_SHARE_AT_RATED, NOT_CHECKED_KEYS, StationResult
from hotspotlv.thermal import IEC_LIMITS_C, IEC_LIMITS_K, K_MAX, REF_HOT_SPOT_C, ThermalParams
from hotspotlv.timeseries import NAMEPLATE_RULE_PCT, VMAX_LIMIT_PU, VMIN_LIMIT_PU
from hotspotlv.verdict import DESIGN_LIFE_DAYS_PER_YEAR, VMAX_PLAN_PU, VMIN_PLAN_PU, WATCH_LIFE_DAYS_PER_YEAR
from hotspotlv.weather import SMHI_STATION, SMHI_STATION_NAME

CHART_W, CHART_H = 900, 340          # pixels rendered; shown at PAGE_W → about 0.75 × (fonts stay readable)
PAGE_W = 680                         # A4 printable width at 96 dpi with 15 mm margins (210 − 30 mm = 180 mm)
DPI = 96
CHARTS = ("period", "duration", "worst_week")


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------
def _settle(plot, max_passes: int = 20) -> None:
    """Process events until the plot's layout stops moving. The axes widen once their labels are set, one
    event pass later than the data; a grab taken before that cuts the right-axis label in half (measured)."""
    last = None
    for _ in range(max_passes):
        QApplication.processEvents()
        plot.plotItem.layout.activate()
        geo = tuple(plot.getAxis(side).geometry().getCoords() for side in ("left", "right", "bottom"))
        if geo == last:
            break
        last = geo
    right = plot.getAxis("right")
    if right.isVisible() and right.label.sceneBoundingRect().right() > plot.viewport().width():
        raise RuntimeError("the right-axis label does not fit the chart: it would be cut in the grab")


def render_charts(res: StationResult, ambient: pd.Series, width: int = CHART_W, height: int = CHART_H) -> dict:
    """Draw the three charts of the Own-station tab off-screen and grab them.
    Returns {name: {"image": QImage, "legend": rich text, "caption": str}}. Needs a QApplication."""
    if QApplication.instance() is None:
        raise RuntimeError("render_charts needs a QApplication")
    step_h = res.station.step_min / 60
    out = {}
    for name, cls in (("period", YearTimeline), ("duration", DurationCurve), ("worst_week", WorstWeek)):
        w = cls()
        w.setAttribute(Qt.WA_DontShowOnScreen, True)          # laid out and painted, never on the screen
        w.plot.setFixedSize(width, height)
        w.show()
        QApplication.processEvents()
        if name == "period":
            w.set_data(res.year.hourly, res.thermal.hourly, ambient)
            w.scrubber.label.setFormat("peak hour")                 # "drag me" means nothing on paper
            caption = "Peak hour (the black line) — " + w.readout.text()
        elif name == "duration":
            w.set_year(res.year.hourly, step_h)
            caption = w.caption.text()
        else:
            w.set_week(res.thermal.worst_week(), res.year.hourly, ambient, step_h)
            caption = w.caption.text()
        _settle(w.plot)
        image = w.plot.grab().toImage()                       # device pixels: 1.25 × at a 125 % Windows scale
        dpr = image.devicePixelRatio()
        if round(image.width() / dpr) != width or round(image.height() / dpr) != height:
            raise RuntimeError(f"chart {name} rendered {image.width()}×{image.height()} at {dpr:g}×, wanted {width}×{height}")
        legend = w.legend.text()
        if name == "period":
            legend = legend_html(TIMELINE_LEGEND[:-1] + (("#000000", "&#9474;", "the peak hour"),))
        out[name] = {"image": image, "legend": legend, "caption": caption}
        w.close()
        w.deleteLater()
    QApplication.processEvents()
    return out


# ---------------------------------------------------------------------------
# the assumption tables (built from the engine's constants, never typed here)
# ---------------------------------------------------------------------------
def thermal_rows(p: ThermalParams) -> list[tuple[str, str, str]]:
    """(name, value, meaning) for every constant the thermal run used."""
    rise = f"{p.d_theta_or:g} K"
    if p.enclosure_rise_k:
        rise += f" (= {p.d_theta_or - p.enclosure_rise_k:g} K outdoor + {p.enclosure_rise_k:g} K kiosk, IEC Table 6 / 2)"
    return [
        ("enclosure", p.enclosure, "kiosk is the Swedish norm; it raises the top-oil rise"),
        ("Δθor", rise, "top-oil rise over ambient at rated load"),
        ("Δθhr", f"{p.d_theta_hr:g} K", "hot-spot rise over top-oil at rated load (H·gr)"),
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
    iec = "IEC 60076-7 Table 4 (distribution transformers)"
    nc = "not checked here: station data carries no feeder model"
    return [
        ("insulation life used", f"> {WATCH_LIFE_DAYS_PER_YEAR:g} days/yr", f"> {DESIGN_LIFE_DAYS_PER_YEAR:g} days/yr",
         "IEC 60076-7 eq. (2), (4): 98 °C = design rate; the Watch margin is a chosen allowance for model optimism", "checked"),
        ("hot-spot", f"> {IEC_LIMITS_C['normal_cyclic_hot_spot']:g} °C", f"> {IEC_LIMITS_C['long_time_emergency_hot_spot']:g} °C", iec, "checked"),
        ("top-oil", f"> {IEC_LIMITS_C['normal_cyclic_top_oil']:g} °C", "—", iec, "checked"),
        ("load", f"> {IEC_LIMITS_K['normal_cyclic']:g} × rated", f"> {IEC_LIMITS_K['long_time_emergency']:g} × rated", iec, "checked"),
        ("house voltage", f"any hour outside {VMIN_PLAN_PU:g}–{VMAX_PLAN_PU:g} pu (planning band)",
         f"any hour outside {VMIN_LIMIT_PU:g}–{VMAX_LIMIT_PU:g} pu", "EIFS 2023:3 6 kap. 3 § (all 10-min values within ±10 %)",
         "checked" if checked else nc),
        ("cable", "> 100 % of its rating", "—", "cable catalogue (feeder.py)", "checked" if checked else nc),
        (f"hours above {NAMEPLATE_RULE_PCT:g} % / 100 % of nameplate", "never changes the verdict", "never changes the verdict",
         "the DSO rule of thumb (Energiforsk 2018:552) — shown for contrast", "counted"),
    ]


def s_estimate_text(res: StationResult) -> str:
    st = res.station
    if st.s_kva_measured is not None:
        return ("Apparent power S at the transformer was taken from the file's kVA column as measured at the "
                "transformer; no losses were added.")
    tr_losses = "the transformer's own losses from the catalogue (no-load loss + load loss × K²) and its reactive consumption uk·K²·S_rated"
    if st.measured_at == "meters":
        return (f"The file is the sum of customer meters. S at the transformer = √((P + losses)² + (Q + Q_loss)²), "
                f"where the LV-cable losses are taken as {CABLE_LOSS_SHARE_AT_RATED * 100:.1f} % of P at rated load, scaling with the "
                f"loading K (I²R), plus {tr_losses}. Q comes from the file when it has a kvar column, otherwise it is "
                "estimated (see the notes).")
    return (f"The file was measured at the transformer's LV terminals: only {tr_losses} were added "
            "(no cable losses).")


# ---------------------------------------------------------------------------
# the card
# ---------------------------------------------------------------------------
def _esc(x) -> str:
    return html.escape(str(x))


def _kv_rows(items: dict) -> str:
    rows = []
    for k, v in items.items():
        muted = f" style='color:{MUTED}'" if v is None else ""
        rows.append(f"<tr><td style='color:{MUTED}'>{_esc(k)}</td><td align='right'{muted}><b>{_esc(fmt_card_value(k, v))}</b></td></tr>")
    return "".join(rows)


def station_card_html(res: StationResult, charts: dict | None = None, source_name: str | None = None,
                      generated: datetime | None = None) -> str:
    """The whole card as HTML for QTextDocument (a subset of HTML 4 / CSS 2: tables, colours, images).
    `charts` as from render_charts(); images are referenced as chart://<name> (resources added by the caller)."""
    v = res.verdict
    st = res.station
    generated = generated or datetime.now()
    source_name = source_name or st.source or "station data"
    color = VERDICT_COLORS.get(v.level, MUTED)
    reasons = []
    for r in v.reasons:
        text = str(r)
        for old, new in SOURCE_WORDING.items():
            text = text.replace(old, new)
        reasons.append(f"<li>{_esc(text)}</li>")
    reasons_html = f"<ul>{''.join(reasons)}</ul>" if reasons else f"<p style='color:{MUTED}'>No rule is violated.</p>"
    not_checked = [k for k in NOT_CHECKED_KEYS if v.really.get(k, 0) is None]

    smhi = f"{res.smhi_station}"
    if res.smhi_station == SMHI_STATION:
        smhi += f" {SMHI_STATION_NAME} (the default — set the station nearest the place)"
    inputs = [
        ("file", source_name),
        ("period", st.period()),
        ("resolution", f"{st.step_min:g}-min values, {st.gaps_filled} gaps filled"),
        ("values are", "the sum of customer meters" if st.measured_at == "meters" else "measured at the transformer"),
        ("transformer rating", f"{res.rated_kva:g} kVA"),
        ("outdoor temperature", f"SMHI station {smhi}"),
        ("enclosure", res.thermal.params.enclosure),
        ("energy in the period", f"{st.p_kw.clip(lower=0).sum() * st.step_min / 60 / 1000:,.1f} MWh (imports only)"),
    ]
    if res.overlay:
        inputs.append(("synthetic overlay", ", ".join(f"+{n} {k[2:].upper() if k.startswith('n_') else k}" for k, n in res.overlay.items())
                       + " — an upper bound added on top of the measured profile"))
    inputs_html = "".join(f"<tr><td style='color:{MUTED}' width='28%'>{_esc(k)}</td><td>{_esc(val)}</td></tr>" for k, val in inputs)

    charts_html = ""
    if charts:
        titles = {"period": "The whole period — loading and temperatures",
                  "duration": "Loading duration curve — for how many hours is the station above x %",
                  "worst_week": "The worst week — the seven days that used the most insulation life"}
        for name in CHARTS:
            c = charts[name]
            # the period and worst-week charts each open a page (a one-cell table does not keep a heading with
            # its image in QTextDocument, and the cell clips the legend): two charts per page, nothing orphaned
            brk = "<p style='page-break-before: always'></p>" if name in ("period", "worst_week") else ""
            charts_html += (f"{brk}<h3>{titles[name]}</h3>"
                            f"<p><img src='chart://{name}' width='{PAGE_W}'></p>"
                            f"<p style='color:{MUTED}; font-size:8.5pt'>{c['legend']}</p>"
                            f"<p style='font-size:9pt'>{_esc(c['caption'])}</p>")

    notes_html = "".join(f"<li>{_esc(n)}</li>" for n in v.notes)
    thermal_html = "".join(f"<tr><td><b>{_esc(n)}</b></td><td>{_esc(val)}</td><td style='color:{MUTED}'>{_esc(m)}</td></tr>"
                           for n, val, m in thermal_rows(res.thermal.params))
    rules_html = "".join(
        f"<tr><td><b>{_esc(w)}</b></td><td>{_esc(wa)}</td><td>{_esc(ar)}</td><td style='color:{MUTED}'>{_esc(src)}</td>"
        f"<td style='color:{MUTED if status.startswith('checked') or status == 'counted' else VERDICT_COLORS['Watch']}'>{_esc(status)}</td></tr>"
        for w, wa, ar, src, status in rule_rows(checked=not not_checked))
    k_clipped = any("thermal run clipped" in n for n in v.notes)

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

<table width='100%' cellpadding='8' bgcolor='{PANEL}'><tr><td width='10' bgcolor='{color}'></td><td>
<p style='font-size:20pt; font-weight:bold; color:{color}; margin:0'>{_esc(v.level.upper())} &nbsp;·&nbsp; {res.rated_kva:g} kVA</p>
<p style='font-size:11pt; margin-top:4px'>{_esc(v.headline())}</p>
{reasons_html}
</td></tr></table>

<table width='100%' cellpadding='4'><tr>
<td width='50%' valign='top'>
  <p style='color:{ACCENT}; font-weight:bold; font-size:9pt; letter-spacing:1px'>ON PAPER (NAMEPLATE RULE)</p>
  <table width='100%'>{_kv_rows(v.on_paper)}</table>
</td>
<td width='50%' valign='top'>
  <p style='color:{ACCENT}; font-weight:bold; font-size:9pt; letter-spacing:1px'>REALLY (THERMAL MODEL + LIMITS)</p>
  <table width='100%'>{_kv_rows(v.really)}</table>
</td></tr></table>
<p style='color:{MUTED}; font-size:9pt'>The verdict is decided by the right-hand column only; the nameplate counters are
shown for contrast. {'Three rules read <i>not checked</i>: ' + _esc(', '.join(k for k in not_checked)) + '.' if not_checked else ''}</p>

<h2>Inputs</h2>
<table width='100%' cellpadding='3'>{inputs_html}</table>

{charts_html}

<h2>Notes written during the run</h2>
<ul>{notes_html}</ul>

<p style='page-break-before: always'></p>
<h2>How the numbers were made</h2>
<p>{_esc(s_estimate_text(res))}</p>
<p>The transformer's loading K = S / {res.rated_kva:g} kVA drives the IEC 60076-7 difference equations (clause 8) with the
outdoor temperature from SMHI as the ambient, at sub-steps of at most half the winding time constant, holding the load
constant within each {st.step_min:g}-min value. The hot-spot of each step is its maximum within the step; the insulation life
used is the sum of the ageing rate over the period, expressed per year.{' Loading above ' + f'{K_MAX:g}' + ' × rated was clipped for the thermal run — see the notes: the hot-spot and life figures are not meaningful there.' if k_clipped else ''}</p>

<h3>Thermal constants used</h3>
<table width='100%' cellpadding='3' border='0'>
<tr><th>constant</th><th>value</th><th>meaning</th></tr>{thermal_html}</table>

<p style='page-break-before: always'></p>
<h3>Rules behind the verdict</h3>
<table width='100%' cellpadding='3' border='0'>
<tr><th width='17%'>what</th><th width='18%'>Watch if</th><th width='18%'>At risk if</th><th width='32%'>source</th><th width='15%'>here</th></tr>{rules_html}</table>

<h3>What this card is not</h3>
<p>A screening with a simulation model and generic constants: the thermal constants are IEC 60076-7 defaults for a
distribution ONAN unit, not this transformer's test-report values; the simplified model has no cold-oil viscosity
correction, which makes it optimistic in a Swedish winter (the Watch margin on life is there for that); loading
within a step is held constant. The ambient is an SMHI station, not the kiosk's own air. It does not replace a
network study, transformer monitoring, or the DSO's own reserve (N-1), growth and accessory criteria. Sources and
licences: the app's <i>Assumptions &amp; sources</i> page (SOURCES.md).</p>
<p style='color:{MUTED}; font-size:9pt'>Hotspot LV v{__version__} · MIT licence · author Basel Haijar · simulation, indicative</p>
</body></html>
"""


def write_station_card(res: StationResult, ambient: pd.Series, path, source_name: str | None = None,
                       generated: datetime | None = None) -> Path:
    """Render the charts, build the card and print it to a PDF. Returns the path written."""
    path = Path(path)
    charts = render_charts(res, ambient)
    doc = QTextDocument()
    doc.setDefaultFont(QFont("Segoe UI", 10))
    for name in CHARTS:
        doc.addResource(QTextDocument.ImageResource, QUrl(f"chart://{name}"), charts[name]["image"])
    doc.setHtml(station_card_html(res, charts, source_name, generated))
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)
    writer.setResolution(DPI)
    writer.setTitle(f"Hotspot LV station card — {source_name or res.station.source or 'station'}")
    writer.setCreator(f"Hotspot LV v{__version__}")
    doc.setPageSize(QSizeF(writer.pageLayout().paintRectPixels(DPI).size()))
    doc.print_(writer)
    if not path.exists() or path.stat().st_size == 0:
        raise OSError(f"could not write {path}")
    return path
