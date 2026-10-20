"""The verdict must follow the thermal/limits column, never the nameplate column."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.thermal import ThermalParams, simulate
from hotspotlv.timeseries import YearResult
from hotspotlv.verdict import judge


def _year(loading_pct, vmin=0.97, vmax=1.01, cable=50.0, n=8760):
    idx = pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC")
    lp = np.full(n, float(loading_pct)) if np.isscalar(loading_pct) else np.asarray(loading_pct, float)
    h = pd.DataFrame({
        "s_kva": lp / 100 * 500, "loading_pct": lp, "p_kw": lp / 100 * 475,
        "vmin_pu": vmin, "vmax_pu": vmax, "losses_kw": 1.0, "line_max_pct": cable,
    }, index=idx)
    return YearResult(h, 500.0)


def _thermal(year, ambient=5.0):
    ta = pd.Series(float(ambient), index=year.hourly.index)
    return simulate(year.hourly["loading_pct"] / 100, ta, ThermalParams.for_transformer(500))


def test_on_paper_overload_in_cold_climate_is_still_ok():
    # 95 % of nameplate all year at 5 °C: "overloaded on paper" by the 80 % rule for 8760 h,
    # but the hot-spot (≈ 5 + 51 + 21 = 77 °C) ages the paper at ~1/11 of the design rate
    yr = _year(95.0)
    v = judge(yr, _thermal(yr))
    assert v.level == "OK"
    assert v.on_paper["hours above 80 % of nameplate"] == 8760
    assert v.really["days of life used per year"] < 100
    assert "OK" in v.headline()


def test_ageing_drives_the_verdict():
    # 130 % all year at 20 °C: hot-spot ≈ 136 °C → V ≈ 80, life > 365 days → At risk
    yr = _year(130.0)
    v = judge(yr, _thermal(yr, ambient=20.0))
    assert v.level == "At risk"
    assert any("ageing" in r.rule for r in v.at_risk)
    assert any("normal cyclic" in r.rule for r in v.watch)       # also above 120 °C


def test_short_hot_episode_gives_watch_not_at_risk():
    # mostly idle, one 3-hour block at 150 % in summer: hot-spot passes 120 °C but, thanks to
    # the 3-hour oil time constant, not 140 °C; life used stays small (6 hours would reach 140 °C)
    lp = np.full(8760, 20.0)
    lp[4000:4003] = 150.0
    yr = _year(lp)
    v = judge(yr, _thermal(yr, ambient=25.0))
    assert v.level == "Watch"
    rules = [r.rule for r in v.watch]
    assert any("hot-spot above normal cyclic" in r for r in rules)
    assert v.really["days of life used per year"] < 182


def test_voltage_and_cable_rules():
    yr = _year(30.0, vmin=0.89)                                   # every hour below 0.90 → > 5 % → At risk
    v = judge(yr, _thermal(yr))
    assert v.level == "At risk" and any("voltage" in r.rule for r in v.at_risk)
    vmin = np.full(8760, 0.97); vmin[:10] = 0.89                  # 10 hours → Watch
    yr = _year(30.0, vmin=vmin)
    v = judge(yr, _thermal(yr))
    assert v.level == "Watch" and any("voltage" in r.rule for r in v.watch)
    yr = _year(30.0, cable=110.0)
    assert judge(yr, _thermal(yr)).level == "Watch"


def test_current_limits():
    lp = np.full(8760, 20.0); lp[100] = 185.0                     # one hour at 1.85 pu → At risk by current limit
    yr = _year(lp)
    v = judge(yr, _thermal(yr, ambient=-20.0))
    assert v.level == "At risk" and any("current limit" in r.rule for r in v.at_risk)
    lp[100] = 160.0
    v = judge(_year(lp), _thermal(_year(lp), ambient=-20.0))
    assert v.level == "Watch" and any("current limit" in r.rule for r in v.watch)


def test_short_period_is_scaled_to_a_year():
    yr = _year(100.0, n=24 * 7)                                   # one week at nameplate, 20 °C
    v = judge(yr, _thermal(yr, ambient=20.0))
    assert v.really["days of life used per year"] == pytest.approx(365, rel=0.02)
    assert v.level == "Watch"                                     # exactly design rate is not "faster than design"
