"""The v0.2 review's reader findings (31 Oct 2026): every way it found to read a real export wrong, or to stop with
an unclear error, as a test. Each docstring names the finding it keeps fixed."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import hotspotlv.fleet as fleet
from hotspotlv.fleet import read_fleet_long
from hotspotlv.station import _to_number, assess_station, read_station_csv

FIX = Path(__file__).resolve().parent / "fixtures" / "dso_exports"
sys.path.insert(0, str(FIX))
import make_fixtures  # noqa: E402


def write(tmp_path, lines, name="x.csv", encoding="utf-8"):
    (tmp_path / name).write_bytes(("\n".join(lines) + "\n").encode(encoding))
    return tmp_path / name


def hours(n=24, start="2025-02-03 00:00", fmt="%Y-%m-%d %H:%M"):
    return list(pd.date_range(start, periods=n, freq="h").strftime(fmt))


def found(st, key):
    return dict(st.found)[key]


def num(values, **kw):
    return _to_number(pd.Series(values, dtype=object), "P", **kw).tolist()


# --- numbers ------------------------------------------------------------------------------------------------------
def test_a_lone_thousands_value_follows_the_columns_decimal_sign():
    """'1.300' among '950,5' was 1.3; '1,300' among '950.5' was 1.3; '1.005' among whole numbers was 1.005."""
    assert num(["950,5", "1.226,5", "1.300"]) == [950.5, 1226.5, 1300.0]
    assert num(["1.226,5", "1.000", "980,25"]) == [1226.5, 1000.0, 980.25]
    assert num(["950.5", "1,226.5", "1,300"]) == [950.5, 1226.5, 1300.0]
    assert num(["980", "1.005", "990"]) == [980.0, 1005.0, 990.0]
    assert num(["1.226", "2.450"], sep=";") == [1226.0, 2450.0]                 # two grouped values are enough


def test_a_column_showing_both_signs_is_read_cell_by_cell_and_said():
    notes = []
    out = _to_number(pd.Series(["1,226.0", "12,3"]), "P", notes=notes).tolist()
    assert out == [1226.0, 12.3] and "both decimal signs" in notes[0]


def test_three_digits_after_every_comma_is_said_outside_a_semicolon_file():
    notes = []
    assert _to_number(pd.Series(["23,000", "18,750"]), "P", sep="\t", notes=notes).tolist() == [23.0, 18.75]
    assert notes and "three digits after it" in notes[0]
    notes = []
    _to_number(pd.Series(["23,000", "18,750"]), "P", sep=";", notes=notes)
    assert not notes                                       # ';' is there because ',' is the decimal sign


def test_a_cell_that_is_not_a_number_is_refused_not_filled():
    """'400,0*' at the peak was turned into a gap and interpolated to the neighbours' 70."""
    with pytest.raises(ValueError, match=r"cannot be read as a number \(first: '400,0\*', table row 2\)"):
        num(["70,5", "400,0*", "71,0"])
    assert num(["−12,5", "-", "3"])[0] == -12.5 and np.isnan(num(["1", "-"])[1])      # '−' is a minus sign


# --- columns ------------------------------------------------------------------------------------------------------
def test_several_columns_for_the_load_are_refused_not_the_first_taken(tmp_path):
    rows = [f"{t};100;100;100" for t in hours()]
    with pytest.raises(ValueError, match="several columns could be the load"):
        read_station_csv(write(tmp_path, ["Tidpunkt;P L1 (kW);P L2 (kW);P L3 (kW)"] + rows))
    rows = [f"{t};10;20" for t in hours()]
    with pytest.raises(ValueError, match="twice"):
        read_station_csv(write(tmp_path, ["Tidpunkt;Energi (kWh);Energi (kWh)"] + rows, "d.csv"))


def test_a_kva_column_is_never_the_active_power(tmp_path):
    rows = [f"315;{t};{100 + i},5" for i, t in enumerate(hours())]
    st = read_station_csv(write(tmp_path, ["Effekt (kVA);Tidpunkt;Energi (kWh)"] + rows))
    assert found(st, "values").startswith("column 'Energi (kWh)'") and st.p_kw.iloc[0] == 100.5
    with pytest.raises(ValueError, match="only an apparent-power column"):
        read_station_csv(write(tmp_path, ["Tidpunkt;S (kVA)"] + [f"{t};300" for t in hours()], "s.csv"))


def test_a_constant_load_column_is_questioned(tmp_path):
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Effekt (kW)"] + [f"{t};315" for t in hours(48)]))
    assert any("same value (315) in every row" in u for u in st.unsure)


