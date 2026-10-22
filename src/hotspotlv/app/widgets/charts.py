"""The three charts, built on pyqtgraph.

  DurationCurve   loading % sorted high → low, with the 80 / 100 / 150 % lines
                  ("for how many hours is the station above x")
  YearTimeline    loading % (left axis) and hot-spot / outdoor °C (right axis) over the
                  whole period, with a draggable scrubber line → emits hour_changed(i);
                  ← / → keys move it one step
  WorstWeek       the 7 days that used the most insulation life: hot-spot, top-oil,
                  outdoor (°C) and, low and faint, the loading (%)

Time axis: the x values are the Swedish wall-clock time expressed as epoch
seconds, and the DateAxisItem is told utcOffset=0 so it prints them as they
are — whatever time zone the PC runs in. (The first version let the axis add
the machine's own offset on top: the ticks were hours off on any PC outside
Sweden.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
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

pg.setConfigOptions(antialias=True, background="w", foreground="#333333")
LOCAL = "Europe/Stockholm"


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
        self.plot.setLabel("left", "transformer loading", units="% of nameplate")
        self.plot.setLabel("bottom", "hours per year above this loading")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        legend = self.plot.addLegend()
        legend.anchor(itemPos=(1, 1), parentPos=(1, 1), offset=(-12, -12))
        self.curve = self.plot.plot([], [], pen=pg.mkPen(LOAD_COLOR, width=2), name="loading duration curve")
        for y, c, txt in ((80, RULE_80, "80 % rule"), (100, RULE_100, "nameplate"), (150, HOTSPOT_COLOR, "IEC 1.5 pu limit")):
            self.plot.addItem(pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen(c, style=Qt.DashLine), label=txt,
                                              labelOpts={"position": 0.97, "color": c}))
        self.caption = QLabel("")
        self.caption.setProperty("muted", True)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.caption)

    def set_year(self, hourly: pd.DataFrame, step_h: float = 1.0):
        lp = np.sort(hourly["loading_pct"].to_numpy())[::-1]
        hours = np.arange(1, len(lp) + 1) * step_h
        self.curve.setData(hours, lp)
        self.plot.setXRange(0, max(hours[-1], 1))
        self.plot.setYRange(0, max(110, float(lp[0]) * 1.05))
        above80 = float((hourly["loading_pct"] > 80).sum() * step_h)
        above100 = float((hourly["loading_pct"] > 100).sum() * step_h)
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
        legend = self.plot.addLegend()
        legend.anchor(itemPos=(1, 1), parentPos=(1, 1), offset=(-12, -12))        # bottom-right, off the data
        self.load = self.plot.plot([], [], pen=pg.mkPen(LOAD_COLOR, width=1), name="loading %")
        self.plot.addItem(pg.InfiniteLine(pos=80, angle=0, pen=pg.mkPen(RULE_80, style=Qt.DashLine)))
        self.plot.addItem(pg.InfiniteLine(pos=100, angle=0, pen=pg.mkPen(RULE_100, style=Qt.DashLine)))
        self.right = pg.ViewBox()
        self.plot.showAxis("right")
        self.plot.scene().addItem(self.right)
        self.plot.getAxis("right").linkToView(self.right)
        self.right.setXLink(self.plot)
        self.plot.setLabel("right", "temperature", units="°C")
        self.hot = pg.PlotCurveItem(pen=pg.mkPen(HOTSPOT_COLOR, width=1))
        self.amb = pg.PlotCurveItem(pen=pg.mkPen(AMBIENT_COLOR, width=1))
        self.right.addItem(self.hot)
        self.right.addItem(self.amb)
        self.plot.plotItem.legend.addItem(self.hot, "hot-spot °C")
        self.plot.plotItem.legend.addItem(self.amb, "outdoor °C")
        self.plot.getViewBox().sigResized.connect(self._sync)
        self.scrubber = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#000000", width=2),
                                        hoverPen=pg.mkPen("#000000", width=4), label="drag me  ◂ ▸",
                                        labelOpts={"position": 0.97, "color": "#000000", "movable": False})
        self.plot.addItem(self.scrubber)
        self.scrubber.sigPositionChanged.connect(self._moved)
        self.readout = ElidedLabel("")         # a wrapped label imposes its height on the column, an unwrapped one its width
        self.readout.setProperty("muted", True)
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
        legend = self.plot.addLegend()
        legend.anchor(itemPos=(1, 1), parentPos=(1, 1), offset=(-12, -12))
        self.hot = self.plot.plot([], [], pen=pg.mkPen(HOTSPOT_COLOR, width=2), name="hot-spot")
        self.oil = self.plot.plot([], [], pen=pg.mkPen(OIL_COLOR, width=1.5), name="top-oil")
        self.amb = self.plot.plot([], [], pen=pg.mkPen(AMBIENT_COLOR, width=1.5), name="outdoor")
        self.plot.addItem(pg.InfiniteLine(pos=98, angle=0, pen=pg.mkPen(RULE_80, style=Qt.DashLine), label="98 °C: ageing rate 1",
                                          labelOpts={"position": 0.03, "color": RULE_80}))
        self.plot.addItem(pg.InfiniteLine(pos=120, angle=0, pen=pg.mkPen(HOTSPOT_COLOR, style=Qt.DashLine), label="120 °C IEC limit",
                                          labelOpts={"position": 0.03, "color": HOTSPOT_COLOR}))
        self.right = pg.ViewBox()
        self.plot.showAxis("right")
        self.plot.scene().addItem(self.right)
        self.plot.getAxis("right").linkToView(self.right)
        self.right.setXLink(self.plot)
        self.plot.setLabel("right", "loading", units="%")
        self.load = pg.PlotCurveItem(pen=pg.mkPen(LOAD_COLOR, width=1, style=Qt.DotLine),
                                     brush=pg.mkBrush(31, 78, 121, 25), fillLevel=0)
        self.right.addItem(self.load)
        self.plot.plotItem.legend.addItem(self.load, "loading % (right axis, shown low)")
        self.plot.getViewBox().sigResized.connect(self._sync)
        self.caption = ElidedLabel("")
        self.caption.setProperty("muted", True)
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
