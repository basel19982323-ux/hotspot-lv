"""The Fixes tab: the four fixes on the same houses and dice, with the cost assumptions as live controls.

    ┌ controls ──────────────────┐ ┌ table: fix · verdict · peak · h>80 · h>100 · life · capex … ┐
    │ battery cap 80 %           │ ├─────────────────────────────────────────────────────────────┤
    │ bigger unit 800 kVA        │ │ bars per fix: hours above 80 % (on paper) | life (really)   │
    │ [Compare fixes (5 runs)]   │ └─────────────────────────────────────────────────────────────┘
    │ ▓▓▓░░ 3/5                  │  notes from the engine (e.g. "battery: even 1 000 kWh cannot
    ├ cost assumptions ──────────┤  hold 400 kVA every hour …")
    │ unit swap 209 kSEK …       │
    └────────────────────────────┘

The engine does the comparison (study.compare_fixes) and the costing
(costs.cost_table); changing a cost control only re-runs the costing, which
is instant. No physics and no prices live in this file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableView, QVBoxLayout, QWidget)

from hotspotlv.app.theme import HOTSPOT_COLOR, LOAD_COLOR, VERDICT_COLORS
from hotspotlv.app.widgets.station_panel import show_error
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.workers import Worker
from hotspotlv.catalogue import TRANSFORMERS
from hotspotlv.costs import CostAssumptions, cost_table
from hotspotlv.study import FixComparison, compare_fixes, fixes_table

COST_COLUMNS = ["capex low (kSEK)", "capex high (kSEK)", "service life (yr)", "peak kVA avoided",
                "reinforcement-equivalent value (kSEK)"]


def _short(name) -> str:
    """Bar label: the fix name without its clock window (the table carries the full name)."""
    return str(name).split(" 22")[0]


def _range_spin(lo, hi, value, step, suffix) -> QSpinBox:
    sb = QSpinBox(); sb.setRange(lo, hi); sb.setSingleStep(step); sb.setValue(value); sb.setSuffix(suffix)
    return sb


class FixesView(QWidget):
    """`case_provider()` returns the Case to compare (the scenario panel's current inputs)."""

    def __init__(self, case_provider, weather: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._case_provider = case_provider
        self.weather = weather
        self.comparison: FixComparison | None = None
        self._worker: Worker | None = None
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)

        g1 = QGroupBox("Fixes to compare")
        g1.setToolTip("Same houses and the same random draw as the inputs on the left of the window.")
        f1 = QFormLayout(g1); f1.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.cap = _range_spin(50, 100, 80, 5, " % of nameplate")
        self.cap.setToolTip("The battery tries to keep the transformer under this share of its rating (kVA).")
        self.bigger = QComboBox(); [self.bigger.addItem(f"{k} kVA", k) for k in sorted(TRANSFORMERS) if k > 500]
        self.bigger.setToolTip("The bigger unit is simulated with its own loss figures and kiosk rise.")
        self.fast = QCheckBox("fast mode"); self.fast.setChecked(True)
        f1.addRow("Battery cap", self.cap)
        f1.addRow("Bigger transformer", self.bigger)
        f1.addRow("", self.fast)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Compare fixes (5 runs)"); self.run_btn.clicked.connect(self.run)
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setVisible(False)
        row.addWidget(self.run_btn, 1); row.addWidget(self.cancel_btn)
        f1.addRow(row)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        f1.addRow(self.progress)
        self.status = QLabel("Runs the baseline, the bigger transformer, two smart-charging variants and a battery sized by "
                             "simulation, all on the current inputs. Heavy streets take a few minutes.")
        self.status.setProperty("muted", True); self.status.setWordWrap(True)
        f1.addRow(self.status)

        g2 = QGroupBox("Cost assumptions (indicative, not quotes)")
        g2.setToolTip("Ranges with stated sources — see Assumptions & sources. Changing a value re-costs instantly.")
        f2 = QFormLayout(g2); f2.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.unit_ksek = _range_spin(50, 1000, 209, 10, " kSEK")
        self.unit_ksek.setToolTip("Ei Normvärdeslista 2024–2027, 800 kVA unit, value year 2022.")
        self.new_lo = _range_spin(100, 2000, 490, 10, " kSEK"); self.new_hi = _range_spin(100, 2000, 634, 10, " kSEK")
        self.new_kiosk = QCheckBox("needs a new station (kiosk too small)")
        self.new_kiosk.setToolTip("Checked: the bigger unit is costed as a new station (low/high above) instead of a unit swap.")
        self.bat_lo = _range_spin(1000, 10000, 4000, 250, " SEK/kWh"); self.bat_hi = _range_spin(1000, 10000, 6000, 250, " SEK/kWh")
        self.bat_lo.setToolTip("Anchors: Europe utility-scale turnkey ≈ 1 900 SEK/kWh (BNEF 2025); Swedish residential installed 6 000–7 500.")
        self.spread_lo = _range_spin(0, 20000, 0, 250, " SEK"); self.spread_hi = _range_spin(0, 20000, 5000, 250, " SEK")
        self.spread_lo.setToolTip("Per EV house: a load-management box 1 200–2 000 SEK + electrician, or a free tariff signal.")
        f2.addRow("Unit swap", self.unit_ksek)
        f2.addRow("New station", self._pair(self.new_lo, self.new_hi))
        f2.addRow("", self.new_kiosk)
        f2.addRow("Battery", self._pair(self.bat_lo, self.bat_hi))
        f2.addRow("Charging control, per charger", self._pair(self.spread_lo, self.spread_hi))
        for w in (self.unit_ksek, self.new_lo, self.new_hi, self.bat_lo, self.bat_hi, self.spread_lo, self.spread_hi):
            w.valueChanged.connect(self.recost)
        self.new_kiosk.toggled.connect(self.recost)
        column = QWidget(); cv = QVBoxLayout(column); cv.setContentsMargins(0, 0, 6, 0)
        cv.addWidget(g1); cv.addWidget(g2); cv.addStretch(1)
        scroll = QScrollArea(); scroll.setWidget(column); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        scroll.setFixedWidth(430)

        results = QWidget(); rv = QVBoxLayout(results); rv.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Vertical)
        self.view = QTableView(); self.model = DataFrameModel(decimals=1); self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        split.addWidget(self.view)
        chart = QWidget(); cl = QHBoxLayout(chart); cl.setContentsMargins(0, 0, 0, 0)
        self.bars_paper = self._bar_plot("ON PAPER — hours above 80 %", LOAD_COLOR)
        self.bars_real = self._bar_plot("REALLY — life used, days/yr", HOTSPOT_COLOR)
        cl.addWidget(self.bars_paper[0]); cl.addWidget(self.bars_real[0])
        split.addWidget(chart)
        self.notes = QLabel(""); self.notes.setProperty("muted", True); self.notes.setWordWrap(True)
        split.setSizes([200, 300])
        rv.addWidget(split, 1); rv.addWidget(self.notes)
        lay.addWidget(scroll); lay.addWidget(results, 1)

    @staticmethod
    def _pair(a, b):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.addWidget(a); h.addWidget(QLabel("–")); h.addWidget(b)
        w.setToolTip("low – high of the range")
        return w

    @staticmethod
    def _bar_plot(title, color):
        plot = pg.PlotWidget(title=title)
        plot.showGrid(y=True, alpha=0.2)
        plot.getAxis("bottom").setTicks([])
        return plot, color

    def assumptions(self) -> CostAssumptions:
        return CostAssumptions(
            transformer_unit_ksek=(self.unit_ksek.value(), self.unit_ksek.value()),
            new_station_ksek=(min(self.new_lo.value(), self.new_hi.value()), max(self.new_lo.value(), self.new_hi.value())),
            needs_new_kiosk=self.new_kiosk.isChecked(),
            battery_sek_per_kwh=(min(self.bat_lo.value(), self.bat_hi.value()), max(self.bat_lo.value(), self.bat_hi.value())),
            spread_charging_sek_per_charger=(min(self.spread_lo.value(), self.spread_hi.value()),
                                             max(self.spread_lo.value(), self.spread_hi.value())),
        )

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ---------------- run
    def run(self):
        if self.is_running():
            return
        try:
            case = self._case_provider()
        except Exception as e:
            show_error(self, "Inputs", f"{type(e).__name__}: {e}"); return
        if case is None:
            return
        self.run_btn.setEnabled(False); self.cancel_btn.setVisible(True)
        self.progress.setVisible(True); self.progress.setRange(0, 5); self.progress.setValue(0)
        self._worker = Worker(compare_fixes, case, self.weather, fast=self.fast.isChecked(),
                              battery_cap_share=self.cap.value() / 100, bigger_kva=self.bigger.currentData(),
                              with_progress=True)
        self._worker.progress.connect(self._progress)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _progress(self, done, total, label):
        self.progress.setRange(0, total); self.progress.setValue(done)
        self.status.setText(f"Running {done + 1} of {total}: {label} …")

    def _finish_ui(self):
        self.run_btn.setEnabled(True); self.cancel_btn.setVisible(False); self.progress.setVisible(False)

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
            self.status.setText(f"Baseline ({base.case.name}): {base.verdict.headline()}. Costs are ranges with stated "
                                "sources; capex alone flatters short-lived assets — see the service-life column.")
        except Exception as e:
            show_error(self, "Could not show the comparison", f"{type(e).__name__}: {e}")

    def recost(self):
        if self.comparison is None:
            return
        fx = fixes_table(self.comparison)
        ct = cost_table(self.comparison, self.assumptions())
        table = fx.join(ct[COST_COLUMNS], how="left")
        table.index.name = "fix"
        self.model.set_df(table)
        self.view.resizeColumnsToContents()
        why_col = list(self.model.df.columns).index("why")
        self.view.setColumnWidth(why_col, 300)
        self._draw_bars(self.bars_paper, fx["h > 80 %"], fx["verdict"])
        self._draw_bars(self.bars_real, fx["life days/yr"], fx["verdict"])
        notes = list(self.comparison.notes)
        notes.append(ct.attrs.get("title", "") + " — " + ct.attrs.get("note", ""))
        self.notes.setText("Notes: " + " · ".join(notes))

    @staticmethod
    def _draw_bars(bar_plot, values: pd.Series, verdicts: pd.Series):
        plot, color = bar_plot
        plot.clear()
        x = np.arange(len(values))
        brushes = [pg.mkBrush(VERDICT_COLORS.get(v, color)) for v in verdicts]
        plot.addItem(pg.BarGraphItem(x=x, height=values.to_numpy(dtype=float), width=0.6, brushes=brushes, pen=pg.mkPen(None)))
        plot.getAxis("bottom").setTicks([[(i, _short(n)) for i, n in enumerate(values.index)]])
        top = float(values.max()) if len(values) and float(values.max()) > 0 else 1.0
        plot.setYRange(0, top * 1.15)
        for i, v in enumerate(values.to_numpy(dtype=float)):
            t = pg.TextItem(f"{v:,.0f}" if v >= 10 else f"{v:.2f}", color="#333333", anchor=(0.5, 1))
            t.setPos(i, v)
            plot.addItem(t)
