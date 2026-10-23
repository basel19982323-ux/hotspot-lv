"""Charts and summary tables of the study: docs/figures/*.png and study/results/summary_*.csv.

    python study/figures.py           (after run_study.py, run_study.py --meters, refine.py and sensitivity.py)

Two views of the same station:
- what a planner sees: the customers' hourly meter values summed per station (active power, no losses), against the
  transformer's kVA rating — the 80 % rule of Energiforsk 2018:552 is applied to this sum (meter_sums*.csv);
- what the transformer sees: the modelled loading in kVA (with reactive power and losses), the IEC 60076-7 thermal
  model and the verdict (runs*.csv).
First counts of the meter rules and of the thermal limits are exact to one house (grid + refine.py); the house
voltage and the cable rating are read on the grid only (see refine.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_study  # noqa: E402
from hotspotlv.verdict import DESIGN_LIFE_DAYS_PER_YEAR as DESIGN  # noqa: E402
from hotspotlv.verdict import WATCH_LIFE_DAYS_PER_YEAR as WATCH_DAYS  # noqa: E402

FIG = HERE.parent / "docs" / "figures"
ORDER = ["rural", "suburb", "dense"]
COLORS = {"rural": "#2e7d32", "suburb": "#1f4e79", "dense": "#8e44ad"}
OFF_TOP = 1e6                     # days/yr: where "not computed (load above 2 × rated)" is drawn
RULE80 = "80 % rule: summed meters above 80 % of the rating"
METER100 = "summed meters above the rating (100 %)"
WATCH = "transformer thermal limit, Watch (IEC 60076-7)"
RISK = "transformer thermal limit, At risk (IEC 60076-7)"
VOLT = "house voltage outside ±10 % (EIFS 2023:3)"
CABLE = "a cable above its continuous rating"
CRITERIA = [RULE80, METER100, WATCH, RISK, VOLT, CABLE]
FOOTER = ("Simulation (Hotspot LV): illustrative streets, synthetic houses, generic IEC 60076-7 constants, "
          "hourly balanced power flow — not measured.")
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})


def label(street: str) -> str:
    s = run_study.STREETS[street]
    return f"{s['label']} — {s['kva']} kVA, {round(s['segment_km'] * 1000)} m between houses, ≤ {s['per_cable']} per cable"


def footer(fig) -> None:
    fig.text(0.01, 0.004, FOOTER, fontsize=6.5, color="#777777", ha="left", va="bottom")


def planned_grid() -> set[tuple[str, int, int]]:
    return {(s, y, n) for s in run_study.STREETS for y in run_study.YEARS for n in run_study.STREETS[s]["houses"]}


def _keys(df: pd.DataFrame) -> list[tuple[str, int, int]]:
    return list(zip(df["street"], df["year"].astype(int), df["houses"].astype(int)))


def load(results: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Grid and refined rows of the full runs and of the meter sums, each with a `grid` column. The grid files must
    hold exactly the planned grid: a missing or doubled run would silently move a first limit."""
    out = []
    for grid_name, fine_name in (("runs.csv", "runs_fine.csv"), ("meter_sums.csv", "meter_sums_fine.csv")):
        grid = pd.read_csv(results / grid_name)
        keys = _keys(grid)
        if len(keys) != len(set(keys)) or set(keys) != planned_grid():
            raise ValueError(f"{grid_name} does not hold exactly the planned grid ({len(keys)} rows, "
                             f"{len(set(keys))} unique, {len(planned_grid())} planned)")
        parts = [grid.assign(grid=True)]
        if (results / fine_name).exists():
            fine = pd.read_csv(results / fine_name)
            if set(_keys(fine)) & set(keys) or len(_keys(fine)) != len(set(_keys(fine))):
                raise ValueError(f"{fine_name} repeats a grid row or itself")
            parts.append(fine.assign(grid=False))
        else:
            print(f"note: no {fine_name} — first counts are read on the grid only (run refine.py)", flush=True)
        out.append(pd.concat(parts, ignore_index=True))
    runs, meters = out
    for name, df, masks in (("runs", runs, (runs["watch_thermal"] | runs["at_risk_thermal"], runs["at_risk_thermal"])),
                            ("meter sums", meters, (meters["meter_peak_pct"] > 80, meters["meter_peak_pct"] > 100))):
        if not (~df["grid"]).any():
            continue
        have = set(_keys(df))
        for mask in masks:
            missing = {(s, y, n) for (s, y), counts in gaps_below_first(df[df["grid"]], mask[df["grid"]]).items()
                       for n in counts} - have
            if missing:
                raise ValueError(f"the refined {name} leave {len(missing)} gaps below a first limit, e.g. "
                                 f"{sorted(missing)[:3]} — run refine.py again")
    return runs, meters


