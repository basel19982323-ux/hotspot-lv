"""Charts and summary tables of the study: docs/figures/*.png and study/results/summary_*.csv.

    python study/figures.py           (after run_study.py and sensitivity.py)
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

FIG = HERE.parent / "docs" / "figures"
ORDER = ["rural", "suburb", "dense"]
COLORS = {"rural": "#2e7d32", "suburb": "#1f4e79", "dense": "#8e44ad"}
DESIGN, WATCH_DAYS = 365.0, 182.5
OFF_TOP = 1e5                     # days/yr: where "not computed, far beyond" is drawn
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})


def label(street: str) -> str:
    s = run_study.STREETS[street]
    return f"{s['label']} — {s['kva']} kVA, {round(s['segment_km'] * 1000)} m between houses, ≤ {s['per_cable']} per cable"


def first_houses(runs: pd.DataFrame) -> pd.DataFrame:
    """Per street, weather year and criterion: the smallest house count that meets it (NaN: not within the range)."""
    crit = {
        "80 % rule: peak above 80 % of nameplate": runs["peak_loading_pct"] > 80,
        "nameplate: peak above 100 %": runs["peak_loading_pct"] > 100,
        "thermal limit, Watch (IEC 60076-7)": runs["watch_thermal"] | runs["at_risk_thermal"],
        "thermal limit, At risk (IEC 60076-7)": runs["at_risk_thermal"],
        "house voltage outside ±10 % (EIFS 2023:3)": runs["at_risk_voltage"],
        "a cable above its current rating": runs["watch_cable"],
    }
    rows = []
    for (street, year), g in runs.groupby(["street", "year"]):
        for name, mask in crit.items():
            hit = g[mask.loc[g.index]].sort_values("houses")
            rows.append({"street": street, "year": year, "criterion": name,
                         "houses": float(hit["houses"].iloc[0]) if len(hit) else np.nan,
                         "range_max": float(g["houses"].max())})
    return pd.DataFrame(rows)


def fig_first_limits(first: pd.DataFrame) -> None:
    crits = list(dict.fromkeys(first["criterion"]))
    fig, axes = plt.subplots(len(ORDER), 1, figsize=(10.5, 7.8), sharex=False)
    for ax, street in zip(axes, ORDER):
        f = first[first["street"] == street]
        for i, c in enumerate(crits):
            v = f[f["criterion"] == c]["houses"]
            y = len(crits) - 1 - i
            top = f["range_max"].max()
            n_none = int(v.isna().sum())
            vv = v.dropna()
            if len(vv):
                ax.plot([vv.min(), vv.max()], [y, y], color=COLORS[street], lw=5, alpha=0.35, solid_capstyle="round")
                ax.plot(vv.median(), y, "o", color=COLORS[street], ms=6)
            note = f"not reached in {n_none} of {len(v)} weather years (≤ {top:.0f} houses)" if n_none else ""
            ax.text(1.02, y, note, va="center", fontsize=7, color="#555555", transform=ax.get_yaxis_transform(),
                    clip_on=False)
        ax.set_yticks(range(len(crits)))
        ax.set_yticklabels(list(reversed(crits)), fontsize=8)
        ax.set_title(label(street), fontsize=9, loc="left")
        ax.set_xlabel("houses on the station (2030 mix: 80 % electric heating, 60 % EVs, 40 % PV)")
        ax.grid(axis="x", alpha=0.3)
    fig.suptitle("Where each limit is first reached — line: range over 13 weather years (2009–2023), dot: median",
                 fontsize=10, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 0.78, 0.97))
    fig.savefig(FIG / "first_limits.png")
    plt.close(fig)


def fig_life(runs: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, len(ORDER), figsize=(10, 3.6), sharey=True)
    for ax, street in zip(axes, ORDER):
        g = runs[runs["street"] == street].copy()
        # not computed (load above 2 x rated) = far beyond any limit: counted as off the top, not left out
        g["life"] = g["life_days_per_year"].fillna(OFF_TOP)
        q = g.groupby("houses")["life"].agg(["min", "median", "max"]).clip(upper=OFF_TOP)
        ax.fill_between(q.index, q["min"].clip(lower=1e-3), q["max"].clip(lower=1e-3), color=COLORS[street], alpha=0.2,
                        label="range over 13 weather years")
        ax.plot(q.index, q["median"].clip(lower=1e-3), color=COLORS[street], lw=2, label="median year")
        ax.axhline(DESIGN, color="#c0392b", ls="--", lw=1)
        ax.axhline(WATCH_DAYS, color="#e67e22", ls=":", lw=1)
        ax.text(q.index.min(), DESIGN * 1.15, "365 days/yr = IEC design rate (At risk above)", fontsize=7, color="#c0392b")
        ax.text(q.index.min(), WATCH_DAYS * 0.62, "182 = half (Watch)", fontsize=7, color="#e67e22")
        ax.set_yscale("log")
        ax.set_ylim(top=OFF_TOP * 1.5)
        ax.set_title(run_study.STREETS[street]["label"] + f" ({run_study.STREETS[street]['kva']} kVA)", fontsize=9)
        ax.set_xlabel("houses")
        ax.grid(alpha=0.3, which="both")
    axes[0].set_ylabel("insulation life used (days per year, log)")
    axes[0].legend(fontsize=7, loc="lower right")
    fig.suptitle("Insulation life used as the street grows — kiosk, generic IEC 60076-7 constants\n"
                 f"A year whose load passes 2 × rated (not computed) is drawn at the top ({OFF_TOP:,.0f} days/yr)",
                 fontsize=9, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(FIG / "life_vs_houses.png")
    plt.close(fig)


def fig_paper_vs_real(runs: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for street in ORDER:
        g = runs[(runs["street"] == street)].dropna(subset=["life_days_per_year"])
        ax.scatter(g["hours_above_80"].clip(lower=0.5), g["life_days_per_year"].clip(lower=1e-3), s=10, alpha=0.6,
                   color=COLORS[street], label=run_study.STREETS[street]["label"])
    ax.axhline(DESIGN, color="#c0392b", ls="--", lw=1)
    ax.text(0.6, DESIGN * 1.15, "365 days/yr: ageing at the IEC design rate", fontsize=7, color="#c0392b")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("hours per year above 80 % of nameplate (the planning rule's view; log; 0 h drawn at the left edge)")
    ax.set_ylabel("insulation life used (days per year, log)")
    ax.set_title("On paper vs really ageing — every run of the study (3 streets × houses × 13 weather years)", fontsize=9,
                 loc="left")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "paper_vs_real.png")
    plt.close(fig)


def fig_sensitivity(sens: pd.DataFrame) -> None:
    years = sorted(sens["year"].unique())
    fig, axes = plt.subplots(1, len(ORDER), figsize=(11, 4.6), sharey=True)
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
                 "Each street's station: the fewest houses whose peak is above nameplate in the median year.  "
                 "Black line: generic constants; red dashed: 365 days/yr, the IEC design rate",
                 fontsize=9, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(FIG / "sensitivity.png")
    plt.close(fig)


def limits_by_variation(lim: pd.DataFrame, first: pd.DataFrame) -> pd.DataFrame:
    """Per street, year and variation: the first house count with a thermal Watch / At risk (NaN: not reached),
    next to where the 80 % rule first flags the same street in the same year (the loading does not change)."""
    rule = first[first["criterion"].str.startswith("80 %")].set_index(["street", "year"])["houses"]
    rows = []
    for (street, year, var), g in lim.groupby(["street", "year", "variation"]):
        watch = g[g["watch_thermal"] | g["at_risk_thermal"]]["houses"]
        risk = g[g["at_risk_thermal"]]["houses"]
        rows.append({"street": street, "year": year, "variation": var,
                     "watch": float(watch.min()) if len(watch) else np.nan,
                     "at_risk": float(risk.min()) if len(risk) else np.nan,
                     "rule80": float(rule.loc[(street, year)]), "range_max": float(g["houses"].max())})
    out = pd.DataFrame(rows)
    out["at_risk_vs_rule80"] = out["at_risk"] / out["rule80"]
    return out


def fig_sensitivity_limits(by_var: pd.DataFrame, names: list[str]) -> None:
    years = {2010: "o", 2023: "s"}
    fig, axes = plt.subplots(1, len(ORDER), figsize=(11, 5.0), sharey=True)
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
                 "it is first reached\n"
                 "● weather year 2010 (coldest)   □ 2023" + ("   ▷ not reached within the studied range" if any_open else "")
                 + ("   (2020, the mildest: not reached in any variation)" if not_2020 else "") + "\n"
                 "grey lines: where the 80 % rule first flags the same street — solid 2010, dashed 2023 "
                 "(the loading, and so the rule, does not depend on the transformer constants)",
                 fontsize=8.5, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(FIG / "sensitivity_limits.png")
    plt.close(fig)


def fig_winters(runs: pd.DataFrame, wx_stats: pd.DataFrame, ref: dict) -> None:
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    order = wx_stats.sort_values("hours_below_minus10", ascending=False)["year"].tolist()
    for street in ORDER:
        g = runs[(runs["street"] == street) & (runs["houses"] == ref[street])].set_index("year").loc[order]
        ax.plot(range(len(order)), g["life_days_per_year"], "o-", color=COLORS[street],
                label=f"{run_study.STREETS[street]['label']}, {ref[street]} houses")
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([f"{y}\n{int(h)} h" for y, h in zip(order, wx_stats.set_index("year").loc[order, "hours_below_minus10"])],
                       fontsize=7)
    ax.set_xlabel("weather year, coldest first (hours per year below −10 °C at SMHI 96190)")
    ax.set_ylabel("life used (days/yr)")
    ax.set_ylim(bottom=0)
    ax.set_title("The same stations in 13 real weather years", fontsize=9, loc="left")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "winters.png")
    plt.close(fig)


def main() -> int:
    FIG.mkdir(parents=True, exist_ok=True)
    runs = pd.read_csv(run_study.RESULTS / "runs.csv")
    first = first_houses(runs)
    first.to_csv(run_study.RESULTS / "first_limits.csv", index=False, lineterminator="\n")
    summary = (first.groupby(["street", "criterion"])["houses"]
               .agg(min="min", median="median", max="max", years_reached="count").reset_index())
    summary.to_csv(run_study.RESULTS / "summary_first_limits.csv", index=False, lineterminator="\n")
    wx_stats = pd.DataFrame([{"year": y, "hours_below_minus10": int((run_study.weather(y)["temp_c"] < -10).sum()),
                              "min_temp_c": float(run_study.weather(y)["temp_c"].min()),
                              "mean_temp_c": float(run_study.weather(y)["temp_c"].mean())} for y in run_study.YEARS])
    wx_stats.to_csv(run_study.RESULTS / "winters.csv", index=False, lineterminator="\n")
    fig_first_limits(first)
    fig_life(runs)
    fig_paper_vs_real(runs)
    sens_file = run_study.RESULTS / "sensitivity.csv"
    if sens_file.exists():
        sens = pd.read_csv(sens_file)
        fig_sensitivity(sens)
        ref = sens.groupby("street")["houses"].first().to_dict()
        fig_winters(runs, wx_stats, ref)
    lim_file = run_study.RESULTS / "sensitivity_limits.csv"
    if lim_file.exists():
        import sensitivity
        from hotspotlv.thermal import ThermalParams
        names = [n for n, _, _ in sensitivity.variations(ThermalParams())]
        by_var = limits_by_variation(pd.read_csv(lim_file), first)
        by_var.to_csv(run_study.RESULTS / "summary_sensitivity_limits.csv", index=False, lineterminator="\n")
        fig_sensitivity_limits(by_var, names)
    print(summary.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
