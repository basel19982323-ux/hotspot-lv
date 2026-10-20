"""
The verdict: OK / Watch / At risk — and the reasons, each tied to a standard.

Two views of the same year, side by side:

    ON PAPER (nameplate)                 REALLY (thermal model + limits)
    ───────────────────────              ─────────────────────────────────────
    hours above 80 % of nameplate        days of insulation life used per year
    hours above 100 %                    max hot-spot vs IEC limits (120 / 140 °C)
                                         max load vs IEC current limits (1.5 / 1.8 pu)
                                         house voltage vs EN 50160 (0.90–1.10 pu)
                                         main-cable loading

The verdict is decided ONLY by the right-hand column. The left-hand column is
reported next to it so the reader sees the gap — that is the point of the tool.

Rules (every threshold with its source):
  AT RISK if any of
    • loss of life  > 365 days per year     (ages faster than the IEC design basis:
                                             hot-spot 98 °C continuously = 1 day/day,
                                             IEC 60076-7 clause 6.2 / eq. (2))
    • hot-spot      > 140 °C at any time    (IEC 60076-7 Table 4, long-time emergency
                                             limit for distribution transformers)
    • load          > 1.8 × rated           (IEC 60076-7 Table 4 current limit, long-time emergency)
    • voltage outside 0.90–1.10 pu for more than 5 % of the hours
                                            (EN 50160: 95 % of 10-min means within ±10 %)
  WATCH if any of
    • loss of life  > 182 days per year     (more than half the design rate — margin for the
                                             model's known optimism in cold oil)
    • hot-spot      > 120 °C at any time    (IEC 60076-7 Table 4, normal cyclic limit)
    • top-oil       > 105 °C                (IEC 60076-7 Table 4, normal cyclic)
    • load          > 1.5 × rated           (IEC 60076-7 Table 4, normal cyclic current limit)
    • any hour outside 0.90–1.10 pu         (EN 50160 margin)
    • main cable    > 100 % of its rating   (feeder.py cable catalogue)
  OK otherwise.

The nameplate counters (hours above 80 % / 100 %) never change the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hotspotlv.thermal import IEC_LIMITS_C, IEC_LIMITS_K, ThermalResult
from hotspotlv.timeseries import NAMEPLATE_RULE_PCT, VMAX_LIMIT_PU, VMIN_LIMIT_PU, YearResult

LEVELS = ("OK", "Watch", "At risk")
DESIGN_LIFE_DAYS_PER_YEAR = 365.0          # V = 1 all year
WATCH_LIFE_DAYS_PER_YEAR = 182.0
EN50160_MAX_VIOLATION_SHARE = 0.05         # 95 % of the time inside ±10 %


@dataclass(frozen=True)
class Reason:
    rule: str          # short name
    value: float
    threshold: float
    unit: str
    source: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.value:.3g} {self.unit} (limit {self.threshold:.3g} {self.unit}) — {self.source}"


@dataclass
class Verdict:
    level: str
    at_risk: list[Reason] = field(default_factory=list)
    watch: list[Reason] = field(default_factory=list)
    on_paper: dict = field(default_factory=dict)     # the nameplate view, for contrast
    really: dict = field(default_factory=dict)       # the thermal / limits view

    @property
    def reasons(self) -> list[Reason]:
        return self.at_risk if self.level == "At risk" else self.watch

    def headline(self) -> str:
        if self.level == "OK":
            return "OK — ages slower than designed, all IEC and EN 50160 limits respected"
        return f"{self.level} — " + "; ".join(r.rule for r in self.reasons)


def judge(year: YearResult, thermal: ThermalResult, hours_in_period: float | None = None) -> Verdict:
    """Combine the power-flow year and the thermal year into one verdict."""
    h = year.hourly
    t = thermal.hourly
    n_hours = hours_in_period or len(h)
    scale = 8760.0 / n_hours                           # express life used per YEAR even for shorter runs
    life_days_per_year = thermal.loss_of_life_days * scale
    max_hot_spot = float(t["theta_h_max"].max())
    max_top_oil = float(t["theta_o"].max())
    max_k = float(h["loading_pct"].max() / 100)
    v_bad_hours = int(((h["vmin_pu"] < VMIN_LIMIT_PU) | (h["vmax_pu"] > VMAX_LIMIT_PU)).sum())
    v_bad_share = v_bad_hours / n_hours
    cable_max = float(h["line_max_pct"].max())

    at_risk, watch = [], []
    iec = "IEC 60076-7 Table 4 (distribution transformers)"
    if life_days_per_year > DESIGN_LIFE_DAYS_PER_YEAR:
        at_risk.append(Reason("ageing faster than design", life_days_per_year, DESIGN_LIFE_DAYS_PER_YEAR,
                              "days of life per year", "IEC 60076-7 eq. (2): 98 °C hot-spot = 1 day per day"))
    elif life_days_per_year > WATCH_LIFE_DAYS_PER_YEAR:
        watch.append(Reason("ageing above half the design rate", life_days_per_year, WATCH_LIFE_DAYS_PER_YEAR,
                            "days of life per year", "margin for model optimism in cold oil"))
    if max_hot_spot > IEC_LIMITS_C["long_time_emergency_hot_spot"]:
        at_risk.append(Reason("hot-spot above long-time emergency limit", max_hot_spot,
                              IEC_LIMITS_C["long_time_emergency_hot_spot"], "°C", iec))
    elif max_hot_spot > IEC_LIMITS_C["normal_cyclic_hot_spot"]:
        watch.append(Reason("hot-spot above normal cyclic limit", max_hot_spot,
                            IEC_LIMITS_C["normal_cyclic_hot_spot"], "°C", iec))
    if max_top_oil > IEC_LIMITS_C["normal_cyclic_top_oil"]:
        watch.append(Reason("top-oil above normal cyclic limit", max_top_oil,
                            IEC_LIMITS_C["normal_cyclic_top_oil"], "°C", iec))
    if max_k > IEC_LIMITS_K["long_time_emergency"]:
        at_risk.append(Reason("load above long-time emergency current limit", max_k,
                              IEC_LIMITS_K["long_time_emergency"], "× rated", iec))
    elif max_k > IEC_LIMITS_K["normal_cyclic"]:
        watch.append(Reason("load above normal cyclic current limit", max_k,
                            IEC_LIMITS_K["normal_cyclic"], "× rated", iec))
    if v_bad_share > EN50160_MAX_VIOLATION_SHARE:
        at_risk.append(Reason("house voltage outside ±10 % too often", 100 * v_bad_share,
                              100 * EN50160_MAX_VIOLATION_SHARE, "% of hours", "EN 50160 (95 % rule)"))
    elif v_bad_hours > 0:
        watch.append(Reason("house voltage outside ±10 % in some hours", v_bad_hours, 0, "hours", "EN 50160"))
    if cable_max > 100.0:
        watch.append(Reason("main cable above its current rating", cable_max, 100.0, "%", "cable catalogue (feeder.py)"))

    level = "At risk" if at_risk else ("Watch" if watch else "OK")
    on_paper = {
        f"hours above {NAMEPLATE_RULE_PCT:.0f} % of nameplate": int((h["loading_pct"] > NAMEPLATE_RULE_PCT).sum()),
        "hours above 100 % of nameplate": int((h["loading_pct"] > 100).sum()),
        "peak load (% of nameplate)": float(h["loading_pct"].max()),
    }
    really = {
        "days of life used per year": life_days_per_year,
        "max hot-spot (°C)": max_hot_spot,
        "max top-oil (°C)": max_top_oil,
        "hours hot-spot above 98 °C": thermal.steps_hot_spot_above(98),
        "hours hot-spot above 120 °C": thermal.steps_hot_spot_above(IEC_LIMITS_C["normal_cyclic_hot_spot"]),
        "max load (× rated)": max_k,
        "hours with voltage outside ±10 %": v_bad_hours,
        "max main-cable loading (%)": cable_max,
    }
    return Verdict(level, at_risk, watch, on_paper, really)
