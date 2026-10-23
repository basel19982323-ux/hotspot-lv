"""The study's own runner (study/): street types, growth by cables, and how the first-limit table is read."""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

STUDY = Path(__file__).resolve().parents[1] / "study"
sys.path.insert(0, str(STUDY))
import run_study  # noqa: E402


@pytest.mark.parametrize("street, houses, cables, kva, seg", [
    ("rural", 10, 2, 315, 0.060), ("rural", 100, 13, 315, 0.060),
    ("suburb", 90, 6, 500, 0.030), ("suburb", 160, 11, 500, 0.030),
    ("dense", 260, 11, 800, 0.015),
])
def test_a_street_grows_by_cables_of_its_own_type(street, houses, cables, kva, seg):
    case = run_study.case_for(street, houses)
    assert case.n_feeders == cables == math.ceil(houses / run_study.STREETS[street]["per_cable"])
    assert case.trafo_kva == kva and case.main_segment_km == seg and case.scenario.n_houses == houses
    assert case.scenario.ev_share == 0.6 and case.scenario.pv_share == 0.4 and case.scenario.seed == 0


def test_every_weather_year_of_the_study_is_a_whole_swedish_year():
    for year in run_study.YEARS:
        wx = run_study.weather(year)
        local = wx.index.tz_convert("Europe/Stockholm")
        assert local[0] == pd.Timestamp(f"{year}-01-01 00:00", tz="Europe/Stockholm")
        assert local[-1] == pd.Timestamp(f"{year}-12-31 23:00", tz="Europe/Stockholm")
        assert not wx.isna().any().any() and len(wx) in (8760, 8784)
    assert 2016 not in run_study.YEARS and 2019 not in run_study.YEARS       # SMHI gaps over 6 h


def test_the_first_limit_is_the_smallest_house_count_that_meets_it():
    import figures
    runs = pd.DataFrame({
        "street": "suburb", "year": 2010, "houses": [50, 60, 70],
        "peak_loading_pct": [70.0, 85.0, 120.0], "watch_thermal": [False, False, True],
        "at_risk_thermal": [False, False, False], "at_risk_voltage": False, "watch_cable": False})
    first = figures.first_houses(runs).set_index("criterion")["houses"]
    assert first["80 % rule: peak above 80 % of nameplate"] == 60
    assert first["nameplate: peak above 100 %"] == 70
    assert first["thermal limit, Watch (IEC 60076-7)"] == 70
    assert np.isnan(first["thermal limit, At risk (IEC 60076-7)"])          # not reached: no number invented
