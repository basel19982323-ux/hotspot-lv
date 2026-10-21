"""
The verdict: OK / Watch / At risk — and the reasons, each tied to a standard.

Two views of the same year, side by side:

    ON PAPER (nameplate)                 REALLY (thermal model + limits)
    ───────────────────────              ─────────────────────────────────────
    hours above 80 % of nameplate        days of insulation life used per year
    hours above 100 %                    max hot-spot vs IEC limits (120 / 140 °C)
                                         max load vs IEC current limits (1.5 / 1.8 pu)
                                         house voltage vs EIFS 2023:3 (0.90–1.10 pu)
                                         main-cable loading

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
    • any hour outside 0.90–1.10 pu         EIFS 2023:3 6 kap. 3 §: ALL ten-minute values within ±10 %
                                             (binding in Sweden; EN 50160's 95 % allowance does not apply)
  WATCH if any of
    • loss of life  > 182 days per year     margin for the model's known optimism (no cold-oil viscosity
                                             correction, hourly means) — a chosen margin, not a standard
    • hot-spot      > 120 °C at any time    IEC 60076-7 Table 4, normal cyclic limit
    • top-oil       > 105 °C                IEC 60076-7 Table 4, normal cyclic
    • load          > 1.5 × rated           IEC 60076-7 Table 4, normal cyclic current limit
    • any hour below 0.92 or above 1.05 pu  planning margin: an hourly balanced mean understates the
                                             10-minute, per-phase extremes the law is about (Chalmers
                                             LV-grid generator uses 1.05 pu as the rise limit)
    • main cable    > 100 % of its rating   feeder.py cable catalogue (rating conditions stated there)
  OK otherwise.

The nameplate counters (hours above 80 % / 100 %) never change the verdict.
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


@dataclass(frozen=True)
class Reason:
    rule: str          # short name
    value: float
    threshold: float
    unit: str
    source: str

    def __str__(self) -> str:
        fmt = ".0f" if self.unit == "hours" else ".3g"
        lim = "none allowed" if self.threshold == 0 else f"limit {self.threshold:{fmt}} {self.unit}"
        return f"{self.rule}: {self.value:{fmt}} {self.unit} ({lim}) — {self.source}"


@dataclass
class Verdict:
    level: str
    at_risk: list[Reason] = field(default_factory=list)
    watch: list[Reason] = field(default_factory=list)
    on_paper: dict = field(default_factory=dict)     # the nameplate view, for contrast
    really: dict = field(default_factory=dict)       # the thermal / limits view

    @property
    def reasons(self) -> list[Reason]:
        return self.at_risk + self.watch

    def headline(self) -> str:
        if self.level == "OK":
            return "OK — ages slower than designed, all IEC and EIFS limits respected"
        main = [r.rule for r in (self.at_risk if self.level == "At risk" else self.watch)]
        text = f"{self.level} — " + "; ".join(main)
        if self.level == "At risk" and self.watch:
            text += " (also: " + "; ".join(r.rule for r in self.watch) + ")"
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
    iec = "IEC 60076-7 Table 4 (distribution transformers)"
    if life_days_per_year > DESIGN_LIFE_DAYS_PER_YEAR * (1 + TOL):
        at_risk.append(Reason("ageing faster than design", life_days_per_year, DESIGN_LIFE_DAYS_PER_YEAR,
                              "days of life per year", "IEC 60076-7 3.5 / 5.1 / 6.3 eq. (4): 98 °C = 1 day per day"))
    elif life_days_per_year > WATCH_LIFE_DAYS_PER_YEAR:
        watch.append(Reason("ageing above half the design rate", life_days_per_year, WATCH_LIFE_DAYS_PER_YEAR,
                            "days of life per year", "chosen margin for model optimism (cold oil, hourly means)"))
    if max_hot_spot > IEC_LIMITS_C["long_time_emergency_hot_spot"] + TOL:
        at_risk.append(Reason("hot-spot above long-time emergency limit", max_hot_spot,
                              IEC_LIMITS_C["long_time_emergency_hot_spot"], "°C", iec))
    elif max_hot_spot > IEC_LIMITS_C["normal_cyclic_hot_spot"] + TOL:
        watch.append(Reason("hot-spot above normal cyclic limit", max_hot_spot,
                            IEC_LIMITS_C["normal_cyclic_hot_spot"], "°C", iec))
    if max_top_oil > IEC_LIMITS_C["normal_cyclic_top_oil"] + TOL:
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
        watch.append(Reason("main cable above its current rating", cable_max, 100.0, "%", "cable catalogue (feeder.py)"))

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
        "hours hot-spot above 98 °C": thermal.steps_hot_spot_above(98) * step_h,
        "hours hot-spot above 120 °C": thermal.steps_hot_spot_above(IEC_LIMITS_C["normal_cyclic_hot_spot"]) * step_h,
        "max load (× rated)": max_k,
        "hours with voltage outside ±10 %": v_law_bad,
        "hours with voltage outside the planning band": v_plan_bad,
        "max main-cable loading (%)": cable_max,
    }
    return Verdict(level, at_risk, watch, on_paper, really)
