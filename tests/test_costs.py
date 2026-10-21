"""The cost table is a labelled sensitivity: ranges, sources, service life, and what each fix buys."""

import pytest

from hotspotlv.costs import CostAssumptions, cost_table
from hotspotlv.neighbourhood import Scenario
from hotspotlv.study import Case, compare_fixes
from hotspotlv.weather import get_weather


@pytest.fixture(scope="module")
def cmp():
    wx = get_weather(2023)
    return compare_fixes(Case("c", Scenario(n_houses=70, ev_share=0.7, pv_share=0.3, seed=3,
                                            heating_mix={"direct_electric": 0.5, "air_water": 0.3, "none": 0.2})), wx)


def test_cost_table_rows_and_ranges(cmp):
    t = cost_table(cmp)
    assert "baseline" not in t.index and "800 kVA" in t.index
    assert (t["capex low (kSEK)"] <= t["capex high (kSEK)"]).all()
    assert t.loc["800 kVA", "capex low (kSEK)"] == 209 and t.loc["800 kVA", "capex high (kSEK)"] == 209   # unit swap, Ei 2022
    assert t.loc["800 kVA", "service life (yr)"] == 40
    bat = [i for i in t.index if i.startswith("battery")]
    if bat:
        kwh = cmp.results[bat[0]].case.battery.energy_kwh
        assert t.loc[bat[0], "capex low (kSEK)"] == pytest.approx(kwh * 4.0, abs=1)
        assert t.loc[bat[0], "service life (yr)"] == 15
    n_ev = int(cmp.results["baseline"].neighbourhood.houses.has_ev.sum())
    assert t.loc["spread 22–06", "capex high (kSEK)"] == pytest.approx(n_ev * 5.0, abs=1)
    assert t.loc["night timer 22–01", "capex high (kSEK)"] == 0
    assert "Indicative" in t.attrs["title"] and "2022" in t.attrs["note"]
    assert t.loc["800 kVA", "hours > 80 % avoided"] > 0
    assert (t["peak kVA avoided"] >= 0).all()
    # reinforcement-equivalent value: 4.4 SEK/kW/yr x 50 yr = 220 SEK/kW
    row = t.loc["800 kVA"]
    assert row["reinforcement-equivalent value (kSEK)"] == pytest.approx(row["peak kVA avoided"] * 0.22, abs=1)
    assert t["verdict before → after"].str.contains("→").all()


def test_new_kiosk_toggle(cmp):
    t = cost_table(cmp, CostAssumptions(needs_new_kiosk=True))
    assert t.loc["800 kVA", "capex low (kSEK)"] == 490 and t.loc["800 kVA", "capex high (kSEK)"] == 634


def test_assumptions_validated():
    with pytest.raises(ValueError):
        CostAssumptions(battery_sek_per_kwh=(6000, 4000))
    with pytest.raises(ValueError):
        CostAssumptions(transformer_unit_ksek=(-1, 10))
    custom = CostAssumptions(transformer_unit_ksek=(300, 400))
    assert custom.transformer_swap_ksek == (300, 400)
