"""The Spread tab: the same case run with different random draws, so a planner sees how much the
numbers — and the verdict — depend on who happened to get a heat pump, an EV or PV.

    ┌ controls ────────────┐ ┌ base case: 60 houses / 500 kVA · … (banner if the inputs differ) ┐
    │ draws 10, first 0    │ │ Verdict over 10 draws: OK 6 · Watch 3 · At risk 1                 │
    │ [Run 10 draws]       │ │ metric [peak load ▾]      ● one dot per draw, band = 10–90 %,     │
    │ ▓▓░ 2/10 · 0:40      │ │                           line = median, dashed = the rule limits │
    └──────────────────────┘ │ ■ ■ ■ ■ ■ ■ ■ ■ ■ ■   verdict squares per draw                   │
                             ├ spread table: metric · mean · min · 10 % · 50 % · 90 % · max ─────┤
                             └ every draw ────────────────────────────────────────────────────────┘
    caption: "10 draws (seeds 0–9): verdict OK 6 / Watch 3 / At risk 1 — not robust; peak load 60–78 %
              (median 70) …"

The engine runs the draws (study.monte_carlo) and the statistics (study.summarise_monte_carlo);
this file only draws them. Thresholds on the chart are the verdict module's own constants.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QTableView, QTabWidget, QVBoxLayout, QWidget)

from hotspotlv.app.theme import HOTSPOT_COLOR, INK, LOAD_COLOR, RULE_80, RULE_100, VERDICT_COLORS
from hotspotlv.app.widgets.describe import describe_case
from hotspotlv.app.widgets.station_panel import show_error
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.workers import Worker
from hotspotlv.study import Case, monte_carlo, summarise_monte_carlo
from hotspotlv.thermal import IEC_LIMITS_C
from hotspotlv.timeseries import NAMEPLATE_RULE_PCT
from hotspotlv.verdict import DESIGN_LIFE_DAYS_PER_YEAR, LEVELS, WATCH_LIFE_DAYS_PER_YEAR

# metric → (label, [(limit value, limit label, colour)]) — limits are the verdict module's own numbers
METRICS = {
    "peak_loading_pct": ("peak load (% of nameplate)", [(NAMEPLATE_RULE_PCT, f"{NAMEPLATE_RULE_PCT:g} % paper rule", RULE_80),
                                                        (100.0, "100 % of nameplate", RULE_100)]),
    "hours_above_80": (f"hours above {NAMEPLATE_RULE_PCT:g} % of nameplate", []),
    "hours_above_100": ("hours above 100 % of nameplate", []),
    "life_days_per_year": ("life used (days/yr)", [(WATCH_LIFE_DAYS_PER_YEAR, "Watch: half the design rate", VERDICT_COLORS["Watch"]),
                                                   (DESIGN_LIFE_DAYS_PER_YEAR, "At risk: IEC design rate", VERDICT_COLORS["At risk"])]),
    "max_hot_spot_c": ("max hot-spot (°C)", [(IEC_LIMITS_C["normal_cyclic_hot_spot"], "Watch: IEC normal cyclic", VERDICT_COLORS["Watch"]),
                                             (IEC_LIMITS_C["long_time_emergency_hot_spot"], "At risk: IEC long-time emergency", VERDICT_COLORS["At risk"])]),
    "voltage_bad_hours": ("hours with voltage outside ±10 %", [(0.0, "At risk: none allowed (EIFS 2023:3)", VERDICT_COLORS["At risk"])]),
    "voltage_plan_hours": ("hours outside the planning band", [(0.0, "Watch: none allowed (planning band 0.92–1.05)", VERDICT_COLORS["Watch"])]),
    "max_cable_pct": ("max main-cable loading (%)", [(100.0, "Watch: cable rating", VERDICT_COLORS["Watch"])]),
}
SUMMARY_COLUMNS = ["mean", "min", "10%", "50%", "90%", "max"]
DRAW_COLUMNS = ["verdict", "peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year", "max_hot_spot_c",
                "voltage_bad_hours", "voltage_plan_hours", "max_cable_pct", "exact_hours", "why"]
BAND_COLOR = (31, 78, 121, 40)      # LOAD_COLOR at 16 % alpha
LEGEND = ("One dot per random draw (x = seed). Shaded band = the 10–90 % range of the draws, solid line = the median, "
          "dashed lines = the rule limits. Squares below the axis = the verdict of that draw: green OK · amber Watch · red At risk. "
          "Same inputs and weather every time — only the draw differs: which houses get a heat pump, an EV or PV, "
          "the charger sizes and when the cars charge.")


def tally(verdicts: pd.Series) -> dict:
    return {lvl: int((verdicts == lvl).sum()) for lvl in LEVELS}


class MonteCarloView(QWidget):
    """`case_provider()` returns the base Case (the scenario panel's current inputs)."""

    run_started = Signal()
    run_finished = Signal()

    def __init__(self, case_provider, weather: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._case_provider = case_provider
        self.weather = weather
        self.table: pd.DataFrame | None = None
        self.summary: pd.DataFrame | None = None
        self._worker: Worker | None = None
        self._run_case: Case | None = None; self._shown_case: Case | None = None
        self._t0 = 0.0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self._progress_text = ""
        self._blocked: str | None = None; self._saved_status = ""; self._done_status = ""
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("Same case, different dice")
        g.setToolTip("Runs the inputs on the left of the window once per random draw (seed). Nothing else changes.")
        f = QFormLayout(g); f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.n_draws = QSpinBox(); self.n_draws.setRange(2, 40); self.n_draws.setValue(10); self.n_draws.setSuffix(" draws")
        self.n_draws.setToolTip("How many random draws to run; each is a full case (seconds in fast mode, up to ~2 min "
                                "per draw when a heavy street falls back to the full power flow).")
        self.first = QSpinBox(); self.first.setRange(0, 9000); self.first.setValue(0)
        self.first.setToolTip("Seed of the first draw; the draws are first, first + 1, … Use the same first seed to repeat a run exactly.")
        self.fast = QCheckBox("fast mode"); self.fast.setChecked(True)
        self.fast.setToolTip("Fits a power-flow proxy on ~300 real power flows and computes every hour near a limit exactly. "
                             "Heavy streets fall back to the full power flow on their own (up to ~2 min per draw).")
        f.addRow("Draws", self.n_draws)
        f.addRow("First seed", self.first)
        f.addRow("", self.fast)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run 10 draws"); self.run_btn.clicked.connect(self.run)
        self.run_btn.setToolTip("Runs one full case per draw on the inputs on the left of the window (not on the case shown in the card).")
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setVisible(False)
        row.addWidget(self.run_btn, 1); row.addWidget(self.cancel_btn)
        f.addRow(row)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        f.addRow(self.progress)
        self.status = QLabel("Each draw is a full case on the current inputs: seconds in fast mode while the proxy holds, "
                             "up to ~2 min per draw when a heavy street falls back to the full power flow.")
        self.status.setProperty("muted", True); self.status.setWordWrap(True)
        f.addRow(self.status)
        self.n_draws.valueChanged.connect(self._count)
        self._controls = [self.n_draws, self.first, self.fast]
        column = QWidget(); cv = QVBoxLayout(column); cv.setContentsMargins(0, 0, 6, 0); cv.addWidget(g); cv.addStretch(1)
        scroll = QScrollArea(); scroll.setWidget(column); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        column.setMinimumWidth(0); scroll.setMinimumWidth(240)

        results = QWidget(); rv = QVBoxLayout(results); rv.setContentsMargins(0, 0, 0, 0)
        self.banner = QLabel(""); self.banner.setWordWrap(True); self.banner.setVisible(False)
        self.banner.setStyleSheet(f"color: {INK}; font-weight: 600; background: #fff4d6; padding: 4px; border-radius: 4px;")
        self.base_label = QLabel(""); self.base_label.setProperty("muted", True); self.base_label.setWordWrap(True)
        self.tally_label = QLabel(""); self.tally_label.setWordWrap(True); self.tally_label.setTextFormat(Qt.RichText)
        self.tally_label.setStyleSheet("font-weight: 600;")
        rv.addWidget(self.banner); rv.addWidget(self.base_label); rv.addWidget(self.tally_label)
        split = QSplitter(Qt.Vertical)
        top = QWidget(); tv = QVBoxLayout(top); tv.setContentsMargins(0, 0, 0, 0)
        pick = QHBoxLayout()
        self.metric_box = QComboBox(); [self.metric_box.addItem(label, key) for key, (label, _) in METRICS.items()]
        self.metric_box.setToolTip("Which number the chart shows; the tables below have all of them.")
        self.metric_box.currentIndexChanged.connect(self._redraw)
        pick.addWidget(QLabel("Chart")); pick.addWidget(self.metric_box, 1)
        tv.addLayout(pick)
        self.plot = pg.PlotWidget(); self.plot.showGrid(x=True, y=True, alpha=0.2); self.plot.setMinimumHeight(130)
        self.plot.setLabel("bottom", "random draw (seed)")
        self.band = pg.LinearRegionItem(orientation="horizontal", movable=False, brush=pg.mkBrush(*BAND_COLOR), pen=pg.mkPen(None))
        self.band.setZValue(-10); self.plot.addItem(self.band)
        self.median_line = pg.InfiniteLine(angle=0, pen=pg.mkPen(LOAD_COLOR, width=2), label="median",
                                           labelOpts={"position": 0.5, "color": LOAD_COLOR, "anchors": [(0.5, 1), (0.5, 1)]})
        self.plot.addItem(self.median_line)
        self.dots = pg.ScatterPlotItem(size=9, brush=LOAD_COLOR, pen=pg.mkPen(None)); self.plot.addItem(self.dots)
        self._limit_lines = []; self._strip = []
        tv.addWidget(self.plot, 1)
        split.addWidget(top)
        self.legend = QLabel(LEGEND); self.legend.setProperty("muted", True); self.legend.setWordWrap(True)
        tabs = QTabWidget()
        self.summary_view = QTableView(); self.summary_model = DataFrameModel(decimals=1); self.summary_view.setModel(self.summary_model)
        self.summary_view.setAlternatingRowColors(True); self.summary_view.setMinimumHeight(84)
        self.draws_view = QTableView(); self.draws_model = DataFrameModel(decimals=1); self.draws_view.setModel(self.draws_model)
        self.draws_view.setAlternatingRowColors(True); self.draws_view.setMinimumHeight(84)
        tabs.addTab(self.summary_view, "Spread of every number"); tabs.addTab(self.draws_view, "Every draw")
        lower = QWidget(); lv = QVBoxLayout(lower); lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.legend); lv.addWidget(tabs, 1)
        split.addWidget(lower)
        split.setSizes([300, 220])
        self.caption = QLabel(""); self.caption.setProperty("muted", True); self.caption.setWordWrap(True)
        rv.addWidget(split, 1); rv.addWidget(self.caption)
        outer = QSplitter(Qt.Horizontal)
        outer.addWidget(scroll); outer.addWidget(results)
        outer.setStretchFactor(0, 0); outer.setStretchFactor(1, 1); outer.setSizes([290, 900])
        lay.addWidget(outer)
        self._count()

    # ---------------- controls
    def seeds(self) -> list[int]:
        return list(range(self.first.value(), self.first.value() + self.n_draws.value()))

    def _count(self):
        self.run_btn.setText(f"Run {self.n_draws.value()} draws")

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ---------------- the scenario panel tells the tab about the inputs
    def set_banner(self, text: str | None):
        self.banner.setText(text or ""); self.banner.setVisible(bool(text))

    def inputs_changed(self, case=None):
        if self.table is None or self.is_running() or self._blocked:
            return
        if case is not None and self._shown_case is not None and case == self._shown_case:
            self.status.setText(self._done_status)
        else:
            self.status.setText("Inputs changed since these draws — press Run to update.")

    def block(self, reason: str | None):
        if self.is_running():
            return
        if reason and self._blocked is None:
            self._saved_status = self.status.text()
        if reason is None and self._blocked is not None:
            self.status.setText(self._saved_status)
        self._blocked = reason
        self.run_btn.setEnabled(reason is None)
        if reason:
            self.status.setText(reason)

    # ---------------- run
    def run(self):
        if self.is_running() or self._blocked:
            return
        try:
            case = self._case_provider()
        except Exception as e:
            show_error(self, "Inputs", f"{type(e).__name__}: {e}"); return
        if case is None:
            return
        seeds = self.seeds()
        self._run_case = case
        for w in self._controls:
            w.setEnabled(False)
        self.run_btn.setEnabled(False); self.cancel_btn.setVisible(True)
        self.progress.setVisible(True); self.progress.setRange(0, len(seeds)); self.progress.setValue(0)
        self._t0 = time.perf_counter(); self._timer.start(1000); self._progress_text = "Starting …"
        self._worker = Worker(monte_carlo, case, self.weather, seeds=seeds, fast=self.fast.isChecked(), with_progress=True)
        self._worker.progress.connect(self._progress)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()
        self.run_started.emit()

    def _progress(self, done, total, label):
        if not self._current():
            return
        self.progress.setRange(0, total); self.progress.setValue(done)
        self._progress_text = f"Running {done + 1} of {total}: {label}"
        self._tick()

    def _tick(self):
        t = time.perf_counter() - self._t0
        self.status.setText(f"{self._progress_text} … {int(t // 60)}:{int(t % 60):02d} elapsed")

    def _finish_ui(self):
        self._timer.stop()
        for w in self._controls:
            w.setEnabled(True)
        self.run_btn.setEnabled(True); self.cancel_btn.setVisible(False); self.progress.setVisible(False)
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
        show_error(self, "Random draws failed", msg)

    def _done(self, table: pd.DataFrame):
        if not self._current():
            return
        self._finish_ui()
        self.set_table(table, self._run_case)

    # ---------------- show
    def set_table(self, table: pd.DataFrame, case: Case | None = None):
        """Show a Monte Carlo result (used by the worker and by tests)."""
        try:
            self.table = table
            self.summary = summarise_monte_carlo(table)
            self._shown_case = case
            t = tally(table["verdict"])
            n = len(table)
            robust = max(t.values()) == n
            parts = " · ".join(f"<span style='color:{VERDICT_COLORS[lvl]}'>{lvl} {t[lvl]}</span>" for lvl in LEVELS if t[lvl])
            self.tally_label.setText(f"Verdict over {n} draws: {parts}" + ("" if robust else
                                     " — the verdict depends on the draw: look at the band, not at one run"))
            summary = self.summary.copy()
            summary.index = [METRICS.get(k, (k, None))[0] for k in summary.index]
            summary.index.name = "number"
            self.summary_model.set_df(summary[SUMMARY_COLUMNS])
            self.summary_view.resizeColumnsToContents()
            draws = table[[c for c in DRAW_COLUMNS if c in table.columns]].copy()
            draws.index.name = "seed"
            self.draws_model.set_df(draws)
            self.draws_view.resizeColumnsToContents()
            self.base_label.setText("Base case: " + describe_case(case) if case is not None else "Base case: the current inputs")
            self.caption.setText(self._caption(table, t))
            self._redraw()
            self._done_status = f"Done: {n} draws (seeds {table.index.min()}–{table.index.max()}). The tables have every number."
            self.status.setText(self._done_status)
        except Exception as e:
            show_error(self, "Could not show the draws", f"{type(e).__name__}: {e}")

    def _redraw(self):
        if self.table is None:
            return
        key = self.metric_box.currentData()
        label, limits = METRICS[key]
        x = np.asarray(self.table.index, dtype=float)
        y = self.table[key].to_numpy(dtype=float)
        self.dots.setData(x, y)
        lo, med, hi = (float(np.nanpercentile(y, p)) for p in (10, 50, 90))
        self.band.setRegion((lo, hi)); self.median_line.setPos(med)
        for item in self._limit_lines + self._strip:
            self.plot.removeItem(item)
        self._limit_lines, self._strip = [], []
        ymax = max(float(np.nanmax(y)), max((v for v, _, _ in limits), default=0.0))
        top = max(ymax * 1.15, 1.0)
        for value, text, color in limits:
            line = pg.InfiniteLine(angle=0, pos=value, pen=pg.mkPen(color, style=Qt.DashLine), label=text,
                                   labelOpts={"position": 0.03, "color": color, "anchors": [(0, 1), (0, 1)]})
            self.plot.addItem(line); self._limit_lines.append(line)
        for xi, v in zip(x, self.table["verdict"]):
            s = pg.ScatterPlotItem([xi], [-0.10 * top], size=14, symbol="s", brush=VERDICT_COLORS.get(v, "#999"), pen=pg.mkPen(None))
            s.setToolTip(str(v)); self.plot.addItem(s); self._strip.append(s)
        self.plot.setLabel("left", label)
        self.plot.setXRange(float(x.min()) - 0.5, float(x.max()) + 0.5)
        self.plot.setYRange(-0.16 * top, top)

    @staticmethod
    def _fmt(key: str, v: float) -> str:
        if key == "life_days_per_year":
            return f"{v:.3f}" if v < 1 else f"{v:.2f}"
        if key in ("hours_above_80", "hours_above_100", "voltage_bad_hours", "voltage_plan_hours"):
            return f"{v:,.0f}"
        return f"{v:.1f}"

    def _caption(self, table: pd.DataFrame, t: dict) -> str:
        n = len(table)
        seeds = f"seeds {table.index.min()}–{table.index.max()}"
        tally_text = " / ".join(f"{lvl} {t[lvl]}" for lvl in LEVELS if t[lvl])
        robust = max(t.values()) == n
        parts = [f"{n} draws ({seeds}): verdict {tally_text}" + (" — robust to the draw" if robust else " — not robust to the draw")]
        for key in ("peak_loading_pct", "hours_above_80", "life_days_per_year", "max_hot_spot_c", "voltage_bad_hours"):
            y = table[key].to_numpy(dtype=float)
            lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
            med = float(np.nanmedian(y))
            unit = {"peak_loading_pct": " %", "max_hot_spot_c": " °C", "life_days_per_year": " days/yr"}.get(key, "")
            name = {"peak_loading_pct": "peak load", "hours_above_80": "hours above 80 %", "life_days_per_year": "life used",
                    "max_hot_spot_c": "max hot-spot", "voltage_bad_hours": "hours with voltage outside ±10 %"}[key]
            if lo == hi:
                parts.append(f"{name} {self._fmt(key, lo)}{unit} in every draw")
            else:
                parts.append(f"{name} {self._fmt(key, lo)}–{self._fmt(key, hi)}{unit} (median {self._fmt(key, med)})")
        return "; ".join(parts) + "."