def gaps_below_first(grid: pd.DataFrame, mask: pd.Series) -> dict[tuple[str, int], range]:
    import refine
    return refine.gaps_below_first(grid, mask)


def first_houses(runs: pd.DataFrame, meters: pd.DataFrame) -> pd.DataFrame:
    """Per street, weather year and criterion: the smallest house count that meets it (NaN: not within the studied
    range). Meter rules and thermal limits use every row (grid and refined); voltage and cable the grid only."""
    rgrid = runs[runs["grid"]]
    crit = {
        RULE80: (meters, meters["meter_peak_pct"] > 80),
        METER100: (meters, meters["meter_peak_pct"] > 100),
        WATCH: (runs, runs["watch_thermal"] | runs["at_risk_thermal"]),
        RISK: (runs, runs["at_risk_thermal"]),
        VOLT: (rgrid, rgrid["at_risk_voltage"]),
        CABLE: (rgrid, rgrid["watch_cable"]),
    }
    rows = []
    for (street, year), g in rgrid.groupby(["street", "year"]):
        for name, (df, mask) in crit.items():
            hit = df[mask & (df["street"] == street) & (df["year"] == year)]["houses"]
            rows.append({"street": street, "year": int(year), "criterion": name,
                         "houses": float(hit.min()) if len(hit) else np.nan,
                         "range_max": float(g["houses"].max())})
    return pd.DataFrame(rows)


def median_13(v: pd.Series) -> float:
    """Median over all weather years, a year that did not reach the limit counted as beyond the range (+inf).
    Returns inf when the median itself lies beyond the range."""
    return float(np.median(v.fillna(np.inf).to_numpy()))


