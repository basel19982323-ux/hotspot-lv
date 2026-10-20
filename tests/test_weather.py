"""Checks on the cached 2023 weather table (no network needed once data/ exists)."""

import pandas as pd
import pytest

from hotspotlv.weather import get_weather


@pytest.fixture(scope="module")
def wx():
    return get_weather(2023)


def test_full_year_hourly(wx):
    assert len(wx) == 8760
    assert wx.index.is_monotonic_increasing
    assert (wx.index.to_series().diff().dropna() == pd.Timedelta("1h")).all()
    assert not wx.isna().any().any()


def test_temperature_is_swedish_inland(wx):
    t = wx["temp_c"]
    assert -30 < t.min() < -10          # a real winter
    assert 20 < t.max() < 35
    assert 4 < t.mean() < 10            # Mälardalen annual mean ≈ 6–8 °C
    assert (t < 0).sum() > 1000         # thousands of frost hours


def test_pv_is_plausible_for_sweden(wx):
    pv = wx["pv_kw_per_kwp"]
    assert (pv >= 0).all()
    assert pv.max() <= 1.0              # never above nameplate per kWp
    assert 800 < pv.sum() < 1100        # kWh/kWp/yr, south-facing, central Sweden
    night = wx.tz_convert("Europe/Stockholm").between_time("00:00", "02:00")["pv_kw_per_kwp"]
    assert night.max() == 0.0           # no sun at night, even in June
