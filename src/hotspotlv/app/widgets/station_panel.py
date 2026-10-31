"""Mode B — a real station from the DSO's own CSV: file and options on the left, verdict and charts on the right.
Export PDF… writes the station card (app/report.py): the same verdict, numbers and charts plus the assumption list.
It is enabled only while the card on screen belongs to the inputs on the left (review of 22 Oct 2026: a card for
500 kVA was written while the panel already said 800 kVA and +50 EV)."""

from __future__ import annotations

import html
import re
import time
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from hotspotlv.app.report import read_report_html, write_station_card
from hotspotlv.app.widgets.charts import DurationCurve, WorstWeek, YearTimeline
from hotspotlv.app.widgets.verdict_card import VerdictCard
from hotspotlv.app.workers import Worker
from hotspotlv.catalogue import CUSTOM_KVA_RANGE, CUSTOM_UK_PERCENT, TRANSFORMERS, custom_transformer
from hotspotlv.loads.ev import EVParams
from hotspotlv.paths import package_data
from hotspotlv.station import (ManyStations, StationResult, ambient_for, assess_station, read_station_csv, to_hourly,
                               with_overlay)
from hotspotlv.verdict import thermal_ok
from hotspotlv.weather import SMHI_STATION, SMHI_STATION_NAME, SMHI_STATIONS_URL

EXAMPLE = package_data() / "examples" / "ns-102.csv"
KNOWN_SMHI = {96190: "Eskilstuna A", 97400: "Stockholm-Arlanda", 97510: "Uppsala Aut", 52350: "Malmö A",
              71420: "Göteborg A", 93230: "Karlstad Airport", 140480: "Umeå Flygplats", 162860: "Luleå-Kallax"}


def _energy_mwh(st) -> float:
    return float(st.p_kw.clip(lower=0).sum() * st.step_min / 60 / 1000)


def _assess(path, rated_kva, measured_at, tz, stamp, smhi_station, smhi_is_default, enclosure, n_ev, n_hp, seed,
            losses=None, unit=None, station=None):
    """The whole mode-B pipeline, run in the worker thread. losses: a custom unit (catalogue.custom_transformer);
    unit: the value column's unit when the file does not state it; station: one station of a multi-station file.
    A file of several stations with none chosen returns its ManyStations (the panel then asks which one)."""
    try:
        st = read_station_csv(path, measured_at=measured_at, tz=tz, stamp=stamp, unit=unit, station=station)
    except ManyStations as e:
        return e
    overlay = {}
    if n_ev or n_hp:
        measured = _energy_mwh(st)                               # from the file's own steps, before hourly means
        st = to_hourly(st)
        st = with_overlay(st, ambient_for(st.index, smhi_station), n_ev=n_ev, n_hp=n_hp, seed=seed)
        overlay = {"n_ev": n_ev, "n_hp": n_hp, "seed": seed, "charger_kw": EVParams().charger_kw,
                   "measured_mwh": measured}                     # so the card can tell measured from synthetic energy
    amb = ambient_for(st.index, smhi_station)
    res = assess_station(st, rated_kva, amb, smhi_station=None if smhi_is_default else smhi_station,
                         enclosure=enclosure, overlay=overlay, losses=losses)
    return res, amb


def _slug(text: str) -> str:
    """A station id as part of a file name: 'NS 101 Åkervägen' → 'NS-101-Åkervägen'."""
    return "-".join(re.sub(r'[\\/:*?"<>|]+', " ", str(text)).split()) or "station"


