"""
Rooftop PV for one house: PVGIS output per kWp (south, 30°) × the house's kWp.

Returned as POSITIVE generation (kW). The neighbourhood keeps consumption and
generation separate: the power flow needs consumption for reactive power
(pf 0.95) and generation at unity pf (inverter default), and the net house
power is consumption − generation (negative = export to the street).

Size: Swedish residential plants in the ≤ 20 kW class average about 10 kW
(Svensk Solenergi / pv magazine, April 2026, cumulative). We draw kWp per
house from a uniform 6–14 kWp range unless given.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

KWP_MIN, KWP_MAX = 6.0, 14.0


def pv_profile(pv_kw_per_kwp: pd.Series, kwp: float) -> pd.Series:
    """Hourly PV generation (kW, positive) of one house."""
    if kwp < 0:
        raise ValueError("kwp must be >= 0")
    return (kwp * pv_kw_per_kwp).rename("pv_kw")


def draw_kwp(rng: np.random.Generator) -> float:
    return float(rng.uniform(KWP_MIN, KWP_MAX))


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    pv = pv_profile(wx["pv_kw_per_kwp"], 10.0)
    print(f"10 kWp: {pv.sum():.0f} kWh/yr, peak {pv.max():.2f} kW at {pv.idxmax():%d %b %H:%M} UTC")
    loc = pv.tz_convert("Europe/Stockholm")
    june = loc[loc.index.month == 6]
    print("June mean kW by local hour:", june.groupby(june.index.hour).mean().round(2).tolist())