def test_the_files_own_rating_is_shown_and_a_different_one_is_questioned():
    st = read_station_csv(FIX / "multi_station_natstation.csv", station="NS 101 Åkervägen")
    assert st.file_kva == 315 and found(st, "rating in the file").startswith("315 kVA")
    res = assess_station(st, 500, pd.Series(-5.0, index=st.index))
    assert any("the file gives 315 kVA, the assessment uses 500 kVA" in n for n in res.verdict.notes)


def test_a_units_row_under_the_names_row(tmp_path):
    rows = [f"{t};5;{100 + i}" for i, t in enumerate(hours())]
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Inmatning;Uttag", ";kWh;kWh"] + rows))
    assert found(st, "header").startswith("lines 1–2 (names, then units)")
    assert st.p_kw.iloc[0] == 95.0                          # Uttag − Inmatning, not the production as the load


def test_an_export_column_without_a_unit_takes_the_load_columns_unit(tmp_path):
    rows = [f"{t};100000;2000" for t in hours()]
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Uttag (Wh);Inmatning"] + rows))
    assert st.p_kw.iloc[0] == pytest.approx(98.0)           # 100 kWh − 2 kWh, not 100 − 2 000


def test_units_the_reader_does_not_know_are_named(tmp_path):
    rows = [f"{t.split()[0]};{t.split()[1]};25;kvarh" for t in hours()]
    with pytest.raises(ValueError, match="'kvarh': not active power or energy"):
        read_station_csv(write(tmp_path, ["Datum;Tid;Värde;Enhet"] + rows))
    rows = [f"{t.split()[0]};{t.split()[1]};25;kWh/h" for t in hours()]
    with pytest.raises(ValueError, match="'kwh/h', which is not a unit the reader knows"):
        read_station_csv(write(tmp_path, ["Datum;Tid;Värde;Enhet"] + rows, "u.csv"))
    rows = [f"{t};{1500 + i} Wh" for i, t in enumerate(hours())]
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Värde"] + rows, "c.csv"))       # the unit from the cells
    assert st.p_kw.iloc[0] == pytest.approx(1.5) and found(st, "unit").startswith("wh — from the cells")


def test_extra_columns_with_brackets_do_not_stop_the_file(tmp_path):
    rows = [f"{t};{100 + i},5;{10 + i},0;0" for i, t in enumerate(hours())]
    st = read_station_csv(write(tmp_path, ["Tidpunkt (CET);Effekt (kW);Reaktiv energi (kvarh);Status (kod)"] + rows))
    assert st.p_kw.iloc[0] == 100.5 and st.q_kvar.iloc[0] == 10.0                  # kvarh per hour = mean kvar
    assert "can mean CET all year or Swedish time" in found(st, "clock hint")
    with pytest.raises(ValueError, match="unit 'gwh' is not understood"):
        read_station_csv(write(tmp_path, ["Tidpunkt;Energi (GWh)"] + [f"{t};1" for t in hours()], "g.csv"))


def test_a_clock_written_in_the_header_is_used(tmp_path):
    st = read_station_csv(write(tmp_path, ["Tidpunkt (UTC);Effekt (kW)"] + [f"{t};{100 + i}" for i, t in enumerate(hours())]))
    assert st.index[0] == pd.Timestamp("2025-02-03 00:00", tz="UTC") and "read as UTC" in found(st, "clock hint")


def test_one_row_per_meter_is_refused_with_what_to_do(tmp_path):
    rows = [f"{m};{t};{10 + m}" for m in (1, 2) for t in hours()]
    with pytest.raises(ValueError, match="one row per meter.*add them up"):
        read_station_csv(write(tmp_path, ["Anläggnings-ID;Tidpunkt;Energi (kWh)"] + rows))
    with pytest.raises(ValueError, match="more than once with different values.*add them up"):
        read_station_csv(write(tmp_path, ["Tidpunkt;Energi (kWh)"] + [r.split(";", 1)[1] for r in rows], "m.csv"))


# --- the table ----------------------------------------------------------------------------------------------------
def test_a_preamble_with_a_metering_point_id_and_dated_lines(tmp_path):
    body = ["Tidpunkt;Energi (kWh)"] + [f"{t};{100 + i},5" for i, t in enumerate(hours())]
    st = read_station_csv(write(tmp_path, ["Mätpunkt;735999123456789012", "Från;2025-02-03", "Till;2025-02-04", ""] + body))
    assert found(st, "header").startswith("line 5: Tidpunkt") and st.p_kw.iloc[0] == 100.5
    st = read_station_csv(write(tmp_path, ["Kund;Exempel AB", "Från;2025-02-03 00:00", "Till;2025-02-04 00:00",
                                           "Period;2025-02-03 - 2025-02-04"] + body, "k.csv"))
    assert found(st, "header").startswith("line 5: Tidpunkt") and len(st.p_kw) == 24


