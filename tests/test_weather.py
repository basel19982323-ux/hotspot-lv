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


def test_the_shipped_station_works_offline_and_another_says_why_it_cannot(tmp_path, monkeypatch):
    """Packaging review (22 Oct 2026): on a PC without internet the shipped examples failed with a raw URLError,
    because station 96190's 2023 temperatures were read from a downloaded archive although they ship with the
    program. Now they come from the package, nothing is written to the profile, and a needed download that cannot
    happen says so in a planner's words."""
    import pandas as pd
    from hotspotlv.paths import package_data
    from hotspotlv.weather import load_smhi_temperature
    cache = tmp_path / "cache"
    monkeypatch.setenv("HOTSPOTLV_CACHE", str(cache))
    monkeypatch.setenv("HOTSPOTLV_OFFLINE", "1")
    t = load_smhi_temperature(2023, 96190)
    shipped = pd.read_csv(package_data() / "weather_vasteras_2023.csv", index_col=0, parse_dates=True)["temp_c"]
    assert len(t) == 8760 and str(t.index.tz) == "UTC" and (t - shipped).abs().max() == 0
    assert not cache.exists()                                                    # nothing written just to read
    with pytest.raises(ConnectionError) as e:
        load_smhi_temperature(2023, 97400)                                       # Stockholm-Arlanda: needs a download
    msg = str(e.value)
    assert "SMHI station 97400" in msg and "downloaded once" in msg and "HOTSPOTLV_OFFLINE" in msg
    assert not list(cache.rglob("*.part")) and not list(cache.rglob("*.csv"))   # no half archive left behind
