"""Mode A — a synthetic street: inputs on the left, verdict and charts on the right.

Every control maps to one engine parameter (Scenario / Case); the panel builds the
Case, hands it to a worker thread, and shows the CaseResult. No physics here.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QSpinBox, QSplitter, QTabWidget, QTableView, QVBoxLayout, QWidget)

from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline
from hotspotlv.app.widgets.diagram import FeederDiagram
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.widgets.verdict_card import VerdictCard
from hotspotlv.app.workers import Worker
from hotspotlv.catalogue import TRANSFORMERS
from hotspotlv.feeder import fuse_kw
from hotspotlv.loads.ev import EVParams
from hotspotlv.neighbourhood import DEFAULT_HEATING_MIX, Scenario
from hotspotlv.study import Case, CaseResult, run_case


class ScenarioPanel(QWidget):
    def __init__(self, weather: pd.DataFrame, parent=None):
        super().__init__(parent)
        self.weather = weather
        self.result: CaseResult | None = None
        self._worker: Worker | None = None

        split = QSplitter(Qt.Horizontal, self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)

        # ---------------- inputs
        left = QWidget()
        form_lay = QVBoxLayout(left)
        g1 = QGroupBox("Station")
        f1 = QFormLayout(g1)
        self.kva = QComboBox(); [self.kva.addItem(f"{k} kVA", k) for k in sorted(TRANSFORMERS)]
        self.houses = QSpinBox(); self.houses.setRange(3, 200); self.houses.setValue(45)
        self.feeders = QSpinBox(); self.feeders.setRange(1, 6); self.feeders.setValue(3)
        self.fuse = QComboBox(); [self.fuse.addItem(f"{a} A", a) for a in (16, 20, 25, 35)]; self.fuse.setCurrentIndex(1)
        self.enclosure = QComboBox(); self.enclosure.addItems(["kiosk", "outdoor"])
        f1.addRow("Transformer", self.kva)
        f1.addRow("Houses", self.houses)
        f1.addRow("Feeders", self.feeders)
        f1.addRow("House main fuse", self.fuse)
        f1.addRow("Enclosure", self.enclosure)
        f1.addRow("Weather", QLabel("Västerås 2023 (SMHI Eskilstuna A, PVGIS)"))

        g2 = QGroupBox("Electrification (share of houses)")
        f2 = QFormLayout(g2)
        self.mix = {}
        for key, label in (("ground_source", "Ground-source heat pump"), ("air_water", "Air/water heat pump"),
                           ("direct_electric", "Direct electric heating"), ("none", "District heat / wood / other")):
            sb = QSpinBox(); sb.setRange(0, 100); sb.setSuffix(" %"); sb.setValue(int(round(DEFAULT_HEATING_MIX[key] * 100)))
            sb.valueChanged.connect(self._check_mix)
            self.mix[key] = sb
            f2.addRow(label, sb)
        self.mix_note = QLabel(""); self.mix_note.setProperty("muted", True)
        f2.addRow("", self.mix_note)
        self.ev = QSpinBox(); self.ev.setRange(0, 100); self.ev.setSuffix(" %"); self.ev.setValue(25)
        self.pv = QSpinBox(); self.pv.setRange(0, 100); self.pv.setSuffix(" %"); self.pv.setValue(20)
        self.charging = QComboBox()
        self.charging.addItem("uncontrolled (plug in on arrival)", "none")
        self.charging.addItem("night timer 22–01 (hourly-price customers)", "timer")
        self.charging.addItem("coordinated, spread 22–06", "spread")
        f2.addRow("Houses with an EV charger", self.ev)
        f2.addRow("Houses with rooftop PV", self.pv)
        f2.addRow("EV charging", self.charging)

        g3 = QGroupBox("Run")
        f3 = QFormLayout(g3)
        self.seed = QSpinBox(); self.seed.setRange(0, 9999); self.seed.setValue(0)
        self.fast = QCheckBox("fast mode (fitted power-flow proxy, exact near limits)"); self.fast.setChecked(True)
        f3.addRow("Dice (seed)", self.seed)
        f3.addRow("", self.fast)
        self.run_btn = QPushButton("Run this case")
        self.run_btn.clicked.connect(self.run)
        self.status = QLabel(""); self.status.setProperty("muted", True); self.status.setWordWrap(True)
        form_lay.addWidget(g1); form_lay.addWidget(g2); form_lay.addWidget(g3)
        form_lay.addWidget(self.run_btn); form_lay.addWidget(self.status); form_lay.addStretch(1)

        # ---------------- results
        right = QWidget()
        rlay = QVBoxLayout(right)
        self.card = VerdictCard()
        rlay.addWidget(self.card)
        self.tabs = QTabWidget()
        self.timeline = YearTimeline()
        self.diagram = FeederDiagram()
        tl = QSplitter(Qt.Vertical)                      # drag the boundary between timeline and diagram
        tl.addWidget(self.timeline); tl.addWidget(self.diagram)
        tl.setSizes([320, 300])
        self.diagram.setMinimumHeight(160)
        self.timeline.hour_changed.connect(lambda i: self.diagram.set_hour(i, self.timeline.label_at(i)))
        self.duration = DurationCurve()
        self.week = WorstWeek()
        self.houses_view = QTableView(); self.houses_model = DataFrameModel(decimals=1)
        self.houses_view.setModel(self.houses_model); self.houses_view.setAlternatingRowColors(True)
        self.tabs.addTab(tl, "Year && diagram")
        self.tabs.addTab(self.duration, "Duration curve")
        self.tabs.addTab(self.week, "Worst week")
        self.tabs.addTab(self.houses_view, "Houses")
        rlay.addWidget(self.tabs, 1)

        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([360, 900])
        self._check_mix()

    # ---------------- helpers
    def _check_mix(self):
        total = sum(sb.value() for sb in self.mix.values())
        self.mix_note.setText("" if total == 100 else f"heating shares sum to {total} % — they are scaled to 100 %")

    def build_case(self) -> Case:
        total = sum(sb.value() for sb in self.mix.values()) or 100
        mix = {k: sb.value() / total for k, sb in self.mix.items()}
        mix = {k: v for k, v in mix.items() if v > 0}
        mode = self.charging.currentData()
        ev = EVParams(smart=mode != "none", smart_start_min_h=22.0, smart_start_max_h=25.0 if mode == "timer" else 30.0)
        sc = Scenario(name="app", n_houses=self.houses.value(), heating_mix=mix, ev_share=self.ev.value() / 100,
                      pv_share=self.pv.value() / 100, fuse_a=self.fuse.currentData(), ev=ev, seed=self.seed.value())
        return Case(name=f"{self.houses.value()} houses / {self.kva.currentData()} kVA", scenario=sc,
                    trafo_kva=self.kva.currentData(), n_feeders=self.feeders.value(),
                    enclosure=self.enclosure.currentText())

    # ---------------- run
    def run(self):
        try:
            case = self.build_case()
        except ValueError as e:
            QMessageBox.warning(self, "Input", str(e)); return
        self.run_btn.setEnabled(False)
        self.status.setText("Running: building houses, fitting the power-flow proxy, exact power flows near limits, thermal year …")
        self._worker = Worker(run_case, case, self.weather, fast=self.fast.isChecked())
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, msg: str):
        self.run_btn.setEnabled(True)
        self.status.setText("")
        QMessageBox.critical(self, "Run failed", msg)

    def _done(self, res: CaseResult):
        self.result = res
        self.run_btn.setEnabled(True)
        mode = "full power flow" if not res.fast else f"fast mode, {res.exact_hours} hours exact"
        self.status.setText(f"Done in {res.runtime_s:.0f} s ({mode}). Drag the black line on the timeline to scrub the year.")
        self.card.set_verdict(res.verdict, res.case.name)
        amb = self.weather["temp_c"]
        self.timeline.set_data(res.year.hourly, res.thermal.hourly, amb)
        self.diagram.set_case(res.neighbourhood.houses, res.neighbourhood.p_kw, res.year.hourly["loading_pct"],
                              res.case.trafo_kva, n_feeders=res.case.n_feeders, cap_kw=fuse_kw(res.case.scenario.fuse_a))
        self.timeline._moved()
        self.duration.set_year(res.year.hourly)
        self.week.set_week(res.thermal.worst_week(), res.year.hourly, amb)
        houses = res.neighbourhood.houses.copy()
        houses.insert(0, "house", houses.index)
        self.houses_model.set_df(houses.reset_index(drop=True))
        self.houses_view.resizeColumnsToContents()
