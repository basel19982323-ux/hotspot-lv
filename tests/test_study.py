"""The study runner: one case end to end, a sweep, Monte Carlo and the fix comparison
(small and fast; assertions include the review's surviving mutants of 21 Oct 2026)."""

import pandas as pd
import pytest

from hotspotlv.neighbourhood import Scenario
from hotspotlv.study import (Case, compare_fixes, fixes_table, monte_carlo, run_case, summarise_monte_carlo,
                             sweep, thermal_params_for)
from hotspotlv.thermal import ThermalParams
from hotspotlv.weather import get_weather


@pytest.fixture(scope="module")
def wx():
    return get_weather(2023)


@pytest.fixture(scope="module")
def heavy():
    # many houses on 500 kVA so that fixes have something to fix (within the proxy's range)
    return Case("heavy", Scenario("heavy", n_houses=80, ev_share=0.7, pv_share=0.3,
                                  heating_mix={"direct_electric": 0.5, "air_water": 0.3, "none": 0.2}, seed=3))


@pytest.fixture(scope="module")
def heavy_cmp(wx, heavy):
    return compare_fixes(heavy, wx)


def test_run_case_fast_end_to_end(wx):
    r = run_case(Case("small", Scenario(n_houses=20, seed=1)), wx)
    assert r.fast and r.proxy_fit_error["loading_pct_max_abs"] < 0.5
    assert len(r.year.hourly) == 8760 and len(r.thermal.hourly) == 8760
    assert r.verdict.level in ("OK", "Watch", "At risk")
    m = r.metrics()
    assert m["houses"] == 20 and m["kVA"] == 500 and m["fast"] is True
    assert m["life_days_per_year"] < 1                      # 20 houses: practically no ageing
    assert r.thermal.params.enclosure == "kiosk"            # Swedish default
    assert r.thermal.hourly["theta_o"].min() < 0            # a Swedish winter is in the oil temperature
    assert thermal_params_for(Case("x", Scenario(), enclosure="outdoor")).enclosure_rise_k == 0


def test_heavy_case_is_flagged_and_refined(wx, heavy_cmp):
    r = heavy_cmp.results["baseline"]
    assert r.verdict.on_paper["hours above 80 % of nameplate"] > 0
    assert 0 < r.verdict.on_paper["hours above 100 % of nameplate"] < r.verdict.on_paper["hours above 80 % of nameplate"]
    assert r.verdict.level in ("Watch", "At risk")
    assert r.exact_hours > 0                                 # hours near the limits were re-run exactly (or the full run)
    assert r.metrics()["voltage_bad_hours"] + r.metrics()["voltage_plan_hours"] > 0


def test_sweep_is_monotonic_in_houses(wx):
    base = Case("sweep", Scenario(n_houses=30, ev_share=0.5, seed=2))
    sw = sweep(base, wx, "n_houses", [30, 90])
    assert list(sw.index) == [30, 90]
    assert sw.loc[90, "peak_loading_pct"] > 2 * sw.loc[30, "peak_loading_pct"]
    assert sw.loc[90, "life_days_per_year"] > sw.loc[30, "life_days_per_year"]
    with pytest.raises(ValueError):
        sweep(base, wx, "name", ["a"])


def test_compare_fixes(wx, heavy, heavy_cmp):
    res = heavy_cmp.results
    names = list(res)
    assert names[0] == "baseline" and "800 kVA" in names
    assert "night timer 22–01" in names and "spread 22–06" in names
    assert any(n.startswith("battery") for n in names)
    base, big = res["baseline"], res["800 kVA"]
    assert big.neighbourhood is base.neighbourhood           # same houses, same dice
    assert big.thermal.params == ThermalParams.for_transformer(800)   # the 800 kVA unit's own constants
    # same houses: the kVA differs only by the transformer's own losses
    assert big.year.hourly["s_kva"].max() == pytest.approx(base.year.hourly["s_kva"].max(), rel=0.05)
    assert big.verdict.on_paper["peak load (% of nameplate)"] < base.verdict.on_paper["peak load (% of nameplate)"]
    assert big.verdict.really["days of life used per year"] < base.verdict.really["days of life used per year"]
    timer, spread = res["night timer 22–01"], res["spread 22–06"]
    for r in (timer, spread):                               # same cars, same energy, different hours
        assert r.neighbourhood.houses.has_ev.sum() == base.neighbourhood.houses.has_ev.sum()
        assert r.neighbourhood.houses.ev_kwh.sum() == pytest.approx(base.neighbourhood.houses.ev_kwh.sum(), rel=0.03)
    assert spread.year.hourly["s_kva"].max() < base.year.hourly["s_kva"].max()
    bat_name = next(n for n in names if n.startswith("battery"))
    bat = res[bat_name]
    assert bat.neighbourhood is base.neighbourhood
    assert bat.battery_sizing is not None
    assert bat.verdict.on_paper["hours above 80 % of nameplate"] < base.verdict.on_paper["hours above 80 % of nameplate"]
    assert bat.metrics()["battery_hours_above_cap"] == bat.battery_sizing.hours_above_cap_after
    table = fixes_table(heavy_cmp)
    assert {"verdict", "peak %", "life days/yr", "voltage bad h", "cable %", "battery h above cap", "why"} <= set(table.columns)
    assert table.loc["800 kVA", "why"].startswith(("OK", "Watch", "At risk"))


def test_compare_fixes_refuses_a_bigger_unit_that_is_not_bigger(wx):
    """An 800 kVA station offered '800 kVA' as a fix would show the baseline twice at 0 kSEK."""
    base = Case("big", Scenario(n_houses=10, seed=1), trafo_kva=800)
    with pytest.raises(ValueError, match="bigger_kva must exceed"):
        compare_fixes(base, wx, bigger_kva=800)


def test_compare_fixes_notes_when_the_baseline_already_uses_a_charging_fix(wx):
    from hotspotlv.loads.ev import EVParams
    sc = Scenario(n_houses=10, seed=1, ev=EVParams(smart=True, smart_start_min_h=22.0, smart_start_max_h=30.0))
    cmp = compare_fixes(Case("spread already", sc), wx)
    assert any("already charges on the spread 22–06" in n for n in cmp.notes)


def test_compare_fixes_reports_when_no_battery_is_needed(wx):
    light = Case("light", Scenario(n_houses=20, seed=1), thermal=ThermalParams.for_transformer(500))
    cmp = compare_fixes(light, wx)
    assert not any(n.startswith("battery") for n in cmp.results)
    assert any("no battery needed" in n for n in cmp.notes)
    # an explicit Case.thermal on the base must not leak into the 800 kVA case
    assert cmp.results["800 kVA"].thermal.params == ThermalParams.for_transformer(800)


def test_monte_carlo_shape(wx):
    base = Case("mc", Scenario(n_houses=30, ev_share=0.5, seed=0))
    mc = monte_carlo(base, wx, seeds=range(3))
    assert list(mc.index) == [0, 1, 2]
    assert mc["peak_loading_pct"].nunique() == 3
    s = summarise_monte_carlo(mc)
    assert "life_days_per_year" in s.index and "90%" in s.columns
    with pytest.raises(ValueError):
        monte_carlo(base, wx, seeds=[0.5])


def test_tz_naive_weather_rejected(wx):
    naive = wx.copy(); naive.index = naive.index.tz_localize(None)
    with pytest.raises(ValueError):
        run_case(Case("x", Scenario(n_houses=5)), naive)
