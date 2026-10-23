"""
The verdict: OK / Watch / At risk — and the reasons, each tied to a standard.

Two views of the same year, side by side:

    ON PAPER (nameplate)                 REALLY (thermal model + limits)
    ───────────────────────              ─────────────────────────────────────
    hours above 80 % of nameplate        days of insulation life used per year
    hours above 100 %                    max hot-spot vs IEC limits (120 / 140 °C)
                                         max load vs IEC current limits (1.5 / 1.8 pu)
                                         house voltage vs EIFS 2023:3 (0.90–1.10 pu)
                                         cable loading (main or service cable)

The verdict is decided ONLY by the right-hand column. The left-hand column is
reported next to it so the reader sees the gap — that is the point of the tool.
It is a thermal / voltage SCREENING, not a replacement for a DSO's reserve
(N-1), growth or accessory criteria.

Rules (every threshold with its source):
  AT RISK if any of
    • loss of life  > 365 days per year     IEC 60076-7: 98 °C hot-spot = ageing rate 1 (6.2, eq. 2);
                                             normal cyclic loading is "equivalent to the rated load" in
                                             ageing terms over the period (3.5, 5.1, 6.3 eq. 4) — a
                                             year using more than a year of life is beyond normal operation
    • hot-spot      > 140 °C at any time    IEC 60076-7 Table 4, long-time emergency limit, distribution
    • load          > 1.8 × rated           IEC 60076-7 Table 4, long-time emergency current limit
    • top-oil       > 115 °C                IEC 60076-7 Table 4, long-time emergency limit, distribution
    • any hour outside 0.90–1.10 pu         EIFS 2023:3 6 kap. 3 §: ALL ten-minute values within ±10 %
                                             (binding in Sweden; EN 50160's 95 % allowance does not apply)
  WATCH if any of
    • loss of life  > 182 days per year     margin for the model's known optimism (no cold-oil viscosity
                                             correction, load held constant within each step) — a chosen
                                             margin, not a standard
    • hot-spot      > 120 °C at any time    IEC 60076-7 Table 4, normal cyclic limit
    • top-oil       > 105 °C                IEC 60076-7 Table 4, normal cyclic
    • load          > 1.5 × rated           IEC 60076-7 Table 4, normal cyclic current limit
    • any hour below 0.92 or above 1.05 pu  planning margin: an hourly balanced mean understates the
                                             10-minute, per-phase extremes the regulation is about (Chalmers
                                             LV-grid generator uses 1.05 pu as the rise limit)
    • any cable     > 100 % of its rating   LV cable current rating of the feeder model (Prysmian AXQJ
                                             in ground, 20 °C soil);
                                             the max over main AND service cables — on a small street
                                             one fuse-capped house on its service cable sets it
  OK otherwise.

Table and equation numbers and all limit values are those of IEC 60076-7:2005,
the edition read for this tool. The 2018 edition renumbers the tables (temperature
limits Table 2, current limits Table 3, thermal constants Table 4, kiosk
correction Table 5) and reviewed the temperature limits; it was not consulted
for the values.

Above 2.0 × rated — the short-time emergency current limit for distribution
transformers, the most IEC 60076-7 foresees even briefly — the thermal model is
not used: life used, hot-spot and top-oil are withdrawn (shown as "–") together
with the rules they decide, and the first At-risk reason says the loading left
that range. Extrapolated figures there (millions of days of life, 300 °C) look
like results and are not.

The nameplate counters (hours above 80 % / 100 %) never change the verdict. They count the transformer's own
loading (a current ratio: with the assumed power factor, the losses and the busbar voltage). The DSO rule of
thumb they echo (Energiforsk 2018:552) applies 80 % to the customers' summed hourly meter kW; at the peak hour
the transformer carries 7–16 % more than that sum implies — so this counter starts a little earlier than that
rule would.
Voltages are hourly balanced means of a simulation; counters use a tiny
tolerance so a value sitting exactly ON a threshold does not count as above it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from hotspotlv.thermal import IEC_LIMITS_C, IEC_LIMITS_K, ThermalResult
from hotspotlv.timeseries import NAMEPLATE_RULE_PCT, VMAX_LIMIT_PU, VMIN_LIMIT_PU, YearResult

LEVELS = ("OK", "Watch", "At risk")
DESIGN_LIFE_DAYS_PER_YEAR = 365.0          # V = 1 all year
WATCH_LIFE_DAYS_PER_YEAR = 182.0
VMIN_PLAN_PU, VMAX_PLAN_PU = 0.92, 1.05    # planning margin band (Watch)
TOL = 1e-6                                 # "above" means above by more than this (float noise at a cap)
HOURS_PER_YEAR = 8760.0
IEC_TABLE_4 = "IEC 60076-7:2005 Table 4 (2018: Tables 2–3), distribution transformers"
CABLE_SOURCE = "LV cable current rating (feeder model: Prysmian AXQJ in ground, 20 °C soil)"
THERMAL_RANGE_K = IEC_LIMITS_K["short_time_emergency"]     # 2.0: above it the thermal figures are withdrawn
OUT_OF_RANGE_RULE = ("loading above the IEC short-time emergency limit — life and temperatures not computed "
                     "(check the rating)")
THERMAL_RULES = ("ageing faster than design", "ageing above half the design rate",
                 "hot-spot above long-time emergency limit", "hot-spot above normal cyclic limit",
                 "top-oil above long-time emergency limit", "top-oil above normal cyclic limit")
THERMAL_KEYS = ("days of life used per year", "max hot-spot (°C)", "max top-oil (°C)",
                "hours hot-spot above 98 °C", "hours hot-spot above 120 °C")


@dataclass(frozen=True)
class Reason:
    rule: str          # short name
    value: float
    threshold: float
    unit: str
    source: str

    def __str__(self) -> str:
        lim = "none allowed" if self.threshold == 0 else f"limit {self.threshold:,g} {self.unit}"
        return f"{self.rule}: {fmt_quantity(self.value, self.unit)} {self.unit} ({lim}) — {self.source}"


def fmt_quantity(value: float, unit: str) -> str:
    """A reason's value at the verdict card's precision: whole hours, days by size, 0.01 × rated, 0.1 °C or %.
    Never scientific notation ('1.66e+16' and '120 °C (limit 120 °C)' were both printed before 22 Oct 2026)."""
    if unit == "hours":
        return f"{value:,.0f}"
    if unit.startswith("days"):
        return f"{value:.3f}" if value < 1 else (f"{value:.2f}" if value < 100 else f"{value:,.0f}")
    if unit == "× rated":
        return f"{value:.2f}"
    return f"{value:,.1f}"


@dataclass
class Verdict:
    level: str
    at_risk: list[Reason] = field(default_factory=list)
    watch: list[Reason] = field(default_factory=list)
    on_paper: dict = field(default_factory=dict)     # the nameplate view, for contrast
    really: dict = field(default_factory=dict)       # the thermal / limits view
    notes: list[str] = field(default_factory=list)   # caveats, e.g. "voltage not checked (no feeder model)"

    @property
    def reasons(self) -> list[Reason]:
        return self.at_risk + self.watch

    def headline(self) -> str:
        unchecked = any("not checked" in n for n in self.notes)
        if self.level == "OK":
            text = ("OK — ages slower than designed, IEC limits respected" if unchecked
                    else "OK — ages slower than designed, all IEC and EIFS limits respected")
        else:
            main = [r.rule for r in (self.at_risk if self.level == "At risk" else self.watch)]
            text = f"{self.level} — " + "; ".join(main)
            if self.level == "At risk" and self.watch:
                text += " (also: " + "; ".join(r.rule for r in self.watch) + ")"
        if unchecked:
            text += " (voltage and cable not checked: no feeder model)"
        return text


def _step_hours(index: pd.Index) -> float:
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("results must be indexed by a DatetimeIndex")
    if len(index) < 2:
        return 1.0
    spacing = np.unique(np.diff(index.to_numpy()) / np.timedelta64(1, "h"))
    if len(spacing) != 1:
        raise ValueError("year and thermal results must be evenly spaced in time")
    return float(spacing[0])


def judge(year: YearResult, thermal: ThermalResult) -> Verdict:
    """Combine the power-flow year and the thermal year into one verdict.
    Life used is expressed per YEAR whatever the length of the period."""
    h = year.hourly
    t = thermal.hourly
    if len(h) == 0:
        raise ValueError("empty results")
    if len(h) != len(t) or not h.index.equals(t.index):
        raise ValueError("year and thermal results must share the same index")
    needed = ["loading_pct", "vmin_pu", "vmax_pu", "line_max_pct"]
    if not np.isfinite(h[needed].to_numpy()).all() or not np.isfinite(t[["theta_h_max", "theta_o", "V_mean"]].to_numpy()).all():
        raise ValueError("results contain NaN/inf")
    step_h = _step_hours(h.index)
    hours = len(h) * step_h
    scale = HOURS_PER_YEAR / hours
    life_days_per_year = thermal.loss_of_life_days * scale
    max_hot_spot = float(t["theta_h_max"].max())
    max_top_oil = float(t["theta_o"].max())
    max_k = float(h["loading_pct"].max() / 100)
    v_law_bad = float(((h["vmin_pu"] < VMIN_LIMIT_PU - TOL) | (h["vmax_pu"] > VMAX_LIMIT_PU + TOL)).sum() * step_h)
    v_plan_bad = float(((h["vmin_pu"] < VMIN_PLAN_PU - TOL) | (h["vmax_pu"] > VMAX_PLAN_PU + TOL)).sum() * step_h)
    cable_max = float(h["line_max_pct"].max())

    at_risk, watch = [], []
    iec = IEC_TABLE_4
    if life_days_per_year > DESIGN_LIFE_DAYS_PER_YEAR * (1 + TOL):
        at_risk.append(Reason("ageing faster than design", life_days_per_year, DESIGN_LIFE_DAYS_PER_YEAR,
                              "days of life per year", "IEC 60076-7 3.5 / 5.1 / 6.3 eq. (4): 98 °C = 1 day per day"))
    elif life_days_per_year > WATCH_LIFE_DAYS_PER_YEAR:
        watch.append(Reason("ageing above half the design rate", life_days_per_year, WATCH_LIFE_DAYS_PER_YEAR,
                            "days of life per year",
                            "chosen margin for model optimism (cold oil, load held constant within each step)"))
    if max_hot_spot > IEC_LIMITS_C["long_time_emergency_hot_spot"] + TOL:
        at_risk.append(Reason("hot-spot above long-time emergency limit", max_hot_spot,
                              IEC_LIMITS_C["long_time_emergency_hot_spot"], "°C", iec))
    elif max_hot_spot > IEC_LIMITS_C["normal_cyclic_hot_spot"] + TOL:
        watch.append(Reason("hot-spot above normal cyclic limit", max_hot_spot,
                            IEC_LIMITS_C["normal_cyclic_hot_spot"], "°C", iec))
    if max_top_oil > IEC_LIMITS_C["long_time_emergency_top_oil"] + TOL:
        at_risk.append(Reason("top-oil above long-time emergency limit", max_top_oil,
                              IEC_LIMITS_C["long_time_emergency_top_oil"], "°C", iec))
    elif max_top_oil > IEC_LIMITS_C["normal_cyclic_top_oil"] + TOL:
        watch.append(Reason("top-oil above normal cyclic limit", max_top_oil,
                            IEC_LIMITS_C["normal_cyclic_top_oil"], "°C", iec))
    if max_k > IEC_LIMITS_K["long_time_emergency"] + TOL:
        at_risk.append(Reason("load above long-time emergency current limit", max_k,
                              IEC_LIMITS_K["long_time_emergency"], "× rated", iec))
    elif max_k > IEC_LIMITS_K["normal_cyclic"] + TOL:
        watch.append(Reason("load above normal cyclic current limit", max_k,
                            IEC_LIMITS_K["normal_cyclic"], "× rated", iec))
    if v_law_bad > 0:
        at_risk.append(Reason("house voltage outside ±10 %", v_law_bad, 0, "hours",
                              "EIFS 2023:3 6 kap. 3 § (all 10-min values within 90–110 %)"))
    elif v_plan_bad > 0:
        watch.append(Reason("house voltage outside the 0.92–1.05 pu planning band", v_plan_bad, 0, "hours",
                            "planning margin for hourly balanced simulation (Chalmers 1.05 pu rise limit)"))
    if cable_max > 100.0 + TOL:
        watch.append(Reason("a cable above its current rating", cable_max, 100.0, "%", CABLE_SOURCE))

    level = "At risk" if at_risk else ("Watch" if watch else "OK")
    on_paper = {
        f"hours above {NAMEPLATE_RULE_PCT:.0f} % of nameplate": float((h["loading_pct"] > NAMEPLATE_RULE_PCT + TOL).sum() * step_h),
        "hours above 100 % of nameplate": float((h["loading_pct"] > 100 + TOL).sum() * step_h),
        "peak load (% of nameplate)": float(h["loading_pct"].max()),
    }
    really = {
        "days of life used per year": life_days_per_year,
        "max hot-spot (°C)": max_hot_spot,
        "max top-oil (°C)": max_top_oil,
        "hours hot-spot above 98 °C": thermal.hours_hot_spot_above(98),
        "hours hot-spot above 120 °C": thermal.hours_hot_spot_above(IEC_LIMITS_C["normal_cyclic_hot_spot"]),
        "max load (× rated)": max_k,
        "hours with voltage outside ±10 %": v_law_bad,
        "hours with voltage outside the planning band": v_plan_bad,
        "max cable loading (%)": cable_max,
    }
    v = Verdict(level, at_risk, watch, on_paper, really)
    if max_k > THERMAL_RANGE_K + TOL:
        withdraw_thermal(v, max_k, THERMAL_RANGE_K,
                         int(((h["loading_pct"] / 100) > THERMAL_RANGE_K + TOL).sum()))
    return v


def thermal_ok(v: Verdict) -> bool:
    """False when the thermal figures were withdrawn (charts must then not draw or print temperatures or life)."""
    return not any(r.rule == OUT_OF_RANGE_RULE for r in v.at_risk)


def withdraw_thermal(v: Verdict, max_k: float, k_max: float, steps: int) -> Verdict:
    """The loading went above k_max × rated: the life, hot-spot and top-oil figures are not meaningful there.
    Withdraw them (NaN — never a number that looks real) and the rules they decided, and say so first.
    The current-limit rule saw the full loading, so it stays."""
    v.at_risk = [r for r in v.at_risk if r.rule not in THERMAL_RULES]
    v.watch = [r for r in v.watch if r.rule not in THERMAL_RULES]
    v.at_risk.insert(0, Reason(OUT_OF_RANGE_RULE, max_k, k_max, "× rated",
                               f"IEC 60076-7:2005 Table 4: {k_max:g} × rated is the short-time emergency current "
                               f"limit for distribution transformers; the thermal model is not used beyond it "
                               f"({steps} steps above)"))
    for key in THERMAL_KEYS:
        v.really[key] = float("nan")
    v.level = "At risk"
    return v
