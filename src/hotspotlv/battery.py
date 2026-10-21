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
√(cap² − Q²) at best, and charging may raise P only up to that same line
(minus a hair, so float noise never lands exactly on the cap).

Dispatch rule (deliberately simple, so a planner can explain it):
  * if S would exceed the cap: discharge up to the battery's power and the
    stored energy to bring S down to the cap;
  * otherwise, if there is head-room below the cap and the battery is not
    full: charge, never pushing S above the cap.
  * round-trip efficiency 90 % (split between charge and discharge), no
    calendar degradation, starts full. Greedy: no foresight.

Sizing is done BY SIMULATION (`size_by_simulation`): the smallest energy, at
the power of the largest excess, for which this dispatch leaves no hour above
the cap. If that energy exceeds a practical maximum for a substation battery
(`energy_max_kwh`, default 1 000 kWh — a choice, not a standard), the result
says `feasible=False`, sizes at the maximum and reports the hours left above
the cap. A busbar battery cannot cover a multi-day heat-pump cold spell; that
is a finding, not a bug.

The battery sits at the busbar, so it changes ONLY the transformer flow. House
voltages and feeder cables are left unchanged (slightly conservative: a lower
transformer flow lifts the busbar a little).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from hotspotlv.timeseries import YearResult

CAP_MARGIN = 1e-9       # relative; keeps S strictly below the cap after charging
ENERGY_MAX_KWH = 1000.0  # practical ceiling for a nätstation battery in the sizing search (a choice)


@dataclass(frozen=True)
class BatteryParams:
    power_kw: float
    energy_kwh: float
    cap_kva: float                      # transformer apparent power the battery tries to stay under
    round_trip_efficiency: float = 0.90
    initial_soc: float = 1.0            # start full (share of energy_kwh)

    def __post_init__(self):
        vals = (self.power_kw, self.energy_kwh, self.cap_kva, self.round_trip_efficiency, self.initial_soc)
        if not all(math.isfinite(v) for v in vals):
            raise ValueError("battery parameters must be finite numbers")
        if min(self.power_kw, self.energy_kwh, self.cap_kva) <= 0:
            raise ValueError("battery power, energy and cap must be > 0")
        if not 0 < self.round_trip_efficiency <= 1 or not 0 <= self.initial_soc <= 1:
            raise ValueError("round_trip_efficiency in (0, 1], initial_soc in [0, 1]")


def _step_hours(index: pd.Index) -> float:
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("index must be a DatetimeIndex")
    if len(index) < 2:
        return 1.0
    spacing = np.unique(np.diff(index.to_numpy()) / np.timedelta64(1, "h"))
    if len(spacing) != 1:
        raise ValueError("index must be evenly spaced")
    return float(spacing[0])


def dispatch(p_kw: pd.Series, q_kvar: pd.Series, b: BatteryParams) -> pd.DataFrame:
    """Greedy peak shaving on S = √(P² + Q²). The step length comes from the index.
    Returns per step: p_after (kW through the transformer), charge, discharge (kW, positive), soc (kWh)."""
    if len(p_kw) != len(q_kvar) or not p_kw.index.equals(q_kvar.index):
        raise ValueError("p_kw and q_kvar must share the same index")
    p = p_kw.to_numpy(dtype=float)
    q = q_kvar.to_numpy(dtype=float)
    if not (np.isfinite(p).all() and np.isfinite(q).all()):
        raise ValueError("p_kw and q_kvar must be finite")
    step_h = _step_hours(p_kw.index)
    p_limit = np.sqrt(np.maximum(b.cap_kva**2 - q**2, 0.0)) * (1 - CAP_MARGIN)   # largest P keeping S ≤ cap
    eff = math.sqrt(b.round_trip_efficiency)                                    # per direction
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
    S is recomputed from the new P and the unchanged reactive power; voltages,
    cable loading and losses are left as they were (busbar battery)."""
    h = year.hourly
    for col in ("s_kva", "p_kw"):
        if col not in h:
            raise ValueError(f"YearResult lacks column {col!r}")
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


@dataclass(frozen=True)
class BatterySizing:
    params: BatteryParams
    feasible: bool                  # True if dispatch leaves no step above the cap
    hours_above_cap_after: float    # hours (not steps) still above the cap with this battery
    discharged_kwh_per_year: float
    full_cycles_per_year: float


def hours_above(year: YearResult, cap_kva: float) -> float:
    h = year.hourly
    return float((h["s_kva"] > cap_kva * (1 + 1e-9)).sum() * _step_hours(h.index))


def size_by_simulation(year: YearResult, cap_kva: float, energy_tolerance_kwh: float = 10.0,
                       energy_max_kwh: float = ENERGY_MAX_KWH) -> BatterySizing:
    """Smallest battery (bisection on energy, power = largest excess) for which the greedy
    dispatch holds S under cap_kva every step. If more than `energy_max_kwh` would be
    needed (typically: no time to recharge between the peaks of a cold spell), returns
    a battery of that size with feasible=False and the hours still above the cap."""
    if not (math.isfinite(energy_tolerance_kwh) and energy_tolerance_kwh > 0
            and math.isfinite(energy_max_kwh) and energy_max_kwh > 0 and math.isfinite(cap_kva) and cap_kva > 0):
        raise ValueError("cap_kva, energy_tolerance_kwh and energy_max_kwh must be finite and > 0")
    h = year.hourly
    q = _q_from(year)
    p_limit = np.sqrt(np.maximum(cap_kva**2 - q**2, 0.0))
    excess = (h["p_kw"] - p_limit).clip(lower=0)
    step_h = _step_hours(h.index)
    if excess.sum() == 0:
        raise ValueError(f"transformer never exceeds {cap_kva} kVA — no battery needed")
    power = float(np.ceil(excess.max()))
    scale_year = 8760.0 / (len(h) * step_h)

    def run(energy):
        b = BatteryParams(power, energy, cap_kva)
        yr = apply_battery(year, b)
        return b, yr, hours_above(yr, cap_kva), float(yr.hourly["battery_kw"].clip(lower=0).sum() * step_h)

    hi = float(energy_max_kwh)
    b, yr, left, discharged = run(hi)
    if left > 0:
        return BatterySizing(b, False, left, discharged * scale_year, discharged * scale_year / b.energy_kwh)
    lo = 0.0
    while hi - lo > energy_tolerance_kwh:
        mid = (lo + hi) / 2
        _, _, left_mid, _ = run(mid)
        if left_mid > 0:
            lo = mid
        else:
            hi = mid
    b, yr, left, discharged = run(float(np.ceil(hi)))
    return BatterySizing(b, left == 0, left, discharged * scale_year, discharged * scale_year / b.energy_kwh)
