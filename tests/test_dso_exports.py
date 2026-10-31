"""Files shaped like real Swedish DSO meter-data exports (tests/fixtures/dso_exports, made by make_fixtures.py
from one known load): each must be read to the right UTC instants and kW, and the reader must say what it
found and what the file could not tell."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import hotspotlv.fleet as fleet
from hotspotlv.fleet import read_fleet, read_fleet_long, screen_fleet
from hotspotlv.station import read_station_csv

FIX = Path(__file__).resolve().parent / "fixtures" / "dso_exports"
sys.path.insert(0, str(FIX))
from make_fixtures import kw_at  # noqa: E402


def read(name, **kw):
    return read_station_csv(FIX / name, **kw)


def found(st, key):
    return dict(st.found)[key]


def assert_truth(st, factor=1.0, station=0):
    """Every step read back equals the load the file was made from, at the right UTC instant."""
    assert st.index.tz is not None and (np.diff(st.index.asi8) == st.step_min * 60e9).all()   # one uniform grid
    assert np.allclose(st.p_kw.to_numpy(), kw_at(st.index, station) * factor, atol=1e-9)


def test_every_fixture_is_tested():
    names = {p.name for p in FIX.iterdir() if p.suffix in (".csv", ".txt")}
    tested = set(Path(__file__).read_text(encoding="utf-8").split('"')) & names
    assert names == tested, names - tested


def test_windows_1252_with_a_preamble_and_the_spring_change(tmp_path):
    st = read("hourly_kwh_cp1252_preamble_spring.csv")
    assert found(st, "encoding") == "Windows-1252" and found(st, "separator") == "semicolon (;)"
    assert found(st, "header").startswith("line 6: Tidpunkt;Energi (kWh) (4 lines above it left out)")
    assert found(st, "timestamps") == "column 'Tidpunkt': YYYY-MM-DD hh:mm"
    assert "no row in the spring hour that does not exist" in found(st, "clock")
    assert found(st, "values") == "column 'Energi (kWh)': kWh per 60-min step → mean kW (÷ 1 h)"
    assert found(st, "numbers").startswith("decimal comma")
    assert st.step_min == 60 and len(st.p_kw) == 71 and st.gaps_filled == 0      # 3 days, the 23-hour Sunday
    assert_truth(st)
    assert not any("clock" in u for u in st.unsure)


def test_utf8_bom_quarter_hours_and_the_autumn_hour_twice():
    st = read("quarter_hour_kw_utf8bom_autumn.csv")
    assert found(st, "encoding") == "UTF-8 with BOM" and found(st, "resolution") == "15-min values"
    assert "appears twice: the first is summer time (+02:00), the second winter time (+01:00)" in found(st, "clock")
    assert len(st.p_kw) == 3 * 96 + 4 and st.gaps_filled == 0                     # the 25-hour Sunday
    assert_truth(st)


def test_day_first_slash_dates_comma_separated():
    st = read("hourly_kw_slash_dates_dayfirst.csv")
    assert found(st, "separator") == "comma (,)"
    assert found(st, "timestamps") == "column 'Datum': DD/MM/YYYY hh:mm (day first)"
    assert found(st, "numbers").startswith("decimal point (shown by")
    assert found(st, "values").startswith("column 'Aktiv effekt (kW)': kW")
    assert_truth(st)
    assert any("normaltid cannot be told apart" in u for u in st.unsure)        # no DST change in January
    assert any(n.startswith("not told by the file — clock") for n in st.notes)   # … and the planner sees it


def test_iso_offsets_and_kwh_per_quarter_hour():
    st = read("quarter_hour_kwh_iso_offsets.csv")
    assert found(st, "timestamps") == "column 'Tidpunkt': ISO 8601 (YYYY-MM-DDThh:mm:ss±hh:mm)"
    assert found(st, "clock") == "UTC offsets in the stamps, read as given"
    assert found(st, "values") == "column 'Energi (kWh)': kWh per 15-min step → mean kW (÷ 0.25 h)"
    assert_truth(st)                                                             # kWh per 15 min × 4 = kW
    assert not st.unsure


@pytest.mark.parametrize("name", ["hourly_kwh_spring_zero_row.csv", "hourly_kwh_spring_duplicate_row.csv"])
def test_a_placeholder_row_in_the_spring_hour_is_dropped_and_said(name):
    st = read(name)
    assert len(st.p_kw) == 71 and st.gaps_filled == 0
    assert_truth(st)
    assert any("spring hour that does not exist" in n and "dropped" in n for n in st.notes)


def test_the_autumn_hour_written_once_is_summer_time_and_the_other_a_filled_gap():
    st = read("hourly_kw_autumn_hour_once.csv")
    assert len(st.p_kw) == 73 and st.gaps_filled == 1
    gap = pd.Timestamp("2025-10-26 01:00", tz="UTC")                            # 02:00 CET, the second 02:00
    ok = st.index != gap
    assert np.allclose(st.p_kw[ok].to_numpy(), kw_at(st.index[ok]))
    assert "appears only once: taken as summer time" in found(st, "clock")
    assert any("twice too high" in n for n in st.notes)
    assert any("a single October hour fits local time with a dropped hour and normaltid alike" in u for u in st.unsure)


def test_normaltid_is_detected_from_a_measured_spring_hour():
    st = read("hourly_kwh_normaltid_spring.csv")
    assert found(st, "clock").startswith("normaltid (CET all year) — detected")
    assert len(st.p_kw) == 72 and st.gaps_filled == 0
    assert_truth(st)
    assert any("alone (the file has no October change)" in u and "UTC without offsets" in u for u in st.unsure)
    with pytest.raises(ValueError, match="normaltid"):
        read("hourly_kwh_normaltid_spring.csv", tz="Europe/Stockholm")         # local time chosen: refused, explained
    assert read("hourly_kwh_normaltid_spring.csv", tz="normaltid").index.equals(st.index)


def test_utf16_tab_separated_with_a_value_and_a_unit_column_and_a_sum_line():
    st = read("quarter_hour_wh_utf16_tab_value_unit.txt")
    assert found(st, "encoding").startswith("UTF-16") and found(st, "separator") == "tab"
    assert found(st, "timestamps") == "columns 'Datum' + 'Tid': YYYY-MM-DD hh:mm"
    assert found(st, "unit") == "wh — from column 'Enhet'"
    assert found(st, "below the table").startswith("1 line left out (first: 'Summa")
    assert_truth(st)                                                             # Wh per 15 min / 250 = kW
    assert len(st.p_kw) == 2 * 96


def test_thousands_dots_dotted_dates_and_a_footer():
    st = read("hourly_kwh_thousands_dots_footer.csv")
    assert found(st, "timestamps") == "column 'Tidpunkt': DD.MM.YYYY hh:mm (day first)"
    assert found(st, "numbers").startswith("decimal comma (shown by")                  # so '1.226,5' and '1.210'
    assert st.p_kw.max() > 1000                                                          # are thousands, not 1.2
    assert "'Summa;" in found(st, "below the table")
    assert_truth(st, factor=10)


# --- several stations in one file -------------------------------------------------------------------------
def test_a_file_of_several_stations_names_them_and_one_can_be_picked():
    with pytest.raises(ValueError, match="3 stations in column 'Nätstation'.*NS 101 Åkervägen"):
        read("multi_station_natstation.csv")
    st = read("multi_station_natstation.csv", station="NS 102 Ängen")
    assert found(st, "station") == "'NS 102 Ängen' (column 'Nätstation')"
    assert_truth(st, station=1)
    with pytest.raises(ValueError, match="not in column"):
        read("multi_station_natstation.csv", station="NS 999")


def test_the_fleet_reads_the_same_file_with_each_stations_own_rating(monkeypatch):
    stations = read_fleet_long(FIX / "multi_station_natstation.csv")
    assert [(s.station_id, s.rated_kva) for s in stations] == [("NS 101 Åkervägen", 315.0), ("NS 102 Ängen", 500.0),
                                                                ("NS 103 Öster", 800.0)]
    for n, s in enumerate(stations):
        assert s.error is None
        assert_truth(s.series, station=n)
    assert [s.station_id for s in read_fleet(FIX / "multi_station_natstation.csv")] == [s.station_id for s in stations]
    monkeypatch.setattr(fleet, "ambient_for", lambda index, smhi=None: pd.Series(-5.0, index=index))   # offline
    table, results = screen_fleet(stations)
    assert (table["error"] == "").all() and set(results) == {s.station_id for s in stations}
    assert set(table["kVA"]) == {315.0, 500.0, 800.0}


def test_a_long_file_without_ratings_takes_a_default_or_says_what_is_missing(tmp_path):
    text = (FIX / "multi_station_natstation.csv").read_bytes().decode("cp1252")
    lines = [";".join(l.split(";")[:1] + l.split(";")[2:]) for l in text.splitlines()]   # the rating column removed
    (tmp_path / "f.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    stations = read_fleet_long(tmp_path / "f.csv")                                     # one error row each,
    assert all(s.series is None and "no rated_kva for station" in s.error for s in stations)   # not an abort
    assert {s.rated_kva for s in read_fleet(tmp_path / "f.csv", default_kva=500)} == {500.0}


# --- what the file cannot tell ----------------------------------------------------------------------------
def _hours(tmp_path, stamps, header="Tidpunkt;Värde", values=None, name="x.csv"):
    values = values if values is not None else [f"{100 + i},5" for i in range(len(stamps))]
    (tmp_path / name).write_text("\n".join([header] + [f"{t};{v}" for t, v in zip(stamps, values)]) + "\n",
                                 encoding="utf-8")
    return tmp_path / name


def test_a_bare_value_column_needs_a_unit_below_the_hour(tmp_path):
    q = pd.date_range("2025-02-03 00:00", periods=8, freq="15min").strftime("%Y-%m-%d %H:%M")
    path = _hours(tmp_path, q)
    with pytest.raises(ValueError, match="does not say its unit.*×4"):
        read_station_csv(path)
    st = read_station_csv(path, unit="kWh")
    assert st.p_kw.iloc[0] == pytest.approx(100.5 * 4) and found(st, "unit") == "kWh — as chosen"
    assert read_station_csv(path, unit="kW").p_kw.iloc[0] == pytest.approx(100.5)
    h = _hours(tmp_path, pd.date_range("2025-02-03", periods=6, freq="h").strftime("%Y-%m-%d %H:%M"), name="h.csv")
    st = read_station_csv(h)                                                     # hourly: kWh/h = mean kW, said so
    assert st.p_kw.iloc[0] == pytest.approx(100.5) and any("same number" in u for u in st.unsure)
    with pytest.raises(ValueError, match="unit must be"):
        read_station_csv(h, unit="kvar")


def test_day_and_month_order(tmp_path):
    one_day = [f"03/02/2025 {h:02d}:00" for h in range(24)]                     # 3 Feb or 2 Mar: cannot be told
    st = read_station_csv(_hours(tmp_path, one_day, "Tidpunkt;Energi (kWh)"))
    assert st.index[0].tz_convert("Europe/Stockholm").month == 2 and any("day first assumed" in u for u in st.unsure)
    st = read_station_csv(tmp_path / "x.csv", dayfirst=False)
    assert st.index[0].tz_convert("Europe/Stockholm").month == 3 and not any("day first" in u for u in st.unsure)
    us = [f"01/{d:02d}/2025 {h:02d}:00" for d in (12, 13) for h in range(24)]  # 01/13: month first, shown by 13
    st = read_station_csv(_hours(tmp_path, us, "Tidpunkt;Energi (kWh)", name="us.csv"))
    assert found(st, "timestamps").endswith("MM/DD/YYYY hh:mm (month first)")
    regular = [f"{d:02d}/01/2025 {h:02d}:00" for d in range(1, 4) for h in range(24)]   # all ≤ 12: the grid decides
    st = read_station_csv(_hours(tmp_path, regular, "Tidpunkt;Energi (kWh)", name="r.csv"))
    assert found(st, "timestamps").endswith("(day first)") and not any("day first" in u for u in st.unsure)
    with pytest.raises(ValueError, match="dayfirst=True does not fit"):
        read_station_csv(tmp_path / "us.csv", dayfirst=True)


def test_a_spring_hour_with_values_in_a_file_that_repeats_the_october_hour_is_a_fill(tmp_path):
    spring = [f"2025-03-30 {h:02d}:00" for h in range(24)]                       # with 02:00, which does not exist
    autumn = [f"2025-10-26 {h:02d}:00" for h in range(24)]
    autumn.insert(3, "2025-10-26 02:00")                                           # the October hour twice
    st = read_station_csv(_hours(tmp_path, spring, "Tidpunkt;Energi (kWh)"))
    assert found(st, "clock").startswith("normaltid")                          # spring alone: normaltid
    both = _hours(tmp_path, spring + autumn, "Tidpunkt;Energi (kWh)", name="both.csv")
    with pytest.raises(ValueError, match="gap"):                               # half a year apart: one long gap …
        read_station_csv(both)
    st = read_station_csv(both, max_gap_steps=10_000)                          # … allowed here to see the clock
    assert "appears twice" in found(st, "clock")
    assert any("spring hour" in n and "filled values" in n for n in st.notes)


def test_stamps_from_0100_to_0000_hint_at_interval_end(tmp_path):
    stamps = [f"2025-02-03 {h:02d}:00" for h in range(1, 24)] + ["2025-02-04 00:00"]
    st = read_station_csv(_hours(tmp_path, stamps, "Tidpunkt;Energi (kWh)"))
    assert any("typical of stamps at the interval END" in u for u in st.unsure)
    assert not any("interval END" in u for u in read_station_csv(tmp_path / "x.csv", stamp="end").unsure)


def test_a_table_without_a_header_row_is_refused(tmp_path):
    (tmp_path / "n.csv").write_text("2025-02-03 00:00;1,5\n2025-02-03 01:00;1,5\n2025-02-03 02:00;1,5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no header row"):
        read_station_csv(tmp_path / "n.csv")


# --- in the app -------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_station_panel_asks_which_station_of_a_multi_station_file(app, monkeypatch):
    from hotspotlv.app.widgets import station_panel as spm
    many = spm._assess(FIX / "multi_station_natstation.csv", 500, "meters", "auto", "start", 96190, True, "kiosk", 0, 0, 0)
    assert isinstance(many, spm.ManyStations) and many.stations == ["NS 101 Åkervägen", "NS 102 Ängen", "NS 103 Öster"]
    panel = spm.StationPanel()
    runs = []
    monkeypatch.setattr(panel, "run", lambda: runs.append(panel._station))
    monkeypatch.setattr(spm.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("NS 102 Ängen", True)))
    panel._done(many)
    assert runs == ["NS 102 Ängen"] and panel._inputs()[-1] == "NS 102 Ängen"
    panel.path.setText("another.csv")                                   # another file: the choice is forgotten
    assert panel._station is None
    monkeypatch.setattr(spm.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    panel._done(many)
    assert runs == ["NS 102 Ängen"] and "none was chosen" in panel.status.text()


def test_the_file_tab_shows_what_was_read_and_what_could_not_be_told(app):
    from hotspotlv.app.report import read_report_html
    from hotspotlv.app.widgets import station_panel as spm
    from hotspotlv.paths import package_data
    panel = spm.StationPanel()
    panel._done(spm._assess(package_data() / "examples" / "ns-102.csv", 500, "meters", "auto", "start", 96190, True,
                            "kiosk", 0, 0, 0))
    text = panel.file_info.toPlainText()
    assert "encoding" in text and "resolution" in text and "period" in text
    unsure = read_report_html(read("hourly_kw_slash_dates_dayfirst.csv"))
    assert "What the file could not tell" in unsure and "normaltid cannot be told apart" in unsure
    assert "Nothing had to be assumed" in read_report_html(read("quarter_hour_kwh_iso_offsets.csv"))


def test_the_fleet_panel_screens_one_multi_station_export(app, monkeypatch):
    from hotspotlv.app.widgets.fleet_panel import FleetPanel, _screen
    monkeypatch.setattr(fleet, "ambient_for", lambda index, smhi=None: pd.Series(-5.0, index=index))   # offline
    table, results = _screen(FIX / "multi_station_natstation.csv", None)
    assert len(table) == 3 and (table["error"] == "").all()
    panel = FleetPanel()
    assert panel.default_kva.itemData(0) is None and 500 in [panel.default_kva.itemData(i)
                                                             for i in range(panel.default_kva.count())]


def test_the_assumptions_page_explains_how_an_export_is_read():
    pytest.importorskip("PySide6")
    import hotspotlv.meterfile as meterfile
    from hotspotlv.app.main_window import _assumptions_markdown
    page = _assumptions_markdown()
    assert "## Reading a meter export" in page and meterfile.__doc__.strip().splitlines()[0] in page
    assert "What a file cannot tell" in page and "×4" in page
