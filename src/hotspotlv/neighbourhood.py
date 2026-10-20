"""
Assemble a neighbourhood: N houses, each with its own base load, a heating
type, and — depending on the scenario's shares — an EV charger and/or PV.

    scenario:  heating mix {ground_source 23 %, air_water 12 %,
                            direct_electric 40 %, none 25 %},
               ev_share 0.4, pv_share 0.3, seed 7
                        │
                        ▼  roll the dice once per house
    house 1: base + air/water HP            + EV (11 kW)
    house 2: base + direct electric   + PV
    house 3: base + ground source     + PV  + EV (3.7 kW)
    house 4: base (district heating / wood)
    ...
                        │
                        ▼  cap each house at its main fuse (20 A ≈ 13.9 kW),
                           the EV charger backs off first
                        ▼
    consumption[t, house], generation[t, house]   hourly kW, 8760 × N

Every house also gets its own size (UA) and pump size, so houses do not all
peak at exactly the same kW. Everything random is driven by one seed → the same
scenario is reproducible, and Monte Carlo = seeds 0, 1, 2, … Each house has
its own random stream, so house 1 is the same whether N is 5 or 45.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from hotspotlv.feeder import HOUSE_FUSE_A, fuse_kw
from hotspotlv.loads.base import base_load_profiles
from hotspotlv.loads.ev import CHARGER_KW_MIX, EVParams, ev_charging_profile
from hotspotlv.loads.heatpump import HEATING_KINDS, HeatPumpParams, heat_pump_profile
from hotspotlv.loads.pv import draw_kwp, pv_profile

# Heating mix of Swedish småhus, mapped from Energimyndigheten EN0102 (2024,
# categories overlap because many houses combine systems):
#   ground_source   = bergvärme/jordvärme 23 %
#   air_water       = luft/vatten 11 %  (+ a little exhaust-air)
#   direct_electric = enbart el 25 % + air/air and exhaust-air pumps on electric
#                     radiators (treated conservatively as electric) + half of bio+el
#   none            = fjärrvärme 15 % + biobränsle, olja/gas, övrigt + half of bio+el
DEFAULT_HEATING_MIX = {"ground_source": 0.23, "air_water": 0.12, "direct_electric": 0.40, "none": 0.25}
ALLOWED_HEATING = HEATING_KINDS + ("none",)
UA_SD_FRACTION = 0.20      # house-to-house spread of heat loss (± 20 %)
Q_NOM_SD_FRACTION = 0.15   # spread of pump size
UA_MIN, Q_NOM_MIN = 0.08, 3.0


@dataclass(frozen=True)
class Scenario:
    name: str = "today"
    n_houses: int = 45
    heating_mix: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_HEATING_MIX))
    ev_share: float = 0.25     # share of houses with a home-charged EV
    pv_share: float = 0.20     # share with rooftop PV
    charger_mix: dict[float, float] = field(default_factory=lambda: dict(CHARGER_KW_MIX))
    fuse_a: float = HOUSE_FUSE_A
    hp: HeatPumpParams = field(default_factory=HeatPumpParams)   # the "typical" house; jittered per house
    ev: EVParams = field(default_factory=EVParams)
    seed: int = 0

    def __post_init__(self):
        if self.n_houses < 1:
            raise ValueError("n_houses must be >= 1")
        for name, mix in (("heating_mix", self.heating_mix), ("charger_mix", self.charger_mix)):
            if any(v < 0 for v in mix.values()) or abs(sum(mix.values()) - 1.0) > 1e-6:
                raise ValueError(f"{name} shares must be >= 0 and sum to 1, got {mix}")
        unknown = set(self.heating_mix) - set(ALLOWED_HEATING)
        if unknown:
            raise ValueError(f"unknown heating kind(s) {unknown}; allowed: {ALLOWED_HEATING}")
        for name, v in (("ev_share", self.ev_share), ("pv_share", self.pv_share)):
            if not 0 <= v <= 1:
                raise ValueError(f"{name} must be within 0..1")
        if self.seed < 0 or self.fuse_a <= 0:
            raise ValueError("seed must be >= 0 and fuse_a > 0")


@dataclass
class Neighbourhood:
    scenario: Scenario
    houses: pd.DataFrame          # one row per house: heating, ua, q_nom, charger_kw, pv_kwp, annual kWh …
    cons_kw: pd.DataFrame         # 8760 × N consumption per house (base + heating + EV), fuse-capped
    gen_kw: pd.DataFrame          # 8760 × N PV generation per house (positive)
    parts: dict[str, pd.Series]   # aggregate kW per component (raw demand): base, heat, ev, pv, fuse_curtailed

    @property
    def p_kw(self) -> pd.DataFrame:
        """Net power per house: consumption − generation (negative = export)."""
        return self.cons_kw - self.gen_kw

    @property
    def total_kw(self) -> pd.Series:
        return self.p_kw.sum(axis=1)


def build_neighbourhood(scenario: Scenario, weather: pd.DataFrame) -> Neighbourhood:
    """Roll the dice for every house and build the hourly kW matrices."""
    root = np.random.SeedSequence(scenario.seed)
    ss_base, ss_houses = root.spawn(2)
    n = scenario.n_houses
    idx = weather.index
    cap_kw = fuse_kw(scenario.fuse_a)

    base = base_load_profiles(idx, n, seed=ss_base)
    base.columns = range(n)

    kinds = list(scenario.heating_mix)
    kind_p = [scenario.heating_mix[k] for k in kinds]
    chargers = list(scenario.charger_mix)
    charger_p = [scenario.charger_mix[c] for c in chargers]

    # every house rolls ALL its own dice from its own stream, so house j is the
    # same whether the neighbourhood has 5 or 45 houses
    heating = np.empty(n, dtype=object)
    has_ev, has_pv = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    charger_kw, pv_kwp = np.zeros(n), np.zeros(n)
    heat_cols, ev_cols, pv_cols = {}, {}, {}
    ua, qnom = np.zeros(n), np.zeros(n)
    for j, stream in enumerate(ss_houses.spawn(n)):
        ss_dice, ss_heat, ss_ev = stream.spawn(3)
        drng, hrng = np.random.default_rng(ss_dice), np.random.default_rng(ss_heat)
        heating[j] = drng.choice(kinds, p=kind_p)
        has_ev[j] = drng.random() < scenario.ev_share
        has_pv[j] = drng.random() < scenario.pv_share
        charger_kw[j] = drng.choice(chargers, p=charger_p) if has_ev[j] else 0.0
        pv_kwp[j] = draw_kwp(drng) if has_pv[j] else 0.0
        if heating[j] == "none":
            heat_cols[j] = 0.0
        else:
            ua[j] = max(UA_MIN, scenario.hp.ua_kw_per_k * hrng.normal(1.0, UA_SD_FRACTION))
            qnom[j] = max(Q_NOM_MIN, scenario.hp.q_nom_kw * hrng.normal(1.0, Q_NOM_SD_FRACTION))
            p = scenario.hp.with_(kind=heating[j], ua_kw_per_k=ua[j], q_nom_kw=qnom[j])
            if heating[j] == "ground_source":
                qnom[j] = p.design_load_kw()            # bergvärme is sized for the design load
                p = p.with_(q_nom_kw=qnom[j])
            heat_cols[j] = heat_pump_profile(weather["temp_c"], p)["total"]
        ev_cols[j] = (ev_charging_profile(idx, replace(scenario.ev, charger_kw=float(charger_kw[j])), seed=ss_ev)
                      if has_ev[j] else 0.0)
        pv_cols[j] = pv_profile(weather["pv_kw_per_kwp"], pv_kwp[j]) if has_pv[j] else 0.0
    heat = pd.DataFrame(heat_cols, index=idx)
    ev = pd.DataFrame(ev_cols, index=idx)
    gen = pd.DataFrame(pv_cols, index=idx)

    # --- main-fuse cap: the EV charger backs off first (what a load-balancing
    # charger does), then anything still above the fuse is curtailed and counted
    cons_raw = base + heat + ev
    excess = (cons_raw - cap_kw).clip(lower=0.0)
    ev_capped = (ev - excess).clip(lower=0.0)
    cons = (base + heat + ev_capped).clip(upper=cap_kw)
    curtailed = cons_raw - cons

    cols = [f"house{j + 1}" for j in range(n)]
    cons.columns = gen.columns = cols
    houses = pd.DataFrame({
        "heating": heating, "ua_kw_per_k": ua.round(3), "q_nom_kw": qnom.round(2),
        "has_ev": has_ev, "charger_kw": charger_kw, "has_pv": has_pv, "pv_kwp": pv_kwp.round(1),
        "base_kwh": base.sum().round(0).to_numpy(),
        "heat_kwh": heat.sum().round(0).to_numpy(),
        "ev_kwh": ev_capped.sum().round(0).to_numpy(),
        "pv_kwh": gen.sum().round(0).to_numpy(),
        "fuse_limited_hours": (curtailed > 1e-9).sum().to_numpy(),
    }, index=cols)
    # parts: raw demand per component; base + heat + ev − fuse_curtailed == consumption exactly
    parts = {"base": base.sum(axis=1), "heat": heat.sum(axis=1), "ev": ev.sum(axis=1),
             "pv": gen.sum(axis=1), "fuse_curtailed": curtailed.sum(axis=1)}
    return Neighbourhood(scenario, houses, cons, gen, parts)


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    scenarios = [
        Scenario("no heating load", heating_mix={"none": 1.0}, ev_share=0, pv_share=0),
        Scenario("today", ev_share=0.25, pv_share=0.20),
        Scenario("2030", heating_mix={"ground_source": 0.30, "air_water": 0.30, "direct_electric": 0.20, "none": 0.20},
                 ev_share=0.60, pv_share=0.40),
    ]
    for sc in scenarios:
        nb = build_neighbourhood(sc, wx)
        tot = nb.total_kw
        peak_t = tot.idxmax().tz_convert("Europe/Stockholm")
        mix = nb.houses.heating.value_counts().to_dict()
        print(f"{sc.name:16s} {mix} EV {nb.houses.has_ev.sum():2d} PV {nb.houses.has_pv.sum():2d} | "
              f"peak {tot.max():6.1f} kW on {peak_t:%d %b %H:%M} (T {wx['temp_c'][tot.idxmax()]:+.1f} °C) | "
              f"min {tot.min():6.1f} kW | {tot.clip(lower=0).sum() / 1000:.0f} MWh/yr | "
              f"fuse-limited house-hours {int(nb.houses.fuse_limited_hours.sum())}")
