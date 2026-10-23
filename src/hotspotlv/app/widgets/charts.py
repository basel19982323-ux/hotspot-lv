"""The three charts, built on pyqtgraph.

  DurationCurve   loading % sorted high → low, with the 80 / 100 / 150 % lines
                  ("for how many hours is the station above x")
  YearTimeline    loading % (left axis) and hot-spot / outdoor °C (right axis) over the
                  whole period, with a draggable scrubber line → emits hour_changed(i);
                  ← / → keys move it one step
  WorstWeek       the 7 days that used the most insulation life: hot-spot, top-oil,
                  outdoor (°C) and, low and faint, the loading (%)

Legends are a text strip ABOVE each plot (LegendStrip), never a box inside it: a
box inside the plot sat on the January peaks — the busiest part of the year
(usability review of 21 Oct 2026). The strip is rich text, so the PDF card can
print the very same legend under its copy of the chart.

Time axis: the x values are UTC epoch seconds — they never repeat or go back
(numbers audit of 23 Oct 2026: as Swedish wall-clock seconds, the October hour
that the clock shows twice stalled the ← → keys). SwedishTimeAxis prints each
tick in Swedish time, CET or CEST as it was at that moment, whatever time zone
the PC runs in; the readout names the hour's interval and its zone.
"""

from __future__ import annotations

import html

import numpy as np
import pandas as pd
import pyqtgraph as pg
import shiboken6
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget


class ElidedLabel(QLabel):
    """A one-line label that never constrains the window: too narrow, it ends in "…" (full text in the tooltip)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full = text
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def text(self) -> str:                       # callers (and tests) read the whole text, not the elided one
        return self._full

    def _elide(self):
        width = max(self.contentsRect().width(), 10)
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

from hotspotlv.app.theme import AMBIENT_COLOR, HOTSPOT_COLOR, LOAD_COLOR, OIL_COLOR, RULE_80, RULE_100
from hotspotlv.timeseries import NAMEPLATE_RULE_PCT
from hotspotlv.verdict import THERMAL_RANGE_K, TOL, fmt_quantity

pg.setConfigOptions(antialias=True, background="w", foreground="#333333")
LOCAL = "Europe/Stockholm"
RIGHT_AXIS_MARGIN = 8     # px: pyqtgraph reserves only 0.8 × its height for a rotated axis label, so a right-axis
                          # label spills ~5 px past the plot and is cut in a fixed-size grab (the PDF card)
LABEL_LEFT = {"position": 0.02, "anchors": [(0, 0), (0, 1)]}      # text starts 2 % in from the left edge
LABEL_RIGHT = {"position": 0.98, "anchors": [(1, 0), (1, 1)]}     # text ends 2 % in from the right edge
LABEL_MIN_PLOT_PX = 120   # below this data-area height the rule labels collide with each other and the data (laptop)


def rule_line(plot, y: float, color, text: str, where: dict, fill=None, priority: int = 0) -> pg.InfiniteLine:
    """A dashed horizontal rule line with a label. A label is shown only while its line is inside the y-range, the
    data area is tall enough, and it does not overlap a label of higher priority (the IEC limit before the
    nameplate before the 80 % rule) — never a label without its line, never two labels on each other (reviews of
    22 Oct 2026: overlapping and floating labels on the laptop and on every At-risk card)."""
    opts = {"color": color, **where}
    if fill is not None:
        opts["fill"] = fill
    line = pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen(color, style=Qt.DashLine), label=text, labelOpts=opts)
    line._rule_priority = priority
    plot.addItem(line)
    lines = getattr(plot, "_rule_lines", None)
    if lines is None:
        lines = plot._rule_lines = []
        vb = plot.getViewBox()
        vb.sigResized.connect(lambda *_: _relabel_soon(plot))
        vb.sigYRangeChanged.connect(lambda *_: _relabel_soon(plot))
    lines.append(line)
    _relabel_soon(plot)
    return line


def _relabel_soon(plot) -> None:
    """Relabel now and once more after the current event: the label positions follow the view's transform, which
    pyqtgraph updates after the range-change signal (measured: two labels read as overlapping, one hidden)."""
    relabel(plot)
    if not getattr(plot, "_relabel_pending", False):
        plot._relabel_pending = True

        def later():
            if shiboken6.isValid(plot):
                plot._relabel_pending = False
                relabel(plot)
        QTimer.singleShot(0, later)


def relabel(plot) -> None:
    """Show each rule label that fits (see rule_line); called on every resize and y-range change."""
    vb = plot.getViewBox()
    lines = getattr(plot, "_rule_lines", [])
    if not shiboken6.isValid(vb) or not all(shiboken6.isValid(x) and shiboken6.isValid(x.label) for x in lines):
        return                                                  # the view box resizes while it is being torn down
    vb.updateMatrix()                                           # the lines' places for the current range
    lo, hi = vb.viewRange()[1]
    tall = vb.height() >= LABEL_MIN_PLOT_PX
    shown = []
    for line in sorted(lines, key=lambda x: -x._rule_priority):
        ok = tall and lo <= line.value() <= hi
        if ok:
            line.label.updatePosition()                         # its place for the current range, before comparing
            rect = line.label.sceneBoundingRect().adjusted(0, 5, 0, -5)   # the text, not its 5-px padding
            ok = not any(rect.intersects(r) for r in shown)
            if ok:
                shown.append(rect)
        line.label.setVisible(ok)


def legend_html(items) -> str:
    """items: (colour, marker, text) → one rich-text line, e.g. '▬ loading % (left axis)  ▬ hot-spot °C …'.
    The marker is an HTML entity; the text is escaped (a future 'θ < 98 °C' must not vanish)."""
    return " &nbsp; ".join(f"<span style='color:{c}'>{m} {html.escape(t, quote=False)}</span>" for c, m, t in items)


class LegendStrip(QLabel):
    """The legend as a wrapped text line above a plot. `text()` returns the rich text (for the PDF card)."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setProperty("muted", True)
        self.setText(legend_html(items))


