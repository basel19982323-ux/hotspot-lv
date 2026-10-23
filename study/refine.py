"""Exact first points: the study's grid (5 or 10 houses) refined to single houses just below each first limit.

    python study/refine.py [--workers 10]        (after run_study.py and run_study.py --meters)

The grid tells in which step a limit is first reached: between the previous grid count and the first one that meets
it. This script fills that step house by house, so the first house count is exact instead of "within one step":
- the 80 % rule and the nameplate on the summed meter values: summed meters only (seconds, no power flow);
- the transformer's thermal Watch and At risk: full runs (power flow, thermal model, verdict), as in run_study.py;
- the exact first counts of the two meter rules get a full run too, so the transformer's state there is known.
The house voltage and the cable rating are not refined: the cable loading comes and goes as cables fill up (it depends
on how many houses the fullest cable carries, not on the total), so a first count between grid points would only
reflect that alignment.

Results: study/results/meter_sums_fine.csv and study/results/runs_fine.csv (same columns as the grid files; the
grid rows are not repeated).
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_study  # noqa: E402

METER_THRESHOLDS = {"meter_peak_pct": (80.0, 100.0)}


def gaps_below_first(grid: pd.DataFrame, mask: pd.Series) -> dict[tuple[str, int], range]:
    """Per (street, year): the house counts strictly between the previous grid count and the first grid count that
    meets the mask (empty when the first grid count is the street's smallest; absent when never met)."""
    out = {}
    for (street, year), g in grid.groupby(["street", "year"]):
        counts = sorted(g["houses"])
        hit = sorted(g[mask.loc[g.index]]["houses"])
        if not hit:
            continue
        first = hit[0]
        below = [c for c in counts if c < first]
        out[(street, int(year))] = range(int(below[-1]) + 1, int(first)) if below else range(0)
    return out


def first_exact(rows: pd.DataFrame, mask: pd.Series) -> dict[tuple[str, int], int]:
    hit = rows[mask]
    return {(s, int(y)): int(g["houses"].min()) for (s, y), g in hit.groupby(["street", "year"])}


def _pool_map(fn, tasks, workers, label):
    rows, t0 = [], time.perf_counter()
    with ProcessPoolExecutor(workers) as pool:
        futures = {pool.submit(fn, *t): t for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            rows.append(fut.result())                          # a failed run stops the script: no silent holes
            if i % 20 == 0 or i == len(tasks):
                print(f"{label}: {i}/{len(tasks)} done, {time.perf_counter() - t0:.0f} s", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()
    res = run_study.RESULTS

    # 1. the two meter rules, house by house below their first grid count
    mgrid = pd.read_csv(res / "meter_sums.csv")
    mtasks = set()
    for thr in METER_THRESHOLDS["meter_peak_pct"]:
        for (s, y), counts in gaps_below_first(mgrid, mgrid["meter_peak_pct"] > thr).items():
            mtasks |= {(s, y, n) for n in counts}
    mfine = pd.DataFrame(_pool_map(run_study.meter_sum, sorted(mtasks), a.workers, "meter sums"),
                         columns=run_study.METER_FIELDS).sort_values(["street", "year", "houses"])
    mfine.to_csv(res / "meter_sums_fine.csv", index=False, float_format="%.3f", lineterminator="\n")
    mall = pd.concat([mgrid, mfine], ignore_index=True)

    # 2. full runs: below the first thermal Watch / At risk, and at the exact first counts of the meter rules
    runs = pd.read_csv(res / "runs.csv")
    done = set(zip(runs["street"], runs["year"].astype(int), runs["houses"].astype(int)))
    rtasks = set()
    for mask in (runs["watch_thermal"] | runs["at_risk_thermal"], runs["at_risk_thermal"]):
        for (s, y), counts in gaps_below_first(runs, mask).items():
            rtasks |= {(s, y, n) for n in counts}
    for thr in METER_THRESHOLDS["meter_peak_pct"]:
        rtasks |= {(s, y, n) for (s, y), n in first_exact(mall, mall["meter_peak_pct"] > thr).items()}
    rtasks = sorted(rtasks - done, key=lambda t: -t[2] * run_study.STREETS[t[0]]["per_cable"])
    print(f"{len(mfine)} meter sums done; {len(rtasks)} full runs to do", flush=True)
    rfine = pd.DataFrame(_pool_map(run_study.run_one, rtasks, a.workers, "full runs"), columns=run_study.FIELDS)
    rfine.sort_values(["street", "year", "houses"]).to_csv(res / "runs_fine.csv", index=False, lineterminator="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
