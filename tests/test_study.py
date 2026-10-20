"""The study runner: one case end to end, a sweep, and the fix comparison (small, fast)."""

import pytest

from hotspotlv.neighbourhood import Scenario
from hotspotlv.study import Case, compare_fixes, fixes_table, monte_carlo, run_case, summarise_monte_carlo, sweep
from hotspotlv.weather import get_weather


@pytest.fixture(scope="module")
def wx():
    return get_weather(2023)


@pytest.fixture(scope="module")
def heavy():
    # many houses on 500 kVA so that fixes have something to fix
    return Case("heavy", Scenario("heavy", n_houses=120, ev_share=0.7, pv_share=0.3,
                                  heating_mix={"direct_electric": 0.5, "air_water": 0.3, "none": 0.2}, seed=3))


def test_run_case_fast_end_to_end(wx):
    r = run_case(Case("small", Scenario(n_houses=20, seed=1)), wx)
    assert r.fast and r.proxy_fit_error["loading_pct_max_abs"] < 0.5
    assert len(r.year.hourly) == 8760 and len(r.thermal.hourly) == 8760
    assert r.verdict.level in ("OK", "Watch", "At risk")
    m = r.metrics()
    assert m["houses"] == 20 and m["kVA"] == 500 and m["fast"] is True
    assert m["life_days_per_year"] < 1                      # 20 houses: practically no ageing


def test_heavy_case_is_flagged(wx, heavy):
    r = run_case(heavy, wx)
    assert r.verdict.on_paper["hours above 80 % of nameplate"] > 0
    assert r.verdict.level in ("Watch", "At risk")


def test_sweep_is_monotonic_in_houses(wx):
    base = Case("sweep", Scenario(n_houses=30, ev_share=0.5, seed=2))
    sw = sweep(base, wx, "n_houses", [30, 90])
    assert list(sw.index) == [30, 90]
    assert sw.loc[90, "peak_loading_pct"] > 2 * sw.loc[30, "peak_loading_pct"]
    assert sw.loc[90, "life_days_per_year"] > sw.loc[30, "life_days_per_year"]


def test_compare_fixes(wx, heavy):
    res = compare_fixes(heavy, wx)
    names = list(res)
    assert names[0] == "baseline" and "800 kVA" in names and "smart charging" in names
    assert any(n.startswith("battery") for n in names)
    base, big = res["baseline"], res["800 kVA"]
    # same houses: the kVA differs only by the transformer's own losses (the 800 kVA unit has fewer at this load)
    assert big.year.hourly["s_kva"].max() == pytest.approx(base.year.hourly["s_kva"].max(), rel=0.05)
    assert big.verdict.on_paper["peak load (% of nameplate)"] < base.verdict.on_paper["peak load (% of nameplate)"]
    assert big.verdict.really["days of life used per year"] < base.verdict.really["days of life used per year"]
    smart = res["smart charging"]
    assert smart.year.hourly["s_kva"].max() < base.year.hourly["s_kva"].max()
    bat = next(r for n, r in res.items() if n.startswith("battery"))
    assert bat.verdict.on_paper["hours above 80 % of nameplate"] < base.verdict.on_paper["hours above 80 % of nameplate"]
    table = fixes_table(res)
    assert set(table.columns) >= {"verdict", "peak %", "life days/yr"}


def test_monte_carlo_shape(wx):
    base = Case("mc", Scenario(n_houses=30, ev_share=0.5, seed=0))
    mc = monte_carlo(base, wx, seeds=range(3))
    assert list(mc.index) == [0, 1, 2]
    assert mc["peak_loading_pct"].nunique() == 3
    s = summarise_monte_carlo(mc)
    assert "life_days_per_year" in s.index and "90%" in s.columns
