"""
Rooftop PV for one house: PVGIS output per kWp (south, 30°) × the house's kWp.

Sign convention: PV is returned NEGATIVE (generation), so that
    house total = base + heat pump + EV + pv
and a negative total means the house exports to the street.

Typical Swedish villa system 2024–25: 8–12 kWp (Energimyndigheten statistics
show the average new residential plant just above 10 kWp). We draw kWp per
house from a uniform 6–14 kWp range unless given.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

KWP_MIN, KWP_MAX = 6.0, 14.0


def pv_profile(pv_kw_per_kwp: pd.Series, kwp: float) -> pd.Series:
    """Hourly PV output (kW, negative = generation) of one house."""
    return (-kwp * pv_kw_per_kwp).rename("pv_kw")


def draw_kwp(rng: np.random.Generator) -> float:
    return float(rng.uniform(KWP_MIN, KWP_MAX))


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    pv = pv_profile(wx["pv_kw_per_kwp"], 10.0)
    print(f"10 kWp: {-pv.sum():.0f} kWh/yr, peak {-pv.min():.2f} kW at {pv.idxmin():%d %b %H:%M} UTC")
    loc = pv.tz_convert("Europe/Stockholm")
    print("June mean kW by local hour:", (-loc[loc.index.month == 6].groupby(loc[loc.index.month == 6].index.hour).mean()).round(2).tolist())
