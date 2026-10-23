"""The study: three street types growing house by house, judged in 13 real winters.

    python study/run_study.py [--workers 10] [--only rural,suburb,dense] [--years 2010,2020]

For each street type, year and number of houses: the program's own scenario model builds the houses (2030 mix:
heat pumps, direct electric heating, 60 % EVs, 40 % rooftop PV — the same dice in every year, only the weather
changes), the full-year power flow and the IEC 60076-7 thermal model run as in the app (fast mode: a fitted proxy
plus exact power flows near the limits, or every hour exactly when the proxy is not good enough), and the verdict
says which limit is reached. Results: study/results/runs.csv, one row per run, appended as runs finish.

The street grows the way a planner would build it: every cable feeds at most N houses, so more houses means more
cables from the same transformer — not ever-longer cables (the earlier sweep only lengthened three cables, which
favoured voltage and cable problems: research of 23 Oct 2026). The street types are illustrative assumptions, not
measured networks:

    rural   Rural road    315 kVA (uk 4 %)   60 m between house connections   at most  8 houses per cable
    suburb  Villa suburb  500 kVA (uk 6 %)   30 m                              at most 15 houses per cable
    dense   Dense town    800 kVA (uk 6 %)   15 m                              at most 25 houses per cable
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEATHER = HERE / "data" / "weather"
RESULTS = HERE / "results"
YEARS = [2009, 2010, 2011, 2012, 2013, 2014, 2015, 2017, 2018, 2020, 2021, 2022, 2023]
STREETS = {
    "rural": dict(label="Rural road", kva=315, segment_km=0.060, per_cable=8, houses=list(range(10, 101, 5))),
    "suburb": dict(label="Villa suburb", kva=500, segment_km=0.030, per_cable=15, houses=list(range(20, 161, 10))),
    "dense": dict(label="Dense town", kva=800, segment_km=0.015, per_cable=25, houses=list(range(40, 261, 10))),
}
HEATING_2030 = {"ground_source": 0.3, "air_water": 0.3, "direct_electric": 0.2, "none": 0.2}
SEED = 0
THERMAL_RULES = ("ageing faster than design", "ageing above half the design rate", "hot-spot above", "top-oil above",
                 "load above", "loading above the IEC short-time")
VOLTAGE_RULES = ("house voltage",)
CABLE_RULES = ("a cable above",)
FIELDS = ["street", "label", "year", "houses", "cables", "kVA", "segment_m", "verdict", "why", "peak_loading_pct",
          "hours_above_80", "hours_above_100", "life_days_per_year", "max_hot_spot_c", "max_top_oil_c", "max_load_pu",
          "voltage_bad_hours", "voltage_plan_hours", "max_cable_pct", "energy_mwh", "at_risk_thermal",
          "at_risk_voltage", "watch_thermal", "watch_voltage", "watch_cable", "rules_at_risk", "rules_watch",
          "mode", "runtime_s"]

_WEATHER: dict = {}


def weather(year: int):
    import pandas as pd
    if year not in _WEATHER:
        _WEATHER[year] = pd.read_csv(WEATHER / f"weather_{year}.csv", index_col=0, parse_dates=True)
    return _WEATHER[year]


def scenario(houses: int):
    from hotspotlv.neighbourhood import Scenario
    return Scenario("2030", n_houses=houses, heating_mix=dict(HEATING_2030), ev_share=0.6, pv_share=0.4, seed=SEED)


def case_for(street: str, houses: int):
    from hotspotlv.study import Case
    s = STREETS[street]
    return Case(f"{s['label']}, {houses} houses", scenario(houses), trafo_kva=s["kva"],
                n_feeders=math.ceil(houses / s["per_cable"]), main_segment_km=s["segment_km"])


def _has(rules, keys) -> bool:
    return any(r.startswith(k) for r in rules for k in keys)


def run_one(street: str, year: int, houses: int) -> dict:
    from hotspotlv.study import run_case
    case = case_for(street, houses)
    res = run_case(case, weather(year), fast=True)
    m, v = res.metrics(), res.verdict
    risk, watch = [r.rule for r in v.at_risk], [r.rule for r in v.watch]
    s = STREETS[street]
    return {"street": street, "label": s["label"], "year": year, "houses": houses, "cables": case.n_feeders,
            "kVA": s["kva"], "segment_m": round(s["segment_km"] * 1000), "verdict": m["verdict"], "why": m["why"],
            "peak_loading_pct": m["peak_loading_pct"], "hours_above_80": m["hours_above_80"],
            "hours_above_100": m["hours_above_100"], "life_days_per_year": m["life_days_per_year"],
            "max_hot_spot_c": m["max_hot_spot_c"], "max_top_oil_c": v.really.get("max top-oil (°C)"),
            "max_load_pu": m["max_load_pu"], "voltage_bad_hours": m["voltage_bad_hours"],
            "voltage_plan_hours": m["voltage_plan_hours"], "max_cable_pct": m["max_cable_pct"],
            "energy_mwh": m["energy_mwh"], "at_risk_thermal": _has(risk, THERMAL_RULES),
            "at_risk_voltage": _has(risk, VOLTAGE_RULES), "watch_thermal": _has(watch, THERMAL_RULES),
            "watch_voltage": _has(watch, VOLTAGE_RULES), "watch_cable": _has(watch, CABLE_RULES),
            "rules_at_risk": " | ".join(risk), "rules_watch": " | ".join(watch),
            "mode": "fast" if res.fast else "full", "runtime_s": round(res.runtime_s, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--only", default=",".join(STREETS))
    ap.add_argument("--years", default=",".join(map(str, YEARS)))
    ap.add_argument("--out", default=str(RESULTS / "runs.csv"))
    a = ap.parse_args()
    streets = a.only.split(",")
    years = [int(y) for y in a.years.split(",")]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():                                            # resume: skip what is already there
        with open(out, newline="", encoding="utf-8") as f:
            done = {(r["street"], int(r["year"]), int(r["houses"])) for r in csv.DictReader(f)}
    tasks = [(s, y, n) for s in streets for y in years for n in STREETS[s]["houses"] if (s, y, n) not in done]
    tasks.sort(key=lambda t: -t[2] * STREETS[t[0]]["per_cable"])   # big ones first: better load balance
    print(f"{len(tasks)} runs to do ({len(done)} already in {out.name}), {a.workers} workers", flush=True)
    t0 = time.perf_counter()
    new = not out.exists()
    with open(out, "a", newline="", encoding="utf-8") as f, ProcessPoolExecutor(a.workers) as pool:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        if new:
            w.writeheader()
        futures = {pool.submit(run_one, *t): t for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            try:
                row = fut.result()
            except Exception as e:                              # one failed run must not stop the study
                print(f"FAILED {t}: {type(e).__name__}: {e}", flush=True)
                continue
            w.writerow(row)
            f.flush()
            if i % 10 == 0 or i == len(tasks):
                print(f"{i}/{len(tasks)} done, {time.perf_counter() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
