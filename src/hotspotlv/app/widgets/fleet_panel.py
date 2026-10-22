"""Mode C — a fleet index CSV → ranked table; select a row to see that station's verdict and charts."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QSplitter, QTableView, QTabWidget, QVBoxLayout, QWidget)

from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline
from hotspotlv.app.widgets.station_panel import show_error
from hotspotlv.app.widgets.tables import DataFrameModel
from hotspotlv.app.widgets.verdict_card import VerdictCard
from hotspotlv.app.workers import Worker
from hotspotlv.verdict import thermal_ok
from hotspotlv.fleet import RESULT_COLUMNS, export_fleet_table, read_fleet_index, screen_fleet
from hotspotlv.paths import package_data
from hotspotlv.station import ambient_for

EXAMPLE = package_data() / "examples" / "fleet_index.csv"
SHOW = ["station_id", "verdict", "why", "kVA", "peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year",
        "max_hot_spot_c", "max_load_pu", "energy_mwh", "period", "smhi_station", "notes", "error"]
RANKING = ("Ranked At risk → Watch → OK (errors last), then by days of life used, then by peak loading. "
           "Voltage and cable are not checked in this mode (no feeder model) — every row says so in 'why'. "
           "Click a column header to sort; the rank column keeps the original order.")


def _screen(path, progress):
    return screen_fleet(read_fleet_index(path), progress=progress)


class FleetPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.table_df: pd.DataFrame | None = None
        self.results = {}
        self._ambient_cache = {}
        self._worker: Worker | None = None
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8)

        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("fleet index CSV: station_id, rated_kva, file [, smhi_station, enclosure, measured_at, tz, stamp]")
        self.path.setToolTip("One row per station; 'file' is that station's profile CSV, relative to the index file. "
                             "Optional columns override the defaults per station.")
        browse = QPushButton("Browse…"); browse.setProperty("secondary", True); browse.clicked.connect(self.browse)
        example = QPushButton("Example"); example.setProperty("secondary", True)
        example.setToolTip("Three synthetic Västerås stations (not measurements)")
        example.clicked.connect(lambda: self.path.setText(str(EXAMPLE)))
        self.run_btn = QPushButton("Screen the fleet"); self.run_btn.clicked.connect(self.run)
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setVisible(False)
        self.export_btn = QPushButton("Export table…"); self.export_btn.setProperty("secondary", True)
        self.export_btn.clicked.connect(self.export); self.export_btn.setEnabled(False)
        row.addWidget(self.path, 1); row.addWidget(browse); row.addWidget(example)
        row.addWidget(self.run_btn); row.addWidget(self.cancel_btn); row.addWidget(self.export_btn)
        lay.addLayout(row)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        self.rule = QLabel(RANKING); self.rule.setProperty("muted", True); self.rule.setWordWrap(True)
        self.status = QLabel("No fleet screened yet. Pick an index CSV or press Example."); self.status.setProperty("muted", True)
        lay.addWidget(self.progress); lay.addWidget(self.rule); lay.addWidget(self.status)

        split = QSplitter(Qt.Vertical)
        self.view = QTableView()
        self.model = DataFrameModel(decimals=2)
        self.model.set_df(pd.DataFrame(columns=SHOW))            # headers visible before any run
        self.proxy = QSortFilterProxyModel(); self.proxy.setSourceModel(self.model); self.proxy.setSortRole(Qt.DisplayRole)
        self.view.setModel(self.proxy)
        self.view.setAlternatingRowColors(True); self.view.setSelectionBehavior(QTableView.SelectRows)
        self.view.setSortingEnabled(True)
        self.view.horizontalHeader().setSortIndicatorShown(True)
        self.view.selectionModel().selectionChanged.connect(self._selected)
        split.addWidget(self.view)
        detail = QSplitter(Qt.Horizontal)                   # card beside the charts: the table keeps its rows and
                                                            # the charts their height (20-px plots before 22 Oct 2026)
        self.card = VerdictCard(empty_text="Select a station in the table to see its verdict and charts.")
        detail.addWidget(self.card.scrolled())
        self.tabs = QTabWidget()
        self.timeline = YearTimeline(); self.duration = DurationCurve(); self.week = WorstWeek()
        self.tabs.addTab(self.timeline, "Period"); self.tabs.addTab(self.duration, "Duration curve"); self.tabs.addTab(self.week, "Worst week")
        detail.addWidget(self.tabs)
        self.tabs.setMinimumHeight(260); self.tabs.setMinimumWidth(420)
        detail.setStretchFactor(0, 0); detail.setStretchFactor(1, 1); detail.setSizes([420, 900])
        split.addWidget(detail)
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 2); split.setSizes([260, 520])
        lay.addWidget(split, 1)

    def browse(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Fleet index CSV", "", "CSV files (*.csv *.txt);;All files (*)")
        if fn:
            self.path.setText(fn)

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def run(self):
        if self.is_running():
            return
        p = Path(self.path.text().strip())
        if not p.exists():
            QMessageBox.warning(self, "File", "Choose an existing fleet index CSV."); return
        self.run_btn.setEnabled(False); self.export_btn.setEnabled(False); self.cancel_btn.setVisible(True)
        self.progress.setVisible(True); self.progress.setRange(0, 0)
        self._worker = Worker(_screen, p, with_progress=True)
        self._worker.progress.connect(self._progress)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _progress(self, done, total, label):
        if not self._current():                         # a tick queued before Cancel must not overwrite "Cancelled."
            return
        self.progress.setRange(0, total); self.progress.setValue(done)
        self.status.setText(f"{done} / {total}: {label}")

    def _finish_ui(self):
        self.run_btn.setEnabled(True); self.cancel_btn.setVisible(False); self.progress.setVisible(False)
        self.export_btn.setEnabled(self.table_df is not None)      # the table on screen stays exportable

    def cancel(self):
        w, self._worker = self._worker, None
        if w is not None and w.isRunning():
            w.cancel()
        self._finish_ui(); self.status.setText("Cancelled.")

    def _current(self) -> bool:
        return self.sender() is None or self.sender() is self._worker

    def _failed(self, msg):
        if not self._current():
            return
        self._finish_ui()
        show_error(self, "Fleet screening failed", msg)

    def _done(self, payload):
        if not self._current():
            return
        self._finish_ui()
        try:
            table, results = payload
            self.table_df, self.results, self._ambient_cache = table, results, {}
            self.export_btn.setEnabled(True)
            counts = table["verdict"].value_counts().to_dict()
            self.status.setText(f"{len(table)} stations: " + ", ".join(f"{k} {v}" for k, v in counts.items())
                                + ". Select a row for details.")
            self.model.set_df(table[[c for c in SHOW if c in table.columns]])
            self.view.sortByColumn(-1, Qt.AscendingOrder)
            self.view.resizeColumnsToContents()
            for c in ("why", "notes"):
                if c in self.model.df.columns:
                    self.view.setColumnWidth(list(self.model.df.columns).index(c), 360)
        except Exception as e:
            show_error(self, "Could not show the table", f"{type(e).__name__}: {e}")

    def _selected(self, *_):
        rows = self.view.selectionModel().selectedRows()
        if not rows or self.table_df is None:
            return
        try:
            src = self.proxy.mapToSource(rows[0]).row()
            sid = str(self.model.df.iloc[src]["station_id"])
            res = self.results.get(sid)
            if res is None:
                err = str(self.model.df.iloc[src].get("error", ""))
                self.card.set_verdict(None)
                self.card._headline.setText(f"{sid}: could not be assessed — {err}")
                for w in (self.timeline, self.duration, self.week):
                    w.setVisible(False)
                return
            for w in (self.timeline, self.duration, self.week):
                w.setVisible(True)
            self.card.set_verdict(res.verdict, sid)
            if sid not in self._ambient_cache:
                self._ambient_cache[sid] = ambient_for(res.station.index, res.smhi_station)
            amb = self._ambient_cache[sid]
            step_h = res.station.step_min / 60
            ok = thermal_ok(res.verdict)
            self.timeline.set_data(res.year.hourly, res.thermal.hourly if ok else None, amb)
            self.duration.set_year(res.year.hourly, step_h)
            self.week.set_week(res.thermal.worst_week(), res.year.hourly, amb, step_h, thermal_valid=ok)
        except Exception as e:
            show_error(self, "Could not show the station", f"{type(e).__name__}: {e}")

    def export(self):
        if self.table_df is None:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Export ranked table", "fleet_screening.csv", "CSV (*.csv)")
        if fn:
            out = self.table_df.copy()
            for c in ("hours_above_80", "hours_above_100", "kVA", "smhi_station", "gaps_filled"):
                if c in out.columns:
                    out[c] = out[c].round(0).astype("Int64")
            export_fleet_table(out, fn)
            self.status.setText(f"Exported to {fn}")
