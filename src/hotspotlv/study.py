"""
The study runner: one case → sweep → Monte Carlo → fix comparison.

    Case = scenario (houses, heating mix, EV/PV shares, seed)
         + transformer size
         + optional fix (smart charging, battery)
                │
                ▼  run_case
    Neighbourhood → power flows (fast proxy or full) → thermal year → verdict
                │
      ┌─────────┼──────────────┐
      ▼         ▼              ▼
    sweep     monte_carlo    compare_fixes
    (vary one  (seeds 0…N,    (baseline, 800 kVA, smart charging, battery:
     knob)     spread)         same houses, same weather, same seed)

Fast mode (default) fits the feeder proxy on ~300 real power flows of the
case and evaluates the rest from it (see proxy.py); every result carries
`fast=True/False` so a table can say so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

import pandas as pd

from hotspotlv.battery import BatteryParams, apply_battery, size_for_cap
from hotspotlv.feeder import build_feeder
from hotspotlv.neighbourhood import Neighbourhood, Scenario, build_neighbourhood
from hotspotlv.proxy import fit_proxy
from hotspotlv.thermal import ThermalParams, ThermalResult, simulate
from hotspotlv.timeseries import YearResult, run_year
from hotspotlv.verdict import Verdict, judge


@dataclass(frozen=True)
class Case:
    name: str
    scenario: Scenario
    trafo_kva: int = 500
    battery: BatteryParams | None = None
    thermal: ThermalParams | None = None          # None → ThermalParams.for_transformer(trafo_kva)


@dataclass
class CaseResult:
    case: Case
    neighbourhood: Neighbourhood
    year: YearResult
    thermal: ThermalResult
    verdict: Verdict
    fast: bool
    proxy_fit_error: dict = field(default_factory=dict)
    runtime_s: float = 0.0

    def metrics(self) -> dict:
        """One flat row for tables and sweeps."""
        v = self.verdict
        return {
            "case": self.case.name,
            "houses": self.case.scenario.n_houses,
            "kVA": self.case.trafo_kva,
            "verdict": v.level,
            "peak_loading_pct": v.on_paper["peak load (% of nameplate)"],
            "hours_above_80": v.on_paper["hours above 80 % of nameplate"],
            "hours_above_100": v.on_paper["hours above 100 % of nameplate"],
            "life_days_per_year": v.really["days of life used per year"],
            "max_hot_spot_c": v.really["max hot-spot (°C)"],
            "hours_hot_spot_above_98": v.really["hours hot-spot above 98 °C"],
            "max_load_pu": v.really["max load (× rated)"],
            "voltage_bad_hours": v.really["hours with voltage outside ±10 %"],
            "max_cable_pct": v.really["max main-cable loading (%)"],
            "energy_mwh": self.year.summary()["energy_mwh"],
            "fast": self.fast,
        }


def run_case(case: Case, weather: pd.DataFrame, fast: bool = True,
             neighbourhood: Neighbourhood | None = None) -> CaseResult:
    t0 = time.perf_counter()
    nb = neighbourhood or build_neighbourhood(case.scenario, weather)
    net = build_feeder(n_houses=case.scenario.n_houses, trafo_kva=case.trafo_kva)
    fit_error = {}
    if fast:
        proxy = fit_proxy(net, nb.cons_kw, nb.gen_kw)
        year = proxy.evaluate(nb.cons_kw, nb.gen_kw)
        fit_error = proxy.fit_error
    else:
        year = run_year(net, nb.cons_kw, nb.gen_kw)
    if case.battery is not None:
        year = apply_battery(year, case.battery)
    params = case.thermal or ThermalParams.for_transformer(case.trafo_kva)
    thermal = simulate(year.hourly["loading_pct"] / 100, weather["temp_c"], params)
    verdict = judge(year, thermal)
    return CaseResult(case, nb, year, thermal, verdict, fast, fit_error, time.perf_counter() - t0)


def sweep(base: Case, weather: pd.DataFrame, knob: str, values, fast: bool = True) -> pd.DataFrame:
    """Vary one scenario knob (e.g. 'n_houses', 'ev_share') and collect the metrics per value."""
    rows = []
    for v in values:
        sc = replace(base.scenario, **{knob: v}, name=f"{base.scenario.name} {knob}={v}")
        res = run_case(replace(base, scenario=sc, name=f"{base.name} {knob}={v}"), weather, fast=fast)
        rows.append({knob: v, **res.metrics()})
    return pd.DataFrame(rows).set_index(knob)


def monte_carlo(base: Case, weather: pd.DataFrame, seeds=range(10), fast: bool = True) -> pd.DataFrame:
    """Same case, different dice (which houses get EVs/PV/heat pumps, when EVs charge…)."""
    rows = []
    for s in seeds:
        sc = replace(base.scenario, seed=int(s))
        res = run_case(replace(base, scenario=sc), weather, fast=fast)
        rows.append({"seed": int(s), **res.metrics()})
    return pd.DataFrame(rows).set_index("seed")


def summarise_monte_carlo(mc: pd.DataFrame) -> pd.DataFrame:
    cols = ["peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year", "max_hot_spot_c"]
    return mc[cols].describe(percentiles=[0.1, 0.5, 0.9]).T[["mean", "min", "10%", "50%", "90%", "max"]]


def compare_fixes(base: Case, weather: pd.DataFrame, fast: bool = True,
                  battery_cap_share: float = 0.8, bigger_kva: int = 800) -> dict[str, CaseResult]:
    """Baseline vs the three fixes on the SAME houses and dice (same seed)."""
    baseline = run_case(base, weather, fast=fast)
    out = {"baseline": baseline}
    # 1. bigger transformer — same neighbourhood, new transformer
    out[f"{bigger_kva} kVA"] = run_case(replace(base, name=f"{bigger_kva} kVA", trafo_kva=bigger_kva),
                                        weather, fast=fast, neighbourhood=baseline.neighbourhood)
    # 2. smart charging — EVs start 22:00–01:00 (same cars, same energy)
    smart_sc = replace(base.scenario, ev=replace(base.scenario.ev, smart=True))
    out["smart charging"] = run_case(replace(base, name="smart charging", scenario=smart_sc), weather, fast=fast)
    # 3. battery sized from the baseline's energy above the cap (a share of nameplate, in kVA)
    try:
        b = size_for_cap(baseline.year, battery_cap_share * base.trafo_kva)
        out[f"battery {b.power_kw:.0f} kW / {b.energy_kwh:.0f} kWh"] = run_case(
            replace(base, name="battery", battery=b), weather, fast=fast, neighbourhood=baseline.neighbourhood)
    except ValueError:
        pass                                                   # never above the cap: no battery to size
    return out


def fixes_table(results: dict[str, CaseResult]) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        m = r.metrics()
        rows.append({"fix": name, "verdict": m["verdict"], "peak %": round(m["peak_loading_pct"], 1),
                     "h > 80 %": m["hours_above_80"], "h > 100 %": m["hours_above_100"],
                     "life days/yr": round(m["life_days_per_year"], 3), "max hot-spot °C": round(m["max_hot_spot_c"], 1)})
    return pd.DataFrame(rows).set_index("fix")


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    base = Case("Västerås 2030", Scenario("2030", n_houses=45, ev_share=0.6, pv_share=0.4,
                                           heating_mix={"ground_source": 0.30, "air_water": 0.30,
                                                        "direct_electric": 0.20, "none": 0.20}))
    t0 = time.perf_counter()
    r = run_case(base, wx)
    print(f"{base.name}: {r.verdict.headline()}  ({r.runtime_s:.1f} s, fast={r.fast}, "
          f"proxy loading error {r.proxy_fit_error.get('loading_pct_max_abs', 0):.2f} pp)")
    print("  on paper:", r.verdict.on_paper)
    print("  really:  ", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.verdict.really.items()})
    print("\nHosting-capacity sweep over number of houses (2030 mix):")
    sw = sweep(base, wx, "n_houses", [45, 60, 75, 90, 105, 120, 150])
    print(sw[["verdict", "peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year",
              "max_hot_spot_c", "voltage_bad_hours", "max_cable_pct"]].round(2).to_string())
    print(f"\nsweep in {time.perf_counter() - t0:.0f} s")
