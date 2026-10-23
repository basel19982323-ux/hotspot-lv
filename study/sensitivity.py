"""How much do the thermal results depend on the generic IEC 60076-7 constants? A one-at-a-time sensitivity.

    python study/sensitivity.py [--part stations|limits|all] [--workers 10]
                                           (after study/run_study.py: it picks its cases from study/results/runs.csv)

Two parts, both in the weather years 2010 (coldest), 2020 (mildest) and 2023:
- stations: for each street type, the reference station is the smallest number of houses at which the transformer
  is "overloaded on paper" (peak above nameplate) in the median winter. Its loading year is computed once per
  weather year, then only the thermal model is re-run with one constant moved at a time.
- limits: the same variations for every house count of the street, to see how far the FIRST thermal limit (Watch,
  At risk) moves when a constant is moved. The loading of a house count does not depend on the transformer
  constants, so the 80 % rule's first flag stays where it is; only the thermal verdict moves.
The ranges are assumptions, chosen to cover what a real unit can differ from the generic constants by:

    top-oil rise at rated load  Δθor    −10 / +10 K       (IEC generic 55 K; units differ by design)
    hot-spot gradient           Δθhr    −8 / +8 K         (UK measurements: generic constants off by up to 8 K)
    loss ratio                  R       ×0.7 / ×1.3       (load / no-load loss of the actual unit)
    oil time constant           τo      90 / 270 min      (IEC 180 min)
    winding exponent            y       1.3 / 2.0         (IEC 1.6 for distribution transformers)
    enclosure                           outdoor / full kiosk rise   (base: kiosk, half of IEC Table 6 air rise)
    insulation paper                    thermally upgraded           (base: kraft, ageing doubles every 6 K)
    air at the transformer              −5 / +5 K          (kiosk site, microclimate, station far from SMHI's)

The air variation moves only the temperature around the transformer, not the heating demand of the houses.

Results: study/results/sensitivity.csv — per street, year and variation: life used, max hot-spot, verdict.
         study/results/sensitivity_limits.csv — per street, year, house count and variation: the thermal flags.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_study  # noqa: E402

YEARS = (2010, 2020, 2023)


def reference_houses(runs: pd.DataFrame) -> dict[str, int]:
    """Per street: the smallest house count whose MEDIAN (over the winters) peak is above nameplate."""
    med = runs.groupby(["street", "houses"])["peak_loading_pct"].median().reset_index()
    out = {}
    for street, g in med.groupby("street"):
        above = g[g["peak_loading_pct"] > 100].sort_values("houses")
        out[street] = int(above["houses"].iloc[0]) if len(above) else int(g["houses"].max())
    return out


def variations(base):
    from hotspotlv.thermal import ThermalParams
    kiosk_rise = base.enclosure_rise_k
    yield "base (generic IEC constants, kiosk)", base, 0.0
    yield "top-oil rise −10 K", replace(base, d_theta_or=base.d_theta_or - 10), 0.0
    yield "top-oil rise +10 K", replace(base, d_theta_or=base.d_theta_or + 10), 0.0
    yield "hot-spot gradient −8 K", replace(base, d_theta_hr=base.d_theta_hr - 8), 0.0
    yield "hot-spot gradient +8 K", replace(base, d_theta_hr=base.d_theta_hr + 8), 0.0
    yield "loss ratio R ×0.7", replace(base, R=base.R * 0.7), 0.0
    yield "loss ratio R ×1.3", replace(base, R=base.R * 1.3), 0.0
    yield "oil time constant 90 min", replace(base, tau_o_min=90.0), 0.0
    yield "oil time constant 270 min", replace(base, tau_o_min=270.0), 0.0
    yield "winding exponent y 1.3", replace(base, y=1.3), 0.0
    yield "winding exponent y 2.0", replace(base, y=2.0), 0.0
    yield "outdoor unit (no kiosk rise)", replace(base, d_theta_or=base.d_theta_or - kiosk_rise, enclosure="outdoor",
                                                 enclosure_rise_k=0.0), 0.0
    yield "full kiosk rise (IEC Table 6)", replace(base, d_theta_or=base.d_theta_or + kiosk_rise,
                                                  enclosure_rise_k=2 * kiosk_rise), 0.0
    yield "thermally upgraded paper", replace(base, paper="upgraded"), 0.0
    yield "air at the transformer −5 K", base, -5.0
    yield "air at the transformer +5 K", base, +5.0
    assert ThermalParams                                       # imported for the reader: the base is a ThermalParams


def limit_rows(street: str, year: int, houses: int) -> list[dict]:
    """One loading year (this street with this many houses), judged with every variation: the thermal flags it raises."""
    from hotspotlv.study import run_case, thermal_params_for
    from hotspotlv.thermal import simulate
    from hotspotlv.verdict import judge
    case = run_study.case_for(street, houses)
    wx = run_study.weather(year)
    res = run_case(case, wx, fast=True)
    K = res.year.hourly["loading_pct"] / 100
    rows = []
    for name, params, dt in variations(thermal_params_for(case)):
        v = judge(res.year, simulate(K, wx["temp_c"] + dt, params))
        risk, watch = [r.rule for r in v.at_risk], [r.rule for r in v.watch]
        rows.append({"street": street, "year": year, "houses": houses, "variation": name,
                     "peak_loading_pct": float(K.max() * 100),
                     "at_risk_thermal": run_study._has(risk, run_study.THERMAL_RULES),
                     "watch_thermal": run_study._has(watch, run_study.THERMAL_RULES),
                     "life_days_per_year": v.really["days of life used per year"],
                     "max_hot_spot_c": v.really["max hot-spot (°C)"]})
    return rows


def limits(workers: int) -> None:
    tasks = [(s, y, n) for s in run_study.STREETS for y in YEARS for n in run_study.STREETS[s]["houses"]]
    tasks.sort(key=lambda t: -t[2] * run_study.STREETS[t[0]]["per_cable"])     # big ones first
    print(f"limits: {len(tasks)} loading years × 16 variations, {workers} workers", flush=True)
    t0, rows = time.perf_counter(), []
    with ProcessPoolExecutor(workers) as pool:
        futures = {pool.submit(limit_rows, *t): t for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            rows += fut.result()                              # a failed run stops the part: no silent holes
            if i % 10 == 0 or i == len(tasks):
                print(f"{i}/{len(tasks)} done, {time.perf_counter() - t0:.0f} s", flush=True)
    out = pd.DataFrame(rows).sort_values(["street", "year", "variation", "houses"])
    out.to_csv(run_study.RESULTS / "sensitivity_limits.csv", index=False, lineterminator="\n")


def stations() -> None:
    from hotspotlv.study import run_case, thermal_params_for
    from hotspotlv.thermal import simulate
    from hotspotlv.verdict import judge
    runs = pd.read_csv(run_study.RESULTS / "runs.csv")
    ref = reference_houses(runs)
    print("reference stations:", ref, flush=True)
    rows = []
    for street, houses in ref.items():
        case = run_study.case_for(street, houses)
        for year in YEARS:
            wx = run_study.weather(year)
            res = run_case(case, wx, fast=True)
            K = res.year.hourly["loading_pct"] / 100
            base = thermal_params_for(case)
            for name, params, dt in variations(base):
                th = simulate(K, wx["temp_c"] + dt, params)
                v = judge(res.year, th)
                rows.append({"street": street, "label": run_study.STREETS[street]["label"], "houses": houses,
                             "year": year, "variation": name, "peak_loading_pct": float(K.max() * 100),
                             "life_days_per_year": v.really["days of life used per year"],
                             "max_hot_spot_c": v.really["max hot-spot (°C)"], "verdict": v.level,
                             "why": v.headline()})
            print(f"{street} {houses} houses {year}: done", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(run_study.RESULTS / "sensitivity.csv", index=False, lineterminator="\n")
    print(out.pivot_table(index="variation", columns=["street", "year"], values="life_days_per_year").round(3))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=("stations", "limits", "all"), default="all")
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()
    if a.part in ("limits", "all"):
        limits(a.workers)
    if a.part in ("stations", "all"):
        stations()
    return 0


if __name__ == "__main__":
    sys.exit(main())
