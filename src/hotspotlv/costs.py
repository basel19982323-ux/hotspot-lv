"""
Indicative cost table for the fixes — a SENSITIVITY table, not a quote.

Why it is here: Swedish law now requires DSOs to compare flexibility with
reinforcement in their network development plans, and Energiforsk 2025:1088
says there are "no established methods" for it. A screening tool should at
least put the fixes on one page with their rough cost and what they buy.

Why it is labelled: every number is a RANGE with a stated price level and
source (data/SOURCES.md). The table says "indicative" in its title; in the
app the ranges are sliders so a planner enters their own figures.

Default price assumptions (low, high):
  * Transformer swap — Ei "Normvärdeslista elnät 2024–2027" (value year 2022):
      - unit only, 800 kVA 12/0.4 kV (NG15921): 209 kSEK — when the existing
        kiosk can take the bigger unit;
      - complete new 800 kVA station incl. unit: kiosk NG15223 (sheet metal)
        281 kSEK or NG15223B (concrete) 425 kSEK + unit 209 → 490–634 kSEK.
      `needs_new_kiosk` chooses; installation/civil works beyond the list
      value are not included.
  * Substation battery, small LV unit, turnkey: 4 000–6 000 SEK/kWh (an
      assumption bracketed by Europe utility-scale turnkey ≈ 1 900 SEK/kWh
      (BNEF survey Dec 2025) and Swedish residential installed 6 000–7 500
      SEK/kWh; no public price for a ≤ 1 MWh LV-station unit was found).
  * Coordinated (spread) charging: 0–5 000 SEK per charger — house-level
      load balancers cost 1 200–2 000 SEK plus an electrician; a tariff or
      DSO signal can be free; roughly a quarter of EV owners already control
      their charging (Energiforsk 2023:966 survey).
  * Night-timer charging: 0 SEK — an optimistic bound: it assumes every EV
      owner is on an hourly contract and schedules; the same survey found
      ~25 % do, and warns that price-optimised charging "can create new
      peaks" (which the comparison shows).
  * Reinforcement-equivalent value of a peak kW avoided: Energiforsk
      2024:1043 §6.2 — a ~450 kW station step ≈ 100 kSEK over ~50 years →
      ≈ 4.4 SEK/kW/yr, i.e. ≈ 220 SEK/kW one-off, undiscounted; flexibility
      most likely DEFERS reinforcement rather than replacing it.
  * Service life (for the reader; no NPV is computed on purpose): transformer
      ~40 yr, battery ~15 yr, charging control ~10 yr — assumptions.

No net-present-value or lifetime cost arithmetic is attempted: the inputs are
too uncertain for it to mean anything. Capex alone favours short-lived
assets, which is why the service-life column is there.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from hotspotlv.study import FixComparison

REINFORCEMENT_SEK_PER_KW_YR = 4.4        # Energiforsk 2024:1043 §6.2 (100 kSEK / 450 kW / 50 yr)
REINFORCEMENT_YEARS = 50.0


@dataclass(frozen=True)
class CostAssumptions:
    transformer_unit_ksek: tuple[float, float] = (209.0, 209.0)        # Ei NG15921, 800 kVA, 2022 value year
    new_station_ksek: tuple[float, float] = (490.0, 634.0)             # kiosk (plåt/betong) + unit, Ei 2022
    needs_new_kiosk: bool = False                                      # existing kiosk takes the bigger unit
    battery_sek_per_kwh: tuple[float, float] = (4000.0, 6000.0)        # assumption; slider 2 000–8 000
    spread_charging_sek_per_charger: tuple[float, float] = (0.0, 5000.0)
    timer_charging_sek_per_charger: tuple[float, float] = (0.0, 0.0)   # optimistic bound
    life_years: dict = None                                            # service life per fix kind
    price_level_note: str = ("transformer: Ei Normvärdeslista 2024–2027, value year 2022; "
                             "battery and charging control: assumptions (see SOURCES.md)")

    def __post_init__(self):
        for name in ("transformer_unit_ksek", "new_station_ksek", "battery_sek_per_kwh",
                     "spread_charging_sek_per_charger", "timer_charging_sek_per_charger"):
            lo, hi = getattr(self, name)
            if not (0 <= lo <= hi):
                raise ValueError(f"{name} must be a (low, high) pair with 0 <= low <= high")
        if self.life_years is None:
            object.__setattr__(self, "life_years", {"transformer": 40, "battery": 15, "charging": 10})

    @property
    def transformer_swap_ksek(self) -> tuple[float, float]:
        return self.new_station_ksek if self.needs_new_kiosk else self.transformer_unit_ksek


def _kind(name: str, result, baseline) -> str:
    if result.case.trafo_kva != baseline.case.trafo_kva:
        return "transformer"
    if result.case.battery is not None:
        return "battery"
    if name.startswith("spread"):
        return "spread"
    if name.startswith("night timer"):
        return "timer"
    return "other"


def _capex_ksek(kind: str, result, a: CostAssumptions) -> tuple[float, float]:
    n_ev = int(result.neighbourhood.houses.has_ev.sum())
    if kind == "transformer":
        return a.transformer_swap_ksek
    if kind == "battery":
        kwh = result.case.battery.energy_kwh
        return (kwh * a.battery_sek_per_kwh[0] / 1000, kwh * a.battery_sek_per_kwh[1] / 1000)
    if kind == "spread":
        return (n_ev * a.spread_charging_sek_per_charger[0] / 1000, n_ev * a.spread_charging_sek_per_charger[1] / 1000)
    if kind == "timer":
        return (n_ev * a.timer_charging_sek_per_charger[0] / 1000, n_ev * a.timer_charging_sek_per_charger[1] / 1000)
    return (0.0, 0.0)


def cost_table(cmp: FixComparison, assumptions: CostAssumptions = CostAssumptions()) -> pd.DataFrame:
    """One row per fix: indicative capex range, service life, what it buys versus the baseline,
    and the reinforcement-equivalent value of the peak kW it avoids (Energiforsk 2024:1043)."""
    baseline = cmp.results["baseline"]
    base = baseline.metrics()
    bh = baseline.year.hourly
    base_peak_kva = float(bh["s_kva"].max())
    at_peak = bh["s_kva"].idxmax()                      # kVA × the peak hour's power factor = the kW the
    pf_peak = float(min(1.0, max(0.0, abs(bh.loc[at_peak, "p_kw"]) / max(base_peak_kva, 1e-9))))   # SEK/kW applies to
    rows = []
    for name, r in cmp.results.items():
        if name == "baseline":
            continue
        m = r.metrics()
        kind = _kind(name, r, baseline)
        lo, hi = _capex_ksek(kind, r, assumptions)
        peak_avoided_kva = max(0.0, base_peak_kva - float(r.year.hourly["s_kva"].max()))
        if kind == "transformer":
            peak_avoided_kva = 0.0           # the swap IS the reinforcement; it avoids no peak on the station
        peak_avoided_kw = peak_avoided_kva * pf_peak       # numbers audit, 23 Oct: kVA was priced as kW
        rows.append({
            "fix": name,
            "capex low (kSEK)": round(lo, 0),
            "capex high (kSEK)": round(hi, 0),
            "service life (yr)": assumptions.life_years.get("charging" if kind in ("spread", "timer") else kind, float("nan")),
            "hours > 80 % avoided": base["hours_above_80"] - m["hours_above_80"],
            "hours > 100 % avoided": base["hours_above_100"] - m["hours_above_100"],
            "life saved (days/yr)": round(base["life_days_per_year"] - m["life_days_per_year"], 3),
            "peak kVA avoided": round(peak_avoided_kva, 0),
            "reinforcement-equivalent value (kSEK)": round(peak_avoided_kw * REINFORCEMENT_SEK_PER_KW_YR * REINFORCEMENT_YEARS / 1000, 0),
            "verdict before → after": f"{base['verdict']} → {m['verdict']}",
            "clears the verdict": m["verdict"] == "OK",
        })
    table = pd.DataFrame(rows).set_index("fix")
    table.attrs["title"] = ("Indicative cost (sensitivity) — ranges, not quotes; capex and an undiscounted "
                            "reinforcement-equivalent value, no net-present-value arithmetic")
    table.attrs["note"] = assumptions.price_level_note
    return table
