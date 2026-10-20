"""
Assemble a neighbourhood: N houses, each with its own base load and — depending
on the scenario's shares — a heat pump, an EV charger and/or a PV system.

    scenario:  hp_share 0.6, ev_share 0.4, pv_share 0.3, seed 7
                        │
                        ▼  roll the dice once per house
    house 1: base + HP        + EV
    house 2: base        + PV
    house 3: base + HP   + PV + EV
    ...
                        │
                        ▼
    P[t, house]  hourly kW, 8760 × N   (what the power flow eats, hour by hour)

Everything random is driven by one seed → the same scenario is reproducible,
and Monte Carlo = run the same scenario with seeds 0, 1, 2, …
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from hotspotlv.loads.base import base_load_profiles
from hotspotlv.loads.ev import EVParams, ev_charging_profile
from hotspotlv.loads.heatpump import HeatPumpParams, heat_pump_profile
from hotspotlv.loads.pv import draw_kwp, pv_profile


@dataclass(frozen=True)
class Scenario:
    name: str = "today"
    n_houses: int = 45
    hp_share: float = 0.6      # share of houses with a heat pump
    ev_share: float = 0.25     # share with a home-charged EV
    pv_share: float = 0.2      # share with rooftop PV
    hp: HeatPumpParams = field(default_factory=HeatPumpParams)
    ev: EVParams = field(default_factory=EVParams)
    seed: int = 0


@dataclass
class Neighbourhood:
    scenario: Scenario
    houses: pd.DataFrame          # one row per house: has_hp, has_ev, has_pv, pv_kwp, …
    p_kw: pd.DataFrame            # 8760 × N total kW per house (net, PV negative)
    parts: dict[str, pd.Series]   # aggregate kW per component: base, hp, ev, pv

    @property
    def total_kw(self) -> pd.Series:
        return self.p_kw.sum(axis=1)


def build_neighbourhood(scenario: Scenario, weather: pd.DataFrame) -> Neighbourhood:
    """Roll the dice for every house and build the hourly kW matrix."""
    rng = np.random.default_rng(scenario.seed)
    n = scenario.n_houses
    idx = weather.index

    base = base_load_profiles(idx, n, seed=scenario.seed)
    base.columns = range(n)

    has_hp = rng.random(n) < scenario.hp_share
    has_ev = rng.random(n) < scenario.ev_share
    has_pv = rng.random(n) < scenario.pv_share
    pv_kwp = np.where(has_pv, [draw_kwp(rng) for _ in range(n)], 0.0)

    hp_one = heat_pump_profile(weather["temp_c"], scenario.hp)["total"]  # same house physics for all
    hp = pd.DataFrame({j: (hp_one if has_hp[j] else 0.0) for j in range(n)}, index=idx)
    ev = pd.DataFrame({j: (ev_charging_profile(idx, scenario.ev, seed=scenario.seed * 1000 + j) if has_ev[j] else 0.0)
                       for j in range(n)}, index=idx)
    pv = pd.DataFrame({j: (pv_profile(weather["pv_kw_per_kwp"], pv_kwp[j]) if has_pv[j] else 0.0)
                       for j in range(n)}, index=idx)

    p_kw = base + hp + ev + pv
    p_kw.columns = [f"house{j + 1}" for j in range(n)]
    houses = pd.DataFrame({
        "has_hp": has_hp, "has_ev": has_ev, "has_pv": has_pv, "pv_kwp": pv_kwp.round(1),
        "base_kwh": base.sum().round(0).to_numpy(),
        "hp_kwh": hp.sum().round(0).to_numpy(),
        "ev_kwh": ev.sum().round(0).to_numpy(),
        "pv_kwh": (-pv.sum()).round(0).to_numpy(),
    }, index=p_kw.columns)
    parts = {"base": base.sum(axis=1), "hp": hp.sum(axis=1), "ev": ev.sum(axis=1), "pv": pv.sum(axis=1)}
    return Neighbourhood(scenario, houses, p_kw, parts)


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    for sc in (Scenario("no electrification", hp_share=0, ev_share=0, pv_share=0),
               Scenario("today", hp_share=0.6, ev_share=0.25, pv_share=0.2),
               Scenario("2030", hp_share=0.8, ev_share=0.6, pv_share=0.4)):
        nb = build_neighbourhood(sc, wx)
        tot = nb.total_kw
        peak_t = tot.idxmax().tz_convert("Europe/Stockholm")
        print(f"{sc.name:18s} HP {nb.houses.has_hp.sum():2d} EV {nb.houses.has_ev.sum():2d} PV {nb.houses.has_pv.sum():2d} | "
              f"peak {tot.max():6.1f} kW on {peak_t:%d %b %H:%M} (T {wx['temp_c'][tot.idxmax()]:+.1f} °C) | "
              f"min {tot.min():6.1f} kW | energy {tot.sum() / 1000:.0f} MWh/yr | "
              f"hours > 475 kW: {(tot > 475).sum()}")
