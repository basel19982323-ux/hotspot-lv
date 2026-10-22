"""
The study runner: one case → sweep → Monte Carlo → fix comparison.

    Case = scenario (houses, heating mix, EV/PV shares, seed)
         + transformer size
         + optional fix (smart charging, battery)
                │
                ▼  run_case
    Neighbourhood → power flows (fast proxy + exact re-runs near limits, or full)
                  → thermal year (kiosk constants for the size) → verdict
                │
      ┌─────────┼──────────────┐
      ▼         ▼              ▼
    sweep     monte_carlo    compare_fixes
    (vary one  (seeds 0…N,    (baseline, 800 kVA, night-timer charging, spread
     knob)     spread)         charging, battery — same houses, same dice)

Fast mode (default) fits the feeder proxy on ~300 real power flows of the
case, evaluates the year from it, then re-runs the exact power flow for every
hour near a verdict limit (see proxy.py). Every result carries `fast`, the
proxy's fit error and the number of exactly computed hours.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field, replace

import pandas as pd

from hotspotlv.battery import BatteryParams, BatterySizing, apply_battery, size_by_simulation
from hotspotlv.feeder import build_feeder
from hotspotlv.neighbourhood import Neighbourhood, Scenario, build_neighbourhood
from hotspotlv.proxy import fit_proxy, refine_mask, refine_near_limits
from hotspotlv.thermal import KIOSK_AIR_RISE_K, ThermalParams, ThermalResult, simulate
from hotspotlv.timeseries import YearResult, run_year
from hotspotlv.verdict import Verdict, judge

SCENARIO_FIELDS = {f.name for f in dataclasses.fields(Scenario)}
MAX_REFINE_HOURS = 1500          # beyond this, the full power flow is cheaper than proxy + refinement
MAX_PROXY_LOADING_ERROR_PP = 1.0  # a worse fit means the feeder is outside the proxy's comfort zone


@dataclass(frozen=True)
class Case:
    name: str
    scenario: Scenario
    trafo_kva: int = 500
    n_feeders: int = 3                            # radial cables from the busbar
    battery: BatteryParams | None = None
    enclosure: str = "kiosk"                      # Swedish nätstation norm; "outdoor" for a pole/pad unit
    thermal: ThermalParams | None = None          # explicit constants override size + enclosure


@dataclass
class CaseResult:
    case: Case
    neighbourhood: Neighbourhood
    year: YearResult
    thermal: ThermalResult
    verdict: Verdict
    fast: bool
    proxy_fit_error: dict = field(default_factory=dict)
    exact_hours: int = 0                          # hours re-run with the full power flow (fast mode)
    battery_sizing: BatterySizing | None = None
    runtime_s: float = 0.0
    mode_note: str = ""                           # how the power flows were computed, in words

    def metrics(self) -> dict:
        """One flat row for tables and sweeps."""
        v = self.verdict
        h = self.year.hourly
        bat = self.battery_sizing
        return {
            "case": self.case.name,
            "houses": self.case.scenario.n_houses,
            "kVA": self.case.trafo_kva,
            "verdict": v.level,
            "why": v.headline(),
            "peak_loading_pct": v.on_paper["peak load (% of nameplate)"],
            "hours_above_80": v.on_paper["hours above 80 % of nameplate"],
            "hours_above_100": v.on_paper["hours above 100 % of nameplate"],
            "life_days_per_year": v.really["days of life used per year"],
            "max_hot_spot_c": v.really["max hot-spot (°C)"],
            "hours_hot_spot_above_98": v.really["hours hot-spot above 98 °C"],
            "max_load_pu": v.really["max load (× rated)"],
            "voltage_bad_hours": v.really["hours with voltage outside ±10 %"],
            "voltage_plan_hours": v.really["hours with voltage outside the planning band"],
            "max_cable_pct": v.really["max cable loading (%)"],
            "energy_mwh": self.year.summary()["energy_mwh"],
            "battery_kw": self.case.battery.power_kw if self.case.battery else 0.0,
            "battery_kwh": self.case.battery.energy_kwh if self.case.battery else 0.0,
            "battery_hours_above_cap": bat.hours_above_cap_after if bat else float("nan"),
            "battery_cycles_per_year": bat.full_cycles_per_year if bat else float("nan"),
            "fast": self.fast,
            "exact_hours": self.exact_hours,
        }


def thermal_params_for(case: Case) -> ThermalParams:
    return case.thermal or ThermalParams.for_transformer(case.trafo_kva, enclosure=case.enclosure)


def run_case(case: Case, weather: pd.DataFrame, fast: bool = True,
             neighbourhood: Neighbourhood | None = None,
             battery_sizing: BatterySizing | None = None) -> CaseResult:
    t0 = time.perf_counter()
    if not isinstance(weather.index, pd.DatetimeIndex) or weather.index.tz is None:
        raise ValueError("weather index must be a tz-aware DatetimeIndex (UTC) — use hotspotlv.weather.get_weather")
    nb = neighbourhood or build_neighbourhood(case.scenario, weather)
    net = build_feeder(n_houses=case.scenario.n_houses, n_feeders=case.n_feeders, trafo_kva=case.trafo_kva)
    fit_error, exact_hours = {}, 0
    if fast:
        proxy = fit_proxy(net, nb.cons_kw, nb.gen_kw)
        year = proxy.evaluate(nb.cons_kw, nb.gen_kw)
        fit_error = proxy.fit_error
        n_refine = int(refine_mask(year, fit_error).sum())
        if n_refine > MAX_REFINE_HOURS or fit_error["loading_pct_max_abs"] > MAX_PROXY_LOADING_ERROR_PP:
            year = run_year(net, nb.cons_kw, nb.gen_kw)        # heavy feeder: the full run is cheaper and exact
            fast, exact_hours = False, len(year.hourly)
            mode_note = (f"full power flow for every hour: the fitted proxy would have needed {n_refine} exact "
                         f"hours (limit {MAX_REFINE_HOURS}) or its loading error was {fit_error['loading_pct_max_abs']:.1f} "
                         f"pp (limit {MAX_PROXY_LOADING_ERROR_PP:g})")
        else:
            year, exact_hours = refine_near_limits(net, year, nb.cons_kw, nb.gen_kw, fit_error=fit_error)
            mode_note = (f"fast mode: fitted proxy ({proxy.n_samples} power flows, loading error "
                         f"{fit_error['loading_pct_max_abs']:.2f} pp) plus {exact_hours} hours computed exactly near the limits")
    else:
        year = run_year(net, nb.cons_kw, nb.gen_kw)
        mode_note = "full power flow for every hour"
    if case.battery is not None:
        year = apply_battery(year, case.battery)
    params = case.thermal or thermal_params_for(case)
    thermal = simulate(year.hourly["loading_pct"] / 100, weather["temp_c"], params)
    verdict = judge(year, thermal)
    verdict.notes.append(enclosure_note(params, case.trafo_kva))
    verdict.notes.append(mode_note)
    return CaseResult(case, nb, year, thermal, verdict, fast, fit_error, exact_hours, battery_sizing,
                      time.perf_counter() - t0, mode_note)


def enclosure_note(params: ThermalParams, kva: float | None = None) -> str:
    if params.enclosure == "kiosk":
        text = (f"transformer in a kiosk: half the kiosk air rise of IEC 60076-7:2005 Table 6 (2018: Table 5) is added to "
                f"the top-oil rise, +{params.enclosure_rise_k:g} K (ageing ≈ ×{2 ** (params.enclosure_rise_k / 6):.1f} at "
                "rated load vs an outdoor unit)")
        sizes = sorted(KIOSK_AIR_RISE_K)
        if kva is not None and not sizes[0] <= kva <= sizes[-1]:
            text += f"; the table covers {sizes[0]}–{sizes[-1]} kVA, so its {min(max(kva, sizes[0]), sizes[-1]):g} kVA value is used"
        return text
    return "transformer outdoors: no enclosure correction"


def sweep(base: Case, weather: pd.DataFrame, knob: str, values, fast: bool = True, progress=None) -> pd.DataFrame:
    """Vary one scenario knob (e.g. 'n_houses', 'ev_share') and collect the metrics per value.
    `progress(done, total, label)` is called before each run."""
    if knob not in SCENARIO_FIELDS or knob in ("name", "hp", "ev"):
        raise ValueError(f"knob must be a numeric Scenario field, got {knob!r}")
    values = list(values)
    rows = []
    for i, v in enumerate(values):
        if progress:
            progress(i, len(values), f"{knob} = {v}")
        sc = replace(base.scenario, **{knob: v}, name=f"{base.scenario.name} {knob}={v}")
        res = run_case(replace(base, scenario=sc, name=f"{base.name} {knob}={v}"), weather, fast=fast)
        rows.append({knob: v, **res.metrics()})
    return pd.DataFrame(rows).set_index(knob)


def monte_carlo(base: Case, weather: pd.DataFrame, seeds=range(10), fast: bool = True, progress=None) -> pd.DataFrame:
    """Same case, different dice (which houses get EVs/PV/heat pumps, when EVs charge…).
    `progress(done, total, label)` is called before each run."""
    seeds = list(seeds)
    if any(int(s) != s or s < 0 for s in seeds):
        raise ValueError("seeds must be non-negative integers")
    rows = []
    for i, s in enumerate(seeds):
        if progress:
            progress(i, len(seeds), f"draw {int(s)}")
        sc = replace(base.scenario, seed=int(s))
        res = run_case(replace(base, scenario=sc), weather, fast=fast)
        rows.append({"seed": int(s), **res.metrics()})
    return pd.DataFrame(rows).set_index("seed")


def summarise_monte_carlo(mc: pd.DataFrame) -> pd.DataFrame:
    cols = ["peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year", "max_hot_spot_c",
            "hours_hot_spot_above_98", "max_load_pu", "voltage_bad_hours", "voltage_plan_hours", "max_cable_pct"]
    return mc[cols].describe(percentiles=[0.1, 0.5, 0.9]).T[["mean", "min", "10%", "50%", "90%", "max"]]


@dataclass
class FixComparison:
    results: dict[str, CaseResult]
    notes: list[str] = field(default_factory=list)


def compare_fixes(base: Case, weather: pd.DataFrame, fast: bool = True,
                  battery_cap_share: float = 0.8, bigger_kva: int = 800, progress=None) -> FixComparison:
    """Baseline vs the fixes on the SAME houses and dice (same seed).
    Smart charging comes in two flavours because they behave differently:
    'night timer' = what Swedish hourly-price customers do today (start 22:00–01:00,
    Energiforsk 2023:966) and 'spread' = coordinated starts spread over 22:00–06:00."""
    if bigger_kva <= base.trafo_kva:
        raise ValueError(f"bigger_kva must exceed the base transformer ({base.trafo_kva} kVA), got {bigger_kva}")
    notes = []
    ev0 = base.scenario.ev
    if ev0.smart and ev0.smart_start_min_h == 22.0:
        same = "night timer 22–01" if ev0.smart_start_max_h == 25.0 else ("spread 22–06" if ev0.smart_start_max_h == 30.0 else None)
        if same:
            notes.append(f"the baseline already charges on the {same}: that fix row repeats the baseline (and costs nothing extra)")
    total = 5
    tick = (lambda i, label: progress(i, total, label)) if progress else (lambda i, label: None)
    tick(0, "baseline")
    baseline = run_case(base, weather, fast=fast)
    out = {"baseline": baseline}
    # 1. bigger transformer — same neighbourhood, new transformer and ITS thermal constants
    tick(1, f"{bigger_kva} kVA")
    big = replace(base, name=f"{bigger_kva} kVA", trafo_kva=bigger_kva, thermal=None)
    out[f"{bigger_kva} kVA"] = run_case(big, weather, fast=fast, neighbourhood=baseline.neighbourhood)
    # 2. smart charging, two flavours — same cars, same energy, different start hours
    tick(2, "night timer 22–01")
    timer = replace(base.scenario, ev=replace(base.scenario.ev, smart=True, smart_start_min_h=22.0, smart_start_max_h=25.0))
    out["night timer 22–01"] = run_case(replace(base, name="night timer 22–01", scenario=timer), weather, fast=fast)
    tick(3, "spread 22–06")
    spread = replace(base.scenario, ev=replace(base.scenario.ev, smart=True, smart_start_min_h=22.0, smart_start_max_h=30.0))
    out["spread 22–06"] = run_case(replace(base, name="spread 22–06", scenario=spread), weather, fast=fast)
    # 3. battery sized by simulation to hold a share of nameplate (kVA)
    tick(4, "battery")
    cap = battery_cap_share * base.trafo_kva
    if not (baseline.year.hourly["s_kva"] > cap).any():
        notes.append(f"battery: transformer never exceeds {cap:.0f} kVA — no battery needed")
    else:
        sizing = size_by_simulation(baseline.year, cap)
        b = sizing.params
        label = f"battery {b.power_kw:.0f} kW / {b.energy_kwh:.0f} kWh"
        if not sizing.feasible:
            notes.append(f"battery: even {b.energy_kwh:.0f} kWh cannot hold {cap:.0f} kVA every hour "
                         f"({sizing.hours_above_cap_after:.0f} h left) — the problem is energy over a cold spell, not a peak")
        out[label] = run_case(replace(base, name=label, battery=b), weather, fast=fast,
                              neighbourhood=baseline.neighbourhood, battery_sizing=sizing)
    return FixComparison(out, notes)


def fixes_table(cmp: FixComparison) -> pd.DataFrame:
    rows = []
    for name, r in cmp.results.items():
        m = r.metrics()
        rows.append({"fix": name, "verdict": m["verdict"], "peak %": round(m["peak_loading_pct"], 1),
                     "h > 80 %": m["hours_above_80"], "h > 100 %": m["hours_above_100"],
                     "life days/yr": round(m["life_days_per_year"], 3), "max hot-spot °C": round(m["max_hot_spot_c"], 1),
                     "voltage bad h": m["voltage_bad_hours"], "cable %": round(m["max_cable_pct"], 1),
                     "battery h above cap": m["battery_hours_above_cap"], "why": m["why"]})
    return pd.DataFrame(rows).set_index("fix")


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    base = Case("Västerås 2030", Scenario("2030", n_houses=45, ev_share=0.6, pv_share=0.4,
                                           heating_mix={"ground_source": 0.30, "air_water": 0.30,
                                                        "direct_electric": 0.20, "none": 0.20}))
    t0 = time.perf_counter()
    r = run_case(base, wx)
    print(f"{base.name}: {r.verdict.headline()}  ({r.runtime_s:.1f} s, fast={r.fast}, exact hours {r.exact_hours}, "
          f"proxy loading error {r.proxy_fit_error.get('loading_pct_max_abs', 0):.2f} pp)")
    print("  on paper:", r.verdict.on_paper)
    print("  really:  ", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.verdict.really.items()})
    print("\nHosting-capacity sweep over number of houses (2030 mix, kiosk):")
    sw = sweep(base, wx, "n_houses", [45, 60, 75, 90, 105, 120])
    print(sw[["verdict", "peak_loading_pct", "hours_above_80", "hours_above_100", "life_days_per_year",
              "max_hot_spot_c", "voltage_bad_hours", "voltage_plan_hours", "max_cable_pct", "exact_hours"]].round(2).to_string())
    print(f"\nsweep in {time.perf_counter() - t0:.0f} s")
