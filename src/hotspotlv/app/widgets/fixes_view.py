"""The Fixes tab: the four fixes on the same houses and dice, with the cost assumptions as live controls.

    ┌ controls ──────────────────┐ ┌ base case: 60 houses / 500 kVA · EV 60 % · … (banner if inputs differ) ┐
    │ battery cap 80 %           │ ┌ table: fix · verdict · why · capex low/high · service life · … · physics ┐
    │ bigger unit 800 kVA        │ ├─────────────────────────────────────────────────────────────────────────┤
    │ [Compare fixes (5 runs)]   │ │ bars per fix: hours above 80 % (on paper)  |  life used (really)       │
    │ ▓▓▓░░ 3/5 · 1:20 elapsed   │ └─────────────────────────────────────────────────────────────────────────┘
    ├ cost assumptions ──────────┤  bar colour = verdict · notes from the engine ("battery: even 1 000 kWh
    │ unit swap 209 kSEK …       │  cannot hold 400 kVA every hour …")
    └────────────────────────────┘

The engine does the comparison (study.compare_fixes) and the costing
(costs.cost_table); changing a cost control only re-runs the costing, which
is instant. No physics and no prices live in this file.
"""

from __future__ import annotations

import re
import time

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QTableView, QTextBrowser, QVBoxLayout, QWidget)

from hotspotlv.app.theme import HOTSPOT_COLOR, INK, LOAD_COLOR, VERDICT_COLORS
from hotspotlv.app.widgets.describe import describe_case
from hotspotlv.app.widgets.station_panel import show_error
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.workers import Worker
from hotspotlv.catalogue import TRANSFORMERS
from hotspotlv.costs import REINFORCEMENT_SEK_PER_KW_YR, REINFORCEMENT_YEARS, CostAssumptions, cost_table
from hotspotlv.study import FixComparison, compare_fixes, fixes_table

COST_COLUMNS = ["capex low (kSEK)", "capex high (kSEK)", "service life (yr)", "peak kVA avoided",
                "reinforcement-equivalent value (kSEK)"]
# what the planner came for first, the physics after
COLUMN_ORDER = ["verdict", "why"] + COST_COLUMNS + ["peak %", "h > 80 %", "h > 100 %", "life days/yr",
                                                     "max hot-spot °C", "voltage bad h", "cable %", "battery h above cap"]
HEADER_TIPS = {
    "capex low (kSEK)": "Low end of the indicative range from the controls on the left (not a quote).",
    "capex high (kSEK)": "High end of the indicative range from the controls on the left (not a quote).",
    "service life (yr)": "Assumed service life: transformer 40, battery 15, charging control 10 years. "
                         "Capex alone flatters short-lived assets.",
    "peak kVA avoided": "Baseline peak kVA minus this fix's peak kVA at the transformer. "
                        "'–' for the transformer swap: the swap IS the reinforcement, it avoids no peak.",
    "reinforcement-equivalent value (kSEK)": (f"peak kVA avoided × {REINFORCEMENT_SEK_PER_KW_YR} SEK/kW/yr × "
                                              f"{REINFORCEMENT_YEARS:.0f} yr (Energiforsk 2024:1043 §6.2), undiscounted — "
                                              "what the same peak reduction would be worth as deferred reinforcement."),
    "peak %": "Peak load as % of that row's OWN nameplate (the 800 kVA row is measured against 800 kVA).",
    "h > 80 %": "Hours per year above 80 % of that row's own nameplate (the paper rule).",
    "h > 100 %": "Hours per year above 100 % of that row's own nameplate.",
    "life days/yr": "Days of insulation life used per year (IEC 60076-7; 365 = the design rate).",
    "why": "The rule that decided the verdict, with its source.",
}
LEGEND = ("Bar colour = that fix's verdict (green OK · amber Watch · red At risk) from the thermal + limits view, "
          "in both charts. Bars carry the fix name, or the table's row number when the chart is narrow. "
          "The 800 kVA row's peak % and hours are relative to its own nameplate.")


