"""
Home EV charging for one house — a simple stochastic model.

Every day the dice decide:

    day d:  charge today?  ──no──►  0 kW
              │ yes (p ≈ 0.40)
              ▼
            plug in at ~17:30 local (truncated normal 13–23 h)
              │
              ▼
            energy needed: lognormal, median 10 kWh, mean 14 kWh
              │
              ▼
            charge at the charger's power until the energy is delivered
            (uncontrolled = starts immediately; "smart" = starts 22:00–01:00)

            kW
            11 ┤        ┌────────┐
               │        │        │
             0 ┼────────┴────────┴──────── local time
                       17:30    18:45

Parameters and their sources: data/ev_charging_parameters.md (Trafikanalys
driving distances, Chalmers GPS study of 334 Swedish EVs, Energiforsk 2023:966,
Nordic residential charging dataset). Charger-power mix and "drives every day"
are labelled assumptions there too.

Known limit: on the autumn DST night the 02:00 local hour occurs twice and
both UTC hours read the same minute bucket (≤ 1 charging hour per year).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Charger power mix for Swedish villas — ASSUMPTION (no market statistic found):
# 11 kW 3-phase 16 A most common for new installs; 3.7 kW 1-phase and 7.4 kW remain.
CHARGER_KW_MIX = {11.0: 0.55, 3.7: 0.30, 7.4: 0.15}


@dataclass(frozen=True)
class EVParams:
    p_charge_per_day: float = 0.40     # Bernoulli: does the car charge today?
    arrival_mean_h: float = 17.5       # local clock hours
    arrival_sd_h: float = 2.0
    arrival_min_h: float = 13.0
    arrival_max_h: float = 23.0
    energy_median_kwh: float = 10.0    # lognormal
    energy_sigma: float = 0.82         # → mean ≈ 14 kWh
    energy_max_kwh: float = 60.0
    charger_kw: float = 11.0           # 3-phase 16 A; 3.7 / 7.4 kW also common
    smart: bool = False                # True → start between 22:00 and 01:00
    smart_start_min_h: float = 22.0
    smart_start_max_h: float = 25.0    # 25 = 01:00 next day

    def __post_init__(self):
        if not 0 <= self.p_charge_per_day <= 1:
            raise ValueError("p_charge_per_day must be within 0..1")
        if self.charger_kw <= 0:
            raise ValueError("charger_kw must be positive")
        if not self.arrival_min_h < self.arrival_max_h:
            raise ValueError("arrival window must have min < max")


def _truncated_normal(rng: np.random.Generator, mean: float, sd: float, lo: float, hi: float) -> float:
    """Draw until inside [lo, hi] (no pile-up at the edges, unlike clipping)."""
    for _ in range(1000):
        x = rng.normal(mean, sd)
        if lo <= x <= hi:
            return float(x)
    return float(np.clip(x, lo, hi))


def ev_charging_profile(
    index_utc: pd.DatetimeIndex,
    params: EVParams = EVParams(),
    seed: int | np.random.SeedSequence = 0,
) -> pd.Series:
    """Hourly charging power (kW) of one EV house on the study-year UTC index.

    Works in 1-minute resolution internally so a session can start mid-hour;
    then averages to hourly kW (what the grid sees as hourly energy)."""
    rng = np.random.default_rng(seed)
    local = index_utc.tz_convert("Europe/Stockholm")
    dates = sorted(set(local.date))
    n_days = len(dates)
    minutes = np.zeros((n_days + 2) * 1440)        # +2 days of spill-over room

    for i in range(n_days):
        if rng.random() > params.p_charge_per_day:
            continue
        energy = min(rng.lognormal(np.log(params.energy_median_kwh), params.energy_sigma), params.energy_max_kwh)
        if params.smart:
            start_h = rng.uniform(params.smart_start_min_h, params.smart_start_max_h)
        else:
            start_h = _truncated_normal(rng, params.arrival_mean_h, params.arrival_sd_h,
                                        params.arrival_min_h, params.arrival_max_h)
        start = int(i * 1440 + start_h * 60)
        duration = int(round(energy / params.charger_kw * 60))
        minutes[start:start + duration] = params.charger_kw   # spill-over: next day's early hours

    hourly = minutes[: n_days * 1440].reshape(n_days * 24, 60).mean(axis=1)
    day_pos = {d: i for i, d in enumerate(dates)}
    pos = np.array([day_pos[d] * 24 + h for d, h in zip(local.date, local.hour)])
    return pd.Series(hourly[pos], index=index_utc, name="ev_kw")


if __name__ == "__main__":
    idx = pd.date_range("2023-01-01", "2023-12-31 23:00", freq="h", tz="UTC")
    for smart in (False, True):
        ev = ev_charging_profile(idx, EVParams(smart=smart), seed=3)
        loc = ev.tz_convert("Europe/Stockholm")
        by_hour = loc.groupby(loc.index.hour).mean()
        print(f"smart={smart!s:5} annual {ev.sum():.0f} kWh, hours charging {(ev > 0).sum()}, "
              f"busiest local hour {by_hour.idxmax()}:00 (mean {by_hour.max():.2f} kW)")
