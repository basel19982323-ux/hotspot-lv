"""The spot checks STUDY.md quotes in its text (not in a table): run them to see the numbers.

    python study/spot_checks.py

1. Steady state at −25 °C air, kiosk, generic constants: the load at which the hot-spot settles at 120 °C and 140 °C
   (IEC 60076-7 steady-state equations; STUDY 5.1 "Why the ratio is about 1.6–2").
2. The village road's first At-risk point in 2010 (74 houses), hour by hour: how long the hot-spot is above 140 °C
   and 120 °C, when, at what load and air temperature (STUDY 5.2 "The deciding peaks").
3. The transformer's current against the summed meters at the peak hour of a few stations (STUDY 4.6).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_study  # noqa: E402


def steady_state() -> None:
    from hotspotlv.thermal import ThermalParams
    print("1. Steady state at −25 °C air (kiosk, generic IEC 60076-7 constants)")
    for kva in (315, 500, 800):
        p = ThermalParams.for_transformer(kva)

        def hot_spot(k, ta=-25.0):
            return ta + p.d_theta_or * ((1 + p.R * k * k) / (1 + p.R)) ** p.x + p.d_theta_hr * k ** p.y
        k120, k140 = (brentq(lambda k: hot_spot(k) - lim, 0.5, 3.0) for lim in (120.0, 140.0))
        print(f"   {kva} kVA: 120 °C at {k120:.2f} × rated, 140 °C at {k140:.2f} × rated")


def deciding_peaks(street: str = "rural", year: int = 2010, houses: int = 74) -> None:
    from hotspotlv.study import run_case
    print(f"2. {run_study.STREETS[street]['label']}, {year}, {houses} houses, hour by hour")
    wx = run_study.weather(year)
    res = run_case(run_study.case_for(street, houses), wx, fast=True)
    h, k = res.thermal.hourly, res.year.hourly["loading_pct"] / 100
    for lim in (140.0, 120.0):
        col = f"h_above_{lim:g}"
        rows = h[h[col] > 0]
        local = rows.index.tz_convert("Europe/Stockholm")
        print(f"   above {lim:.0f} °C: {res.thermal.hours_hot_spot_above(lim):.2f} h in {len(rows)} clock hours, "
              f"local hours {sorted(set(local.hour))}")
        if lim == 140.0:
            for t, r in rows.iterrows():
                print(f"     {t.tz_convert('Europe/Stockholm'):%d %b %H:%M}: {r[col] * 60:.0f} min above, "
                      f"max {r['theta_h_max']:.2f} °C, load {k.loc[t]:.2f} × rated, air {wx['temp_c'].loc[t]:.1f} °C")


def meter_factor() -> None:
    from hotspotlv.study import run_case
    print("3. Transformer loading ÷ summed meters at the transformer's peak hour")
    for street, year, houses in (("rural", 2010, 39), ("suburb", 2010, 63), ("dense", 2010, 101)):
        res = run_case(run_study.case_for(street, houses), run_study.weather(year), fast=True)
        nb = res.neighbourhood
        meters = (nb.cons_kw - nb.gen_kw).clip(lower=0).sum(axis=1) / run_study.STREETS[street]["kva"] * 100
        lp = res.year.hourly["loading_pct"]
        t = lp.idxmax()
        print(f"   {street} {year}, {houses} houses: peak {lp.max():.1f} % at {t.tz_convert('Europe/Stockholm'):%d %b %H:%M}, "
              f"meters {meters.loc[t]:.1f} % then (their own peak {meters.max():.1f} %): factor {lp.max() / meters.loc[t]:.3f}"
              f" (of which 1/0.95 = {1 / 0.95:.3f} is the assumed power factor)")


def main() -> int:
    np.set_printoptions(precision=3)
    steady_state()
    deciding_peaks()
    meter_factor()
    return 0


if __name__ == "__main__":
    sys.exit(main())
