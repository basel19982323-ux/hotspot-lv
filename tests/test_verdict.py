"""The verdict must follow the thermal/limits column, never the nameplate column.
Assertions include the ones that killed the review's surviving mutants (21 Oct 2026)."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.thermal import ThermalParams, simulate
from hotspotlv.timeseries import YearResult
from hotspotlv.verdict import judge


def _year(loading_pct, vmin=0.97, vmax=1.01, cable=50.0, n=8760, freq="h"):
    idx = pd.date_range("2023-01-01", periods=n, freq=freq, tz="UTC")
    lp = np.full(n, float(loading_pct)) if np.isscalar(loading_pct) else np.asarray(loading_pct, float)
    h = pd.DataFrame({
        "s_kva": lp / 100 * 500, "loading_pct": lp, "p_kw": lp / 100 * 475,
        "vmin_pu": vmin, "vmax_pu": vmax, "losses_kw": 1.0, "line_max_pct": cable,
    }, index=idx)
    return YearResult(h, 500.0)


def _thermal(year, ambient=5.0, step_min=60.0):
    ta = pd.Series(float(ambient), index=year.hourly.index)
    # bare IEC constants: these tests probe the rule anchors (98 °C at rated load = 365 days), not the kiosk
    return simulate(year.hourly["loading_pct"] / 100, ta, ThermalParams.for_transformer(500, enclosure="outdoor"),
                    step_min=step_min)


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
    assert "also:" in v.headline()                              # At risk headline shows the Watch reasons too


def test_short_hot_episode_gives_watch_not_at_risk():
    # 3 hours at 150 % in summer: hot-spot passes 120 °C but, thanks to the 3-hour oil time
    # constant, not 140 °C; life used stays small
    lp = np.full(8760, 20.0)
    lp[4000:4003] = 150.0
    yr = _year(lp)
    v = judge(yr, _thermal(yr, ambient=25.0))
    assert v.level == "Watch"
    assert any("hot-spot above normal cyclic" in r.rule for r in v.watch)
    assert v.really["days of life used per year"] < 182


def test_six_hours_at_150_pct_is_at_risk():
    # the same episode twice as long reaches the 140 °C long-time emergency limit and 214 days of life
    lp = np.full(8760, 20.0)
    lp[4000:4006] = 150.0
    yr = _year(lp)
    v = judge(yr, _thermal(yr, ambient=25.0))
    assert v.level == "At risk"
    assert any("long-time emergency" in r.rule for r in v.at_risk)


def test_top_oil_rule():
    # 130 % at 40 °C: top-oil = 40 + 55·((1 + 8.5·1.69)/9.5)^0.8 ≈ 121 °C > 115 → At risk (long-time emergency)
    yr = _year(130.0)
    v = judge(yr, _thermal(yr, ambient=40.0))
    assert any(r.rule == "top-oil above long-time emergency limit" for r in v.at_risk)
    assert not any("top-oil" in r.rule for r in v.watch)
    # 125 % at 35 °C: top-oil ≈ 35 + 55·1.385 ≈ 111 °C — between 105 and 115 → Watch, not At risk
    yr = _year(125.0)
    v = judge(yr, _thermal(yr, ambient=35.0))
    top = v.really["max top-oil (°C)"]
    assert 105 < top < 115, top
    assert any(r.rule == "top-oil above normal cyclic limit" for r in v.watch)
    assert not any("top-oil" in r.rule for r in v.at_risk)


def test_reasons_print_at_the_cards_precision():
    """Catches: '120 °C (limit 120 °C)' for 120.2 °C, '1.66e+16 days', '1.36 × rated' as '1.4'."""
    from hotspotlv.verdict import Reason
    assert str(Reason("r", 120.24, 120.0, "°C", "s")).startswith("r: 120.2 °C (limit 120 °C)")
    assert str(Reason("r", 1.3649, 1.5, "× rated", "s")).startswith("r: 1.36 × rated (limit 1.5 × rated)")
    assert str(Reason("r", 12345.6, 365.0, "days of life per year", "s")).startswith("r: 12,346 days of life per year (limit 365")
    assert str(Reason("r", 0.14567, 182.0, "days of life per year", "s")).startswith("r: 0.146 days")
    assert str(Reason("r", 1234.0, 0.0, "hours", "s")).startswith("r: 1,234 hours (none allowed)")
    assert "e+" not in str(Reason("r", 1.66e16, 365.0, "days of life per year", "s"))


def test_withdraw_thermal_leaves_no_number_that_looks_real():
    """Catches: a clipped thermal run printing '440 °C' or '1.66e+16 days' as the verdict's reasons."""
    import math
    from hotspotlv.verdict import OUT_OF_RANGE_RULE, THERMAL_KEYS, withdraw_thermal
    yr = _year(190.0)                                                         # hot, and above the 1.8 current limit
    v = judge(yr, _thermal(yr, ambient=20.0))
    assert any(r.rule.startswith(("ageing", "hot-spot")) for r in v.at_risk)
    w = withdraw_thermal(v, 4.1, 3.0, 12)
    assert w.level == "At risk" and w.at_risk[0].rule == OUT_OF_RANGE_RULE
    assert not any(r.rule.startswith(("ageing", "hot-spot", "top-oil")) for r in w.reasons)
    assert any("current limit" in r.rule for r in w.at_risk)                 # the load rule saw the full loading: kept
    assert all(math.isnan(w.really[k]) for k in THERMAL_KEYS)
    assert "4.10 × rated (limit 3 × rated)" in str(w.at_risk[0])