def test_a_separator_at_the_end_of_every_data_line_and_an_empty_line(tmp_path):
    rows = [f"{t};{100 + i},5;" for i, t in enumerate(hours())]
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Energi (kWh)"] + rows[:10] + [";;"] + rows[10:]))
    assert len(st.p_kw) == 24 and st.p_kw.iloc[0] == 100.5 and st.gaps_filled == 0


# --- stamps -------------------------------------------------------------------------------------------------------
def test_hour_numbers_hour_ranges_written_intervals_and_excel_days(tmp_path):
    rows = [f"2025-02-03;{h};{100 + h}" for h in range(1, 25)]                  # Swedish 'timme 1' = 00:00–01:00
    st = read_station_csv(write(tmp_path, ["Datum;Timme;Energi (kWh)"] + rows))
    assert st.index[0].tz_convert("Europe/Stockholm").hour == 0 and st.p_kw.iloc[0] == 101
    assert "1 = 00:00–01:00" in found(st, "timestamps")
    rows = [f"2025-02-03;{h:02d}-{h + 1:02d};{100 + h}" for h in range(24)]
    assert read_station_csv(write(tmp_path, ["Datum;Timme;Energi (kWh)"] + rows, "r.csv")).p_kw.iloc[0] == 100
    rows = [f"2025-02-03 {h:02d}:00 - {h + 1:02d}:00;{100 + h}" for h in range(23)]
    st = read_station_csv(write(tmp_path, ["Period;Energi (kWh)"] + rows, "p.csv"), stamp="end")
    assert st.index[0].tz_convert("Europe/Stockholm").hour == 0 and "stamp='end' not applied" in " ".join(st.notes)
    days = [f"{45691 + h / 24:.5f}" for h in range(24)]                          # 45691 = 2025-02-03
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Energi (kWh)"] + [f"{d};{100 + i}" for i, d in enumerate(days)], "e.csv"))
    assert st.index[0].tz_convert("Europe/Stockholm") == pd.Timestamp("2025-02-03 00:00", tz="Europe/Stockholm")
    assert "Excel day numbers" in found(st, "timestamps")


def test_the_grid_can_decide_month_first(tmp_path):
    stamps = [f"01/{d:02d}/2025 {h:02d}:00" for d in (1, 2, 3) for h in range(24)]   # Jan 1–3, month first
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Energi (kWh)"] + [f"{t};{100 + i}" for i, t in enumerate(stamps)]))
    assert found(st, "timestamps").endswith("(month first)") and len(st.p_kw) == 72


def test_a_clock_typo_and_a_unit_against_the_file_are_explained(tmp_path):
    path = write(tmp_path, ["Tidpunkt;Effekt (kW)"] + [f"{t};{100 + i}" for i, t in enumerate(hours())])
    with pytest.raises(ValueError, match="unknown clock 'Europe/Stokholm'"):
        read_station_csv(path, tz="Europe/Stokholm")
    with pytest.raises(ValueError, match=r"states its unit \(kw\), but the unit chosen is kWh"):
        read_station_csv(path, unit="kWh")
    assert dict(read_station_csv(path).found)["interval"].startswith("each stamp is the START of its interval — your")


# --- the daylight-saving days -------------------------------------------------------------------------------------
def _spring(step, fill):
    """Local-time stamps 29–31 March 2025 with the spring hour written by `fill(stamp, rows)`."""
    t = make_fixtures.utc_range("2025-03-29 00:00", "2025-04-01 00:00", step)
    loc = make_fixtures.local_text(t, "%Y-%m-%d %H:%M")
    rows = [f"{s};{v:.1f}".replace(".", ",") for s, v in zip(loc, make_fixtures.kw_at(t) * step / 60)]
    k = loc.index("2025-03-30 03:00")
    return t, rows[:k] + fill(rows, k) + rows[k:]


@pytest.mark.parametrize("step, fill, weak", [
    (60, lambda rows, k: ["2025-03-30 02:00;" + rows[k - 1].split(";")[1]], True),     # a copy of 01:00
    (15, lambda rows, k: [f"2025-03-30 02:{m:02d};0" for m in (0, 15, 30, 45)], False),  # four zeros
    (15, lambda rows, k: [f"2025-03-30 02:{m:02d};" + rows[k].split(";")[1] for m in (0, 15, 30, 45)], True),  # 03:00 × 4
])
def test_spring_placeholders_of_every_shape_are_local_time(tmp_path, step, fill, weak):
    t, rows = _spring(step, fill)
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Energi (kWh)"] + rows))
    assert st.index.equals(pd.DatetimeIndex(t)) and np.allclose(st.p_kw.to_numpy(), make_fixtures.kw_at(t), atol=0.21)
    assert found(st, "clock").startswith("Swedish local time")
    assert any("only repeats its neighbouring hour" in u for u in st.unsure) == weak       # a copy is weak evidence