def summarise(first: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (street, crit), g in first.groupby(["street", "criterion"], sort=False):
        v = g["houses"]
        m = median_13(v)
        rows.append({"street": street, "criterion": crit, "min": v.min(), "max_reached": v.max(),
                     "median_13_years": m if np.isfinite(m) else np.nan,
                     "median_beyond_range": not np.isfinite(m), "years_reached": int(v.notna().sum()),
                     "years": len(v), "range_max": g["range_max"].iloc[0]})
    return pd.DataFrame(rows)


def ratios(first: pd.DataFrame) -> pd.DataFrame:
    """Per street, year and thermal level: first thermal count ÷ first 80 % rule count. When the thermal limit was
    not reached, `above` is the lower bound (largest studied count ÷ rule count)."""
    p = first.pivot_table(index=["street", "year"], columns="criterion", values="houses", dropna=False)
    rmax = first.groupby(["street", "year"])["range_max"].first()
    rows = []
    for (street, year), r in p.iterrows():
        for level, crit in (("Watch", WATCH), ("At risk", RISK)):
            reached = not np.isnan(r[crit])
            rows.append({"street": street, "year": year, "level": level, "rule80": r[RULE80], "thermal": r[crit],
                         "ratio": r[crit] / r[RULE80] if reached else np.nan,
                         "above": np.nan if reached else rmax.loc[(street, year)] / r[RULE80]})
    return pd.DataFrame(rows)


def fig_first_limits(first: pd.DataFrame) -> None:
    fig, axes = plt.subplots(len(ORDER), 1, figsize=(12, 9.0), sharex=False)
    for ax, street in zip(axes, ORDER):
        f = first[first["street"] == street]
        top = f["range_max"].max()
        for i, c in enumerate(CRITERIA):
            v = f[f["criterion"] == c]["houses"]
            y = len(CRITERIA) - 1 - i
            n_none = int(v.isna().sum())
            vv = v.dropna()
            if len(vv):
                ax.plot([vv.min(), vv.max()], [y, y], color=COLORS[street], lw=5, alpha=0.35, solid_capstyle="round")
                ax.plot([vv.min(), vv.max()], [y, y], "|", color=COLORS[street], ms=9, mew=1.5)   # visible even
                # when min = max (a limit reached in a single year)
            m = median_13(v)
            if np.isfinite(m):
                ax.plot(m, y, "o", color=COLORS[street], ms=6)
            note = f"not reached in {n_none} of {len(v)} years (≤ {top:.0f} houses)" if n_none else ""
            ax.text(1.02, y, note, va="center", fontsize=7, color="#555555", transform=ax.get_yaxis_transform(),
                    clip_on=False)
        ax.set_yticks(range(len(CRITERIA)))
        ax.set_yticklabels(list(reversed(CRITERIA)), fontsize=8)
        ax.set_title(label(street), fontsize=9, loc="left")
        ax.set_xlabel("houses on the station (2030 mix: 80 % electric heating, 60 % EVs, 40 % PV; one random draw)")
        ax.grid(axis="x", alpha=0.3)
    fig.suptitle("Where each limit is first reached, over 13 weather years (2009–2023 without 2016 and 2019)\n"
                 "line: range over the years that reached it;  dot: median of all 13, a year not reached counted as "
                 "beyond the range (no dot: the median is beyond)", fontsize=9, x=0.01, ha="left")
    footer(fig)
    fig.subplots_adjust(left=0.30, right=0.74, top=0.90, bottom=0.07, hspace=0.62)
    fig.savefig(FIG / "first_limits.png")
    plt.close(fig)


def fig_life(runs: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, len(ORDER), figsize=(10, 3.8), sharey=True)
    for ax, street in zip(axes, ORDER):
        g = runs[(runs["street"] == street) & runs["grid"]].copy()
        # not computed (load above 2 x rated) = far beyond any limit: drawn at the top, not left out
        g["life"] = g["life_days_per_year"].fillna(OFF_TOP)
        q = g.groupby("houses")["life"].agg(["min", "median", "max"])
        ax.fill_between(q.index, q["min"].clip(lower=1e-3), q["max"].clip(lower=1e-3), color=COLORS[street], alpha=0.2,
                        label="range over 13 weather years")
        ax.plot(q.index, q["median"].clip(lower=1e-3), color=COLORS[street], lw=2, label="median year")
        ax.axhline(DESIGN, color="#c0392b", ls="--", lw=1)
        ax.axhline(WATCH_DAYS, color="#e67e22", ls=":", lw=1)
        ax.text(q.index.min(), DESIGN * 1.15, f"{DESIGN:.0f} days/yr = IEC design rate (At risk above)", fontsize=7,
                color="#c0392b")
        ax.text(q.index.min(), WATCH_DAYS * 0.62, f"{WATCH_DAYS:.0f} = half (Watch)", fontsize=7, color="#e67e22")
        ax.set_yscale("log")
        ax.set_ylim(top=OFF_TOP * 1.5)
        ax.set_title(run_study.STREETS[street]["label"] + f" ({run_study.STREETS[street]['kva']} kVA)", fontsize=9)
        ax.set_xlabel("houses")
        ax.grid(alpha=0.3, which="both")
    axes[0].set_ylabel("insulation life used (days per year, log)")
    axes[0].legend(fontsize=7, loc="lower right")
    fig.suptitle("Insulation life used as the street grows — modelled, kiosk, generic IEC 60076-7 constants\n"
                 f"A year whose load passes 2 × rated (not computed) is drawn at the top ({OFF_TOP:,.0f} days/yr)",
                 fontsize=9, x=0.01, ha="left")
    footer(fig)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / "life_vs_houses.png")
    plt.close(fig)


def fig_paper_vs_real(runs: pd.DataFrame, meters: pd.DataFrame) -> None:
    g = runs[runs["grid"]].merge(meters[meters["grid"]][["street", "year", "houses", "meter_hours_above_80"]],
                                 on=["street", "year", "houses"], validate="one_to_one")
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    n_off = 0
    for street in ORDER:
        s = g[g["street"] == street]
        ok = s.dropna(subset=["life_days_per_year"])
        off = s[s["life_days_per_year"].isna()]
        n_off += len(off)
        ax.scatter(ok["meter_hours_above_80"].clip(lower=0.5), ok["life_days_per_year"].clip(lower=1e-3), s=10,
                   alpha=0.6, color=COLORS[street], label=run_study.STREETS[street]["label"])
        ax.scatter(off["meter_hours_above_80"].clip(lower=0.5), np.full(len(off), OFF_TOP), s=14, marker="^",
                   color=COLORS[street], alpha=0.8)
    ax.axhline(DESIGN, color="#c0392b", ls="--", lw=1)
    ax.text(0.6, DESIGN * 1.15, f"{DESIGN:.0f} days/yr: ageing at the IEC design rate", fontsize=7, color="#c0392b")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_ylim(top=OFF_TOP * 2.5)
    ax.set_xlabel("hours per year the summed meters are above 80 % of the rating (the 80 % rule's view; log; "
                  "0 h at the left edge)", fontsize=8)
    ax.set_ylabel("insulation life used (days per year, log)")
    ax.set_title("Nameplate view vs modelled ageing — every grid run (3 streets × houses × 13 weather years)\n"
                 f"▲ at the top: {n_off} runs whose load passes 2 × rated (ageing not computed)", fontsize=9, loc="left")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8, loc="upper left")
    footer(fig)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / "paper_vs_real.png")
    plt.close(fig)


