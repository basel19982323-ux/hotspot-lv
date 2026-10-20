"""
Assemble a neighbourhood: N houses, each with its own base load, a heating
type, and — depending on the scenario's shares — an EV charger and/or PV.

    scenario:  heating mix {air_water 45 %, ground_source 15 %,
                            direct_electric 20 %, none 20 %},
               ev_share 0.4, pv_share 0.3, seed 7
                        │
                        ▼  roll the dice once per house
    house 1: base + air/water HP            + EV
    house 2: base + direct electric   + PV
    house 3: base + ground source     + PV  + EV
    house 4: base (district heating / wood)
    ...
                        │
                        ▼
    P[t, house]  hourly kW, 8760 × N   (what the power flow eats, hour by hour)

Every house also gets its own size (UA) and pump size, so houses do not all
peak at exactly the same kW. Everything random is driven by one seed → the same
scenario is reproducible, and Monte Carlo = seeds 0, 1, 2, …
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from hotspotlv.loads.base import base_load_profiles
from hotspotlv.loads.ev import EVParams, ev_charging_profile
from hotspotlv.loads.heatpump import HeatPumpParams, heat_pump_profile
from hotspotlv.loads.pv import draw_kwp, pv_profile

# Heating mix of Swedish småhus — an ASSUMPTION for the default scenario.
# Energimyndigheten EN0102 gives the official split; cite it before publishing.
DEFAULT_HEATING_MIX = {"air_water": 0.45, "ground_source": 0.15, "direct_electric": 0.20, "none": 0.20}
UA_SD_FRACTION = 0.20      # house-to-house spread of heat loss (± 20 %)
Q_NOM_SD_FRACTION = 0.15   # spread of pump size


@dataclass(frozen=True)
class Scenario:
    name: str = "today"
    n_houses: int = 45
    heating_mix: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_HEATING_MIX))
    ev_share: float = 0.25     # share of houses with a home-charged EV
    pv_share: float = 0.20     # share with rooftop PV
    hp: HeatPumpParams = field(default_factory=HeatPumpParams)   # the "typical" house; jittered per house
    ev: EVParams = field(default_factory=EVParams)
    seed: int = 0

    def __post_init__(self):
        total = sum(self.heating_mix.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"heating_mix must sum to 1, got {total}")


@dataclass
class Neighbourhood:
    scenario: Scenario
    houses: pd.DataFrame          # one row per house: heating, has_ev, has_pv, pv_kwp, …
    p_kw: pd.DataFrame            # 8760 × N total kW per house (net, PV negative)
    parts: dict[str, pd.Series]   # aggregate kW per component: base, heat, ev, pv

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

    kinds = list(scenario.heating_mix)
    heating = rng.choice(kinds, size=n, p=[scenario.heating_mix[k] for k in kinds])
    has_ev = rng.random(n) < scenario.ev_share
    has_pv = rng.random(n) < scenario.pv_share
    pv_kwp = np.where(has_pv, [draw_kwp(rng) for _ in range(n)], 0.0)

    heat_cols, ua, qnom = {}, np.zeros(n), np.zeros(n)
    for j in range(n):
        if heating[j] == "none":
            heat_cols[j] = 0.0
            continue
        ua[j] = scenario.hp.ua_kw_per_k * rng.normal(1.0, UA_SD_FRACTION)
        qnom[j] = scenario.hp.q_nom_kw * rng.normal(1.0, Q_NOM_SD_FRACTION)
        p = scenario.hp.with_(kind=heating[j], ua_kw_per_k=max(0.08, ua[j]), q_nom_kw=max(3.0, qnom[j]))
        heat_cols[j] = heat_profile = heat_pump_profile(weather["temp_c"], p)["total"]
    heat = pd.DataFrame(heat_cols, index=idx)
    ev = pd.DataFrame({j: (ev_charging_profile(idx, scenario.ev, seed=scenario.seed * 1000 + j) if has_ev[j] else 0.0)
                       for j in range(n)}, index=idx)
    pv = pd.DataFrame({j: (pv_profile(weather["pv_kw_per_kwp"], pv_kwp[j]) if has_pv[j] else 0.0)
                       for j in range(n)}, index=idx)

    p_kw = base + heat + ev + pv
    p_kw.columns = [f"house{j + 1}" for j in range(n)]
    houses = pd.DataFrame({
        "heating": heating, "ua_kw_per_k": ua.round(3), "has_ev": has_ev, "has_pv": has_pv,
        "pv_kwp": pv_kwp.round(1),
        "base_kwh": base.sum().round(0).to_numpy(),
        "heat_kwh": heat.sum().round(0).to_numpy(),
        "ev_kwh": ev.sum().round(0).to_numpy(),
        "pv_kwh": (-pv.sum()).round(0).to_numpy(),
    }, index=p_kw.columns)
    parts = {"base": base.sum(axis=1), "heat": heat.sum(axis=1), "ev": ev.sum(axis=1), "pv": pv.sum(axis=1)}
    return Neighbourhood(scenario, houses, p_kw, parts)


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    scenarios = [
        Scenario("no heating load", heating_mix={"none": 1.0}, ev_share=0, pv_share=0),
        Scenario("today", ev_share=0.25, pv_share=0.20),
        Scenario("2030", heating_mix={"air_water": 0.55, "ground_source": 0.20, "direct_electric": 0.10, "none": 0.15},
                 ev_share=0.60, pv_share=0.40),
    ]
    for sc in scenarios:
        nb = build_neighbourhood(sc, wx)
        tot = nb.total_kw
        peak_t = tot.idxmax().tz_convert("Europe/Stockholm")
        mix = nb.houses.heating.value_counts().to_dict()
        print(f"{sc.name:16s} {mix} EV {nb.houses.has_ev.sum():2d} PV {nb.houses.has_pv.sum():2d} | "
              f"peak {tot.max():6.1f} kW on {peak_t:%d %b %H:%M} (T {wx['temp_c'][tot.idxmax()]:+.1f} °C) | "
              f"min {tot.min():6.1f} kW | {tot.sum() / 1000:.0f} MWh/yr | hours > 400 kW: {(tot > 400).sum()}")