TIMELINE_LEGEND = ((LOAD_COLOR, "&#9644;", "loading % (left axis)"),
                   (RULE_100, "&#9476;", "dashed: 80 % rule and nameplate (loading %, not °C)"),
                   (HOTSPOT_COLOR, "&#9644;", "hot-spot °C (right axis)"),
                   (AMBIENT_COLOR, "&#9644;", "outdoor °C (right axis)"),
                   ("#000000", "&#9474;", "scrubber: drag it, or ← →"))
DURATION_LEGEND = ((LOAD_COLOR, "&#9644;", "loading sorted high → low: read it as 'above x % for y hours'"),
                   (RULE_100, "&#9476;", "dashed: 80 % rule · nameplate · 1.5 × rated (IEC 60076-7 normal cyclic current limit, "
                                         "on the chart when the peak reaches it)"))
WORST_WEEK_LEGEND = ((HOTSPOT_COLOR, "&#9644;", "hot-spot °C"),
                     (OIL_COLOR, "&#9644;", "top-oil °C"),
                     (AMBIENT_COLOR, "&#9644;", "outdoor °C (all on the left axis)"),
                     (RULE_100, "&#9476;", "dashed: 98 °C = ageing rate 1 · 120 °C IEC normal cyclic limit"),
                     (LOAD_COLOR, "&#9617;", "loading % (right axis, drawn in the lower third: under the 98 °C and "
                                             "120 °C lines, it can overlap the outdoor line)"))


WORST_WEEK_LEGEND_LOADING_ONLY = ((AMBIENT_COLOR, "&#9644;", "outdoor °C (left axis)"),
                                  (LOAD_COLOR, "&#9617;", "loading % (right axis)"))


def without_transformer_temperatures(items):
    """The legend items left when the thermal figures are withdrawn: no hot-spot, no top-oil entry."""
    return tuple(i for i in items if "hot-spot °C" not in i[2] and "top-oil °C" not in i[2])


def _axis_date(index: pd.DatetimeIndex) -> np.ndarray:
    """UTC epoch seconds: always increasing, also through the October hour the Swedish clock shows twice."""
    return np.asarray(index.tz_convert("UTC").tz_localize(None).to_numpy().astype("datetime64[s]").astype("int64"),
                      dtype=float)


def _local(seconds: float) -> pd.Timestamp:
    return pd.Timestamp(seconds, unit="s", tz="UTC").tz_convert(LOCAL)


def _step_seconds(index: pd.DatetimeIndex) -> float:
    """One row of the data in seconds (the x range ends where the last interval ends, not where it starts)."""
    return float(pd.Series(index).diff().median().total_seconds()) if len(index) > 1 else 3600.0


def interval_text(t: pd.Timestamp, step: pd.Timedelta) -> str:
    """'Wed 06 Dec 2023 16:00–17:00 CET': the interval a row covers, in Swedish time with its zone."""
    t = t.tz_convert(LOCAL)
    end = (t + step).tz_convert(LOCAL)
    if end.tzname() != t.tzname():                       # the hour in which the clock changes: name both zones
        return f"{t:%a %d %b %Y %H:%M} {t.tzname()}–{end:%H:%M} {end.tzname()}"
    return f"{t:%a %d %b %Y %H:%M}–{end:%H:%M} {t.tzname()}"


