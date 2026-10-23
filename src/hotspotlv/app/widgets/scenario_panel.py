"""Mode A — a synthetic street: inputs on the left, verdict and charts on the right.

Every control maps to one engine parameter (Scenario / Case); the panel builds the
Case, hands it to a worker thread, and shows the CaseResult. No physics here.
"""

from __future__ import annotations

import time

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget, QTableView,
                               QVBoxLayout, QWidget)

from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline
from hotspotlv.app.widgets.diagram import LEGEND as DIAGRAM_LEGEND, FeederDiagram
from hotspotlv.app.widgets.describe import describe_case
from hotspotlv.app.widgets.fixes_view import FixesView
from hotspotlv.app.widgets.mc_view import MonteCarloView, same_but_seed
from hotspotlv.app.widgets.sweep_view import SweepView
from hotspotlv.app.widgets.station_panel import show_error
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.widgets.verdict_card import VerdictCard
from hotspotlv.app.workers import Worker
from hotspotlv.catalogue import TRANSFORMERS
from hotspotlv.feeder import fuse_kw
from hotspotlv.loads.ev import EVParams
from hotspotlv.neighbourhood import DEFAULT_HEATING_MIX, Scenario
from hotspotlv.verdict import thermal_ok
from hotspotlv.study import Case, CaseResult, run_case

HEATING_LABELS = (("ground_source", "Ground-source heat pump"), ("air_water", "Air/water heat pump"),
                  ("direct_electric", "Direct electric heating"), ("none", "District heat / wood / other"))


