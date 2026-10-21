"""
Indicative cost table for the fixes — a SENSITIVITY table, not a quote.

Why it is here: Swedish law now requires DSOs to compare flexibility with
reinforcement in their network development plans, and Energiforsk 2025:1088
says there are "no established methods" for it. A screening tool should at
least put the fixes on one page with their rough cost and what they buy.

Why it is labelled: nobody publishes LV-battery or smart-charging prices for
Sweden, transformer station prices are regulatory reference values at an old
price level, and every number here is a RANGE. The table says "indicative"
in its title and carries the source of every price; the app shows the ranges
as sliders so a planner can put in their own numbers.

Default price assumptions (each a range: low, high) and their sources:
  * Transformer station swap (new 800 kVA kiosk incl. installation):
      190–290 kSEK — Ei "normvärden" for nätstationer (2010 price level; Ei
      REL00885 bilaga 6). Shown as-is with the price level stated; a planner
      scales to today's prices.
  * Substation battery, turnkey small LV unit:
      4 000–6 000 SEK per kWh — small LV units are far above utility-scale
      turnkey (BNEF 2024: ≈ US$ 275/kWh ≈ 2 900 SEK/kWh); 400–600 €/kWh is the
      assumption from the study plan, converted at 10 SEK/€.
  * Coordinated (spread) charging: a load-management box or a tariff signal
      per EV house: 0–3 000 SEK per charger (assumption; many new chargers
      already support scheduling, so the low end is zero).
  * Night-timer charging: 0 SEK (customers already do it on hourly prices).
What each fix buys is read from the FixComparison (hours above 80 % and 100 %
avoided, days of life saved, verdict before/after). No net-present-value or
lifetime arithmetic is attempted: the inputs are too uncertain for that to
mean anything, and the table says so.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from hotspotlv.study import FixComparison

SEK_PER_EUR = 10.0


@dataclass(frozen=True)
class CostAssumptions:
    transformer_swap_ksek: tuple[float, float] = (190.0, 290.0)      # Ei normvärden, 2010 price level
    battery_sek_per_kwh: tuple[float, float] = (4000.0, 6000.0)      # small LV turnkey, assumption (400–600 €/kWh)
    spread_charging_sek_per_charger: tuple[float, float] = (0.0, 3000.0)  # assumption
    timer_charging_sek_per_charger: tuple[float, float] = (0.0, 0.0)
    price_level_note: str = "transformer: Ei normvärden 2010 price level; battery and charging: assumptions"

    def __post_init__(self):
        for name in ("transformer_swap_ksek", "battery_sek_per_kwh", "spread_charging_sek_per_charger",
                     "timer_charging_sek_per_charger"):
            lo, hi = getattr(self, name)
            if not (0 <= lo <= hi):
                raise ValueError(f"{name} must be a (low, high) pair with 0 <= low <= high")


def _capex_ksek(name: str, result, assumptions: CostAssumptions) -> tuple[float, float]:
    n_ev = int(result.neighbourhood.houses.has_ev.sum())
    if name.endswith("kVA"):
        return assumptions.transformer_swap_ksek
    if name.startswith("battery"):
        kwh = result.case.battery.energy_kwh
        lo, hi = assumptions.battery_sek_per_kwh
        return (kwh * lo / 1000, kwh * hi / 1000)
    if name.startswith("spread"):
        lo, hi = assumptions.spread_charging_sek_per_charger
        return (n_ev * lo / 1000, n_ev * hi / 1000)
    if name.startswith("night timer"):
        lo, hi = assumptions.timer_charging_sek_per_charger
        return (n_ev * lo / 1000, n_ev * hi / 1000)
    return (0.0, 0.0)


def cost_table(cmp: FixComparison, assumptions: CostAssumptions = CostAssumptions()) -> pd.DataFrame:
    """One row per fix: indicative capex range and what it buys versus the baseline.
    Title the table "Indicative cost (sensitivity)" wherever it is shown."""
    base = cmp.results["baseline"].metrics()
    rows = []
    for name, r in cmp.results.items():
        if name == "baseline":
            continue
        m = r.metrics()
        lo, hi = _capex_ksek(name, r, assumptions)
        rows.append({
            "fix": name,
            "capex low (kSEK)": round(lo, 0),
            "capex high (kSEK)": round(hi, 0),
            "hours > 80 % avoided": base["hours_above_80"] - m["hours_above_80"],
            "hours > 100 % avoided": base["hours_above_100"] - m["hours_above_100"],
            "life saved (days/yr)": round(base["life_days_per_year"] - m["life_days_per_year"], 3),
            "verdict before → after": f"{base['verdict']} → {m['verdict']}",
            "clears the verdict": m["verdict"] == "OK",
        })
    table = pd.DataFrame(rows).set_index("fix")
    table.attrs["title"] = "Indicative cost (sensitivity) — ranges, not quotes"
    table.attrs["note"] = assumptions.price_level_note
    return table
