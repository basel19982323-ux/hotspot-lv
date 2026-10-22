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

Time axis: the x values are the Swedish wall-clock time expressed as epoch
seconds, and the DateAxisItem is told utcOffset=0 so it prints them as they
are — whatever time zone the PC runs in. (The first version let the axis add
the machine's own offset on top: the ticks were hours off on any PC outside
Sweden.)
"""

from __future__ import annotations

import html

import numpy as np
import pandas as pd
import pyqtgraph as pg
import shiboken6
from PySide6.QtCore import Qt, Signal
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
from hotspotlv.verdict import TOL

pg.setConfigOptions(antialias=True, background="w", foreground="#333333")
LOCAL = "Europe/Stockholm"
RIGHT_AXIS_MARGIN = 8     # px: pyqtgraph reserves only 0.8 × its height for a rotated axis label, so a right-axis
                          # label spills ~5 px past the plot and is cut in a fixed-size grab (the PDF card)
LABEL_LEFT = {"position": 0.02, "anchors": [(0, 0), (0, 1)]}      # text starts 2 % in from the left edge
LABEL_RIGHT = {"position": 0.98, "anchors": [(1, 0), (1, 1)]}     # text ends 2 % in from the right edge
LABEL_MIN_PLOT_PX = 120   # below this data-area height the rule labels collide with each other and the data (laptop)


def rule_line(plot, y: float, color, text: str, where: dict, fill=None) -> pg.InfiniteLine:
    """A dashed horizontal rule line with a label. The label is shown only while its line is inside the y-range
    and the data area is tall enough — never a label without its line, never labels piled on each other
    (review of 22 Oct 2026: at 1093 × 614 all three duration labels overlapped and the 1.5 × one floated alone)."""
    opts = {"color": color, **where}
    if fill is not None:
        opts["fill"] = fill
    line = pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen(color, style=Qt.DashLine), label=text, labelOpts=opts)
    plot.addItem(line)
    vb = plot.getViewBox()

    def update(*_):
        if not (shiboken6.isValid(vb) and shiboken6.isValid(line) and shiboken6.isValid(line.label)):
            return                                              # the view box resizes while it is being torn down
        lo, hi = vb.viewRange()[1]
        line.label.setVisible(vb.height() >= LABEL_MIN_PLOT_PX and lo <= y <= hi)

    vb.sigResized.connect(update)
    vb.sigYRangeChanged.connect(update)
    update()
    return line


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


def _axis_date(index: pd.DatetimeIndex) -> np.ndarray:
    """Seconds since the epoch of the LOCAL wall-clock time (shown as-is by a utcOffset=0 axis)."""
    local = index.tz_convert(LOCAL).tz_localize(None)
    return np.asarray(local.to_numpy().astype("datetime64[s]").astype("int64"), dtype=float)


def _nearest(x: np.ndarray, value: float) -> int:
    """Index of the sample nearest to value (not the next one, as searchsorted alone would give)."""
    i = int(np.searchsorted(x, value))
    if i <= 0:
        return 0
    if i >= len(x):
        return len(x) - 1
    return i if abs(x[i] - value) < abs(x[i - 1] - value) else i - 1


def _date_axis():
    return pg.DateAxisItem(utcOffset=0)


class DurationCurve(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "loading", units="% of nameplate")
        self.plot.setLabel("bottom", "hours in the period above this loading")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.legend = LegendStrip(DURATION_LEGEND)
        self.curve = self.plot.plot([], [], pen=pg.mkPen(LOAD_COLOR, width=2))
        for y, c, txt in ((NAMEPLATE_RULE_PCT, RULE_80, "80 % rule"), (100, RULE_100, "nameplate"),
                          (150, HOTSPOT_COLOR, "IEC 1.5 × rated")):
            rule_line(self.plot, y, c, txt, LABEL_RIGHT)
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
        self.caption.setText(f"Above 80 %: {above80:,.0f} h · above 100 %: {above100:,.0f} h · peak {lp[0]:.0f} %")


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
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.legend = LegendStrip(TIMELINE_LEGEND)
        self.load = self.plot.plot([], [], pen=pg.mkPen(LOAD_COLOR, width=1))
        # the rule labels sit mid-year (the summer trough), where the loading curve is far below them
        for y, c, txt in ((NAMEPLATE_RULE_PCT, RULE_80, "80 % rule"), (100, RULE_100, "nameplate")):
            rule_line(self.plot, y, c, txt, {"position": 0.5}, fill=pg.mkBrush(255, 255, 255, 220))   # white backing:
                                                                                     # a hot summer can run through it
        self.right = pg.ViewBox()
        self.plot.showAxis("right")
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
        if ambient is not None:
            self.amb.setData(self._x, ambient.to_numpy())
        self.plot.setXRange(self._x[0], self._x[-1])
        self.plot.setYRange(0, max(110, float(hourly["loading_pct"].max()) * 1.05))
        lo = min(float(ambient.min()) if ambient is not None else 0, 0)
        hi = max(float(thermal["theta_h_max"].max()) if thermal is not None else 100, 100)
        self.right.setYRange(lo - 5, hi + 5)
        self._sync()
        self.go_to(int(np.argmax(hourly["loading_pct"].to_numpy())))

    def _moved(self):
        if len(self._x) == 0:
            return
        self._i = i = _nearest(self._x, self.scrubber.value())
        h = self._hourly.iloc[i]
        txt = f"{self.label_at(i)} — loading {h['loading_pct']:.0f} % ({h['s_kva']:.0f} kVA)"
        if self._thermal is not None:
            txt += f" · hot-spot {self._thermal['theta_h_max'].iloc[i]:.0f} °C · top-oil {self._thermal['theta_o'].iloc[i]:.0f} °C"
        if self._ambient is not None:
            txt += f" · outdoor {self._ambient.iloc[i]:.1f} °C"
        if "vmin_pu" in h and h["vmin_pu"] != 1.0:
            txt += f" · lowest house {h['vmin_pu']:.3f} pu"
        self.readout.setText(txt)
        self.hour_changed.emit(i)

    def go_to(self, i: int):
        if len(self._x):
            self.scrubber.setPos(self._x[int(np.clip(i, 0, len(self._x) - 1))])
            self._moved()

    def label_at(self, i: int) -> str:
        if self._hourly is None:
            return ""
        t = self._hourly.index[int(np.clip(i, 0, len(self._hourly) - 1))].tz_convert(LOCAL)
        return f"{t:%a %d %b %Y %H:%M} local"

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
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.legend = LegendStrip(WORST_WEEK_LEGEND)
        self.hot = self.plot.plot([], [], pen=pg.mkPen(HOTSPOT_COLOR, width=2))
        self.oil = self.plot.plot([], [], pen=pg.mkPen(OIL_COLOR, width=1.5))
        self.amb = self.plot.plot([], [], pen=pg.mkPen(AMBIENT_COLOR, width=1.5))
        rule_line(self.plot, 98, RULE_80, "98 °C: ageing rate 1", LABEL_LEFT)
        rule_line(self.plot, 120, HOTSPOT_COLOR, "120 °C IEC limit", LABEL_LEFT)
        self.right = pg.ViewBox()
        self.plot.showAxis("right")
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

    def set_week(self, week: pd.DataFrame, hourly: pd.DataFrame, ambient: pd.Series, step_h: float = 1.0):
        x = _axis_date(week.index)
        self.hot.setData(x, week["theta_h_max"].to_numpy())
        self.oil.setData(x, week["theta_o"].to_numpy())
        self.amb.setData(x, ambient.loc[week.index].to_numpy())
        lp = hourly.loc[week.index, "loading_pct"].to_numpy()
        self.load.setData(x, lp)
        self.plot.setXRange(x[0], x[-1])
        lo = min(float(ambient.loc[week.index].min()), 0) - 5
        hi = max(float(week["theta_h_max"].max()), 125) + 5
        self.plot.setYRange(lo, hi)
        # the loading sits in the lower third of the chart so it never visually crosses the °C limit lines
        self.right.setYRange(0, max(110, float(lp.max()) * 1.05) * 3)
        self._sync()
        life = float(week["V_mean"].sum() * step_h / 24)
        start = week.index[0].tz_convert(LOCAL)
        self.caption.setText(f"Worst week starts {start:%d %b %Y}: {life:.3f} days of insulation life used, "
                             f"max hot-spot {week['theta_h_max'].max():.0f} °C, "
                             f"outdoor {ambient.loc[week.index].min():.0f} … {ambient.loc[week.index].max():.0f} °C")