class ScenarioPanel(QWidget):
    def __init__(self, weather: pd.DataFrame, parent=None):
        super().__init__(parent)
        self.weather = weather
        self.result: CaseResult | None = None
        self._worker: Worker | None = None
        self._blocked: str | None = None; self._saved_status = ""
        self._t0 = 0.0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)

        split = QSplitter(Qt.Horizontal, self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)

        # ---------------- inputs (inside a scroll area so the window can shrink)
        left_inner = QWidget()
        form_lay = QVBoxLayout(left_inner)
        g1 = QGroupBox("Station")
        f1 = QFormLayout(g1)
        f1.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow); f1.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.kva = QComboBox(); [self.kva.addItem(f"{k} kVA", k) for k in sorted(TRANSFORMERS)]
        self.kva.setCurrentIndex(self.kva.findData(500))          # the villa-suburb default, not the smallest
        self.kva.setToolTip("Sizes with Ecodesign Tier-2 loss figures in the catalogue: "
                            + ", ".join(f"{k}" for k in sorted(TRANSFORMERS)) + " kVA.")
        self.houses = QSpinBox(); self.houses.setRange(3, 200); self.houses.setValue(45)
        self.houses.setToolTip("Single-family houses behind the station. Swedish practice: ~40–50 per 500 kVA.")
        self.feeders = QSpinBox(); self.feeders.setRange(1, 6); self.feeders.setValue(3)
        self.feeders.setToolTip("Radial Al 240 mm² cables from the busbar; houses are spread evenly, 30 m apart.")
        self.fuse = QComboBox(); [self.fuse.addItem(f"{a} A", a) for a in (16, 20, 25, 35)]; self.fuse.setCurrentIndex(1)
        self.fuse.setToolTip("Main fuse of every house; its kW cap is applied hour by hour (the EV charger backs off first).")
        self.enclosure = QComboBox(); self.enclosure.addItems(["kiosk", "outdoor"])
        self.enclosure.setToolTip("Kiosk (the Swedish norm) adds half the IEC 60076-7:2005 Table 6 kiosk air rise to the top-oil rise: ageing ≈ ×2.4 at rated load.")
        f1.addRow("Transformer", self.kva)
        f1.addRow("Houses", self.houses)
        f1.addRow("Feeders", self.feeders)
        f1.addRow("House main fuse", self.fuse)
        f1.addRow("Enclosure", self.enclosure)
        wx = QLabel("2023 (1 Jan – 31 Dec, Swedish time): SMHI 96190 Eskilstuna A temperature, PVGIS solar "
                    "for Västerås"); wx.setWordWrap(True)
        wx.setMinimumHeight(3 * wx.fontMetrics().lineSpacing() + 4)   # a wrapped label in a form row got one line
        wx.setToolTip("Measured hourly air temperature from SMHI's checked archive and PVGIS hourly PV output. Other "
                      "places and years come later; mode B (own station data) already takes any SMHI station.")
        f1.addRow("Weather", wx)

        g2 = QGroupBox("Electrification (share of houses)")
        f2 = QFormLayout(g2)
        f2.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow); f2.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.mix = {}
        for key, label in HEATING_LABELS:
            sb = QSpinBox(); sb.setRange(0, 100); sb.setSuffix(" %"); sb.setValue(int(round(DEFAULT_HEATING_MIX[key] * 100)))
            sb.valueChanged.connect(self._check_mix)
            self.mix[key] = sb
            f2.addRow(label, sb)
        self.mix_note = QLabel(""); self.mix_note.setProperty("muted", True); self.mix_note.setWordWrap(True)
        f2.addRow("", self.mix_note)
        self.ev = QSpinBox(); self.ev.setRange(0, 100); self.ev.setSuffix(" %"); self.ev.setValue(25)
        self.ev.setToolTip("Chargers are an 11 / 3.7 / 7.4 kW mix (55 / 30 / 15 %).")
        self.pv = QSpinBox(); self.pv.setRange(0, 100); self.pv.setSuffix(" %"); self.pv.setValue(20)
        self.pv.setToolTip("6–14 kWp per PV house, south-facing 30° roof (PVGIS).")
        self.charging = QComboBox()
        self.charging.addItem("uncontrolled (plug in on arrival)", "none")
        self.charging.addItem("night timer 22–01 (what hourly-price customers do)", "timer")
        self.charging.addItem("coordinated, spread over 22–06", "spread")
        self.charging.setToolTip("Same cars and energy in all three; only the start hour differs.")
        f2.addRow("Houses with an EV charger", self.ev)
        f2.addRow("Houses with rooftop PV", self.pv)
        f2.addRow("EV charging", self.charging)

        g3 = QGroupBox("Run")
        f3 = QFormLayout(g3)
        f3.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.seed = QSpinBox(); self.seed.setRange(0, 9999); self.seed.setValue(0)
        self.seed.setToolTip("Random draw: which houses get what, when the cars charge. Change it to see the spread.")
        self.fast = QCheckBox("fast mode"); self.fast.setChecked(True)
        self.fast.setToolTip("Fits a power-flow proxy on ~300 real power flows and computes every hour near a limit exactly. "
                             "Heavy streets fall back to the full power flow on their own; the status line says which ran.")
        f3.addRow("Random draw", self.seed)
        f3.addRow("", self.fast)
        btns = QHBoxLayout()
        self.run_btn = QPushButton("Run this case"); self.run_btn.clicked.connect(self.run)
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setVisible(False)
        btns.addWidget(self.run_btn, 1); btns.addWidget(self.cancel_btn)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.setVisible(False)
        self.status = QLabel(""); self.status.setProperty("muted", True); self.status.setWordWrap(True)
        form_lay.addWidget(g1); form_lay.addWidget(g2); form_lay.addWidget(g3); form_lay.addStretch(1)
        scroll = QScrollArea(); scroll.setWidget(left_inner); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_inner.setMinimumWidth(0)
        left = QWidget(); left_lay = QVBoxLayout(left); left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(scroll, 1)                       # only the option boxes scroll …
        left_lay.addLayout(btns); left_lay.addWidget(self.progress); left_lay.addWidget(self.status)   # … Run stays visible
        left.setMinimumWidth(340)

        # ---------------- results
        right = QSplitter(Qt.Vertical)                      # drag the boundary between card and charts
        self.card = VerdictCard(empty_text="Set up the street on the left and press Run. The verdict and the "
                                            "'on paper vs really' comparison appear here.")
        right.addWidget(self.card.scrolled())
        self.tabs = QTabWidget()
        self.timeline = YearTimeline()
        self.diagram = FeederDiagram()
        tl = QSplitter(Qt.Vertical)                      # drag the boundary between timeline and diagram
        tl.addWidget(self.timeline)
        dbox = QWidget(); dl = QVBoxLayout(dbox); dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(self.diagram, 1)
        self.diagram_legend = QLabel(DIAGRAM_LEGEND); self.diagram_legend.setProperty("muted", True); self.diagram_legend.setWordWrap(True)
        self.diagram_legend.setMinimumHeight(3 * self.diagram_legend.fontMetrics().lineSpacing() + 4)   # never under the diagram
        dl.addWidget(self.diagram_legend)
        tl.addWidget(dbox)
        tl.setSizes([380, 240])                          # the timeline drives the diagram: it gets the larger share
        self.diagram.setMinimumHeight(120)
        self.timeline.setMinimumHeight(150)              # a 46-px timeline on a laptop before 22 Oct 2026; 150 keeps
                                                         # the window minimum within the declared 900 × 570
        self.timeline.hour_changed.connect(lambda i: self.diagram.set_hour(i, self.timeline.label_at(i)))
        self.duration = DurationCurve()
        self.week = WorstWeek()
        self.houses_view = QTableView(); self.houses_model = DataFrameModel(decimals=1)
        self.houses_view.setModel(self.houses_model); self.houses_view.setAlternatingRowColors(True)
        self.tabs.addTab(tl, "Year && diagram")
        self.tabs.addTab(self.duration, "Duration curve")
        self.tabs.addTab(self.week, "Worst week")
        self.tabs.addTab(self.houses_view, "Houses")
        self.fixes = FixesView(self.case_for_views, weather)
        self.sweep = SweepView(self.case_for_views, weather)
        self.tabs.addTab(self.fixes, "Fixes")
        self.tabs.addTab(self.sweep, "Sweep (where it flips)")
        self.mc = MonteCarloView(self.case_for_views, weather)
        self.tabs.addTab(self.mc, "Spread (random draws)")
        right.addWidget(self.tabs)
        right.setStretchFactor(0, 0); right.setStretchFactor(1, 1); right.setSizes([400, 460])
        self._right = right; self._card_sizes = None
        self.tabs.currentChanged.connect(self._tab_changed)

        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([420, 900])
        self._check_mix()
        for view in self.views:                             # one run at a time: the others' Run buttons wait
            view.run_started.connect(self._sync_busy); view.run_finished.connect(self._sync_busy)
        for w in [self.kva, self.fuse, self.enclosure, self.charging]:
            w.currentIndexChanged.connect(self._inputs_changed)
        for w in [self.houses, self.feeders, self.ev, self.pv, self.seed, *self.mix.values()]:
            w.valueChanged.connect(self._inputs_changed)

    @property
    def views(self):
        """The result tabs that run on the current inputs (Fixes, Sweep, Spread)."""
        return (self.fixes, self.sweep, self.mc)

    # ---------------- the Fixes, Sweep and Spread tabs need the room: the card shrinks to its headline there
    def _tab_changed(self, index: int):
        sizes = self._right.sizes()
        total = sum(sizes)
        if total <= 0:
            return
        if index >= 4:
            if self._card_sizes is None:
                self._card_sizes = sizes
                headline = self.card._reasons.geometry().bottom() + 16     # level + headline + reasons, no readouts cut in half
                small = max(90, min(sizes[0], headline))
                self._right.setSizes([small, total - small])
        elif self._card_sizes is not None:
            self._right.setSizes(self._card_sizes); self._card_sizes = None

    # ---------------- the two result tabs follow the inputs, the card follows its run
    def _inputs_changed(self, *_):
        """Warn on the Fixes, Sweep and Spread tabs when the inputs no longer match the case on the card."""
        banner, tip, now = None, "", None
        if self.build_case_error() is None:
            try:
                now = self.build_case()
            except ValueError:
                now = None
        if self.result is not None and now is not None and now != self.result.case:
            banner = f"Inputs differ from the card ({self.result.case.name}): this tab uses the current inputs below."
            tip = f"Card: {describe_case(self.result.case)}\nInputs now: {describe_case(now)}"
        for view in (self.fixes, self.sweep):
            view.set_banner(banner); view.banner.setToolTip(tip); view.inputs_changed(now)
        # the Spread tab replaces the random draw, so a seed-only change is no difference there
        mc_banner = banner if (self.result is not None and now is not None and not same_but_seed(now, self.result.case)) else None
        mc_tip = (f"Card: {describe_case(self.result.case, seed=False)}\nInputs now: {describe_case(now, seed=False)}"
                  if mc_banner else "")
        self.mc.set_banner(mc_banner); self.mc.banner.setToolTip(mc_tip); self.mc.inputs_changed(now)

    def _sync_busy(self):
        """Run, Compare fixes, Run sweep and Run draws share one CPU and one time estimate: only one at a time."""
        own = self._worker is not None and self._worker.isRunning()
        running = ("this case" if own else "the Fixes tab" if self.fixes.is_running()
                   else "the Sweep tab" if self.sweep.is_running() else "the Spread tab" if self.mc.is_running() else None)
        reason = f"A run is in progress on {running} — wait for it or cancel it there." if running else None
        for view in self.views:
            view.block(reason)
        if not own:
            if reason and self._blocked is None:
                self._saved_status = self.status.text()
            if reason is None and self._blocked is not None:
                self.status.setText(self._saved_status)
            self._blocked = reason
            self.run_btn.setEnabled(reason is None)
            if reason:
                self.status.setText(reason)

    # ---------------- helpers
    def _shares(self) -> tuple[dict, int]:
        total = sum(sb.value() for sb in self.mix.values())
        return ({k: sb.value() / total for k, sb in self.mix.items() if sb.value() > 0} if total else {}), total

    def _check_mix(self):
        shares, total = self._shares()
        if total == 0:
            self.mix_note.setText("Heating shares are all 0 % — set at least one.")
        elif total != 100:
            eff = ", ".join(f"{dict(HEATING_LABELS)[k].split(' ')[0]} {v * 100:.0f} %" for k, v in shares.items())
            self.mix_note.setText(f"Shares sum to {total} % — scaled to 100 %: {eff}")
        else:
            self.mix_note.setText("")

    def build_case_error(self) -> str | None:
        """Why the inputs cannot run, in the planner's words — or None."""
        _, total = self._shares()
        if total == 0:
            return "Set at least one heating share above 0 %."
        if self.feeders.value() > self.houses.value():
            return "More feeders than houses — reduce the feeders or add houses."
        return None

    def build_case(self) -> Case:
        shares, _ = self._shares()
        mode = self.charging.currentData()
        ev = EVParams(smart=mode != "none", smart_start_min_h=22.0, smart_start_max_h=25.0 if mode == "timer" else 30.0)
        sc = Scenario(name="app", n_houses=self.houses.value(), heating_mix=shares, ev_share=self.ev.value() / 100,
                      pv_share=self.pv.value() / 100, fuse_a=self.fuse.currentData(), ev=ev, seed=self.seed.value())
        return Case(name=f"{self.houses.value()} houses / {self.kva.currentData()} kVA", scenario=sc,
                    trafo_kva=self.kva.currentData(), n_feeders=self.feeders.value(),
                    enclosure=self.enclosure.currentText())

    def case_for_views(self) -> Case | None:
        """The current inputs as a Case for the Fixes, Sweep and Spread tabs (None after a warning)."""
        err = self.build_case_error()
        if err:
            QMessageBox.warning(self, "Inputs", err); return None
        return self.build_case()

    def is_running(self) -> bool:
        own = self._worker is not None and self._worker.isRunning()
        return own or any(v.is_running() for v in self.views)

    def cancel_all(self):
        """Closing the window: stop every run on this panel."""
        self.cancel()
        for view in self.views:
            if view.is_running():
                view.cancel()

    # ---------------- run
    def run(self):
        if self.is_running():                                 # own run, or a run on the Fixes / Sweep tab
            return
        err = self.build_case_error()
        if err:
            QMessageBox.warning(self, "Inputs", err); return
        try:
            case = self.build_case()
        except ValueError as e:
            QMessageBox.warning(self, "Inputs", str(e)); return
        self.run_btn.setEnabled(False); self.cancel_btn.setVisible(True); self.progress.setVisible(True)
        self._t0 = time.perf_counter(); self._timer.start(500)
        self.status.setText("Building houses …")
        self._worker = Worker(run_case, case, self.weather, fast=self.fast.isChecked(), with_checkpoint=True)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()
        self._sync_busy()

    def _tick(self):
        t = time.perf_counter() - self._t0
        hint = "fitting the power-flow proxy" if t < 15 else ("exact power flows near the limits" if t < 60 else "full power flow, up to ~2 min")
        self.status.setText(f"Working … {t:.0f} s ({hint})")

    def _finish_ui(self):
        self._timer.stop(); self.progress.setVisible(False); self.cancel_btn.setVisible(False); self.run_btn.setEnabled(True)
        self._sync_busy()

    def cancel(self):
        """The Cancel next to Run stops this case only; the tabs have their own Cancel buttons."""
        w, self._worker = self._worker, None           # retire it: a late result from it is ignored
        was_running = w is not None and w.isRunning()
        if was_running:
            w.cancel()
        self._finish_ui()
        if was_running:
            self.status.setText("Cancelled.")

    def _current(self) -> bool:
        return self.sender() is None or self.sender() is self._worker

    def _failed(self, msg: str):
        if not self._current():
            return
        self._finish_ui(); self.status.setText("")
        show_error(self, "Run failed", msg)

    def _done(self, res: CaseResult):
        if not self._current():
            return
        self._finish_ui()
        try:
            self.result = res
            self._inputs_changed()
            self.card.set_verdict(res.verdict, res.case.name)
            amb = self.weather["temp_c"]
            ok = thermal_ok(res.verdict)
            self.timeline.set_data(res.year.hourly, res.thermal.hourly if ok else None, amb)
            self.diagram.set_case(res.neighbourhood.houses, res.neighbourhood.p_kw, res.year.hourly["loading_pct"],
                                  res.case.trafo_kva, n_feeders=res.case.n_feeders, cap_kw=fuse_kw(res.case.scenario.fuse_a))
            self.timeline._moved()
            self.duration.set_year(res.year.hourly)
            self.week.set_week(res.thermal.worst_week(), res.year.hourly, amb, thermal_valid=ok)
            houses = res.neighbourhood.houses.copy()
            houses.insert(0, "house", range(1, len(houses) + 1))
            self.houses_model.set_df(houses.reset_index(drop=True))
            self.houses_view.resizeColumnsToContents()
            self.status.setText(f"Done in {res.runtime_s:.0f} s — {res.mode_note}. Drag the black line on the timeline "
                                "(or use ← →) to scrub the year; the diagram follows.")
        except Exception as e:
            self.status.setText("")
            show_error(self, "Could not show the result", f"{type(e).__name__}: {e}")