PESSIMISTIC = ("* a pessimistic bound beyond a real unit: +10 K top-oil or +8 K gradient would fail the IEC 60076-2 "
               "temperature-rise test; the doubled kiosk correction goes beyond IEC 60076-7")


def fig_sensitivity(sens: pd.DataFrame) -> None:
    years = sorted(sens["year"].unique())
    fig, axes = plt.subplots(1, len(ORDER), figsize=(11, 4.8), sharey=True)
    for ax, street in zip(axes, ORDER):
        g = sens[(sens["street"] == street) & (sens["year"] == 2010)]
        base = float(g[g["variation"].str.startswith("base")]["life_days_per_year"].iloc[0])
        g = g[~g["variation"].str.startswith("base")]
        y = np.arange(len(g))
        vals = g["life_days_per_year"].to_numpy()
        ax.barh(y, vals - base, left=base, color=[("#c0392b" if v > base else "#2e7d32") for v in vals], alpha=0.7)
        ax.axvline(base, color="#333333", lw=1)
        ax.axvline(DESIGN, color="#c0392b", ls="--", lw=1)
        ax.set_xscale("log")
        ax.set_yticks(y); ax.set_yticklabels(g["variation"], fontsize=7)
        houses = int(g["houses"].iloc[0])
        ax.set_title(f"{run_study.STREETS[street]['label']}, {houses} houses, weather year 2010", fontsize=8)
        ax.set_xlabel("life used (days/yr, log)")
        for yi, (v, verdict) in enumerate(zip(vals, g["verdict"])):
            ax.text(max(v, base) * 1.08, yi, verdict, va="center", fontsize=6, color="#555555")
    fig.suptitle("How much the generic transformer constants matter — one at a time, in the coldest of the 13 weather years "
                 f"(also computed: {', '.join(str(y) for y in years if y != 2010)}; all in sensitivity.csv)\n"
                 "Each street's station: the fewest houses whose transformer peak, as a median over the 13 years, is "
                 "above its rating.  Black line: generic constants; red dashed: the IEC design rate\n" + PESSIMISTIC,
                 fontsize=8.5, x=0.01, ha="left")
    footer(fig)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / "sensitivity.png")
    plt.close(fig)


def limits_by_variation(lim: pd.DataFrame, first: pd.DataFrame) -> pd.DataFrame:
    """Per street, year and variation: the first house count with a thermal Watch / At risk (NaN: not reached; exact
    when the rows include sensitivity_limits_fine.csv), next to where the 80 % rule first flags the same street in
    the same year (exact; the loading, and so the rule, does not depend on the transformer constants)."""
    rule = first[first["criterion"] == RULE80].set_index(["street", "year"])["houses"]
    rows = []
    for (street, year, var), g in lim.groupby(["street", "year", "variation"]):
        watch = g[g["watch_thermal"] | g["at_risk_thermal"]]["houses"]
        risk = g[g["at_risk_thermal"]]["houses"]
        rows.append({"street": street, "year": int(year), "variation": var,
                     "watch": float(watch.min()) if len(watch) else np.nan,
                     "at_risk": float(risk.min()) if len(risk) else np.nan,
                     "rule80": float(rule.loc[(street, int(year))]), "range_max": float(g["houses"].max())})
    out = pd.DataFrame(rows)
    out["watch_vs_rule80"] = out["watch"] / out["rule80"]
    out["at_risk_vs_rule80"] = out["at_risk"] / out["rule80"]
    return out


