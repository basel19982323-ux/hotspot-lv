"""Mode B — a real station from the DSO's own CSV: file and options on the left, verdict and charts on the right."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline
from hotspotlv.app.widgets.verdict_card import VerdictCard
from hotspotlv.app.workers import Worker
from hotspotlv.paths import package_data
from hotspotlv.station import StationResult, ambient_for, assess_station, read_station_csv, to_hourly, with_overlay
from hotspotlv.weather import SMHI_STATION

EXAMPLE = package_data() / "examples" / "ns-102.csv"


def _assess(path, rated_kva, measured_at, tz, stamp, smhi_station, enclosure, n_ev, n_hp, seed):
    """The whole mode-B pipeline, run in the worker thread."""
    st = read_station_csv(path, measured_at=measured_at, tz=tz, stamp=stamp)
    overlay = {}
    if n_ev or n_hp:
        st = to_hourly(st)
        amb_h = ambient_for(st.index, smhi_station)
        st = with_overlay(st, amb_h, n_ev=n_ev, n_hp=n_hp, seed=seed)
        overlay = {"n_ev": n_ev, "n_hp": n_hp}
    amb = ambient_for(st.index, smhi_station)
    return assess_station(st, rated_kva, amb, smhi_station=smhi_station, enclosure=enclosure, overlay=overlay), amb


class StationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: StationResult | None = None
        self._worker: Worker | None = None
        split = QSplitter(Qt.Horizontal, self)
        lay = QHBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8); lay.addWidget(split)

        left = QWidget(); ll = QVBoxLayout(left)
        g1 = QGroupBox("Station data (CSV)")
        f1 = QFormLayout(g1)
        row = QHBoxLayout()
        self.path = QLineEdit(); self.path.setPlaceholderText("timestamp + kW / kWh per hour or quarter hour …")
        browse = QPushButton("Browse…"); browse.setProperty("secondary", True); browse.clicked.connect(self.browse)
        example = QPushButton("Example"); example.setProperty("secondary", True)
        example.clicked.connect(lambda: self.path.setText(str(EXAMPLE)))
        row.addWidget(self.path, 1); row.addWidget(browse); row.addWidget(example)
        f1.addRow(row)
        self.kva = QSpinBox(); self.kva.setRange(50, 2500); self.kva.setSingleStep(50); self.kva.setValue(500); self.kva.setSuffix(" kVA")
        self.measured = QComboBox(); self.measured.addItem("sum of customer meters", "meters"); self.measured.addItem("transformer measurement", "transformer")
        self.tz = QComboBox(); self.tz.addItem("Swedish local time (with DST)", "Europe/Stockholm"); self.tz.addItem("normaltid (CET all year)", "normaltid")
        self.stamp = QComboBox(); self.stamp.addItem("stamp = start of interval", "start"); self.stamp.addItem("stamp = end of interval", "end")
        self.smhi = QSpinBox(); self.smhi.setRange(1, 999999); self.smhi.setValue(SMHI_STATION)
        self.enclosure = QComboBox(); self.enclosure.addItems(["kiosk", "outdoor"])
        f1.addRow("Transformer rating", self.kva)
        f1.addRow("Values are", self.measured)
        f1.addRow("Timestamps", self.tz)
        f1.addRow("", self.stamp)
        f1.addRow("SMHI station (ambient)", self.smhi)
        f1.addRow("Enclosure", self.enclosure)
        note = QLabel("Rated kVA and the SMHI station must match the real place; everything else is read from the file "
                      "and written to the notes."); note.setWordWrap(True); note.setProperty("muted", True)
        f1.addRow(note)

        g2 = QGroupBox("What if … (synthetic overlay, upper bound)")
        f2 = QFormLayout(g2)
        self.n_ev = QSpinBox(); self.n_ev.setRange(0, 500)
        self.n_hp = QSpinBox(); self.n_hp.setRange(0, 500)
        self.seed = QSpinBox(); self.seed.setRange(0, 9999)
        f2.addRow("Extra EV chargers (11 kW)", self.n_ev)
        f2.addRow("Extra air/water heat pumps", self.n_hp)
        f2.addRow("Dice (seed)", self.seed)

        self.run_btn = QPushButton("Assess this station"); self.run_btn.clicked.connect(self.run)
        self.status = QLabel(""); self.status.setProperty("muted", True); self.status.setWordWrap(True)
        ll.addWidget(g1); ll.addWidget(g2); ll.addWidget(self.run_btn); ll.addWidget(self.status); ll.addStretch(1)

        right = QWidget(); rl = QVBoxLayout(right)
        self.card = VerdictCard(); rl.addWidget(self.card)
        self.tabs = QTabWidget()
        self.timeline = YearTimeline(); self.duration = DurationCurve(); self.week = WorstWeek()
        self.tabs.addTab(self.timeline, "Period"); self.tabs.addTab(self.duration, "Duration curve"); self.tabs.addTab(self.week, "Worst week")
        rl.addWidget(self.tabs, 1)
        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setSizes([380, 900])

    def browse(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Station CSV", "", "CSV files (*.csv *.txt);;All files (*)")
        if fn:
            self.path.setText(fn)

    def run(self):
        p = Path(self.path.text().strip())
        if not p.exists():
            QMessageBox.warning(self, "File", "Choose an existing CSV file."); return
        self.run_btn.setEnabled(False)
        self.status.setText("Reading the file, fetching SMHI temperature if needed, thermal year …")
        self._worker = Worker(_assess, p, self.kva.value(), self.measured.currentData(), self.tz.currentData(),
                              self.stamp.currentData(), self.smhi.value(), self.enclosure.currentText(),
                              self.n_ev.value(), self.n_hp.value(), self.seed.value())
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, msg: str):
        self.run_btn.setEnabled(True); self.status.setText("")
        QMessageBox.critical(self, "Could not assess the station", msg)

    def _done(self, payload):
        res, amb = payload
        self.result = res
        self.run_btn.setEnabled(True)
        self.status.setText(f"Done. {res.station.period()}, {res.station.step_min:g}-min steps, "
                            f"{res.station.gaps_filled} gaps filled. Notes are under the verdict.")
        self.card.set_verdict(res.verdict, f"{res.rated_kva:.0f} kVA")
        step_h = res.station.step_min / 60
        self.timeline.set_data(res.year.hourly, res.thermal.hourly, amb)
        self.timeline._moved()
        self.duration.set_year(res.year.hourly, step_h)
        self.week.set_week(res.thermal.worst_week(), res.year.hourly, amb, step_h)
