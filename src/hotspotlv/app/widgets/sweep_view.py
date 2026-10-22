"""The Sweep tab: vary the number of houses (or the EV / PV share) and show where each view of
the station flips.

    ┌ controls ────────────┐ ┌ base case: 60 houses / 500 kVA · … (banner if the inputs differ) ┐
    │ knob [houses ▾]      │ ┌ chart ──────────────────────────────────────────────────────────┐
    │ from 45 to 120 by 15 │ │ left axis: hours above 80 % (on paper) and above 100 %           │
    │ values: 45, 60, …    │ │ right axis: life used, days/yr (log) with the 365 design line     │
    │ [Run sweep (6 runs)] │ │ verdict squares below the axis · first At risk ┊                 │
    │ ▓▓░ 2/6 · 1:05       │ ├─────────────────────────────────────────────────────────────────┤
    └──────────────────────┘ │ legend strip · table of the sweep rows                           │
                             └─────────────────────────────────────────────────────────────────┘
    caption: "the 80 % rule first flags the station at 75 houses; the insulation stays below the
              IEC design rate …; the verdict first turns At risk at 90 houses because of …"

The engine runs the sweep (study.sweep); this file only draws it.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QTableView, QVBoxLayout, QWidget)

from hotspotlv.app.theme import HOTSPOT_COLOR, INK, LOAD_COLOR, RULE_100, VERDICT_COLORS
from hotspotlv.app.widgets.describe import describe_case
from hotspotlv.app.widgets.station_panel import show_error
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.workers import Worker
from hotspotlv.study import Case, sweep

NOT_COMPUTED = 'not computed (loading above 2 × rated)'

# knob → (label, scale from the spinbox number to the engine value, unit suffix, (lo, hi, default from, to, step))
KNOBS = {
    "n_houses": ("houses", 1.0, " houses", (3, 200, 45, 120, 15)),
    "ev_share": ("share of houses with an EV charger (%)", 0.01, " %", (0, 100, 0, 100, 20)),
    "pv_share": ("share of houses with rooftop PV (%)", 0.01, " %", (0, 100, 0, 100, 20)),
}
SHOW = ["verdict", "peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year", "max_hot_spot_c",
        "voltage_bad_hours", "voltage_plan_hours", "max_cable_pct", "exact_hours", "why"]
DESIGN_RATE_DAYS = 365.0            # IEC 60076-7: V = 1 uses one day of life per day
LIFE_FLOOR = 1e-4                   # smallest value drawn on the log axis
LEGEND_HTML = (f"<span style='color:{LOAD_COLOR}'>&#9679; hours above 80 % (the paper rule)</span> &nbsp; "
               f"<span style='color:{RULE_100}'>&#9650; hours above 100 %</span> &nbsp; "
               f"<span style='color:{HOTSPOT_COLOR}'>&#9679; life used, days/yr (right axis, log; dashed = 365, the IEC design rate; &#9650; = not computed, loading above 2 × rated)</span>"
               " &nbsp; squares below the axis = verdict: "
               f"<span style='color:{VERDICT_COLORS['OK']}'>&#9632; OK</span> "
               f"<span style='color:{VERDICT_COLORS['Watch']}'>&#9632; Watch</span> "
               f"<span style='color:{VERDICT_COLORS['At risk']}'>&#9632; At risk</span>")


class LogAxis(pg.AxisItem):
    """A log axis whose ticks read 0.001 … 1 … 10 … 100 (pyqtgraph's default mixes 0.1 with 10¹)."""

    def logTickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            x = 10 ** float(v) * scale
            out.append(f"{x:g}" if x >= 1 else f"{x:.{max(0, -int(np.floor(np.log10(x) + 1e-9)))}f}")
        return out


class SweepView(QWidget):
    """`case_provider()` returns the base Case (the scenario panel's current inputs)."""

    run_started = Signal()
    run_finished = Signal()

    def __init__(self, case_provider, weather: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._case_provider = case_provider
        self.weather = weather
        self.table: pd.DataFrame | None = None
        self.knob = "n_houses"
        self._worker: Worker | None = None
        self._run_knob = "n_houses"; self._run_case: Case | None = None; self._run_values: list = []
        self._t0 = 0.0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self._progress_text = ""
        self._blocked: str | None = None; self._saved_status = ""; self._done_status = ""
        self._shown_case: Case | None = None
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("Sweep one knob")
        g.setToolTip("Everything else stays as the inputs on the left of the window, with the same random draw.")
        f = QFormLayout(g); f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.knob_box = QComboBox(); [self.knob_box.addItem(label, key) for key, (label, *_) in KNOBS.items()]
        self.knob_box.setToolTip("Which input to vary; the others stay as set on the left.")
        self.knob_box.currentIndexChanged.connect(self._knob_changed)
        self.start = QSpinBox(); self.stop = QSpinBox(); self.step = QSpinBox()
        self.start.setToolTip("First value of the sweep."); self.stop.setToolTip("Last value (reached only if the step lands on it).")
        self.step.setToolTip("Distance between values; each value is one full case.")
        self.fast = QCheckBox("fast mode"); self.fast.setChecked(True)
        self.fast.setToolTip("Fits a power-flow proxy on ~300 real power flows and computes every hour near a limit exactly. "
                             "Heavy streets fall back to the full power flow on their own (up to ~2 min per value).")
        f.addRow("Knob", self.knob_box)
        f.addRow("From", self.start)
        f.addRow("To", self.stop)
        f.addRow("Step", self.step)
        self.values_label = QLabel(""); self.values_label.setProperty("muted", True); self.values_label.setWordWrap(True)
        f.addRow(self.values_label)
        f.addRow("", self.fast)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run sweep"); self.run_btn.clicked.connect(self.run)
        self.run_btn.setToolTip("Runs one full case per value on the inputs on the left of the window (not on the case shown in the card).")
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setVisible(False)
        row.addWidget(self.run_btn, 1); row.addWidget(self.cancel_btn)
        f.addRow(row)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        f.addRow(self.progress)
        self.status = QLabel("Each value is a full case on the current inputs: seconds in fast mode while the proxy holds, "
                             "up to ~2 min per value when a heavy street falls back to the full power flow.")
        self.status.setProperty("muted", True); self.status.setWordWrap(True)
        f.addRow(self.status)
        for sb in (self.start, self.stop, self.step):
            sb.valueChanged.connect(self._count)
        self._controls = [self.knob_box, self.start, self.stop, self.step, self.fast]
        column = QWidget(); cv = QVBoxLayout(column); cv.setContentsMargins(0, 0, 6, 0); cv.addWidget(g); cv.addStretch(1)
        scroll = QScrollArea(); scroll.setWidget(column); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        column.setMinimumWidth(0); scroll.setMinimumWidth(240)

        results = QWidget(); rv = QVBoxLayout(results); rv.setContentsMargins(0, 0, 0, 0)
        self.banner = QLabel(""); self.banner.setWordWrap(True); self.banner.setVisible(False)
        self.banner.setStyleSheet(f"color: {INK}; font-weight: 600; background: #fff4d6; padding: 4px; border-radius: 4px;")
        self.base_label = QLabel(""); self.base_label.setProperty("muted", True); self.base_label.setWordWrap(True)
        rv.addWidget(self.banner); rv.addWidget(self.base_label)
        split = QSplitter(Qt.Vertical)
        self.plot = pg.PlotWidget(axisItems={"right": LogAxis("right")})
        self.plot.setLabel("left", "h/yr above 80 % / 100 % of nameplate")
        self.plot.setMinimumHeight(130)
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.h80 = self.plot.plot([], [], pen=pg.mkPen(LOAD_COLOR, width=2), symbol="o", symbolBrush=LOAD_COLOR)
        self.h100 = self.plot.plot([], [], pen=pg.mkPen(RULE_100, width=2, style=Qt.DashLine), symbol="t", symbolBrush=RULE_100)
        self.right = pg.ViewBox()
        self.plot.showAxis("right"); self.plot.scene().addItem(self.right)
        self.plot.getAxis("right").linkToView(self.right); self.right.setXLink(self.plot)
        self.plot.setLabel("right", "life used, days/yr (log)")
        self.right.setLogMode("y", True)
        self.plot.getAxis("right").setLogMode(True)
        self.life = pg.PlotCurveItem(pen=pg.mkPen(HOTSPOT_COLOR, width=2))
        self.life_pts = pg.ScatterPlotItem(size=8, brush=HOTSPOT_COLOR, pen=pg.mkPen(None))
        self.design_line = pg.InfiniteLine(angle=0, pos=np.log10(DESIGN_RATE_DAYS),
                                           pen=pg.mkPen(HOTSPOT_COLOR, style=Qt.DashLine),
                                           label="365 days/yr = IEC design rate", labelOpts={"position": 0.5, "color": HOTSPOT_COLOR})
        self.life_na = pg.ScatterPlotItem(size=11, symbol="t1", brush=HOTSPOT_COLOR, pen=pg.mkPen(None))   # ▲ at the top
        self.right.addItem(self.life); self.right.addItem(self.life_pts); self.right.addItem(self.design_line)
        self.right.addItem(self.life_na)
        self.plot.getViewBox().sigResized.connect(self._sync)
        self.flip_line = pg.InfiniteLine(angle=90, pen=pg.mkPen(VERDICT_COLORS["At risk"], style=Qt.DashLine),
                                         label="first At risk", labelOpts={"position": 0.9, "color": VERDICT_COLORS["At risk"]})
        self.flip_line.setVisible(False); self.plot.addItem(self.flip_line)
        self._strip = []
        split.addWidget(self.plot)
        lower = QWidget(); lv = QVBoxLayout(lower); lv.setContentsMargins(0, 0, 0, 0)
        self.legend = QLabel(LEGEND_HTML); self.legend.setWordWrap(True); self.legend.setTextFormat(Qt.RichText)
        self.view = QTableView(); self.model = DataFrameModel(decimals=1); self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True); self.view.setMinimumHeight(84)        # header + two rows, never less
        lv.addWidget(self.legend); lv.addWidget(self.view, 1)
        split.addWidget(lower)
        split.setSizes([300, 200])
        self.caption = QLabel(""); self.caption.setProperty("muted", True); self.caption.setWordWrap(True)
        rv.addWidget(split, 1); rv.addWidget(self.caption)
        outer = QSplitter(Qt.Horizontal)
        outer.addWidget(scroll); outer.addWidget(results)
        outer.setStretchFactor(0, 0); outer.setStretchFactor(1, 1); outer.setSizes([290, 900])
        lay.addWidget(outer)
        self._knob_changed()

    # ---------------- controls
    def _knob_changed(self):
        self.knob = self.knob_box.currentData()
        _, _, suffix, (lo, hi, a, b, st) = KNOBS[self.knob]
        for sb in (self.start, self.stop):
            sb.blockSignals(True); sb.setRange(lo, hi); sb.setSuffix(suffix); sb.blockSignals(False)
        self.step.blockSignals(True); self.step.setRange(1, max(1, hi - lo)); self.step.setSuffix(suffix); self.step.blockSignals(False)
        self.start.setValue(a); self.stop.setValue(b); self.step.setValue(st)
        self._count()

    def raw_values(self) -> list[int]:
        lo, hi, st = self.start.value(), self.stop.value(), max(1, self.step.value())
        return list(range(lo, hi + 1, st)) if lo <= hi else []

    def values(self) -> list:
        scale = KNOBS[self.knob][1]
        return [v if scale == 1.0 else round(v * scale, 4) for v in self.raw_values()]

    def _fmt(self, v, knob=None) -> str:
        """An engine value printed with its unit: 75 houses / 60 %."""
        scale = KNOBS[knob or self.knob][1]
        return f"{v:g} houses" if scale == 1.0 else f"{v * 100:.0f} %"

    def _count(self):
        raw = self.raw_values()
        n = len(raw)
        unit = KNOBS[self.knob][2].strip()
        if n:
            text = "values: " + ", ".join(str(v) for v in raw) + f" {unit}"
            if raw[-1] != self.stop.value():
                text += f" — To {self.stop.value()} is not reached with this step"
        else:
            text = "From is above To — nothing to run"
        self.values_label.setText(text)
        self.run_btn.setText(f"Run sweep ({n} runs)" if n else "Run sweep")
        if not self.is_running() and self._blocked is None:
            self.run_btn.setEnabled(n > 0)
        self.plot.setLabel("bottom", KNOBS[self.knob][0])

    def _sync(self):
        self.right.setGeometry(self.plot.getViewBox().sceneBoundingRect())
        self.right.linkedViewChanged(self.plot.getViewBox(), self.right.XAxis)

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ---------------- the scenario panel tells the tab about the inputs
    def set_banner(self, text: str | None):
        self.banner.setText(text or ""); self.banner.setVisible(bool(text))

    def inputs_changed(self, case=None):
        """`case` = the current inputs; stale only while they differ from the sweep's own base case."""
        if self.table is None or self.is_running() or self._blocked:
            return
        if case is not None and self._shown_case is not None and case == self._shown_case:
            self.status.setText(self._done_status)
        else:
            self.status.setText("Inputs changed since this sweep — press Run sweep to update.")

    def block(self, reason: str | None):
        """Another run is in progress elsewhere in the panel (or None to lift the block)."""
        if self.is_running():
            return
        if reason and self._blocked is None:
            self._saved_status = self.status.text()
        if reason is None and self._blocked is not None:
            self.status.setText(self._saved_status)
        self._blocked = reason
        self.run_btn.setEnabled(reason is None and len(self.raw_values()) > 0)
        if reason:
            self.status.setText(reason)

    # ---------------- run
    def run(self):
        if self.is_running() or self._blocked:
            return
        vals = self.values()
        if not vals:
            return
        try:
            case = self._case_provider()
        except Exception as e:
            show_error(self, "Inputs", f"{type(e).__name__}: {e}"); return
        if case is None:
            return
        if self.knob == "n_houses" and vals[0] < case.n_feeders:
            show_error(self, "Inputs", f"The sweep starts at {vals[0]} houses but the street has {case.n_feeders} feeders — "
                       "raise From or reduce the feeders.")
            return
        self._run_knob, self._run_case, self._run_values = self.knob, case, vals
        for w in self._controls:
            w.setEnabled(False)
        self.run_btn.setEnabled(False); self.cancel_btn.setVisible(True)
        self.progress.setVisible(True); self.progress.setRange(0, len(vals)); self.progress.setValue(0)
        self._t0 = time.perf_counter(); self._timer.start(1000); self._progress_text = "Starting …"; self._tick()
        self._worker = Worker(sweep, case, self.weather, self.knob, vals, fast=self.fast.isChecked(), with_progress=True,
                              with_checkpoint=True)
        self._worker.progress.connect(self._progress)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()
        self.run_started.emit()

    def _progress(self, done, total, label):
        if not self._current():
            return
        self.progress.setRange(0, total); self.progress.setValue(done)
        value = self._fmt(self._run_values[done], self._run_knob) if done < len(self._run_values) else label
        self._progress_text = f"Running {done + 1} of {total}: {value}"
        self._tick()

    def _tick(self):
        t = time.perf_counter() - self._t0
        self.status.setText(f"{self._progress_text} … {int(t // 60)}:{int(t % 60):02d} elapsed")

    def _finish_ui(self):
        self._timer.stop()
        for w in self._controls:
            w.setEnabled(True)
        self.run_btn.setEnabled(True); self.cancel_btn.setVisible(False); self.progress.setVisible(False); self._count()
        self.run_finished.emit()

    def _current(self) -> bool:
        return self.sender() is None or self.sender() is self._worker

    def cancel(self):
        w, self._worker = self._worker, None
        if w is not None and w.isRunning():
            w.cancel()
        self._finish_ui(); self.status.setText("Cancelled.")

    def _failed(self, msg):
        if not self._current():
            return
        self._finish_ui(); self.status.setText("")
        show_error(self, "Sweep failed", msg)

    def _done(self, table: pd.DataFrame):
        if not self._current():
            return
        self._finish_ui()
        self.set_table(table, self._run_knob, self._run_case)

    # ---------------- show
    def set_table(self, table: pd.DataFrame, knob: str | None = None, case: Case | None = None):
        """Draw a sweep result (used by the worker and by tests). `knob` is the swept field (the
        table's index); it is captured at run time so that the combo can be changed meanwhile."""
        try:
            knob = knob or self.knob
            self.table = table
            label, scale, _, _ = KNOBS[knob]
            x = np.asarray(table.index, dtype=float) / (scale if scale != 1.0 else 1.0)
            hours = table[["hours_above_80", "hours_above_100"]].to_numpy(dtype=float)
            top = max(10.0, float(np.nanmax(hours)) * 1.15)
            self.h80.setData(x, hours[:, 0])
            self.h100.setData(x, hours[:, 1])
            life_raw = table["life_days_per_year"].to_numpy(dtype=float)
            ok = np.isfinite(life_raw)                       # NaN = withdrawn above 2 × rated: never drawn as a value
            life = np.maximum(np.where(ok, life_raw, LIFE_FLOOR), LIFE_FLOOR)
            self.life.setData(x[ok], np.log10(life[ok])); self.life_pts.setData(x[ok], np.log10(life[ok]))
            self.plot.setLabel("bottom", label)
            self.plot.setXRange(float(x.min()) - 1, float(x.max()) + 1)
            self.plot.setYRange(-0.16 * top, top)
            life_top = np.log10(max(6 * DESIGN_RATE_DAYS, float(life[ok].max()) * 2 if ok.any() else 0))
            self.right.setYRange(np.log10(LIFE_FLOOR), life_top)
            self.life_na.setData(x[~ok], np.full(int((~ok).sum()), life_top * 0.93 + np.log10(LIFE_FLOOR) * 0.07))
            self.life_na.setToolTip(f"life used {NOT_COMPUTED}" if (~ok).any() else "")
            self._sync()
            # verdict squares in their own strip below the hours axis
            for item in self._strip:
                self.plot.removeItem(item)
            self._strip = []
            for xi, v in zip(x, table["verdict"]):
                s = pg.ScatterPlotItem([xi], [-0.10 * top], size=14, symbol="s", brush=VERDICT_COLORS.get(v, "#999"), pen=pg.mkPen(None))
                s.setToolTip(str(v)); self.plot.addItem(s); self._strip.append(s)
            at_risk = table.index[table["verdict"] == "At risk"]
            if len(at_risk):
                self.flip_line.setPos(float(at_risk[0]) / (scale if scale != 1.0 else 1.0)); self.flip_line.setVisible(True)
            else:
                self.flip_line.setVisible(False)
            self.caption.setText(self._caption(table, knob))
            shown = table[[c for c in SHOW if c in table.columns]].copy()
            shown.insert(0, label, x)
            self.model.set_df(shown.reset_index(drop=True))
            self.view.resizeColumnsToContents()
            self._shown_case = case
            self.base_label.setText("Base case: " + describe_case(case) if case is not None else "Base case: the current inputs")
            self._done_status = (f"Done: {len(table)} runs. Squares below the chart's axis show the verdict per value; "
                                 "the table has every number.")
            self.status.setText(self._done_status)
        except Exception as e:
            show_error(self, "Could not show the sweep", f"{type(e).__name__}: {e}")

    def _caption(self, t: pd.DataFrame, knob: str) -> str:
        fmt = lambda v: self._fmt(v, knob)
        parts = []
        paper = t.index[t["hours_above_80"] > 0]
        parts.append(f"the 80 % rule first flags the station at {fmt(paper[0])}" if len(paper)
                     else "the 80 % rule never flags the station in this range")
        life = t["life_days_per_year"]
        over = t.index[life > DESIGN_RATE_DAYS]
        withdrawn = t.index[life.isna()]
        if len(over):
            parts.append(f"the insulation ages faster than its IEC design rate (365 days/yr) from {fmt(over[0])}")
        elif life.notna().any():
            computed = life.dropna()
            parts.append(f"where it is computed, the insulation stays below its IEC design rate of 365 days/yr "
                         f"(max {computed.max():.2f} days/yr at {fmt(computed.idxmax())})" if len(withdrawn) else
                         f"the insulation stays below its IEC design rate of 365 days/yr "
                         f"(max {computed.max():.2f} days/yr at {fmt(computed.idxmax())})")
        if len(withdrawn):
            parts.append(f"life used is not computed from {fmt(withdrawn[0])} (loading above 2 × rated, marked ▲)")
        risk = t.index[t["verdict"] == "At risk"]
        if len(risk):
            why = str(t.loc[risk[0], "why"]).split(" — ", 1)[-1].split(" (also")[0]
            parts.append(f"the verdict first turns At risk at {fmt(risk[0])} because of {why}")
            after = t.loc[t.index > risk[0], "verdict"]
            if (after != "At risk").any():
                parts.append("the verdicts are not monotonic (the random draw differs per value)")
        else:
            parts.append("the verdict never turns At risk in this range")
        return "; ".join(parts) + "."