def test_voltage_rules_follow_eifs():
    yr = _year(30.0, vmin=0.89)                                   # any hour below 0.90 → At risk (EIFS 2023:3)
    v = judge(yr, _thermal(yr))
    assert v.level == "At risk" and any("EIFS" in r.source for r in v.at_risk)
    vmin = np.full(8760, 0.97); vmin[:1] = 0.899                  # even ONE hour
    v = judge(_year(30.0, vmin=vmin), _thermal(_year(30.0, vmin=vmin)))
    assert v.level == "At risk"
    vmin = np.full(8760, 0.97); vmin[:10] = 0.915                 # inside the law, outside the planning band → Watch
    yr = _year(30.0, vmin=vmin)
    v = judge(yr, _thermal(yr))
    assert v.level == "Watch" and any("planning band" in r.rule for r in v.watch)
    assert v.really["hours with voltage outside the planning band"] == 10
    yr = _year(30.0, vmax=1.06)                                   # rise above 1.05 → Watch; 1.11 → At risk
    assert judge(yr, _thermal(yr)).level == "Watch"
    yr = _year(30.0, vmax=1.11)
    assert judge(yr, _thermal(yr)).level == "At risk"
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


def test_counters_are_strict_with_tolerance():
    lp = np.full(8760, 20.0); lp[:100] = 80.0 + 1e-13; lp[100:110] = 80.5   # float noise at the cap must not count
    yr = _year(lp)
    v = judge(yr, _thermal(yr))
    assert v.on_paper["hours above 80 % of nameplate"] == 10


def test_short_period_is_scaled_to_a_year_from_the_index():
    yr = _year(100.0, n=24 * 7)                                   # one week at nameplate, 20 °C
    v = judge(yr, _thermal(yr, ambient=20.0))
    assert v.really["days of life used per year"] == pytest.approx(365, rel=0.02)
    assert v.level == "Watch"                                     # exactly design rate is not "faster than design"
    q = _year(100.0, n=24 * 7 * 4, freq="15min")                  # the same week in quarter hours
    vq = judge(q, _thermal(q, ambient=20.0, step_min=15.0))
    assert vq.really["days of life used per year"] == pytest.approx(365, rel=0.02)
    assert vq.on_paper["hours above 80 % of nameplate"] == pytest.approx(168)     # hours, not quarter-hour rows
    assert vq.really["hours hot-spot above 98 °C"] <= 168
    assert "e+" not in str(v.reasons[0])                                          # counts print as plain hours


def test_validation():
    yr = _year(50.0, n=48)
    th = _thermal(yr)
    with pytest.raises(ValueError):
        judge(_year(50.0, n=47), th)                              # length mismatch
    bad = _year(50.0, n=48); bad.hourly.loc[bad.hourly.index[3], "vmin_pu"] = np.nan
    with pytest.raises(ValueError):
        judge(bad, th)
    with pytest.raises(ValueError):
        judge(YearResult(yr.hourly.iloc[:0], 500.0), th)
    ri = YearResult(yr.hourly.reset_index(drop=True), 500.0)
    with pytest.raises(ValueError):
        judge(ri, th)                                             # RangeIndex