class SwedishTimeAxis(pg.DateAxisItem):
    """A date axis over UTC seconds whose tick labels are Swedish time (CET in winter, CEST in summer)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, utcOffset=-3600, **kwargs)

    def tickValues(self, minVal, maxVal, size):
        self.utcOffset = -_local((minVal + maxVal) / 2).utcoffset().total_seconds()  # ticks on local midnights/hours
        return super().tickValues(minVal, maxVal, size)

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            self.utcOffset = -_local(v).utcoffset().total_seconds()                   # each label in its own zone
            out += super().tickStrings([v], scale, spacing)
        return out


def _nearest(x: np.ndarray, value: float) -> int:
    """Index of the sample nearest to value (not the next one, as searchsorted alone would give)."""
    i = int(np.searchsorted(x, value))
    if i <= 0:
        return 0
    if i >= len(x):
        return len(x) - 1
    return i if abs(x[i] - value) < abs(x[i - 1] - value) else i - 1


def _date_axis():
    return SwedishTimeAxis()


class DurationCurve(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "loading", units="% of nameplate")
        self.plot.setLabel("bottom", "hours in the period above this loading")
        for side in ("left", "bottom"):
            self.plot.getAxis(side).enableAutoSIPrefix(False)    # never "k%" or "×0.001"
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.legend = LegendStrip(DURATION_LEGEND)
        self.curve = self.plot.plot([], [], pen=pg.mkPen(LOAD_COLOR, width=2))
        for y, c, txt, prio in ((NAMEPLATE_RULE_PCT, RULE_80, "80 % rule", 0), (100, RULE_100, "nameplate", 1),
                                (150, HOTSPOT_COLOR, "IEC 1.5 × rated", 2)):
            rule_line(self.plot, y, c, txt, LABEL_RIGHT, priority=prio)
        self.caption = QLabel("")
        self.caption.setProperty("muted", True)
        lay.addWidget(self.legend)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.caption)

    def set_year(self, hourly: pd.DataFrame, step_h: float = 1.0):
        lp = np.sort(hourly["loading_pct"].to_numpy())[::-1]
        hours = np.arange(1, len(lp) + 1) * step_h
        self.curve.setData(hours, lp)
        self.plot.setXRange(0, max(hours[-1], 1))
        self.plot.setYRange(0, max(110, float(lp[0]) * 1.05))
        above80 = float((hourly["loading_pct"] > NAMEPLATE_RULE_PCT + TOL).sum() * step_h)     # the verdict's own count
        above100 = float((hourly["loading_pct"] > 100 + TOL).sum() * step_h)
        self.caption.setText(f"Above 80 %: {above80:,.0f} h · above 100 %: {above100:,.0f} h · peak {lp[0]:.1f} %")


class YearTimeline(QWidget):
    """Loading and temperatures over the period with a scrubber. Emits the row index under the scrubber."""
    hour_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget(axisItems={"bottom": _date_axis()})
        self.plot.setLabel("left", "loading", units="%")
        self.plot.setLabel("bottom", "Swedish time (CET / CEST)")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.legend = LegendStrip(TIMELINE_LEGEND)
        self.load = self.plot.plot([], [], pen=pg.mkPen(LOAD_COLOR, width=1))
        # the rule labels sit mid-year (the summer trough), where the loading curve is far below them
        for y, c, txt, prio in ((NAMEPLATE_RULE_PCT, RULE_80, "80 % rule", 0), (100, RULE_100, "nameplate", 1)):
            rule_line(self.plot, y, c, txt, {"position": 0.5}, fill=pg.mkBrush(255, 255, 255, 220),   # white backing:
                      priority=prio)                                                 # a hot summer can run through it
        self.right = pg.ViewBox()
        self.plot.showAxis("right")
        for side in ("left", "right"):
            self.plot.getAxis(side).enableAutoSIPrefix(False)    # "1500 %", never "1.5 k%"
        self.plot.plotItem.layout.setContentsMargins(1, 1, RIGHT_AXIS_MARGIN, 1)
        self.plot.scene().addItem(self.right)
        self.plot.getAxis("right").linkToView(self.right)
        self.right.setXLink(self.plot)
        self.plot.setLabel("right", "temperature", units="°C")
        self.hot = pg.PlotCurveItem(pen=pg.mkPen(HOTSPOT_COLOR, width=1))
        self.amb = pg.PlotCurveItem(pen=pg.mkPen(AMBIENT_COLOR, width=1))
        self.right.addItem(self.hot)
        self.right.addItem(self.amb)
        self.plot.getViewBox().sigResized.connect(self._sync)
        self.scrubber = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#000000", width=2),
                                        hoverPen=pg.mkPen("#000000", width=4), label="drag me  ◂ ▸",
                                        labelOpts={"position": 0.97, "color": "#000000", "movable": False})
        self.plot.addItem(self.scrubber)
        self.scrubber.sigPositionChanged.connect(self._moved)
        self.readout = ElidedLabel("")         # a wrapped label imposes its height on the column, an unwrapped one its width
        self.readout.setProperty("muted", True)
        lay.addWidget(self.legend)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.readout)
        self._x = np.array([])
        self._hourly = None
        self._thermal = None
        self._ambient = None
        self._i = 0

    def _sync(self):
        self.right.setGeometry(self.plot.getViewBox().sceneBoundingRect())
        self.right.linkedViewChanged(self.plot.getViewBox(), self.right.XAxis)

    def set_data(self, hourly: pd.DataFrame, thermal: pd.DataFrame | None, ambient: pd.Series | None):
        self._hourly, self._thermal, self._ambient = hourly, thermal, ambient
        self._x = _axis_date(hourly.index)
        self.load.setData(self._x, hourly["loading_pct"].to_numpy())
        if thermal is not None:
            self.hot.setData(self._x, thermal["theta_h_max"].to_numpy())
        else:                                                   # withdrawn (above THERMAL_RANGE_K) or none
            self.hot.setData([], [])
        self.legend.setText(legend_html(TIMELINE_LEGEND if thermal is not None
                                        else without_transformer_temperatures(TIMELINE_LEGEND)))
        if ambient is not None:
            self.amb.setData(self._x, ambient.to_numpy())
        self._step = pd.Timedelta(seconds=_step_seconds(hourly.index))
        # exactly the data: from the first interval's start to the last one's end — the default padding (about
        # 12 days each side) put a "2024" tick under a 2023 file (numbers audit of 23 Oct 2026)
        self.plot.setXRange(self._x[0], self._x[-1] + self._step.total_seconds(), padding=0)
        self.plot.setYRange(0, max(110, float(hourly["loading_pct"].max()) * 1.05))
        lo = min(float(ambient.min()) if ambient is not None else 0, 0)
        hi = max(float(thermal["theta_h_max"].max()) if thermal is not None else 100, 100)
        self.right.setYRange(lo - 5, hi + 5)
        self._sync()
        self.go_to(int(np.argmax(hourly["loading_pct"].to_numpy())))

    def _moved(self):
        """The scrubber was dragged: snap to the nearest row."""
        if len(self._x) == 0:
            return
        self._show(_nearest(self._x, self.scrubber.value()))

    def _show(self, i: int):
        self._i = i
        h = self._hourly.iloc[i]
        txt = f"{self.label_at(i)} — loading {h['loading_pct']:.1f} % ({h['s_kva']:.0f} kVA)"
        if self._thermal is not None:
            txt += f" · hot-spot {self._thermal['theta_h_max'].iloc[i]:.0f} °C · top-oil {self._thermal['theta_o'].iloc[i]:.0f} °C"
        if self._ambient is not None:
            txt += f" · outdoor {self._ambient.iloc[i]:.1f} °C"
        if "vmin_pu" in h and h["vmin_pu"] != 1.0:
            txt += f" · lowest house {h['vmin_pu']:.3f} pu"
        self.readout.setText(txt)
        self.hour_changed.emit(i)

    def go_to(self, i: int):
        """Show row i — exactly that row, also where two rows share a wall-clock time (the ← → keys)."""
        if len(self._x):
            i = int(np.clip(i, 0, len(self._x) - 1))
            self.scrubber.blockSignals(True)
            self.scrubber.setPos(self._x[i])
            self.scrubber.blockSignals(False)
            self._show(i)

    def label_at(self, i: int) -> str:
        if self._hourly is None:
            return ""
        t = self._hourly.index[int(np.clip(i, 0, len(self._hourly) - 1))]
        return interval_text(t, getattr(self, "_step", pd.Timedelta(hours=1)))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left:
            self.go_to(self._i - 1)
        elif event.key() == Qt.Key_Right:
            self.go_to(self._i + 1)
        else:
            super().keyPressEvent(event)


class WorstWeek(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget(axisItems={"bottom": _date_axis()})
        self.plot.setLabel("left", "temperature", units="°C")
        self.plot.setLabel("bottom", "Swedish time (CET / CEST)")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.legend = LegendStrip(WORST_WEEK_LEGEND)
        self.hot = self.plot.plot([], [], pen=pg.mkPen(HOTSPOT_COLOR, width=2))
        self.oil = self.plot.plot([], [], pen=pg.mkPen(OIL_COLOR, width=1.5))
        self.amb = self.plot.plot([], [], pen=pg.mkPen(AMBIENT_COLOR, width=1.5))
        self._rules = [rule_line(self.plot, 98, RULE_80, "98 °C: ageing rate 1", LABEL_LEFT, priority=0),
                       rule_line(self.plot, 120, HOTSPOT_COLOR, "120 °C IEC limit", LABEL_LEFT, priority=1)]
        self.right = pg.ViewBox()
        self.plot.showAxis("right")
        for side in ("left", "right"):
            self.plot.getAxis(side).enableAutoSIPrefix(False)    # "1500 %", never "1.5 k%"
        self.plot.plotItem.layout.setContentsMargins(1, 1, RIGHT_AXIS_MARGIN, 1)
        self.plot.scene().addItem(self.right)
        self.plot.getAxis("right").linkToView(self.right)
        self.right.setXLink(self.plot)
        self.plot.setLabel("right", "loading", units="%")
        self.load = pg.PlotCurveItem(pen=pg.mkPen(LOAD_COLOR, width=1, style=Qt.DotLine),
                                     brush=pg.mkBrush(31, 78, 121, 25), fillLevel=0)
        self.right.addItem(self.load)
        self.plot.getViewBox().sigResized.connect(self._sync)
        self.caption = ElidedLabel("")
        self.caption.setProperty("muted", True)
        lay.addWidget(self.legend)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.caption)

    def _sync(self):
        self.right.setGeometry(self.plot.getViewBox().sceneBoundingRect())
        self.right.linkedViewChanged(self.plot.getViewBox(), self.right.XAxis)

    def set_week(self, week: pd.DataFrame, hourly: pd.DataFrame, ambient: pd.Series, step_h: float = 1.0,
                 thermal_valid: bool = True):
        """thermal_valid=False (the verdict withdrew the thermal figures: loading above 2 × rated): the hot-spot and
        top-oil curves are not drawn and the caption gives no life or temperature — a clipped or extrapolated run
        printed '1583043024046439680.000 days' and '458 °C' here before 22 Oct 2026."""
        if not thermal_valid:                           # the withdrawn ageing ranks nothing: the heaviest loading week
            n = min(int(round(7 * 24 / step_h)), len(hourly))
            end = hourly["loading_pct"].rolling(n).sum().idxmax()
            week = hourly.loc[:end].iloc[-n:]
        x = _axis_date(week.index)
        if thermal_valid:
            self.hot.setData(x, week["theta_h_max"].to_numpy())
            self.oil.setData(x, week["theta_o"].to_numpy())
        else:
            self.hot.setData([], [])
            self.oil.setData([], [])
        for line in self._rules:                        # no °C rule lines without a transformer temperature
            line.setVisible(thermal_valid)
        self.legend.setText(legend_html(WORST_WEEK_LEGEND if thermal_valid else WORST_WEEK_LEGEND_LOADING_ONLY))
        self.amb.setData(x, ambient.loc[week.index].to_numpy())
        lp = hourly.loc[week.index, "loading_pct"].to_numpy()
        self.load.setData(x, lp)
        self.plot.setXRange(x[0], x[-1] + _step_seconds(hourly.index), padding=0)
        lo = min(float(ambient.loc[week.index].min()), 0) - 5
        hi = max(float(week["theta_h_max"].max()) if thermal_valid else 0, 125) + 5
        self.plot.setYRange(lo, hi)
        # the loading sits in the lower third of the chart so it never visually crosses the °C limit lines
        self.right.setYRange(0, max(110, float(lp.max()) * 1.05) * 3)
        self._sync()
        start = week.index[0].tz_convert(LOCAL)
        outdoor = f"outdoor {ambient.loc[week.index].min():.1f} … {ambient.loc[week.index].max():.1f} °C"
        starts = f"{start:%a %d %b %Y %H:%M} {start.tzname()}"
        if not thermal_valid:
            self.caption.setText(f"Heaviest week by loading, starting {starts}: life used and transformer "
                                 f"temperatures not computed — the loading went above {THERMAL_RANGE_K:g} × rated, the "
                                 f"IEC short-time emergency limit; {outdoor}")
            return
        life = float(week["V_mean"].sum() * step_h / 24)
        self.caption.setText(f"Worst week starts {starts}: {fmt_quantity(life, 'days')} days of insulation life "
                             f"used, max hot-spot {week['theta_h_max'].max():.1f} °C, {outdoor}")
