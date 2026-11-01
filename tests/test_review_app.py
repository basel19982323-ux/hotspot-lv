"""The v0.2 review's findings on the app and the report command (31 Oct 2026), each as a test."""

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import hotspotlv.station as station_module  # noqa: E402
from hotspotlv import cli  # noqa: E402
from hotspotlv.app.report import read_report_html, station_card_html  # noqa: E402
from hotspotlv.app.widgets import station_panel as spm  # noqa: E402
from hotspotlv.paths import package_data  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "dso_exports"
EXAMPLE = package_data() / "examples" / "ns-102.csv"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def example_result(app):
    return spm._assess(EXAMPLE, 500, "meters", "auto", "start", 96190, True, "kiosk", 0, 0, 0)


# --- the station panel ------------------------------------------------------------------------------------------
def test_a_unit_chosen_for_one_file_does_not_carry_over_to_the_next(app):
    """A Unit set for a bare-Värde file, then an 'Effekt (kW)' file loaded: P came out ×4."""
    panel = spm.StationPanel()
    panel.unit.setCurrentIndex(panel.unit.findData("kWh"))
    panel.path.setText("another.csv")
    assert panel.unit.currentData() is None


def test_the_file_tab_shows_why_a_file_was_refused_not_the_last_file(app, example_result, monkeypatch):
    monkeypatch.setattr(spm, "show_error", lambda *a: None)
    panel = spm.StationPanel()
    panel._pending_inputs = panel._inputs()
    panel._done(example_result)
    assert "encoding" in panel.file_info.toPlainText()
    panel._failed("ValueError: P: 1 value cannot be read as a number (first: '400,0*', table row 2)")
    text = panel.file_info.toPlainText()
    assert "could not be read or assessed" in text and "400,0*" in text and "encoding" not in text


def test_a_chosen_station_is_named_on_the_card_and_can_be_changed(app, example_result, monkeypatch):
    panel = spm.StationPanel()
    panel._station = "NS 102 Ängen"
    panel._pending_inputs = panel._inputs()
    panel._done(example_result)
    assert panel.card._level.text().endswith("·  NS 102 Ängen · 500 kVA")
    assert panel.station_label.text() == "station: NS 102 Ängen" and panel.station_row.isVisibleTo(panel)
    runs = []
    monkeypatch.setattr(panel, "run", lambda: runs.append(panel._station))
    panel.other_station.click()
    assert runs == [None]                                        # asks again, for another station
    assert spm._slug("NS 101/Åkervägen") == "NS-101-Åkervägen"


def test_custom_losses_start_empty_and_are_required(app):
    """Pre-filled 500 kVA losses passed the checks for any rating, then read 'entered by the user'."""
    panel = spm.StationPanel()
    panel.kva.setCurrentIndex(panel.kva.findData("custom"))
    assert (panel.custom_kva.value(), panel.custom_p0.value(), panel.custom_pk.value()) == (0, 0, 0)
    with pytest.raises(ValueError, match="enter its rating and both losses"):
        panel.rating()
    panel.custom_uk.stepUp()
    assert panel.custom_uk.value() == 2.0                        # 'not known', then 2.0, 2.5 … — not 2.4, 2.9


def test_the_done_line_counts_what_the_file_could_not_tell(app, example_result):
    from dataclasses import replace
    res, amb = example_result
    told = replace(res, station=replace(res.station, unsure=["one thing", "another"]))
    panel = spm.StationPanel()
    panel._pending_inputs = panel._inputs()
    panel._done((told, amb))
    assert "2 things the file could not tell — see the File tab" in panel.status.text()
    panel._done(example_result)
    assert "could not tell" not in panel.status.text()


def test_the_file_tab_names_the_interval_convention_as_a_setting(example_result):
    res, _ = example_result
    page = read_report_html(res.station)
    assert "each stamp is the START of its interval" in page and "Nothing else had to be assumed" in page


def test_the_report_says_report_where_the_card_says_card(example_result):
    res, _ = example_result
    assert "What this report is not" in station_card_html(res, report=True)
    assert "What this card is not" in station_card_html(res)


# --- the report command ------------------------------------------------------------------------------------------
def test_every_option_reaches_the_reader_and_the_assessment(monkeypatch, tmp_path):
    seen = {}

    def fake_read(path, **kw):
        seen["read"] = kw
        raise ValueError("stop here")

    monkeypatch.setattr(station_module, "read_station_csv", fake_read)
    code = cli.main([str(EXAMPLE), "--kva", "500", "--station", "NS 7", "--tz", "normaltid", "--stamp", "end",
                     "--unit", "kWh", "--monthfirst", "--measured-at", "transformer", "--decimal", ",",
                     "--max-gap", "12", "--out", str(tmp_path / "r.html")])
    assert code == 1
    assert seen["read"] == dict(measured_at="transformer", tz="normaltid", stamp="end", unit="kWh", station="NS 7",
                                dayfirst=False, decimal=",", max_gap_steps=12)


def test_a_clock_typo_is_an_argument_error_and_an_unknown_size_fails_before_smhi(capsys, monkeypatch):
    with pytest.raises(SystemExit) as e:
        cli.parser().parse_args([str(EXAMPLE), "--kva", "500", "--tz", "Europe/Stokholm"])
    assert e.value.code == 2
    asked = []
    monkeypatch.setattr(station_module, "ambient_for", lambda *a, **k: asked.append(1))
    assert cli.main([str(EXAMPLE), "--kva", "300"]) == 1
    assert "no catalogue data for 300 kVA" in capsys.readouterr().out and not asked


def test_the_default_report_name_carries_the_station(tmp_path):
    """Running the command for station B after A overwrote A's report."""
    src = tmp_path / "fleet.csv"
    assert cli.default_out(src, "NS 101 Åkervägen") == tmp_path / "fleet-NS-101-Åkervägen-report.pdf"
    assert cli.default_out(src, "NS/2") == tmp_path / "fleet-NS-2-report.pdf"
    assert cli.default_out(src) == tmp_path / "fleet-report.pdf"


def test_without_a_console_the_command_writes_its_lines_next_to_the_report(monkeypatch, tmp_path):
    """The Windows build has no console: a refused file must still say why (v0.2.0 release check)."""
    out = tmp_path / "r.pdf"
    monkeypatch.setattr(sys, "stdout", None)
    assert cli.main([str(EXAMPLE), "--kva", "300", "--out", str(out)]) == 1
    log = (tmp_path / "r.pdf.log.txt").read_text(encoding="utf-8")
    assert "no catalogue data for 300 kVA" in log and log.rstrip().endswith("exit code 1")
    assert cli.log_path(cli.parser().parse_args([str(tmp_path / "s.csv"), "--kva", "500", "--station", "B 2"])) \
        == tmp_path / "s-B-2-report.pdf.log.txt"
