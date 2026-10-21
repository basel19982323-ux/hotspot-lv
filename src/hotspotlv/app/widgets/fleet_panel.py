"""Mode C — a fleet index CSV → ranked table; select a row to see that station's verdict and charts."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QSplitter, QTableView, QTabWidget, QVBoxLayout, QWidget)

from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.widgets.verdict_card import VerdictCard
from hotspotlv.app.workers import Worker
from hotspotlv.fleet import export_fleet_table, read_fleet_index, screen_fleet
from hotspotlv.paths import package_data
from hotspotlv.station import ambient_for

EXAMPLE = package_data() / "examples" / "fleet_index.csv"
SHOW = ["station_id", "verdict", "kVA", "peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year",
        "max_hot_spot_c", "max_load_pu", "energy_mwh", "period", "smhi_station", "why", "notes", "error"]


def _screen(path, progress):
    return screen_fleet(read_fleet_index(path), progress=progress)


class FleetPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.table_df: pd.DataFrame | None = None
        self.results = {}
        self._worker: Worker | None = None
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8)

        row = QHBoxLayout()
        self.path = QLineEdit(); self.path.setPlaceholderText("fleet index CSV: station_id, rated_kva, file[, smhi_station, enclosure, measured_at, tz, stamp]")
        browse = QPushButton("Browse…"); browse.setProperty("secondary", True); browse.clicked.connect(self.browse)
        example = QPushButton("Example"); example.setProperty("secondary", True)
        example.clicked.connect(lambda: self.path.setText(str(EXAMPLE)))
        self.run_btn = QPushButton("Screen the fleet"); self.run_btn.clicked.connect(self.run)
        self.export_btn = QPushButton("Export table…"); self.export_btn.setProperty("secondary", True)
        self.export_btn.clicked.connect(self.export); self.export_btn.setEnabled(False)
        row.addWidget(self.path, 1); row.addWidget(browse); row.addWidget(example); row.addWidget(self.run_btn); row.addWidget(self.export_btn)
        lay.addLayout(row)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        self.status = QLabel("Ranked At risk → Watch → OK, then by days of life used. Every row keeps its notes."); self.status.setProperty("muted", True)
        lay.addWidget(self.progress); lay.addWidget(self.status)

        split = QSplitter(Qt.Vertical)
        self.view = QTableView(); self.model = DataFrameModel(decimals=2); self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True); self.view.setSelectionBehavior(QTableView.SelectRows)
        self.view.setSortingEnabled(False)
        self.view.selectionModel().selectionChanged.connect(self._selected)
        split.addWidget(self.view)
        detail = QWidget(); dl = QVBoxLayout(detail); dl.setContentsMargins(0, 0, 0, 0)
        self.card = VerdictCard(); dl.addWidget(self.card)
        self.tabs = QTabWidget()
        self.timeline = YearTimeline(); self.duration = DurationCurve(); self.week = WorstWeek()
        self.tabs.addTab(self.timeline, "Period"); self.tabs.addTab(self.duration, "Duration curve"); self.tabs.addTab(self.week, "Worst week")
        dl.addWidget(self.tabs, 1)
        split.addWidget(detail)
        split.setSizes([220, 680])
        lay.addWidget(split, 1)

    def browse(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Fleet index CSV", "", "CSV files (*.csv *.txt);;All files (*)")
        if fn:
            self.path.setText(fn)

    def run(self):
        p = Path(self.path.text().strip())
        if not p.exists():
            QMessageBox.warning(self, "File", "Choose an existing fleet index CSV."); return
        self.run_btn.setEnabled(False); self.export_btn.setEnabled(False)
        self.progress.setVisible(True); self.progress.setValue(0)
        self._worker = Worker(_screen, p, with_progress=True)
        self._worker.progress.connect(self._progress)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _progress(self, done, total, label):
        self.progress.setMaximum(total); self.progress.setValue(done)
        self.status.setText(f"{done} / {total}: {label}")

    def _failed(self, msg):
        self.run_btn.setEnabled(True); self.progress.setVisible(False)
        QMessageBox.critical(self, "Fleet screening failed", msg)

    def _done(self, payload):
        table, results = payload
        self.table_df, self.results = table, results
        self.run_btn.setEnabled(True); self.export_btn.setEnabled(True); self.progress.setVisible(False)
        counts = table["verdict"].value_counts().to_dict()
        self.status.setText(f"{len(table)} stations: " + ", ".join(f"{k} {v}" for k, v in counts.items())
                            + ". Select a row for details. Voltage and cable are not checked in this mode (no feeder model).")
        self.model.set_df(table[[c for c in SHOW if c in table.columns]])
        self.view.resizeColumnsToContents()
        for c in ("why", "notes"):
            if c in table.columns:
                self.view.setColumnWidth(list(self.model.df.columns).index(c), 320)

    def _selected(self, *_):
        rows = self.view.selectionModel().selectedRows()
        if not rows or self.table_df is None:
            return
        sid = str(self.model.df.iloc[rows[0].row()]["station_id"])
        res = self.results.get(sid)
        if res is None:
            self.card.set_verdict(None); return
        self.card.set_verdict(res.verdict, sid)
        amb = ambient_for(res.station.index, res.smhi_station)
        step_h = res.station.step_min / 60
        self.timeline.set_data(res.year.hourly, res.thermal.hourly, amb); self.timeline._moved()
        self.duration.set_year(res.year.hourly, step_h)
        self.week.set_week(res.thermal.worst_week(), res.year.hourly, amb, step_h)

    def export(self):
        if self.table_df is None:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Export ranked table", "fleet_screening.csv", "CSV (*.csv)")
        if fn:
            export_fleet_table(self.table_df, fn)
            self.status.setText(f"Exported to {fn}")
