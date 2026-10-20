"""
A small battery at the substation busbar — the third "fix".

    MV ──[ transformer ]──┬── LV busbar ──► feeders (houses)
                          │
                       [ battery ]   charges when the transformer is lightly loaded,
                                     discharges when it would go above the cap

    kVA through transformer
    cap ─────────────────────┐        ┌───────
                        ▄▄▄▄▄│▄▄▄▄▄▄▄▄│       ← discharge shaves what is above the cap
    ─────────────────────────┴────────┴────────────── hours
         ▒▒▒ charge at night ▒▒▒

The cap is on APPARENT power S (kVA) — what the transformer rating and the
"80 % rule" are about. The battery only moves active power P; the houses'
reactive power Q stays, so with S² = P² + Q² the battery can bring P down to
√(cap² − Q²) at best, and charging may raise P only up to that same line.

Dispatch rule (deliberately simple, so a planner can explain it):
  * if S would exceed the cap: discharge up to the battery's power and the
    stored energy to bring S down to the cap;
  * otherwise, if there is head-room below the cap and the battery is not
    full: charge, never pushing S above the cap.
  * round-trip efficiency 90 % (split between charge and discharge), no
    calendar degradation, starts full.

The battery sits at the busbar, so it changes ONLY the transformer flow —
house voltages and feeder cables are untouched. Sizing helper: the energy of
the worst day above the cap and the peak above the cap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from hotspotlv.timeseries import YearResult


@dataclass(frozen=True)
class BatteryParams:
    power_kw: float
    energy_kwh: float
    cap_kva: float                      # transformer apparent power the battery tries to stay under
    round_trip_efficiency: float = 0.90
    initial_soc: float = 1.0            # start full

    def __post_init__(self):
        if min(self.power_kw, self.energy_kwh, self.cap_kva) <= 0 or not 0 < self.round_trip_efficiency <= 1:
            raise ValueError("battery power, energy, cap must be > 0; efficiency in (0, 1]")


def dispatch(p_kw: pd.Series, q_kvar: pd.Series, b: BatteryParams, step_h: float = 1.0) -> pd.DataFrame:
    """Greedy peak shaving on S = √(P² + Q²). Returns per hour: p_after (kW through the
    transformer), charge, discharge (kW, positive), soc (kWh)."""
    p = p_kw.to_numpy(dtype=float)
    q = q_kvar.to_numpy(dtype=float)
    p_limit = np.sqrt(np.maximum(b.cap_kva**2 - q**2, 0.0))   # largest |P| that keeps S ≤ cap
    eff = np.sqrt(b.round_trip_efficiency)                     # per direction
    soc = b.energy_kwh * b.initial_soc
    out = np.empty((len(p), 4))
    for i, (pi, lim) in enumerate(zip(p, p_limit)):
        charge = discharge = 0.0
        if pi > lim:
            discharge = min(pi - lim, b.power_kw, soc * eff / step_h)
            soc -= discharge * step_h / eff
        elif pi < lim and soc < b.energy_kwh:
            charge = min(lim - pi, b.power_kw, (b.energy_kwh - soc) / eff / step_h)
            soc += charge * step_h * eff
        out[i] = (pi - discharge + charge, charge, discharge, soc)
    return pd.DataFrame(out, index=p_kw.index, columns=["p_after", "charge", "discharge", "soc"])


def _q_from(year: YearResult) -> pd.Series:
    h = year.hourly
    return np.sqrt(np.maximum(h["s_kva"] ** 2 - h["p_kw"] ** 2, 0.0))


def apply_battery(year: YearResult, b: BatteryParams) -> YearResult:
    """Re-express a YearResult with the battery shaving the transformer flow.
    S is recomputed from the new P and the unchanged reactive power."""
    h = year.hourly
    q = _q_from(year)
    d = dispatch(h["p_kw"], q, b)
    s_new = np.hypot(d["p_after"], q)
    new = h.copy()
    new["p_kw"] = d["p_after"]
    new["s_kva"] = s_new
    new["loading_pct"] = s_new / year.rated_kva * 100
    new["battery_kw"] = d["discharge"] - d["charge"]
    new["battery_soc_kwh"] = d["soc"]
    return YearResult(new, year.rated_kva)


def size_for_cap(year: YearResult, cap_kva: float) -> BatteryParams:
    """Smallest battery that could hold S under cap_kva with perfect foresight:
    power = max active-power excess, energy = worst single day's excess (grossed up for losses)."""
    h = year.hourly
    q = _q_from(year)
    p_limit = np.sqrt(np.maximum(cap_kva**2 - q**2, 0.0))
    excess = (h["p_kw"] - p_limit).clip(lower=0)
    if excess.sum() == 0:
        raise ValueError(f"transformer never exceeds {cap_kva} kVA — no battery needed")
    local = excess.tz_convert("Europe/Stockholm") if excess.index.tz is not None else excess
    worst_day_kwh = float(local.groupby(local.index.date).sum().max())
    return BatteryParams(power_kw=float(np.ceil(excess.max())), energy_kwh=float(np.ceil(worst_day_kwh / 0.9)),
                         cap_kva=cap_kva)
