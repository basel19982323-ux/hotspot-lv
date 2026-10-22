"""The Spread tab: the same case run with different random draws, so a planner sees how much the
numbers — and the verdict — depend on who happened to get a heat pump, an EV or PV.

    ┌ controls ────────────┐ ┌ base case: 60 houses / 500 kVA · … (banner if the inputs differ) ┐
    │ draws 10, first 0    │ │ Same verdict in all 10 draws: OK   /  the verdict changed: OK 6 … │
    │ [Run 10 draws]       │ │ chart [peak load ▾]     ● one dot per draw, band = middle 80 %,   │
    │ ▓▓░ 2/10 · 0:40      │ │                         line = median, dashed = the rule limits   │
    └──────────────────────┘ │ ■ ■ ■ ■ ■ ■ ■ ■ ■ ■   verdict squares per draw                   │
                             │ limits line: "dashed: 80 % paper rule · 100 % of nameplate"       │
                             ├ spread table: quantity · mean · min · 10 % · 50 % · 90 % · max ──┤
                             └ every draw ────────────────────────────────────────────────────────┘
    caption: "10 draws (seeds 0–9): same verdict in all: OK; peak load 60–78 % (median 70) …"

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

from hotspotlv.app.theme import INK, LOAD_COLOR, RULE_80, RULE_100, VERDICT_COLORS
from hotspotlv.app.widgets.describe import describe_case
from hotspotlv.app.widgets.station_panel import show_error
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.workers import Worker
from hotspotlv.study import Case, monte_carlo, summarise_monte_carlo
from hotspotlv.thermal import IEC_LIMITS_C, IEC_LIMITS_K
from hotspotlv.timeseries import NAMEPLATE_RULE_PCT
from hotspotlv.verdict import DESIGN_LIFE_DAYS_PER_YEAR, LEVELS, WATCH_LIFE_DAYS_PER_YEAR

# metric → (label, unit for the caption, [(limit value, limit label, colour)]) — limits are the verdict module's numbers
METRICS = {
    "peak_loading_pct": ("peak load (% of nameplate)", " %",
                         [(NAMEPLATE_RULE_PCT, f"{NAMEPLATE_RULE_PCT:g} % paper rule", RULE_80), (100.0, "100 % of nameplate", RULE_100)]),
    "hours_above_80": (f"hours above {NAMEPLATE_RULE_PCT:g} % of nameplate", " h", []),
    "hours_above_100": ("hours above 100 % of nameplate", " h", []),
    "life_days_per_year": ("life used (days/yr)", " days/yr",
                           [(WATCH_LIFE_DAYS_PER_YEAR, "Watch: half the design rate", VERDICT_COLORS["Watch"]),
                            (DESIGN_LIFE_DAYS_PER_YEAR, "At risk: IEC design rate", VERDICT_COLORS["At risk"])]),
    "max_hot_spot_c": ("max hot-spot (°C)", " °C",
                       [(IEC_LIMITS_C["normal_cyclic_hot_spot"], "Watch: IEC normal cyclic", VERDICT_COLORS["Watch"]),
                        (IEC_LIMITS_C["long_time_emergency_hot_spot"], "At risk: IEC long-time emergency", VERDICT_COLORS["At risk"])]),
    "hours_hot_spot_above_98": ("hours hot-spot above 98 °C", " h", []),
    "max_load_pu": ("max load (× rated)", " × rated",
                    [(IEC_LIMITS_K["normal_cyclic"], "Watch: IEC normal cyclic", VERDICT_COLORS["Watch"]),
                     (IEC_LIMITS_K["long_time_emergency"], "At risk: IEC long-time emergency", VERDICT_COLORS["At risk"])]),
    "voltage_bad_hours": ("hours with voltage outside ±10 %", " h", [(0.0, "At risk: none allowed (EIFS 2023:3)", VERDICT_COLORS["At risk"])]),
    "voltage_plan_hours": ("hours outside the planning band", " h", [(0.0, "Watch: none allowed (planning band 0.92–1.05 pu)", VERDICT_COLORS["Watch"])]),
    "max_cable_pct": ("max main-cable loading (%)", " %", [(100.0, "Watch: cable rating", VERDICT_COLORS["Watch"])]),
}
HOUR_KEYS = ("hours_above_80", "hours_above_100", "hours_hot_spot_above_98", "voltage_bad_hours", "voltage_plan_hours")
AXIS_LABELS = {"peak_loading_pct": "peak load (%)", "hours_above_80": f"h above {NAMEPLATE_RULE_PCT:g} %", "hours_above_100": "h above 100 %",
               "life_days_per_year": "life used (days/yr)", "max_hot_spot_c": "max hot-spot (°C)", "hours_hot_spot_above_98": "h hot-spot > 98 °C",
               "max_load_pu": "max load (× rated)", "voltage_bad_hours": "h outside ±10 %", "voltage_plan_hours": "h outside plan band",
               "max_cable_pct": "max cable (%)"}
SUMMARY_COLUMNS = ["mean", "min", "10%", "50%", "90%", "max"]
SUMMARY_TIPS = {"mean": "Average of the draws.", "min": "Smallest draw.", "max": "Largest draw.",
                "10%": "10th percentile: one draw in ten falls below this (linear interpolation between sorted draws).",
                "50%": "Median: half the draws are below, half above.",
                "90%": "90th percentile: one draw in ten lies above this.",
                "quantity": "One row per number the engine reports for each draw; the chart above shows one of them."}
DRAW_COLUMNS = ["verdict", "peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year", "max_hot_spot_c",
                "hours_hot_spot_above_98", "max_load_pu", "voltage_bad_hours", "voltage_plan_hours", "max_cable_pct", "exact_hours", "why"]
MIN_DRAWS, BAND_FROM = 5, 10          # below BAND_FROM draws the band is the full range, not percentiles
BAND_COLOR = (31, 78, 121, 40)        # LOAD_COLOR at 16 % alpha
WHAT_VARIES = ("What the draw changes: which houses get a heat pump, an EV or PV; their base-load profile and annual kWh; "
               "heat-pump sizes; PV kWp; charger sizes; charging days, energies and arrival times. What stays fixed: the inputs "
               "on the left, the weather year (one winter), the IEC constants, the cable and the rules.")


def tally(verdicts: pd.Series) -> dict:
    return {lvl: int((verdicts == lvl).sum()) for lvl in LEVELS}


def fmt_value(key: str, v: float) -> str:
    if key == "life_days_per_year":
        return f"{v:.3f}" if v < 1 else f"{v:.2f}"
    if key in HOUR_KEYS:
        return f"{v:,.0f}"
    if key == "max_load_pu":
        return f"{v:.2f}"
    return f"{v:.1f}"


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

        g = QGroupBox("Same case, different draws")
        g.setToolTip("Runs the inputs on the left of the window once per random draw (seed). Nothing else changes.")
        f = QFormLayout(g); f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.n_draws = QSpinBox(); self.n_draws.setRange(MIN_DRAWS, 40); self.n_draws.setValue(10); self.n_draws.setSuffix(" draws")
        self.n_draws.setToolTip(f"How many random draws to run (at least {MIN_DRAWS}); each is a full case: seconds in fast mode, "
                                "up to ~2 min per draw when a heavy street falls back to the full power flow. "
                                f"Below {BAND_FROM} draws the chart shows the full range instead of the 10–90 % band.")
        self.first = QSpinBox(); self.first.setRange(0, 9000); self.first.setValue(0)
        self.first.setToolTip("Draw (seed) of the first run; the draws are first, first + 1, … The 'Random draw' box on the left "
                              "is ignored here. Use the same first draw to repeat a run exactly.")
        self.fast = QCheckBox("fast mode"); self.fast.setChecked(True)
        self.fast.setToolTip("Fits a power-flow proxy on ~300 real power flows and computes every hour near a limit exactly. "
                             "Heavy streets fall back to the full power flow on their own (up to ~2 min per draw).")
        self.metric_box = QComboBox(); [self.metric_box.addItem(label, key) for key, (label, _, _) in METRICS.items()]
        self.metric_box.setToolTip("Which number the chart shows; the tables below have all of them.")
        self.metric_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); self.metric_box.setMinimumContentsLength(14)
        self.metric_box.currentIndexChanged.connect(self._redraw)
        f.addRow("Draws", self.n_draws)
        f.addRow("First draw (seed)", self.first)
        f.addRow("", self.fast)
        f.addRow("Chart", self.metric_box)
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

        results = QWidget(); rv = QVBoxLayout(results); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(3)
        self.banner = QLabel(""); self.banner.setWordWrap(True); self.banner.setVisible(False)
        self.banner.setStyleSheet(f"color: {INK}; font-weight: 600; background: #fff4d6; padding: 4px; border-radius: 4px;")
        self.base_label = QLabel(""); self.base_label.setProperty("muted", True); self.base_label.setWordWrap(True)
        self.tally_label = QLabel(""); self.tally_label.setWordWrap(True); self.tally_label.setTextFormat(Qt.RichText)
        self.tally_label.setStyleSheet("font-weight: 600;")
        self.tally_label.setToolTip("How many of the draws ended in each verdict. Agreement across a handful of draws is "
                                    "evidence, not proof: a station near a limit can flip on the next draw.")
        rv.addWidget(self.banner); rv.addWidget(self.base_label); rv.addWidget(self.tally_label)
        split = QSplitter(Qt.Vertical)
        top = QWidget(); tv = QVBoxLayout(top); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(2)
        self.plot = pg.PlotWidget(); self.plot.showGrid(x=True, y=True, alpha=0.2); self.plot.setMinimumHeight(110)
        self.plot.setLabel("bottom", "random draw (seed) — a label, not a quantity")
        self.plot.setToolTip("One dot per draw. Shaded band = the middle 80 % of the draws (or the full range below "
                             f"{BAND_FROM} draws), solid line = median, dashed = the rule limits. Squares below the axis = "
                             "that draw's verdict (green OK, amber Watch, red At risk).")
        self.band = pg.LinearRegionItem(orientation="horizontal", movable=False, brush=pg.mkBrush(*BAND_COLOR), pen=pg.mkPen(None))
        self.band.setZValue(-10); self.plot.addItem(self.band)
        self.median_line = pg.InfiniteLine(angle=0, pen=pg.mkPen(LOAD_COLOR, width=2), label="median",
                                           labelOpts={"position": 0.5, "color": LOAD_COLOR, "anchors": [(0.5, 1), (0.5, 1)]})
        self.plot.addItem(self.median_line)
        self.dots = pg.ScatterPlotItem(size=9, brush=LOAD_COLOR, pen=pg.mkPen(None)); self.plot.addItem(self.dots)
        self._limit_lines = []; self._strip = []
        tv.addWidget(self.plot, 1)
        self.limits_note = QLabel(""); self.limits_note.setProperty("muted", True); self.limits_note.setWordWrap(True)
        self.legend = QLabel(""); self.legend.setProperty("muted", True); self.legend.setWordWrap(True)
        self.legend.setToolTip(WHAT_VARIES)
        self.caption = QLabel(""); self.caption.setProperty("muted", True); self.caption.setWordWrap(True)
        strip = QWidget(); sl = QVBoxLayout(strip); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(1)
        sl.addWidget(self.limits_note); sl.addWidget(self.legend); sl.addWidget(self.caption)
        notes = QScrollArea(); notes.setWidget(strip); notes.setWidgetResizable(True); notes.setFrameShape(QScrollArea.NoFrame)
        notes.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); notes.setMaximumHeight(112); notes.setMinimumHeight(48)
        notes.setToolTip(WHAT_VARIES)
        tv.addWidget(notes)
        split.addWidget(top)
        tabs = QTabWidget()
        self.summary_view = QTableView(); self.summary_model = DataFrameModel(decimals=1, header_tips=SUMMARY_TIPS)
        self.summary_view.setModel(self.summary_model)
        self.summary_view.setAlternatingRowColors(True); self.summary_view.setMinimumHeight(72)
        self.draws_view = QTableView(); self.draws_model = DataFrameModel(decimals=1); self.draws_view.setModel(self.draws_model)
        self.draws_view.setAlternatingRowColors(True); self.draws_view.setMinimumHeight(72)
        tabs.addTab(self.summary_view, "Spread of every number"); tabs.addTab(self.draws_view, "Every draw")
        split.addWidget(tabs)
        split.setSizes([320, 200])
        rv.addWidget(split, 1)
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
        """`case` = the current inputs; stale only while they differ (seed aside) from the draws' own base case."""
        if self.table is None or self.is_running() or self._blocked:
            return
        if case is not None and self._shown_case is not None and same_but_seed(case, self._shown_case):
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
        self._t0 = time.perf_counter(); self._timer.start(1000); self._progress_text = "Starting …"; self._tick()
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
    @staticmethod
    def _seeds_text(table: pd.DataFrame) -> str:
        idx = [int(i) for i in table.index]
        if idx == list(range(idx[0], idx[0] + len(idx))):
            return f"seeds {idx[0]}–{idx[-1]}"
        return "seeds " + ", ".join(str(i) for i in idx)

    def set_table(self, table: pd.DataFrame, case: Case | None = None):
        """Show a Monte Carlo result (used by the worker and by tests)."""
        try:
            self.table = table
            self.summary = summarise_monte_carlo(table)
            self._shown_case = case
            n = len(table)
            t = tally(table["verdict"])
            agreed = max(t.values()) == n
            parts = " · ".join(f"<span style='color:{VERDICT_COLORS[lvl]}'>{lvl} {t[lvl]}</span>" for lvl in LEVELS if t[lvl])
            self.tally_label.setText(f"Same verdict in all {n} draws: {parts}" if agreed else
                                     f"The verdict changed between draws — {parts} of {n}: look at the band, not at one run")
            # the spread table, formatted per row (hours whole, life to 3 decimals …), all the engine's numbers
            rows = {}
            for key in self.summary.index:
                label = METRICS.get(key, (key, "", []))[0]
                rows[label] = {c: fmt_value(key, float(self.summary.loc[key, c])) for c in SUMMARY_COLUMNS}
            summary = pd.DataFrame(rows).T[SUMMARY_COLUMNS]
            summary.index.name = "quantity"
            self.summary_model.set_df(summary)
            self.summary_view.resizeColumnsToContents()
            draws = table[[c for c in DRAW_COLUMNS if c in table.columns]].copy()
            draws.index.name = "seed"
            self.draws_model.set_df(draws)
            self.draws_view.resizeColumnsToContents()
            base = describe_case(case, seed=False) if case is not None else "the current inputs"
            self.base_label.setText(f"Base case: {base} · {self._seeds_text(table)}")
            band = "middle 80 % of the draws (10th–90th percentile)" if n >= BAND_FROM else f"full range of these {n} draws"
            self.legend.setText(f"Band = {band}; line = median; dashed = the rule limits (listed under the chart); "
                                f"squares = verdict per draw. Only the draw changes — hover for what that means.")
            self.caption.setText(self._caption(table, t))
            self._redraw()
            self._done_status = f"Done: {n} draws ({self._seeds_text(table)}). The tables have every number the engine reports per draw."
            self.status.setText(self._done_status)
        except Exception as e:
            show_error(self, "Could not show the draws", f"{type(e).__name__}: {e}")

    def _redraw(self):
        if self.table is None:
            return
        key = self.metric_box.currentData()
        label, _, limits = METRICS[key]
        n = len(self.table)
        x = np.asarray(self.table.index, dtype=float)
        y = self.table[key].to_numpy(dtype=float)
        self.dots.setData(x, y)
        if n >= BAND_FROM:
            lo, hi = (float(np.nanpercentile(y, p)) for p in (10, 90))
        else:
            lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
        med = float(np.nanmedian(y))
        self.band.setRegion((lo, hi)); self.median_line.setPos(med)
        for item in self._limit_lines + self._strip:
            self.plot.removeItem(item)
        self._limit_lines, self._strip = [], []
        # the range follows the SPREAD of the draws (a 0.05–0.10 days/yr spread fills the chart); a limit inside
        # the range is drawn, one above it is named under the chart instead of squashing the draws onto one pixel row
        y_lo, y_hi = self._data_range(key, y)
        on, off = [], []
        for value, text, color in limits:
            if y_lo <= value <= y_hi:
                line = pg.InfiniteLine(angle=0, pos=value, pen=pg.mkPen(color, style=Qt.DashLine))
                self.plot.addItem(line); self._limit_lines.append(line); on.append(f"{text} at {value:g}")
            else:
                off.append(f"{text} at {value:g}")
        note = []
        if on:
            note.append("dashed: " + " · ".join(on))
        if off:
            far = all(v > 3 * max(float(np.nanmax(y)), 1e-9) for v, _, _ in limits if not (y_lo <= v <= y_hi))
            note.append(("off the chart, far above the draws: " if far else "above the chart: ") + " · ".join(off))
        if not limits:
            note.append("no rule limit on this number")
        self.limits_note.setText("; ".join(note))
        span = y_hi - y_lo
        strip_y = y_lo + 0.08 * span                       # a strip of its own, below every dot
        for xi, v in zip(x, self.table["verdict"]):
            s = pg.ScatterPlotItem([xi], [strip_y], size=14, symbol="s", brush=VERDICT_COLORS.get(v, "#999"), pen=pg.mkPen(None))
            s.setToolTip(str(v)); self.plot.addItem(s); self._strip.append(s)
        self.plot.setLabel("left", AXIS_LABELS.get(key, label))
        self._ticks()
        self.plot.setXRange(float(x.min()) - 0.5, float(x.max()) + 0.5)
        self.plot.setYRange(y_lo, y_hi)

    @staticmethod
    def _data_range(key: str, y: np.ndarray) -> tuple[float, float]:
        """Bottom and top of the chart: the draws' range plus margins (a strip for the squares below, room above)."""
        lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
        span = hi - lo
        if span <= 0:                                      # every draw the same: show it with a sensible scale
            span = 2.0 if key in HOUR_KEYS else max(abs(hi) * 0.1, 0.01)
        if key in HOUR_KEYS:
            span = max(span, 2.0)                          # whole-hour ticks, never 0 / 0.5 / 1.0 h
        return lo - 0.35 * span, hi + 0.25 * span

    def _ticks(self):
        """One label per draw while they fit, else every k-th draw (40 draws on a laptop would smear)."""
        if self.table is None:
            return
        x = [float(s) for s in self.table.index]
        px = max(self.plot.getViewBox().width(), 1.0)
        per_label = 30.0 * (x[-1] - x[0] + 1) / px if len(x) > 1 else 0.0
        k = max(1, int(np.ceil(per_label / 1.0))) if per_label > 1.0 else 1
        major = [(xi, str(int(xi))) for i, xi in enumerate(x) if i % k == 0]
        minor = [(xi, "") for i, xi in enumerate(x) if i % k != 0]
        self.plot.getAxis("bottom").setTicks([major, minor])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._ticks()

    def _caption(self, table: pd.DataFrame, t: dict) -> str:
        n = len(table)
        tally_text = " / ".join(f"{lvl} {t[lvl]}" for lvl in LEVELS if t[lvl])
        agreed = max(t.values()) == n
        parts = [f"{n} draws ({self._seeds_text(table)}): " + (f"same verdict in all: {tally_text}" if agreed
                                                                else f"the verdict changed between draws: {tally_text}")]
        names = {"peak_loading_pct": "peak load", "hours_above_80": f"hours above {NAMEPLATE_RULE_PCT:g} %",
                 "life_days_per_year": "life used", "max_hot_spot_c": "max hot-spot",
                 "voltage_bad_hours": "hours with voltage outside ±10 %"}
        for key, name in names.items():
            y = table[key].to_numpy(dtype=float)
            unit = METRICS[key][1]
            raw_lo, raw_hi = float(np.nanmin(y)), float(np.nanmax(y))
            lo, hi, med = fmt_value(key, raw_lo), fmt_value(key, raw_hi), fmt_value(key, float(np.nanmedian(y)))
            if raw_lo == raw_hi:
                parts.append(f"{name} {lo}{unit} in every draw")
            elif lo == hi:
                parts.append(f"{name} ≈ {lo}{unit} in all draws (spread {raw_hi - raw_lo:.2g}{unit})")
            else:
                parts.append(f"{name} {lo}–{hi}{unit} (median {med})")
        return "; ".join(parts) + "."


def same_but_seed(a: Case, b: Case) -> bool:
    """The two cases are the same inputs apart from the random draw (which this tab replaces)."""
    from dataclasses import replace
    strip = lambda c: replace(c, scenario=replace(c.scenario, seed=0))
    return strip(a) == strip(b)