def _short(name) -> str:
    """Bar label: the fix name without its clock window (the table carries the full name)."""
    return re.sub(r" \d\d–\d\d$", "", str(name))


def _range_spin(lo, hi, value, step, suffix) -> QSpinBox:
    sb = QSpinBox(); sb.setRange(lo, hi); sb.setSingleStep(step); sb.setValue(value); sb.setSuffix(suffix)
    return sb


class FixesView(QWidget):
    """`case_provider()` returns the Case to compare (the scenario panel's current inputs)."""

    run_started = Signal()
    run_finished = Signal()

    def __init__(self, case_provider, weather: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._case_provider = case_provider
        self.weather = weather
        self.comparison: FixComparison | None = None
        self._worker: Worker | None = None
        self._t0 = 0.0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self._progress_text = ""
        self._blocked: str | None = None; self._saved_status = ""; self._done_status = ""
        self._bar_names: list = []
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)

        g1 = QGroupBox("Fixes to compare")
        g1.setToolTip("Same houses and the same random draw as the inputs on the left of the window.")
        f1 = QFormLayout(g1); f1.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.cap = _range_spin(50, 100, 80, 5, " % of nameplate")
        self.cap.setToolTip("The battery tries to keep the transformer under this share of its rating (kVA).")
        self.bigger = QComboBox()
        [self.bigger.addItem(f"{k} kVA", k) for k in sorted(TRANSFORMERS)[1:]]    # re-filtered against the station at run time
        self.bigger.setToolTip("Filled at run time with the catalogue sizes above the current transformer "
                               f"(v0.1 catalogue: {', '.join(str(k) for k in sorted(TRANSFORMERS))} kVA). "
                               "The bigger unit is simulated with its own loss figures and kiosk rise.")
        self.fast = QCheckBox("fast mode"); self.fast.setChecked(True)
        self.fast.setToolTip("Fits a power-flow proxy on ~300 real power flows and computes every hour near a limit exactly. "
                             "Heavy streets fall back to the full power flow on their own (up to ~2 min per run).")
        f1.addRow("Battery cap", self.cap)
        f1.addRow("Bigger transformer", self.bigger)
        f1.addRow("", self.fast)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Compare fixes (5 runs)"); self.run_btn.clicked.connect(self.run)
        self.run_btn.setToolTip("Runs 5 cases on the inputs on the left of the window (not on the case shown in the card).")
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setVisible(False)
        row.addWidget(self.run_btn, 1); row.addWidget(self.cancel_btn)
        f1.addRow(row)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        f1.addRow(self.progress)
        self.status = QLabel("Runs the baseline, the bigger transformer, two smart-charging variants and a battery sized by "
                             "simulation, all on the current inputs. Seconds per run in fast mode; up to ~2 min per run "
                             "when a heavy street falls back to the full power flow.")
        self.status.setProperty("muted", True); self.status.setWordWrap(True)
        f1.addRow(self.status)

        g2 = QGroupBox("Cost assumptions (indicative, not quotes)")
        g2.setToolTip("Ranges with stated sources — see the Assumptions & sources tab. Changing a value re-costs instantly.")
        f2 = QFormLayout(g2); f2.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.unit_ksek = _range_spin(50, 1000, 209, 10, " kSEK")
        self.unit_ksek.setToolTip("List value, Ei Normvärdeslista 2024–2027: NG15921, 800 kVA unit, value year 2022 "
                                  "(12/0,4 kV; a 24/0,4 kV unit is 218 kSEK).")
        self.new_lo = _range_spin(100, 2000, 490, 10, " kSEK"); self.new_hi = _range_spin(100, 2000, 634, 10, " kSEK")
        new_tip = ("Ei Normvärdeslista 2024–2027, value year 2022: a complete new station = kiosk (plåt 490 … betong 634 kSEK "
                   "incl. the unit). Used only when the box below is ticked.")
        self.new_lo.setToolTip(new_tip); self.new_hi.setToolTip(new_tip)
        self.new_kiosk = QCheckBox("needs a new station (kiosk too small)")
        self.new_kiosk.setToolTip("Ticked: the bigger unit is costed as a new station (low/high above) instead of a unit swap.")
        self.bat_lo = _range_spin(1000, 10000, 4000, 250, " SEK/kWh"); self.bat_hi = _range_spin(1000, 10000, 6000, 250, " SEK/kWh")
        bat_tip = ("Installed cost per kWh, assumption. Anchors: Europe utility-scale turnkey ≈ 1 900 SEK/kWh (BNEF 2025); "
                   "Swedish residential installed 6 000–7 500 SEK/kWh.")
        self.bat_lo.setToolTip(bat_tip); self.bat_hi.setToolTip(bat_tip)
        self.spread_lo = _range_spin(0, 20000, 0, 250, " SEK"); self.spread_hi = _range_spin(0, 20000, 5000, 250, " SEK")
        spread_tip = "Per EV house, assumption: a load-management box 1 200–2 000 SEK + electrician, or a free tariff signal."
        self.spread_lo.setToolTip(spread_tip); self.spread_hi.setToolTip(spread_tip)
        self.timer_lo = _range_spin(0, 20000, 0, 250, " SEK"); self.timer_hi = _range_spin(0, 20000, 0, 250, " SEK")
        timer_tip = ("Per EV house. 0 SEK is an optimistic bound: it assumes every EV house sets a timer; "
                     "about 25 % of hourly-price customers do (Energiforsk 2023:966).")
        self.timer_lo.setToolTip(timer_tip); self.timer_hi.setToolTip(timer_tip)
        f2.addRow("Unit swap (list value)", self.unit_ksek)
        f2.addRow("New station", self._pair(self.new_lo, self.new_hi))
        f2.addRow("", self.new_kiosk)
        f2.addRow("Battery", self._pair(self.bat_lo, self.bat_hi))
        f2.addRow("Spread charging, per charger", self._pair(self.spread_lo, self.spread_hi))
        f2.addRow("Night timer, per charger", self._pair(self.timer_lo, self.timer_hi))
        for w in (self.unit_ksek, self.new_lo, self.new_hi, self.bat_lo, self.bat_hi, self.spread_lo, self.spread_hi,
                  self.timer_lo, self.timer_hi):
            w.valueChanged.connect(self.recost)
        self.new_kiosk.toggled.connect(self.recost)
        self.new_kiosk.toggled.connect(self._new_station_enabled); self._new_station_enabled()
        self._controls = [self.cap, self.bigger, self.fast]

        column = QWidget(); cv = QVBoxLayout(column); cv.setContentsMargins(0, 0, 6, 0)
        cv.addWidget(g1); cv.addWidget(g2); cv.addStretch(1)
        scroll = QScrollArea(); scroll.setWidget(column); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        column.setMinimumWidth(0); scroll.setMinimumWidth(250)

        results = QWidget(); rv = QVBoxLayout(results); rv.setContentsMargins(0, 0, 0, 0)
        self.banner = QLabel(""); self.banner.setWordWrap(True); self.banner.setVisible(False)
        self.banner.setStyleSheet(f"color: {INK}; font-weight: 600; background: #fff4d6; padding: 4px; border-radius: 4px;")
        self.base_label = QLabel(""); self.base_label.setProperty("muted", True); self.base_label.setWordWrap(True)
        rv.addWidget(self.banner); rv.addWidget(self.base_label)
        split = QSplitter(Qt.Vertical)
        self.view = QTableView(); self.model = DataFrameModel(decimals=1, header_tips=HEADER_TIPS); self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True); self.view.setMinimumHeight(84)        # header + two rows, never less
        split.addWidget(self.view)
        chart = QWidget(); cl = QHBoxLayout(chart); cl.setContentsMargins(0, 0, 0, 0)
        self.bars_paper = self._bar_plot("ON PAPER — hours above 80 %", LOAD_COLOR)
        self.bars_real = self._bar_plot("REALLY — life used, days/yr", HOTSPOT_COLOR)
        cl.addWidget(self.bars_paper[0]); cl.addWidget(self.bars_real[0])
        chart.setMinimumHeight(110)
        split.addWidget(chart)
        split.setSizes([210, 260])
        # legend + engine notes in one small scrollable box, so a short window never squeezes the charts
        self.notes = QTextBrowser(); self.notes.setFrameShape(QFrame.NoFrame); self.notes.setMaximumHeight(72)
        self.notes.setStyleSheet("color: #6b6b6b; font-size: 9.5pt; border: none; background: transparent;")
        self.notes.setPlainText(LEGEND)
        rv.addWidget(split, 1); rv.addWidget(self.notes)
        outer = QSplitter(Qt.Horizontal)
        outer.addWidget(scroll); outer.addWidget(results)
        outer.setStretchFactor(0, 0); outer.setStretchFactor(1, 1); outer.setSizes([300, 900])
        lay.addWidget(outer)

    @staticmethod
    def _pair(a, b):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.addWidget(a); h.addWidget(QLabel("–")); h.addWidget(b)
        w.setToolTip(a.toolTip() + " Low – high of the range.")
        return w

    @staticmethod
    def _bar_plot(title, color):
        plot = pg.PlotWidget(title=title)
        plot.showGrid(y=True, alpha=0.2)
        plot.getAxis("bottom").setTicks([])
        return plot, color

    def _new_station_enabled(self):
        on = self.new_kiosk.isChecked()
        self.new_lo.setEnabled(on); self.new_hi.setEnabled(on)

    def assumptions(self) -> CostAssumptions:
        pair = lambda a, b: (min(a.value(), b.value()), max(a.value(), b.value()))
        return CostAssumptions(
            transformer_unit_ksek=(self.unit_ksek.value(), self.unit_ksek.value()),
            new_station_ksek=pair(self.new_lo, self.new_hi),
            needs_new_kiosk=self.new_kiosk.isChecked(),
            battery_sek_per_kwh=pair(self.bat_lo, self.bat_hi),
            spread_charging_sek_per_charger=pair(self.spread_lo, self.spread_hi),
            timer_charging_sek_per_charger=pair(self.timer_lo, self.timer_hi),
        )

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ---------------- the scenario panel tells the tab about the inputs
    def set_banner(self, text: str | None):
        """A warning above the results when the inputs differ from the case shown in the card."""
        self.banner.setText(text or ""); self.banner.setVisible(bool(text))

    def inputs_changed(self, case=None):
        """`case` = the current inputs; stale only while they differ from the comparison's own base case."""
        if self.comparison is None or self.is_running() or self._blocked:
            return
        if case is not None and case == self.comparison.results["baseline"].case:
            self.status.setText(self._done_status)
        else:
            self.status.setText("Inputs changed since this comparison — press Compare fixes to update.")

    def block(self, reason: str | None):
        """Another run is in progress elsewhere in the panel (or None to lift the block)."""
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
        sizes = [k for k in sorted(TRANSFORMERS) if k > case.trafo_kva]
        if not sizes:
            show_error(self, "No bigger transformer", f"The v0.1 catalogue has {', '.join(str(k) for k in sorted(TRANSFORMERS))} kVA; "
                       f"there is no unit bigger than the current {case.trafo_kva} kVA to compare.")
            return
        chosen = self.bigger.currentData()
        self.bigger.clear(); [self.bigger.addItem(f"{k} kVA", k) for k in sizes]
        if chosen in sizes:
            self.bigger.setCurrentIndex(sizes.index(chosen))
        for w in self._controls:
            w.setEnabled(False)
        self.run_btn.setEnabled(False); self.cancel_btn.setVisible(True)
        self.progress.setVisible(True); self.progress.setRange(0, 5); self.progress.setValue(0)
        self._t0 = time.perf_counter(); self._timer.start(1000); self._progress_text = "Starting …"; self._tick()
        self._worker = Worker(compare_fixes, case, self.weather, fast=self.fast.isChecked(),
                              battery_cap_share=self.cap.value() / 100, bigger_kva=self.bigger.currentData(),
                              with_progress=True, with_checkpoint=True)
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
        show_error(self, "Fix comparison failed", msg)

    def _done(self, cmp: FixComparison):
        if not self._current():
            return
        self._finish_ui()
        self.set_comparison(cmp)

    # ---------------- show
    def set_comparison(self, cmp: FixComparison):
        """Show a comparison (used by the worker and by tests)."""
        try:
            self.comparison = cmp
            self.recost()
            base = cmp.results["baseline"]
            self.base_label.setText("Base case: " + describe_case(base.case))
            self._done_status = (f"Baseline: {base.verdict.headline()}. Costs are ranges with stated sources; "
                                 "capex alone flatters short-lived assets — see the service-life column.")
            self.status.setText(self._done_status)
        except Exception as e:
            show_error(self, "Could not show the comparison", f"{type(e).__name__}: {e}")

    def recost(self):
        if self.comparison is None:
            return
        fx = fixes_table(self.comparison)
        ct = cost_table(self.comparison, self.assumptions())
        table = fx.join(ct[COST_COLUMNS], how="left")[COLUMN_ORDER]
        base_kva = self.comparison.results["baseline"].case.trafo_kva
        for name, r in self.comparison.results.items():
            if r.case.trafo_kva != base_kva:                 # the swap IS the reinforcement: no peak avoided to show
                table.loc[name, "peak kVA avoided"] = np.nan
                table.loc[name, "reinforcement-equivalent value (kSEK)"] = np.nan
        table.index.name = "fix"
        self.model.set_df(table)
        self.view.resizeColumnsToContents()
        why_col = list(self.model.df.columns).index("why")
        self.view.setColumnWidth(why_col, 260)
        self._bar_names = list(fx.index)
        self._draw_bars(self.bars_paper, fx["h > 80 %"], fx["verdict"], whole=True)
        self._draw_bars(self.bars_real, fx["life days/yr"], fx["verdict"], whole=False)
        self._relabel_bars()
        notes = list(self.comparison.notes)
        notes.append(ct.attrs.get("title", "") + " — " + ct.attrs.get("note", "").replace("see SOURCES.md",
                                                                                           "see the Assumptions & sources tab"))
        self.notes.setPlainText(LEGEND + chr(10) + "Notes: " + " · ".join(notes))

    def _relabel_bars(self):
        """Fix names under the bars when ~90 px per bar are available, else the table's row numbers."""
        n = len(self._bar_names)
        if not n:
            return
        for plot, _ in (self.bars_paper, self.bars_real):
            roomy = plot.width() / n >= 80
            ticks = [(i, _short(name) if roomy else str(i + 1)) for i, name in enumerate(self._bar_names)]
            plot.getAxis("bottom").setTicks([ticks])
            plot.setToolTip("" if roomy else "bars numbered as the table rows: " + ", ".join(f"{i + 1} = {_short(nm)}" for i, nm in enumerate(self._bar_names)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relabel_bars()

    @staticmethod
    def _draw_bars(bar_plot, values: pd.Series, verdicts: pd.Series, whole: bool):
        plot, color = bar_plot
        plot.clear()
        x = np.arange(len(values))
        vals = values.to_numpy(dtype=float)
        brushes = [pg.mkBrush(VERDICT_COLORS.get(v, color)) for v in verdicts]
        plot.getAxis("bottom").setTicks([[(i, _short(n)) for i, n in enumerate(values.index)]])
        if len(vals) and (np.all(np.isnan(vals)) or float(np.nanmax(vals)) <= 0):
            plot.setYRange(0, 1)
            t = pg.TextItem("no hours above 80 % of nameplate for any fix" if whole else "no life used for any fix",
                            color="#333333", anchor=(0.5, 0.5))
            t.setPos((len(vals) - 1) / 2, 0.5); plot.addItem(t)
            return
        plot.addItem(pg.BarGraphItem(x=x, height=vals, width=0.6, brushes=brushes, pen=pg.mkPen(None)))
        top = float(np.nanmax(vals))
        plot.setYRange(0, top * 1.15)
        for i, v in enumerate(vals):
            label = f"{v:,.0f}" if whole or v >= 10 else f"{v:.2f}"
            t = pg.TextItem(label, color="#333333", anchor=(0.5, 1))
            t.setPos(i, v)
            plot.addItem(t)