def fig_sensitivity_limits(by_var: pd.DataFrame, names: list[str], exact: bool = False) -> None:
    years = {2010: "o", 2023: "s"}
    fig, axes = plt.subplots(1, len(ORDER), figsize=(11, 5.3), sharey=True)
    y_of = {n: len(names) - 1 - i for i, n in enumerate(names)}
    for ax, street in zip(axes, ORDER):
        g = by_var[by_var["street"] == street]
        top = float(g["range_max"].max())
        for year, mk in years.items():
            gy = g[g["year"] == year]
            ax.axvline(float(gy["rule80"].iloc[0]), color="#777777", lw=1, ls="-" if year == 2010 else "--")
            for _, r in gy.iterrows():
                y = y_of[r["variation"]] + (0.15 if year == 2010 else -0.15)
                if np.isnan(r["at_risk"]):
                    ax.plot(top, y, marker=">", color=COLORS[street], mfc="white", ms=6)
                else:
                    ax.plot(r["at_risk"], y, marker=mk, color=COLORS[street], ms=5,
                            mfc=COLORS[street] if year == 2010 else "white")
        ax.set_xlim(0, top * 1.08)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(list(reversed(names)), fontsize=7)
        ax.set_ylim(-0.7, len(names) - 0.3)
        ax.set_title(f"{run_study.STREETS[street]['label']} ({run_study.STREETS[street]['kva']} kVA)", fontsize=9)
        ax.set_xlabel("houses")
        ax.grid(axis="x", alpha=0.3)
    not_2020 = by_var[by_var["year"] == 2020]["at_risk"].isna().all()
    any_open = by_var[by_var["year"].isin(list(years))]["at_risk"].isna().any()
    fig.suptitle("How far the first thermal At-risk limit moves when one transformer constant is moved — houses where "
                 "it is first reached (" + ("exact to one house" if exact else "on the 5/10-house grid") + ")\n"
                 "● weather year 2010 (coldest)   □ 2023" + ("   ▷ not reached within the studied range" if any_open else "")
                 + ("   (2020, the mildest: not reached in any variation)" if not_2020 else "") + "\n"
                 "grey lines: where the 80 % rule (summed meters) first flags the same street — solid 2010, dashed 2023; "
                 "it does not depend on the transformer constants\n" + PESSIMISTIC,
                 fontsize=8, x=0.01, ha="left")
    footer(fig)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / "sensitivity_limits.png")
    plt.close(fig)


def fig_winters(runs: pd.DataFrame, wx_stats: pd.DataFrame, ref: dict) -> None:
    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    order = wx_stats.sort_values("hours_below_minus10", ascending=False)["year"].tolist()
    for street in ORDER:
        g = runs[(runs["street"] == street) & (runs["houses"] == ref[street]) & runs["grid"]].set_index("year").loc[order]
        ax.plot(range(len(order)), g["life_days_per_year"], "o-", color=COLORS[street],
                label=f"{run_study.STREETS[street]['label']}, {ref[street]} houses")
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([f"{y}\n{int(h)} h" for y, h in zip(order, wx_stats.set_index("year").loc[order, "hours_below_minus10"])],
                       fontsize=7)
    ax.set_xlabel("weather year, coldest first (hours per year below −10 °C at SMHI 96190)")
    ax.set_ylabel("life used (days/yr)")
    ax.set_ylim(bottom=0)
    ax.set_title("The same stations in 13 real weather years (modelled insulation ageing)", fontsize=9, loc="left")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    footer(fig)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / "winters.png")
    plt.close(fig)


def main() -> int:
    FIG.mkdir(parents=True, exist_ok=True)
    res = run_study.RESULTS
    runs, meters = load(res)
    first = first_houses(runs, meters)
    first.to_csv(res / "first_limits.csv", index=False, lineterminator="\n")
    summary = summarise(first)
    summary.to_csv(res / "summary_first_limits.csv", index=False, lineterminator="\n")
    ratios(first).to_csv(res / "summary_ratios.csv", index=False, float_format="%.4f", lineterminator="\n")
    wx_stats = pd.DataFrame([{"year": y, "hours_below_minus10": int((run_study.weather(y)["temp_c"] < -10).sum()),
                              "min_temp_c": float(run_study.weather(y)["temp_c"].min()),
                              "mean_temp_c": float(run_study.weather(y)["temp_c"].mean())} for y in run_study.YEARS])
    wx_stats.to_csv(res / "winters.csv", index=False, lineterminator="\n")
    fig_first_limits(first)
    fig_life(runs)
    fig_paper_vs_real(runs, meters)
    sens_file = res / "sensitivity.csv"
    if sens_file.exists():
        sens = pd.read_csv(sens_file)
        fig_sensitivity(sens)
        ref = sens.groupby("street")["houses"].first().to_dict()
        fig_winters(runs, wx_stats, ref)
    lim_file = res / "sensitivity_limits.csv"
    if lim_file.exists():
        import sensitivity
        from hotspotlv.thermal import ThermalParams
        names = [n for n, _, _ in sensitivity.variations(ThermalParams())]
        lim = pd.read_csv(lim_file)
        lim_fine = res / "sensitivity_limits_fine.csv"
        exact = lim_fine.exists()
        if exact:                                    # the step below each first grid count, house by house
            lim = pd.concat([lim, pd.read_csv(lim_fine)], ignore_index=True)
        by_var = limits_by_variation(lim, first)
        by_var.to_csv(res / "summary_sensitivity_limits.csv", index=False, lineterminator="\n")
        fig_sensitivity_limits(by_var, names, exact)
    print(summary.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