def show_error(parent, title: str, msg: str):
    """First line for the planner, the traceback behind 'Show details'."""
    from hotspotlv.errors import plain
    first = plain(msg)                                       # the planner's sentence, without the Python class name
    box = QMessageBox(QMessageBox.Critical, title, first, parent=parent)
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
        self.kva = QComboBox()                               # sizes whose losses are verified (catalogue.py) …
        for kva in sorted(TRANSFORMERS):
            self.kva.addItem(f"{kva} kVA", kva)
        self.kva.addItem("custom (enter the losses)", "custom")   # … or a unit whose losses the user knows
        self.kva.setCurrentIndex(self.kva.findData(500))
        self.kva.setToolTip("Nameplate rating of the transformer. The listed sizes use the Ecodesign Tier-2 maximum losses "
                            "(see the Assumptions tab). 'custom': another size, or a known unit's own losses from its "
                            "test report — other sizes are not guessed.")
        self.custom_box = QWidget(); fc = QFormLayout(self.custom_box); fc.setContentsMargins(0, 0, 0, 0)
        fc.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow); fc.setRowWrapPolicy(QFormLayout.WrapLongRows)
        # empty until entered (v0.2 review: values pre-filled for 500 kVA passed the checks for any rating and were
        # then reported as "entered by the user")
        self.custom_kva = QDoubleSpinBox(); self.custom_kva.setRange(0, CUSTOM_KVA_RANGE[1]); self.custom_kva.setDecimals(0)
        self.custom_kva.setSpecialValueText("enter the rating"); self.custom_kva.setSuffix(" kVA")
        self.custom_p0 = QDoubleSpinBox(); self.custom_p0.setRange(0, 50_000); self.custom_p0.setDecimals(0)
        self.custom_p0.setSpecialValueText("enter, in W"); self.custom_p0.setSuffix(" W")
        self.custom_p0.setToolTip("No-load (iron) loss from the unit's test report, in watts.")
        self.custom_pk = QDoubleSpinBox(); self.custom_pk.setRange(0, 200_000); self.custom_pk.setDecimals(0)
        self.custom_pk.setSpecialValueText("enter, in W"); self.custom_pk.setSuffix(" W")
        self.custom_pk.setToolTip("Load (copper) loss at rated current from the unit's test report, in watts.")
        self.custom_uk = QDoubleSpinBox(); self.custom_uk.setRange(1.5, 10); self.custom_uk.setDecimals(1)
        self.custom_uk.setSingleStep(0.5); self.custom_uk.setSuffix(" %")           # 1.5 = not known; then 2.0, 2.5 …
        self.custom_uk.setSpecialValueText(f"not known ({CUSTOM_UK_PERCENT:g} % assumed)"); self.custom_uk.setValue(1.5)
        self.custom_uk.setToolTip("Short-circuit impedance from the rating plate. It only sets the transformer's own "
                                  "reactive power here (a small part of S).")
        fc.addRow("Rating", self.custom_kva); fc.addRow("No-load loss", self.custom_p0)
        fc.addRow("Load loss", self.custom_pk); fc.addRow("uk", self.custom_uk)
        self.custom_box.setVisible(False)
        self.kva.currentIndexChanged.connect(lambda *_: self.custom_box.setVisible(self.kva.currentData() == "custom"))
        self.measured = QComboBox()
        self.measured.addItem("sum of customer meters", "meters"); self.measured.addItem("measured at the transformer", "transformer")
        self.measured.setToolTip("Sum of meters: cable and transformer losses are added to reach the transformer.\n"
                                 "Measured at the transformer: only the transformer's own losses are added; a kVA column is used as is.")
        self.tz = QComboBox()
        self.tz.addItem("detect from the file", "auto")
        self.tz.addItem("Swedish local time (with summer time)", "Europe/Stockholm"); self.tz.addItem("normaltid (CET all year)", "normaltid")
        self.tz.addItem("UTC (no summer time)", "UTC")
        self.tz.setToolTip("How timestamps without a UTC offset are meant. 'detect': Swedish local time, unless the file's "
                           "daylight-saving days show normaltid (CET all year: registered meter values are kept in "
                           "normaltid, EIFS 2025:1 1 kap. 2 §; values for customers carry the current time) — the File "
                           "tab says what was found, and when it could not be told. A clock chosen here is used as it "
                           "is: a file that does not fit it is refused with a hint, or read with the assumption stated.")
        self.unit = QComboBox()
        self.unit.addItem("from the file", None); self.unit.addItem("kW (mean power)", "kW")
        self.unit.addItem("kWh per interval", "kWh")
        self.unit.setToolTip("Only needed when the file does not say it (a bare 'Värde' column with quarter-hour values: "
                             "kW and kWh per 15 min differ by ×4). Otherwise the header or an Enhet column decides.")
        self._station = None                                # the station chosen from a multi-station file
        self.station_row = QWidget(); sr = QHBoxLayout(self.station_row); sr.setContentsMargins(0, 0, 0, 0)
        self.station_label = QLabel(""); self.station_label.setProperty("muted", True)
        self.other_station = QPushButton("Other station…"); self.other_station.setProperty("secondary", True)
        self.other_station.setToolTip("Assess another station of this file.")
        self.other_station.clicked.connect(self._other_station)
        sr.addWidget(self.station_label, 1); sr.addWidget(self.other_station)
        self.station_row.setVisible(False)
        self.stamp = QComboBox()
        self.stamp.addItem("timestamp = start of the interval", "start"); self.stamp.addItem("timestamp = end of the interval", "end")
        self.stamp.setToolTip("A row stamped 17:00: is it the hour 17–18 (start) or 16–17 (end)? Files using 24:00 are end-stamped.")
        self.smhi = QSpinBox(); self.smhi.setRange(1, 999999); self.smhi.setValue(SMHI_STATION)
        self.smhi.setToolTip("SMHI automatic station id for the outdoor temperature (find it on SMHI's station map, "
                             f"{SMHI_STATIONS_URL} → Lufttemperatur, timvärde). "
                             "The default is Eskilstuna A near Västerås — set the station nearest your place.")
        self.smhi_name = QLabel(""); self.smhi_name.setProperty("muted", True)
        self.smhi.valueChanged.connect(self._smhi_label); self._smhi_label()
        self.enclosure = QComboBox(); self.enclosure.addItems(["kiosk", "outdoor"])
        self.enclosure.setToolTip("Kiosk (the Swedish norm) adds half the IEC 60076-7:2005 Table 6 kiosk air rise to the top-oil rise (+7.5 K at 500 kVA, ageing ≈ ×2.4).")
        f1.addRow("Transformer rating", self.kva)
        f1.addRow(self.custom_box)
        f1.addRow(self.station_row)
        f1.addRow("Values are", self.measured)
        f1.addRow("Unit", self.unit)
        f1.addRow("Timestamps", self.tz)
        f1.addRow("", self.stamp)
        f1.addRow("SMHI station", self.smhi)
        f1.addRow("", self.smhi_name)
        f1.addRow("Enclosure", self.enclosure)
        note = QLabel("The rating, the SMHI station and the interval convention are yours to set; encoding, separator, "
                      "header, column names, units, decimal signs, date order, clock and gaps are read from the file — "
                      "the File tab lists what was found and what the file could not tell.")
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
        self.file_info = QTextBrowser(); self.file_info.setOpenLinks(False)
        self.file_info.setPlaceholderText("What was read from the file appears here after Assess.")
        self.tabs.addTab(self.file_info, "File")
        right.addWidget(self.tabs)
        self.tabs.setMinimumHeight(300)                     # a short window takes the height from the card (it scrolls),
                                                            # not from the charts: 52-px plots on a laptop before 22 Oct
        right.setStretchFactor(0, 0); right.setStretchFactor(1, 1); right.setSizes([360, 460])
        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setSizes([440, 900])
        self._shown_inputs = None                           # the inputs that produced the card on screen
        self._pending_inputs = None
        for w in (self.kva, self.measured, self.unit, self.tz, self.stamp, self.enclosure):
            w.currentIndexChanged.connect(self._update_export)
        self.path.textChanged.connect(self._forget_station)              # another file: the station and the unit
                                                                          # chosen for the last one do not carry over
        for w in (self.smhi, self.n_ev, self.n_hp, self.seed, self.custom_kva, self.custom_p0, self.custom_pk, self.custom_uk):
            w.valueChanged.connect(self._update_export)
        self.path.textChanged.connect(self._update_export)
        self._update_export()

    def _inputs(self) -> tuple:
        overlay = self.n_ev.value() or self.n_hp.value()          # the random draw matters only with an overlay
        custom = self._custom_values() if self.kva.currentData() == "custom" else None
        return (self.path.text().strip(), self.kva.currentData(), self.measured.currentData(), self.tz.currentData(),
                self.stamp.currentData(), self.smhi.value(), self.enclosure.currentText(), self.n_ev.value(),
                self.n_hp.value(), self.seed.value() if overlay else None, custom, self.unit.currentData(),
                self._station)

    def _forget_station(self, *_):
        self._station = None                                # another file: its stations are not this one's …
        self.station_row.setVisible(False)
        self.unit.setCurrentIndex(0)                        # … nor is a unit chosen for a file that did not say it

    def _other_station(self):
        self._station = None
        self.run()

    def _custom_values(self) -> tuple:
        uk = self.custom_uk.value()
        return (self.custom_kva.value(), self.custom_p0.value(), self.custom_pk.value(),
                None if uk == self.custom_uk.minimum() else uk)

    def rating(self) -> tuple[float, dict | None]:
        """(kVA, custom losses or None) from the inputs; a ValueError says what is wrong with custom values."""
        if self.kva.currentData() != "custom":
            return float(self.kva.currentData()), None
        kva, p0, pk, uk = self._custom_values()
        if not (kva and p0 and pk):
            raise ValueError("custom transformer: enter its rating and both losses (in watts, from the test report)")
        return kva, custom_transformer(kva, p0, pk, uk)

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
        self.smhi_name.setText((f"{name}" if name else "id not in the short list — check it on SMHI's station map (see the tooltip)")
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
        try:
            kva, losses = self.rating()
        except ValueError as e:
            show_error(self, "Custom transformer", str(e)); return
        self._pending_inputs = self._inputs()
        self._busy = True
        self.run_btn.setEnabled(False); self.export_btn.setEnabled(False)
        self.cancel_btn.setVisible(True); self.progress.setVisible(True)
        self._t0 = time.perf_counter(); self._timer.start(500)
        self.status.setText("Reading the file …")
        self.file_info.setHtml("<p>Reading the file …</p>")         # never the last file's read under this one
        self._worker = Worker(_assess, p, kva, self.measured.currentData(), self.tz.currentData(),
                              self.stamp.currentData(), self.smhi.value(), self.smhi.value() == SMHI_STATION,
                              self.enclosure.currentText(), self.n_ev.value(), self.n_hp.value(), self.seed.value(),
                              losses, self.unit.currentData(), self._station)
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
        from hotspotlv.errors import plain
        self.file_info.setHtml(f"<p><b>The file could not be read or assessed.</b></p><p>{html.escape(plain(msg))}</p>"
                               "<p>The reason above says what was found and what to change: the file, or an input on "
                               "the left (Unit, Timestamps, the rating).</p>")
        show_error(self, "Could not assess the station", msg)

    def _done(self, payload):
        if not self._current():
            return
        self._finish_ui()
        if isinstance(payload, ManyStations):                   # a multi-station file: which one?
            self._choose_station(payload)
            return
        try:
            res, amb = payload
            station = self._pending_inputs[-1] if self._pending_inputs else None
            self.card.set_verdict(res.verdict, (f"{station} · " if station else "") + f"{res.rated_kva:.0f} kVA"
                                  + (" (custom)" if res.losses else ""))
            self.file_info.setHtml(read_report_html(res.station))
            self.station_label.setText(f"station: {station}" if station else "")
            self.station_row.setVisible(bool(station))
            step_h = res.station.step_min / 60
            ok = thermal_ok(res.verdict)
            self.timeline.set_data(res.year.hourly, res.thermal.hourly if ok else None, amb)
            self.duration.set_year(res.year.hourly, step_h)
            self.week.set_week(res.thermal.worst_week(), res.year.hourly, amb, step_h, thermal_valid=ok)
            self.result, self._amb = res, amb                    # only now is this the card on screen
            self._shown_inputs = self._pending_inputs
            self._update_export()
            took = f"{time.perf_counter() - self._t0:.0f} s" if self._t0 else "a moment"
            n_unsure = len(res.station.unsure)
            told = (f" {n_unsure} thing{'s' if n_unsure != 1 else ''} the file could not tell — see the File tab."
                    if n_unsure else "")
            self.status.setText(f"Done in {took}. {res.station.period()}, "
                                f"{res.station.step_min:g}-min steps, {res.station.gaps_filled} missing steps filled."
                                f"{told} Notes are under the verdict; voltage and cable are not checked in this mode.")
        except Exception as e:  # never leave a half-drawn screen saying "Done" — nor exportable
            self.result, self._amb, self._shown_inputs = None, None, None
            self._update_export()
            self.status.setText("")
            show_error(self, "Could not show the result", f"{type(e).__name__}: {e}")

    def _choose_station(self, many: ManyStations):
        self.status.setText("")
        name, ok = QInputDialog.getItem(
            self, "Several stations in the file",
            f"The file holds {len(many.stations)} stations in column {many.column!r}. Assess which one?\n"
            "(To rank them all, use Fleet screening.)", many.stations, 0, False)
        if not ok:
            self.status.setText("Not assessed: the file holds several stations and none was chosen.")
            return
        self._station = name
        self.run()

    def export_pdf(self):
        """The station card of the result on screen. Runs on the GUI thread (about a second): the charts are
        Qt widgets and the PDF writer paints with Qt fonts, neither of which belongs in a worker."""
        if self.result is None or self._busy or self._inputs() != self._shown_inputs:
            return
        source = Path(self.result.station.source or self._shown_inputs[0])
        station = self._shown_inputs[-1]
        name = (source.name or "station") + (f" [{station}]" if station else "")
        folder = source.parent
        if not source.is_absolute() or folder.resolve() == EXAMPLE.parent.resolve():   # the shipped example lives
            folder = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))   # inside the program
        stem = (source.stem or "station") + (f"-{_slug(station)}" if station else "")
        suggested = folder / f"station-card-{stem}.pdf"                                     # next to the station's CSV
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
                                f"in the folder {out.parent.name or out.parent} (full path in the tooltip).")
            self.status.setToolTip(str(out))
