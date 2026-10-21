"""The Hosting-capacity tab: sweep the number of houses (or the EV share) and show where each
view of the station flips.

    ┌ controls ────────────┐ ┌ chart ──────────────────────────────────────────────────────┐
    │ knob [houses ▾]      │ │ left axis: hours above 80 % (on paper) and above 100 %       │
    │ from 45 to 120 by 15 │ │ right axis: days of life used per year (really, log scale)   │
    │ [Run sweep (6 runs)] │ │ verdict colour strip along the bottom · first At risk ┊      │
    │ ▓▓░ 2/6              │ ├─────────────────────────────────────────────────────────────┤
    └──────────────────────┘ │ table of the sweep rows                                     │
                             └─────────────────────────────────────────────────────────────┘
    caption: "the 80 % rule flags from N houses; the insulation stays below 1 day/yr to M;
              the verdict flips to At risk at K because of …"

The engine runs the sweep (study.sweep); this file only draws it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QSizePolicy, QSpinBox, QSplitter, QTableView, QVBoxLayout, QWidget)

from hotspotlv.app.theme import HOTSPOT_COLOR, LOAD_COLOR, RULE_100, VERDICT_COLORS
from hotspotlv.app.widgets.station_panel import show_error
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.workers import Worker
from hotspotlv.study import sweep

KNOBS = {"n_houses": ("houses", 1.0), "ev_share": ("share of houses with an EV charger (%)", 0.01),
         "pv_share": ("share of houses with rooftop PV (%)", 0.01)}
SHOW = ["verdict", "peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year", "max_hot_spot_c",
        "voltage_bad_hours", "voltage_plan_hours", "max_cable_pct", "exact_hours", "why"]


class SweepView(QWidget):
    """`case_provider()` returns the base Case (the scenario panel's current inputs)."""

    def __init__(self, case_provider, weather: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._case_provider = case_provider
        self.weather = weather
        self.table: pd.DataFrame | None = None
        self.knob = "n_houses"
        self._worker: Worker | None = None
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("Sweep one knob")
        g.setToolTip("Everything else stays as the inputs on the left of the window, with the same random draw.")
        g.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred); g.setFixedWidth(300)
        f = QFormLayout(g); f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.knob_box = QComboBox(); [self.knob_box.addItem(label, key) for key, (label, _) in KNOBS.items()]
        self.knob_box.currentIndexChanged.connect(self._knob_changed)
        self.start = QSpinBox(); self.stop = QSpinBox(); self.step = QSpinBox()
        for sb in (self.start, self.stop, self.step):
            sb.setRange(1, 300)
        self.start.setValue(45); self.stop.setValue(120); self.step.setValue(15)
        self.fast = QCheckBox("fast mode"); self.fast.setChecked(True)
        f.addRow("Knob", self.knob_box)
        f.addRow("From", self.start)
        f.addRow("To", self.stop)
        f.addRow("Step", self.step)
        f.addRow("", self.fast)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run sweep"); self.run_btn.clicked.connect(self.run)
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setVisible(False)
        row.addWidget(self.run_btn, 1); row.addWidget(self.cancel_btn)
        f.addRow(row)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        f.addRow(self.progress)
        self.status = QLabel("Each value is a full case on the current inputs (seconds in fast mode, up to ~2 min for a heavy street).")
        self.status.setProperty("muted", True); self.status.setWordWrap(True)
        f.addRow(self.status)
        for sb in (self.start, self.stop, self.step):
            sb.valueChanged.connect(self._count)
        column = QWidget(); cv = QVBoxLayout(column); cv.setContentsMargins(0, 0, 6, 0); cv.addWidget(g); cv.addStretch(1)
        column.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        lay.addWidget(column)

        results = QWidget(); rv = QVBoxLayout(results); rv.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Vertical)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "hours per year above the line")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        legend = self.plot.addLegend(); legend.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(60, 10))
        self.h80 = self.plot.plot([], [], pen=pg.mkPen(LOAD_COLOR, width=2), symbol="o", symbolBrush=LOAD_COLOR, name="hours above 80 % (the paper rule)")
        self.h100 = self.plot.plot([], [], pen=pg.mkPen(RULE_100, width=2, style=Qt.DashLine), symbol="s", symbolBrush=RULE_100, name="hours above 100 %")
        self.right = pg.ViewBox()
        self.plot.showAxis("right"); self.plot.scene().addItem(self.right)
        self.plot.getAxis("right").linkToView(self.right); self.right.setXLink(self.plot)
        self.plot.setLabel("right", "life used, days/yr (log)")
        self.right.setLogMode("y", True)
        self.plot.getAxis("right").setLogMode(True)
        self.life = pg.PlotCurveItem(pen=pg.mkPen(HOTSPOT_COLOR, width=2))
        self.life_pts = pg.ScatterPlotItem(size=8, brush=HOTSPOT_COLOR, pen=pg.mkPen(None))
        self.right.addItem(self.life); self.right.addItem(self.life_pts)
        legend.addItem(self.life, "days of life used per year (the thermal model)")
        self.plot.getViewBox().sigResized.connect(self._sync)
        self.flip_line = pg.InfiniteLine(angle=90, pen=pg.mkPen(VERDICT_COLORS["At risk"], style=Qt.DashLine),
                                         label="first At risk", labelOpts={"position": 0.9, "color": VERDICT_COLORS["At risk"]})
        self.flip_line.setVisible(False); self.plot.addItem(self.flip_line)
        self._strip = []
        split.addWidget(self.plot)
        self.view = QTableView(); self.model = DataFrameModel(decimals=1); self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        split.addWidget(self.view)
        split.setSizes([330, 170])
        self.caption = QLabel(""); self.caption.setProperty("muted", True); self.caption.setWordWrap(True)
        rv.addWidget(split, 1); rv.addWidget(self.caption)
        lay.addWidget(results, 1)
        self._kva = None
        self._count()

    # ---------------- controls
    def _knob_changed(self):
        self.knob = self.knob_box.currentData()
        if self.knob == "n_houses":
            self.start.setValue(45); self.stop.setValue(120); self.step.setValue(15)
        else:
            self.start.setValue(0); self.stop.setValue(100); self.step.setValue(20)
        self._count()

    def values(self) -> list:
        lo, hi, st = self.start.value(), self.stop.value(), max(1, self.step.value())
        raw = list(range(lo, hi + 1, st)) if lo <= hi else []
        scale = KNOBS[self.knob][1]
        return [v if scale == 1.0 else round(v * scale, 4) for v in raw]

    def _count(self):
        n = len(self.values())
        self.run_btn.setText(f"Run sweep ({n} runs)" if n else "Run sweep")
        self.run_btn.setEnabled(n > 0)
        self.plot.setLabel("bottom", KNOBS[self.knob][0])

    def _sync(self):
        self.right.setGeometry(self.plot.getViewBox().sceneBoundingRect())
        self.right.linkedViewChanged(self.plot.getViewBox(), self.right.XAxis)

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ---------------- run
    def run(self):
        if self.is_running():
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
        self._kva = case.trafo_kva
        self.run_btn.setEnabled(False); self.cancel_btn.setVisible(True)
        self.progress.setVisible(True); self.progress.setRange(0, len(vals)); self.progress.setValue(0)
        self._worker = Worker(sweep, case, self.weather, self.knob, vals, fast=self.fast.isChecked(), with_progress=True)
        self._worker.progress.connect(self._progress)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _progress(self, done, total, label):
        self.progress.setRange(0, total); self.progress.setValue(done)
        self.status.setText(f"Running {done + 1} of {total}: {label} …")

    def _finish_ui(self):
        self.run_btn.setEnabled(True); self.cancel_btn.setVisible(False); self.progress.setVisible(False); self._count()

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
        self.set_table(table)

    # ---------------- show
    def set_table(self, table: pd.DataFrame):
        """Draw a sweep result (used by the worker and by tests)."""
        try:
            self.table = table
            scale = KNOBS[self.knob][1]
            x = np.asarray(table.index, dtype=float) / (scale if scale != 1.0 else 1.0)
            self.h80.setData(x, table["hours_above_80"].to_numpy(dtype=float))
            self.h100.setData(x, table["hours_above_100"].to_numpy(dtype=float))
            life = np.maximum(table["life_days_per_year"].to_numpy(dtype=float), 1e-3)
            self.life.setData(x, np.log10(life)); self.life_pts.setData(x, np.log10(life))
            self.plot.setXRange(float(x.min()) - 1, float(x.max()) + 1)
            self.plot.setYRange(0, max(10.0, float(table[["hours_above_80", "hours_above_100"]].to_numpy().max()) * 1.15))
            self.right.setYRange(np.log10(1e-3), np.log10(max(10.0, float(life.max()) * 2)))
            self._sync()
            # verdict strip along the bottom
            for item in self._strip:
                self.plot.removeItem(item)
            self._strip = []
            for xi, v in zip(x, table["verdict"]):
                s = pg.ScatterPlotItem([xi], [0], size=14, symbol="s", brush=VERDICT_COLORS.get(v, "#999"), pen=pg.mkPen(None))
                s.setToolTip(str(v)); self.plot.addItem(s); self._strip.append(s)
            at_risk = table.index[table["verdict"] == "At risk"]
            if len(at_risk):
                self.flip_line.setPos(float(at_risk[0]) / (scale if scale != 1.0 else 1.0)); self.flip_line.setVisible(True)
            else:
                self.flip_line.setVisible(False)
            self.caption.setText(self._caption(table))
            shown = table[[c for c in SHOW if c in table.columns]].copy()
            shown.insert(0, KNOBS[self.knob][0], x)
            self.model.set_df(shown.reset_index(drop=True))
            self.view.resizeColumnsToContents()
            base = f" on the {self._kva} kVA station" if self._kva else ""
            self.status.setText(f"Done: {len(table)} runs{base}. Squares along the bottom of the chart show the verdict per value.")
        except Exception as e:
            show_error(self, "Could not show the sweep", f"{type(e).__name__}: {e}")

    def _caption(self, t: pd.DataFrame) -> str:
        scale = KNOBS[self.knob][1]
        fmt = (lambda v: f"{v:g}") if scale == 1.0 else (lambda v: f"{v * 100:.0f} %")
        unit = KNOBS[self.knob][0]
        parts = []
        paper = t.index[t["hours_above_80"] > 0]
        parts.append(f"the 80 % rule first flags the station at {fmt(paper[0])} {unit}" if len(paper) else "the 80 % rule never flags the station")
        over = t.index[t["life_days_per_year"] > 365]
        parts.append(f"the insulation ages faster than designed from {fmt(over[0])}" if len(over)
                     else f"the insulation never reaches its design rate (max {t['life_days_per_year'].max():.2f} days/yr)")
        risk = t.index[t["verdict"] == "At risk"]
        if len(risk):
            why = str(t.loc[risk[0], "why"]).split(" — ", 1)[-1].split(" (also")[0]
            parts.append(f"the verdict turns At risk at {fmt(risk[0])} because of {why}")
        else:
            parts.append("the verdict never turns At risk in this range")
        return "; ".join(parts) + "."
