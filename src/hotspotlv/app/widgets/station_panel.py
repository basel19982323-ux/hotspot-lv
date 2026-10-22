"""Mode B — a real station from the DSO's own CSV: file and options on the left, verdict and charts on the right.
Export PDF… writes the station card (app/report.py): the same verdict, numbers and charts plus the assumption list.
It is enabled only while the card on screen belongs to the inputs on the left (review of 22 Oct 2026: a card for
500 kVA was written while the panel already said 800 kVA and +50 EV)."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from hotspotlv.app.report import write_station_card
from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline
from hotspotlv.app.widgets.verdict_card import VerdictCard
from hotspotlv.app.workers import Worker
from hotspotlv.catalogue import TRANSFORMERS
from hotspotlv.loads.ev import EVParams
from hotspotlv.paths import package_data
from hotspotlv.station import StationResult, ambient_for, assess_station, read_station_csv, to_hourly, with_overlay
from hotspotlv.verdict import thermal_ok
from hotspotlv.weather import SMHI_STATION, SMHI_STATION_NAME

EXAMPLE = package_data() / "examples" / "ns-102.csv"
KNOWN_SMHI = {96190: "Eskilstuna A", 97400: "Stockholm-Arlanda", 97510: "Uppsala Aut", 52350: "Malmö A",
              71420: "Göteborg A", 93230: "Karlstad Airport", 140480: "Umeå Flygplats", 162860: "Luleå-Kallax"}


def _energy_mwh(st) -> float:
    return float(st.p_kw.clip(lower=0).sum() * st.step_min / 60 / 1000)


def _assess(path, rated_kva, measured_at, tz, stamp, smhi_station, smhi_is_default, enclosure, n_ev, n_hp, seed):
    """The whole mode-B pipeline, run in the worker thread."""
    st = read_station_csv(path, measured_at=measured_at, tz=tz, stamp=stamp)
    overlay = {}
    if n_ev or n_hp:
        measured = _energy_mwh(st)                               # from the file's own steps, before hourly means
        st = to_hourly(st)
        st = with_overlay(st, ambient_for(st.index, smhi_station), n_ev=n_ev, n_hp=n_hp, seed=seed)
        overlay = {"n_ev": n_ev, "n_hp": n_hp, "seed": seed, "charger_kw": EVParams().charger_kw,
                   "measured_mwh": measured}                     # so the card can tell measured from synthetic energy
    amb = ambient_for(st.index, smhi_station)
    res = assess_station(st, rated_kva, amb, smhi_station=None if smhi_is_default else smhi_station,
                         enclosure=enclosure, overlay=overlay)
    return res, amb


def show_error(parent, title: str, msg: str):
    """First line for the planner, the traceback behind 'Show details'."""
    box = QMessageBox(QMessageBox.Critical, title, msg.split("\n")[0].replace("ValueError: ", ""), parent=parent)
    if "\n" in msg:
        box.setDetailedText(msg)
    box.exec()


class StationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: StationResult | None = None
        self._amb: pd.Series | None = None
        self._worker: Worker | None = None
        self._busy = False                                  # a run started and its result not yet shown or dropped
        self._t0 = 0.0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        split = QSplitter(Qt.Horizontal, self)
        lay = QHBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8); lay.addWidget(split)

        left_inner = QWidget(); ll = QVBoxLayout(left_inner); ll.setContentsMargins(0, 0, 4, 0)
        g1 = QGroupBox("Station data (CSV)")
        f1 = QFormLayout(g1)
        f1.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow); f1.setRowWrapPolicy(QFormLayout.WrapLongRows)
        row = QHBoxLayout()
        self.path = QLineEdit(); self.path.setPlaceholderText("timestamp + kW or kWh per hour / quarter hour …")
        self.path.setToolTip("Accepted: one timestamp column or Datum + Tid; kW, kWh, Wh, MWh with the unit in the header; "
                             "optional kvar / kVA; decimal comma; Swedish or English headers.")
        browse = QPushButton("Browse…"); browse.setProperty("secondary", True); browse.clicked.connect(self.browse)
        example = QPushButton("Example"); example.setProperty("secondary", True)
        example.setToolTip("A synthetic 80-house station (not a measurement) to try the tool")
        example.clicked.connect(lambda: self.path.setText(str(EXAMPLE)))
        row.addWidget(self.path, 1); row.addWidget(browse); row.addWidget(example)
        f1.addRow(row)
        self.kva = QComboBox()                               # only sizes whose losses are verified (catalogue.py)
        for kva in sorted(TRANSFORMERS):
            self.kva.addItem(f"{kva} kVA", kva)
        self.kva.setCurrentIndex(self.kva.findData(500))
        self.kva.setToolTip("Nameplate rating of the transformer. Only sizes whose Ecodesign Tier-2 losses are verified in the "
                            "catalogue are offered (v0.1: " + " and ".join(f"{k}" for k in sorted(TRANSFORMERS)) +
                            " kVA) — other sizes are not guessed.")
        self.measured = QComboBox()
        self.measured.addItem("sum of customer meters", "meters"); self.measured.addItem("measured at the transformer", "transformer")
        self.measured.setToolTip("Sum of meters: cable and transformer losses are added to reach the transformer.\n"
                                 "Measured at the transformer: only the transformer's own losses are added; a kVA column is used as is.")
        self.tz = QComboBox()
        self.tz.addItem("Swedish local time (with summer time)", "Europe/Stockholm"); self.tz.addItem("normaltid (CET all year)", "normaltid")
        self.tz.setToolTip("How tz-naive timestamps are meant. Registered meter values are often in normaltid (EIFS 2023:1); "
                           "a file that does not fit the chosen rule is refused with a hint, never guessed.")
        self.stamp = QComboBox()
        self.stamp.addItem("timestamp = start of the interval", "start"); self.stamp.addItem("timestamp = end of the interval", "end")
        self.stamp.setToolTip("A row stamped 17:00: is it the hour 17–18 (start) or 16–17 (end)? Files using 24:00 are end-stamped.")
        self.smhi = QSpinBox(); self.smhi.setRange(1, 999999); self.smhi.setValue(SMHI_STATION)
        self.smhi.setToolTip("SMHI automatic station id for the outdoor temperature (opendata.smhi.se → Lufttemperatur, timvärde). "
                             "The default is Eskilstuna A near Västerås — set the station nearest your place.")
        self.smhi_name = QLabel(""); self.smhi_name.setProperty("muted", True)
        self.smhi.valueChanged.connect(self._smhi_label); self._smhi_label()
        self.enclosure = QComboBox(); self.enclosure.addItems(["kiosk", "outdoor"])
        self.enclosure.setToolTip("Kiosk (the Swedish norm) adds half the IEC 60076-7:2005 Table 6 kiosk air rise to the top-oil rise (+7.5 K at 500 kVA, ageing ≈ ×2.4).")
        f1.addRow("Transformer rating", self.kva)
        f1.addRow("Values are", self.measured)
        f1.addRow("Timestamps", self.tz)
        f1.addRow("", self.stamp)
        f1.addRow("SMHI station", self.smhi)
        f1.addRow("", self.smhi_name)
        f1.addRow("Enclosure", self.enclosure)
        note = QLabel("The rating, the SMHI station and the timestamp convention are yours to set; column names, units, "
                      "decimal signs and gaps are read from the file and every assumption is written to the notes.")
        note.setWordWrap(True); note.setProperty("muted", True)
        f1.addRow(note)

        g2 = QGroupBox("What if more load is added (synthetic, upper bound)")
        f2 = QFormLayout(g2)
        f2.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow); f2.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.n_ev = QSpinBox(); self.n_ev.setRange(0, 500)
        self.n_ev.setToolTip("Adds this many EV chargers (11 kW, uncontrolled) from the scenario model on top of the measured profile.")
        self.n_hp = QSpinBox(); self.n_hp.setRange(0, 500)
        self.n_hp.setToolTip("Adds this many air/water heat-pump houses from the scenario model, using this station's own outdoor temperature.")
        self.seed = QSpinBox(); self.seed.setRange(0, 9999)
        self.seed.setToolTip("Random draw: which days the cars charge, how big each house is. Change it to see the spread.")
        f2.addRow("Extra EV chargers", self.n_ev)
        f2.addRow("Extra heat pumps", self.n_hp)
        f2.addRow("Random draw", self.seed)
        upper = QLabel("Upper bound: synthetic houses carry no base load, every charger is 11 kW, nothing else changes.")
        upper.setWordWrap(True); upper.setProperty("muted", True)
        f2.addRow(upper)

        btns = QHBoxLayout()
        self.run_btn = QPushButton("Assess this station"); self.run_btn.clicked.connect(self.run)
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setVisible(False)
        self.export_btn = QPushButton("Export PDF…"); self.export_btn.setProperty("secondary", True)
        self.export_btn.setToolTip("Save this station's card as a PDF: verdict, the two columns, the three charts, "
                                   "the notes and the full assumption list (thermal constants, rules, what was not checked).")
        self.export_btn.clicked.connect(self.export_pdf); self.export_btn.setEnabled(False)
        btns.addWidget(self.run_btn, 1); btns.addWidget(self.cancel_btn); btns.addWidget(self.export_btn)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.setVisible(False)
        self.status = QLabel(""); self.status.setProperty("muted", True); self.status.setWordWrap(True)
        ll.addWidget(g1); ll.addWidget(g2); ll.addStretch(1)
        scroll = QScrollArea(); scroll.setWidget(left_inner); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_inner.setMinimumWidth(0)
        # the buttons sit under the scrolling inputs, always on screen (on a laptop they were 110 px below the fold)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(scroll, 1); lv.addLayout(btns); lv.addWidget(self.progress); lv.addWidget(self.status)
        left.setMinimumWidth(340)

        right = QSplitter(Qt.Vertical)                      # drag the boundary between card and charts
        self.card = VerdictCard(empty_text="Choose a station CSV (or the example), set the rating and the SMHI station, "
                                            "then press Assess.")
        right.addWidget(self.card.scrolled())
        self.tabs = QTabWidget()
        self.timeline = YearTimeline(); self.duration = DurationCurve(); self.week = WorstWeek()
        self.tabs.addTab(self.timeline, "Period"); self.tabs.addTab(self.duration, "Duration curve"); self.tabs.addTab(self.week, "Worst week")
        right.addWidget(self.tabs)
        self.tabs.setMinimumHeight(300)                     # a short window takes the height from the card (it scrolls),
                                                            # not from the charts: 52-px plots on a laptop before 22 Oct
        right.setStretchFactor(0, 0); right.setStretchFactor(1, 1); right.setSizes([360, 460])
        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setSizes([440, 900])
        self._shown_inputs = None                           # the inputs that produced the card on screen
        self._pending_inputs = None
        for w in (self.kva, self.measured, self.tz, self.stamp, self.enclosure):
            w.currentIndexChanged.connect(self._update_export)
        for w in (self.smhi, self.n_ev, self.n_hp, self.seed):
            w.valueChanged.connect(self._update_export)
        self.path.textChanged.connect(self._update_export)
        self._update_export()

    def _inputs(self) -> tuple:
        overlay = self.n_ev.value() or self.n_hp.value()          # the random draw matters only with an overlay
        return (self.path.text().strip(), self.kva.currentData(), self.measured.currentData(), self.tz.currentData(),
                self.stamp.currentData(), self.smhi.value(), self.enclosure.currentText(), self.n_ev.value(),
                self.n_hp.value(), self.seed.value() if overlay else None)

    def _update_export(self, *_):
        if self.result is None:
            ok, tip = False, "Assess a station first: the PDF is the card of the result on screen."
        elif self._busy:                                    # not the thread's isRunning: the result arrives before it ends
            ok, tip = False, "Wait for the run to finish."
        elif self._inputs() != self._shown_inputs:
            ok, tip = False, ("The inputs on the left changed since this result — press Assess (or set them back) "
                              "so the PDF and the inputs agree.")
        else:
            ok, tip = True, ("Save this station's card as a PDF: verdict, the two columns, the three charts, the notes and "
                             "the full assumption list (thermal constants, rules, what was not checked).")
        self.export_btn.setEnabled(ok)
        self.export_btn.setToolTip(tip)

    def _smhi_label(self):
        sid = self.smhi.value()
        name = KNOWN_SMHI.get(sid)
        self.smhi_name.setText((f"{name}" if name else "id not in the short list — check it on opendata.smhi.se")
                               + (" (default: Västerås region)" if sid == SMHI_STATION else ""))

    def browse(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Station CSV", "", "CSV files (*.csv *.txt);;All files (*)")
        if fn:
            self.path.setText(fn)

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def run(self):
        if self.is_running() or self._busy:
            return
        p = Path(self.path.text().strip())
        if not p.exists():
            QMessageBox.warning(self, "File", "Choose an existing CSV file."); return
        self._pending_inputs = self._inputs()
        self._busy = True
        self.run_btn.setEnabled(False); self.export_btn.setEnabled(False)
        self.cancel_btn.setVisible(True); self.progress.setVisible(True)
        self._t0 = time.perf_counter(); self._timer.start(500)
        self.status.setText("Reading the file …")
        self._worker = Worker(_assess, p, self.kva.currentData(), self.measured.currentData(), self.tz.currentData(),
                              self.stamp.currentData(), self.smhi.value(), self.smhi.value() == SMHI_STATION,
                              self.enclosure.currentText(), self.n_ev.value(), self.n_hp.value(), self.seed.value())
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _tick(self):
        self.status.setText(f"Working … {time.perf_counter() - self._t0:.0f} s (reading, SMHI temperature, thermal year)")

    def _finish_ui(self):
        self._timer.stop(); self.progress.setVisible(False); self.cancel_btn.setVisible(False); self.run_btn.setEnabled(True)
        self._busy = False
        self._update_export()                                   # the card on screen stays exportable while it matches

    def cancel(self):
        w, self._worker = self._worker, None           # retire it: a late result from it is ignored
        if w is not None and w.isRunning():
            w.cancel()
        self._finish_ui()
        self.status.setText("Cancelled.")

    def _current(self) -> bool:
        return self.sender() is None or self.sender() is self._worker

    def _failed(self, msg: str):
        if not self._current():
            return
        self._finish_ui(); self.status.setText("")
        show_error(self, "Could not assess the station", msg)

    def _done(self, payload):
        if not self._current():
            return
        self._finish_ui()
        try:
            res, amb = payload
            self.card.set_verdict(res.verdict, f"{res.rated_kva:.0f} kVA")
            step_h = res.station.step_min / 60
            ok = thermal_ok(res.verdict)
            self.timeline.set_data(res.year.hourly, res.thermal.hourly if ok else None, amb)
            self.duration.set_year(res.year.hourly, step_h)
            self.week.set_week(res.thermal.worst_week(), res.year.hourly, amb, step_h, thermal_valid=ok)
            self.result, self._amb = res, amb                    # only now is this the card on screen
            self._shown_inputs = self._pending_inputs
            self._update_export()
            took = f"{time.perf_counter() - self._t0:.0f} s" if self._t0 else "a moment"
            self.status.setText(f"Done in {took}. {res.station.period()}, "
                                f"{res.station.step_min:g}-min steps, {res.station.gaps_filled} missing steps filled. "
                                "Notes are under the verdict; voltage and cable are not checked in this mode.")
        except Exception as e:  # never leave a half-drawn screen saying "Done" — nor exportable
            self.result, self._amb, self._shown_inputs = None, None, None
            self._update_export()
            self.status.setText("")
            show_error(self, "Could not show the result", f"{type(e).__name__}: {e}")

    def export_pdf(self):
        """The station card of the result on screen. Runs on the GUI thread (about a second): the charts are
        Qt widgets and the PDF writer paints with Qt fonts, neither of which belongs in a worker."""
        if self.result is None or self._busy or self._inputs() != self._shown_inputs:
            return
        source = Path(self.result.station.source or self._shown_inputs[0])
        name = source.name or "station"
        folder = source.parent
        if not source.is_absolute() or folder.resolve() == EXAMPLE.parent.resolve():   # the shipped example lives
            folder = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))   # inside the program
        suggested = folder / f"station-card-{source.stem or 'station'}.pdf"                # next to the station's CSV
        fn, _ = QFileDialog.getSaveFileName(self, "Save the station card", str(suggested), "PDF (*.pdf)")
        if not fn:
            return
        if not fn.lower().endswith(".pdf"):
            fn += ".pdf"
        self.export_btn.setEnabled(False); self.run_btn.setEnabled(False)   # no second export, no run meanwhile
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        error = None
        try:
            out = write_station_card(self.result, self._amb, fn, source_name=name)
        except OSError as e:                                   # file or folder problem: the sentence says what to do
            error = str(e)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        finally:
            QApplication.restoreOverrideCursor()               # before any dialog: no wait cursor over a message
            self.run_btn.setEnabled(True)
            self._update_export()
        if error:
            self.status.setText("The station card was not saved."); self.status.setToolTip("")
            show_error(self, "Could not write the PDF", error)
        else:
            self.status.setText(f"Station card for {name} ({self.result.rated_kva:g} kVA) saved as {out.name} "
                                f"in {out.parent}.")
            self.status.setToolTip(str(out))
