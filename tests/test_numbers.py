"""Numbers in a DSO's export: decimal comma or point, thousands dot, comma or space, units after the value —
decided per cell where the cell shows it, per column where it does not (v0.2: '1.226,5' was read as 1.2265)."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.station import _to_number, read_station_csv


def num(values, **kw):
    return _to_number(pd.Series(values, dtype=object), "P", **kw).tolist()


@pytest.mark.parametrize("cell, value", [
    ("1.226,5", 1226.5),            # both signs, comma last: dot = thousands, comma = decimal
    ("1,226.5", 1226.5),            # both signs, dot last: comma = thousands, dot = decimal
    ("12.345.678,9", 12345678.9),
    ("12,345,678.9", 12345678.9),
    ("-1.226,5", -1226.5),
    ("+12,5", 12.5),
    ("12,5", 12.5),                 # only a comma: decimal comma
    ("0,05", 0.05),
    ("12.5", 12.5),                 # only a dot, not three digits after it: decimal point
    ("1 226,5", 1226.5),            # space as thousands separator
    ("1 226,5", 1226.5),       # no-break space
    ("1 226,5", 1226.5),       # narrow no-break space
    ("12 345", 12345.0),
    ("12,5 kW", 12.5),              # trailing units
    ("12,5kWh", 12.5),
    ("1 226,5 kWh", 1226.5),
    ("3,2 kvar", 3.2),
    ("1226", 1226.0),
])
def test_one_cell(cell, value):
    assert num([cell]) == [pytest.approx(value)]


@pytest.mark.parametrize("blank", ["", "  ", "-", "null", "NaN", "None", "n/a"])
def test_blanks_are_missing(blank):
    assert np.isnan(num([blank, "1,5"])[0])


def test_several_commas_are_thousands():
    assert num(["1,226,500"]) == [1226500.0]


# --- the dot-only case is decided for the whole column --------------------------------------------------------
def test_a_column_of_three_digit_groups_reads_dots_as_thousands():
    notes = []
    out = _to_number(pd.Series(["1.226", "2.450", "12.003", "", "980"]), "P", notes=notes).tolist()
    assert out[:3] == [1226.0, 2450.0, 12003.0] and np.isnan(out[3]) and out[4] == 980.0
    assert notes and "thousands separator" in notes[0] and "decimal='.'" in notes[0]


def test_one_value_that_breaks_the_pattern_makes_the_whole_column_decimal():
    """Catches: a per-cell decision, which would read '1.226' as 1226 next to '2.45' as 2.45 in one column."""
    assert num(["1.226", "2.45", "3.500"]) == [1.226, 2.45, 3.5]


def test_a_leading_zero_is_never_a_thousands_group():
    assert num(["0.125", "1.250", "2.500"]) == [0.125, 1.25, 2.5]


def test_one_value_alone_is_not_a_pattern():
    assert num(["1.226"]) == [1.226]
    assert num(["1.226", ""])[0] == 1.226


def test_two_or_more_dots_mean_thousands_for_the_whole_column():
    assert num(["1.226.500", "12.500"]) == [1226500.0, 12500.0]


def test_a_european_column_with_thousands_dots_and_decimal_commas():
    assert num(["1.226,5", "980,25", "2.001,0"]) == [1226.5, 980.25, 2001.0]


def test_comma_separated_file_keeps_dots_as_decimals():
    assert num(["1.226", "2.450"], sep=",") == [1.226, 2.45]


def test_decimal_can_be_given_when_the_numbers_cannot_tell():
    assert num(["1.226", "2.450"], decimal=".") == [1.226, 2.45]       # three decimals, not thousands
    assert num(["1.226"], decimal=",") == [1226.0]
    assert num(["1,226", "2,450"], decimal=".") == [1226.0, 2450.0]     # comma as thousands, on request
    with pytest.raises(ValueError, match="decimal"):
        num(["1"], decimal=";")


def test_a_whole_file_with_swedish_thousands_and_decimal_comma(tmp_path):
    """Catches the v0.1 bug end to end: a semicolon file of kWh per hour with '1.226,5' must give 1226.5 kW."""
    lines = ["Tidpunkt;Energi (kWh)"] + [f"2025-01-01 {h:02d}:00;1.{200 + h:03d},5" for h in range(24)]
    path = tmp_path / "s.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    st = read_station_csv(path)
    assert st.p_kw.iloc[0] == pytest.approx(1200.5) and st.p_kw.iloc[23] == pytest.approx(1223.5)


def test_a_whole_file_with_three_digit_dot_groups_says_what_it_did(tmp_path):
    lines = ["Tidpunkt;Effekt (kW)"] + [f"2025-01-01 {h:02d}:00;1.{100 + h:03d}" for h in range(24)]
    path = tmp_path / "s.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    st = read_station_csv(path)
    assert st.p_kw.iloc[0] == 1100.0 and any("thousands separator" in n for n in st.notes)
    assert read_station_csv(path, decimal=".").p_kw.iloc[0] == pytest.approx(1.1)