def test_a_spring_hour_with_values_but_no_0300_is_refused(tmp_path):
    t = make_fixtures.utc_range("2025-03-29 00:00", "2025-04-01 00:00", 60)
    naive = [(x.tz_convert("Europe/Stockholm").tz_localize(None) + pd.Timedelta(hours=1)) for x in t]   # start + 1 h
    rows = [f"{s:%Y-%m-%d %H:%M};{100 + i}" for i, s in enumerate(naive)]
    assert any(r.startswith("2025-03-30 02:00") for r in rows) and not any(r.startswith("2025-03-30 03:00") for r in rows)
    with pytest.raises(ValueError, match=r"start \+ 1 h"):
        read_station_csv(write(tmp_path, ["Tidpunkt;Energi (kWh)"] + rows))


def test_an_october_hour_written_once_with_both_hours_in_it_is_flagged(tmp_path):
    t = make_fixtures.utc_range("2025-10-25 00:00", "2025-10-28 00:00", 60)
    loc = make_fixtures.local_text(t, "%Y-%m-%d %H:%M")
    v = make_fixtures.kw_at(t)
    rows, seen = [], set()
    for s, x, i in zip(loc, v, range(len(v))):
        if s in seen:
            continue
        seen.add(s)
        rows.append(f"{s};{(2 * x if s == '2025-10-26 02:00' else x):.1f}")
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Effekt (kW)"] + rows))
    assert any("probably adds both 02:00 hours into one row" in u for u in st.unsure)


def test_normaltid_read_from_the_spring_hour_always_says_utc_looks_the_same():
    st = read_station_csv(FIX / "hourly_kwh_normaltid_spring.csv")
    assert any("UTC without offsets" in u for u in st.unsure)


# --- plausibility -------------------------------------------------------------------------------------------------
def test_a_peak_no_station_has_asks_to_check_the_decimal_sign_and_unit(tmp_path):
    rows = [f"{t};2{i % 10}.{100 + i:03d}" for i, t in enumerate(hours(48))]      # 3-decimal kWh read as thousands
    st = read_station_csv(write(tmp_path, ["Tidpunkt;Energi (kWh)"] + rows))
    assert st.p_kw.max() > 20_000 and any("thousands separator" in u for u in st.unsure)
    res = assess_station(st, 500, pd.Series(0.0, index=st.index))
    assert any(n.startswith("check the file: the peak load is") for n in res.verdict.notes)


# --- fleet ----------------------------------------------------------------------------------------------------------
def test_a_blank_rating_cell_does_not_stop_the_fleet_and_a_bad_one_is_its_stations_error(tmp_path):
    text = (FIX / "multi_station_natstation.csv").read_bytes().decode("cp1252").splitlines()
    lines = [text[0]] + [l.replace(";500;", ";;", 1) if i == 30 + 72 else l for i, l in enumerate(text[1:], 1)]
    stations = read_fleet_long(write(tmp_path, lines, encoding="cp1252"))
    assert [s.rated_kva for s in stations] == [315.0, 500.0, 800.0] and all(s.error is None for s in stations)
    lines = [l.replace(";315;", ";400;", 1) if i == 5 else l for i, l in enumerate(text)]
    stations = read_fleet_long(write(tmp_path, lines, "two.csv", encoding="cp1252"))
    assert "more than one rating" in stations[0].error and stations[1].error is None


def test_the_fleet_decides_one_decimal_sign_for_the_whole_file(tmp_path):
    """A station whose values happen to show no decimal sign ('1.300') reads them as the others' (comma)."""
    rows = ([f'A,{t},"{950 + i},5"' for i, t in enumerate(hours())] +         # a ',' file: alone, B's dots would
            [f"B,{t},1.{300 + i}" for i, t in enumerate(hours())])              # be decimals (1.3)
    a, b = read_fleet_long(write(tmp_path, ["Station,Tidpunkt,Energi (kWh)"] + rows), default_kva=500)
    assert b.series.p_kw.iloc[0] == 1300.0 and "one sign for every station" in dict(b.series.found)["numbers"]


def test_the_fixtures_are_exactly_what_their_generator_writes(tmp_path, monkeypatch):
    """Catches a fixture edited by hand, out of step with the truth function the tests compare with."""
    monkeypatch.setattr(make_fixtures, "HERE", tmp_path)
    make_fixtures.main()
    made = sorted(p.name for p in tmp_path.iterdir())
    assert made == sorted(p.name for p in FIX.iterdir() if p.suffix in (".csv", ".txt"))
    for name in made:
        assert (tmp_path / name).read_bytes() == (FIX / name).read_bytes(), name
