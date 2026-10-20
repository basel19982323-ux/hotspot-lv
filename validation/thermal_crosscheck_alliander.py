"""
Cross-check of hotspotlv.thermal (IEC 60076-7 difference equations, explicit
Euler, hourly K and ambient held constant) against the independent open
implementation alliander-opensource/transformer-thermal-model 0.6.0
(exact-exponential update of the same equations).

The two codes cannot live in one interpreter: the library pins pandas>=3,
pandapower (pulled in by hotspotlv.feeder) pins pandas<3. So this script has
three entry points and the driver runs the first two in separate interpreters:

    python thermal_crosscheck.py ours  CASE DT_MIN OUT.csv     (Basel's Python, repo on sys.path)
    python thermal_crosscheck.py lib   CASE OUT.csv            (venv with transformer-thermal-model)
    python thermal_crosscheck.py drive OURS_PY LIB_PY WORKDIR  (runs everything, prints the table)

Cases
    a : 48 h, K = 0 for 24 h then 1.0 for 24 h, ambient 20 C
    b : 72 h random, seed 0, K ~ U(0.2, 1.3), ambient ~ U(-20, 25) (same draw as
        tests/test_thermal.py::test_result_independent_of_step_size)

Alignment: the library evaluates step i with load[i], ambient[i] over
(t[i-1], t[i]]. Hour h in our model covers (t0 + h h, t0 + (h+1) h]. So the
library gets a 2-min grid where the value at t_j (j >= 1) is the hourly value
of the hour containing t_j - 1 min, i.e. K[(j-1)//30], and its sample at
t_{30(h+1)} is compared with our end-of-hour-h sample. Both start from the
steady state of (K[0], ambient[0]).

Loss of life: ours = sum over sub-steps of V(theta_h) * dt (right-point rule);
library days_aged = sum_i V[i] * (t[i]-t[i-1]) which is the same rule on the
same 2-min grid, so any difference comes from the temperatures only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# identical constants on both sides
P = dict(x=0.8, y=1.6, k11=1.0, k21=1, k22=2, tau_o=180.0, tau_w=4.0,
         d_theta_or=55.0, d_theta_hr=23.0, p_load_w=3900.0, p_noload_w=459.0)
SUB_MIN = 2
SUB_PER_H = 60 // SUB_MIN


YEAR_CSV = Path(__file__).with_name("year_inputs.csv")   # written by year_inputs.py


def make_case(case: str) -> tuple[pd.Series, pd.Series]:
    if case == "year":
        df = pd.read_csv(YEAR_CSV, index_col=0, parse_dates=True)
        return df["K"], df["theta_a"]
    idx = pd.date_range("2023-01-01", periods=48 if case == "a" else 72, freq="h", tz="UTC")
    if case == "a":
        K = pd.Series([0.0] * 24 + [1.0] * 24, index=idx)
        ta = pd.Series(20.0, index=idx)
    elif case == "b":
        rng = np.random.default_rng(0)
        K = pd.Series(rng.uniform(0.2, 1.3, 72), index=idx)
        ta = pd.Series(rng.uniform(-20, 25, 72), index=idx)
    else:
        raise SystemExit(f"unknown case {case}")
    return K, ta


# ------------------------------------------------- IEC 60076-7:2005 Annex C example
# Transcribed from IS 2026 (Part 7):2009 (identical adoption of IEC 60076-7:2005),
# Annex C "Illustration of the differential equations solution method", Tables C.1
# (input) and C.2 (output). Dt = 3 min. Thermally upgraded paper (eq. C.13).
ANNEX_C = dict(d_theta_or=45.0, d_theta_hr=35.0, tau_o=150.0, tau_w=7.0, R=8.0, x=0.8, y=1.3,
               k11=0.5, k21=2.0, k22=2.0, dt=3.0)
ANNEX_C_THETA_A = [30.3, 29.9, 29.8, 29.5, 29.6, 29.5, 29.5, 28.9, 29.0, 28.6, 28.0, 28.7, 27.8, 28.1,
                   27.9, 27.1, 26.9, 26.7, 27.2, 26.7, 26.9, 26.5, 26.2, 26.3, 25.4, 25.6, 25.3, 24.8,
                   24.5, 24.3, 24.1, 24.3, 24.1, 23.4, 23.6, 23.8, 23.1, 23.3, 23.1, 22.3, 22.2]
ANNEX_C_K = [0.81, 0.87, 0.88, 0.86, 0.90, 0.92, 0.95, 0.96, 0.97, 1.00, 1.70, 1.70, 1.73, 1.72,
             1.69, 1.68, 1.71, 1.69, 1.67, 1.68, 1.63, 1.59, 1.53, 1.49, 1.41, 1.38, 1.32, 1.28,
             1.21, 1.19, 0.87, 0.88, 0.87, 0.86, 0.85, 0.87, 0.83, 0.86, 0.85, 0.82, 0.86]
ANNEX_C_HOT_SPOT = [90.5, 91.6, 92.7, 93.2, 94.3, 95.6, 97.2, 98.6, 100.0, 101.6, 118.6, 132.1, 143.5,
                    152.4, 158.8, 163.6, 168.2, 171.5, 173.6, 175.7, 176.1, 175.6, 173.8, 171.5, 167.8,
                    164.3, 160.1, 156.0, 151.1, 146.8, 136.9, 129.1, 122.8, 117.5, 113.1, 110.0, 106.6,
                    104.5, 102.6, 100.4, 99.3]
ANNEX_C_LOL_DAYS_END = 6.15       # Table C.2, step 40 (L = 8851 min)


def annex_c_reference() -> tuple[np.ndarray, float]:
    """Straight transcription of IEC 60076-7 eq. (C.6)-(C.14): theta_o integrated with
    theta_a inside the bracket, explicit difference steps, V for upgraded paper."""
    c = ANNEX_C
    th_a, K = np.array(ANNEX_C_THETA_A), np.array(ANNEX_C_K)
    th_o = ((1 + K[0] ** 2 * c["R"]) / (1 + c["R"])) ** c["x"] * c["d_theta_or"] + th_a[0]
    dh1 = c["k21"] * K[0] ** c["y"] * c["d_theta_hr"]
    dh2 = (c["k21"] - 1) * K[0] ** c["y"] * c["d_theta_hr"]
    out = [th_o + dh1 - dh2]
    L = 0.0
    for n in range(1, len(K)):
        th_o += c["dt"] / (c["k11"] * c["tau_o"]) * (((1 + K[n] ** 2 * c["R"]) / (1 + c["R"])) ** c["x"]
                                                      * c["d_theta_or"] - (th_o - th_a[n]))
        dh1 += c["dt"] / (c["k22"] * c["tau_w"]) * (c["k21"] * K[n] ** c["y"] * c["d_theta_hr"] - dh1)
        dh2 += c["dt"] / (c["tau_o"] / c["k22"]) * ((c["k21"] - 1) * K[n] ** c["y"] * c["d_theta_hr"] - dh2)
        th_h = th_o + dh1 - dh2
        out.append(th_h)
        L += np.exp(15000 / 383 - 15000 / (th_h + 273)) * c["dt"]
    return np.array(out), L / 1440


def run_ours_annex_c() -> tuple[np.ndarray, float]:
    """Run hotspotlv.thermal.simulate UNCHANGED on the Annex C data. simulate() hard-codes
    60 min per row and n_sub = 60/dt_min sub-steps; with dt_min = 60 there is exactly one
    sub-step per row, and scaling every time constant by 20 makes that one 60-min step
    numerically identical to one IEC 3-min step (60/(20 tau) == 3/tau)."""
    import os
    src = os.environ.get("HOTSPOTLV_SRC")
    if src:
        sys.path.insert(0, src)
    from hotspotlv.thermal import ThermalParams, simulate
    c = ANNEX_C
    s = 60.0 / c["dt"]
    p = ThermalParams(x=c["x"], y=c["y"], k11=c["k11"], k21=c["k21"], k22=c["k22"],
                      tau_o_min=c["tau_o"] * s, tau_w_min=c["tau_w"] * s,
                      d_theta_or=c["d_theta_or"], d_theta_hr=c["d_theta_hr"], R=c["R"], paper="upgraded")
    idx = pd.date_range("2000-01-01 14:00", periods=len(ANNEX_C_K), freq="h")
    r = simulate(pd.Series(ANNEX_C_K, index=idx), pd.Series(ANNEX_C_THETA_A, index=idx), p, dt_min=60.0)
    lol_days = r.hourly["V_mean"].iloc[1:].sum() * c["dt"] / 1440     # row 0 is the initial state
    return r.hourly["theta_h"].to_numpy(), float(lol_days)


def annex_c_report() -> None:
    iec = np.array(ANNEX_C_HOT_SPOT)
    ref, ref_lol = annex_c_reference()
    ours, ours_lol = run_ours_annex_c()
    print("\nIEC 60076-7:2005 Annex C example (41 steps x 3 min, varying ambient 30.3 -> 22.2 C)")
    print(f"  transcription of C.6-C.14 vs Table C.2 : max |d theta_h| = {np.abs(ref - iec).max():.2f} K, "
          f"LoL {ref_lol:.2f} vs {ANNEX_C_LOL_DAYS_END} days")
    print(f"  hotspotlv.simulate (unchanged)  vs Table C.2 : max |d theta_h| = {np.abs(ours - iec).max():.2f} K "
          f"(mean {np.abs(ours - iec).mean():.2f} K), LoL {ours_lol:.2f} vs {ANNEX_C_LOL_DAYS_END} days "
          f"({(ours_lol - ANNEX_C_LOL_DAYS_END) / ANNEX_C_LOL_DAYS_END:+.1%})")
    print(f"  hotspotlv.simulate vs transcription          : max |d theta_h| = {np.abs(ours - ref).max():.2f} K")
    print("  step  theta_a   K   IEC_C.2  transcription  hotspotlv")
    for n in (0, 1, 10, 20, 30, 40):
        print(f"  {n:4d}  {ANNEX_C_THETA_A[n]:6.1f} {ANNEX_C_K[n]:5.2f} {iec[n]:8.1f} {ref[n]:12.1f} {ours[n]:10.1f}")


# --------------------------------------------------------------------------- ours
def run_ours(case: str, dt_min: float, out: Path) -> None:
    repo_src = Path(__file__).resolve().parents[0]
    # the repo lives next to the scratchpad only by convention; allow override
    import os
    src = os.environ.get("HOTSPOTLV_SRC")
    if src:
        sys.path.insert(0, src)
    from hotspotlv.thermal import ThermalParams, simulate

    K, ta = make_case(case)
    p = ThermalParams(x=P["x"], y=P["y"], k11=P["k11"], k21=float(P["k21"]), k22=float(P["k22"]),
                      tau_o_min=P["tau_o"], tau_w_min=P["tau_w"], d_theta_or=P["d_theta_or"],
                      d_theta_hr=P["d_theta_hr"], R=P["p_load_w"] / P["p_noload_w"])
    r = simulate(K, ta, p, dt_min=dt_min)
    df = r.hourly[["theta_o", "theta_h", "theta_h_max", "V_mean"]].copy()
    df["lol_days_cum"] = r.hourly["life_used_h"] / 24
    df.to_csv(out)
    (out.with_suffix(".json")).write_text(json.dumps({"loss_of_life_days": r.loss_of_life_days, "dt_min": r.dt_min}))


# ---------------------------------------------------------------------------- lib
def run_lib(case: str, out: Path) -> None:
    from transformer_thermal_model.aging import days_aged
    from transformer_thermal_model.model import Model
    from transformer_thermal_model.schemas import InputProfile, UserTransformerSpecifications
    from transformer_thermal_model.schemas.thermal_model.initial_state import InitialLoad
    from transformer_thermal_model.transformer import DistributionTransformer, PaperInsulationType

    K, ta = make_case(case)
    n = len(K)
    t0 = K.index[0]
    grid = pd.date_range(t0, periods=n * SUB_PER_H + 1, freq=f"{SUB_MIN}min")
    j = np.arange(len(grid))
    hour_of = np.clip((j - 1) // SUB_PER_H, 0, n - 1)        # forcing over (t_{j-1}, t_j]
    load = K.to_numpy()[hour_of]
    amb = ta.to_numpy()[hour_of]

    specs = UserTransformerSpecifications(
        load_loss=P["p_load_w"], no_load_loss=P["p_noload_w"],
        nom_load_sec_side=1.0,                       # so "load" is directly K
        top_oil_temp_rise=P["d_theta_or"],
        winding_oil_gradient=P["d_theta_hr"], hot_spot_fac=1.0,   # H*gr = 23 K
        amb_temp_surcharge=0.0,                      # library default for distribution is +10 K
        time_const_oil=P["tau_o"], time_const_windings=P["tau_w"],
        oil_const_k11=P["k11"], winding_const_k21=P["k21"], winding_const_k22=P["k22"],
        oil_exp_x=P["x"], winding_exp_y=P["y"],
    )
    tr = DistributionTransformer(user_specs=specs)
    prof = InputProfile.create(datetime_index=grid.tz_localize(None).to_pydatetime(),
                               load_profile=load, ambient_temperature_profile=amb)
    res = Model(temperature_profile=prof, transformer=tr,
                initial_condition=InitialLoad(initial_load=float(K.iloc[0]))).run()
    to = res.top_oil_temp_profile.to_numpy()
    th = res.hot_spot_temp_profile.to_numpy()
    V = 2.0 ** ((th - 98.0) / 6.0)
    end = (np.arange(n) + 1) * SUB_PER_H
    th_max = np.array([th[h * SUB_PER_H + 1: (h + 1) * SUB_PER_H + 1].max() for h in range(n)])
    v_mean = np.array([V[h * SUB_PER_H + 1: (h + 1) * SUB_PER_H + 1].mean() for h in range(n)])
    df = pd.DataFrame({"theta_o": to[end], "theta_h": th[end], "theta_h_max": th_max, "V_mean": v_mean},
                      index=K.index)
    df["lol_days_cum"] = np.cumsum(v_mean) / 24
    df.to_csv(out)
    lol = days_aged(res.hot_spot_temp_profile, PaperInsulationType.NORMAL)
    out.with_suffix(".json").write_text(json.dumps({"loss_of_life_days": float(lol), "dt_min": SUB_MIN}))


# ------------------------------------------------------------------------- driver
def compare(ours: Path, lib: Path, label: str) -> dict:
    a = pd.read_csv(ours, index_col=0)
    b = pd.read_csv(lib, index_col=0)
    la = json.loads(ours.with_suffix(".json").read_text())["loss_of_life_days"]
    lb = json.loads(lib.with_suffix(".json").read_text())["loss_of_life_days"]
    d_o = (a.theta_o - b.theta_o).abs()
    d_h = (a.theta_h - b.theta_h).abs()
    d_hm = (a.theta_h_max - b.theta_h_max).abs()
    return dict(case=label, max_d_top_oil=d_o.max(), mean_d_top_oil=d_o.mean(),
                max_d_hot_spot=d_h.max(), mean_d_hot_spot=d_h.mean(), max_d_hot_spot_hourly_max=d_hm.max(),
                lol_ours=la, lol_lib=lb, lol_rel_diff=(la - lb) / lb if lb else float("nan"),
                max_theta_h_ours=a.theta_h_max.max(), max_theta_h_lib=b.theta_h_max.max())


def drive(ours_py: str, lib_py: str, work: Path, cases: tuple[str, ...]) -> None:
    work.mkdir(parents=True, exist_ok=True)
    me = str(Path(__file__).resolve())
    rows = []
    for case in cases:
        lib_out = work / f"lib_{case}.csv"
        subprocess.run([lib_py, "-I", me, "lib", case, str(lib_out)], check=True)
        for dt in ((2.0, 0.25) if case != "year" else (2.0,)):
            ours_out = work / f"ours_{case}_dt{dt}.csv"
            subprocess.run([ours_py, me, "ours", case, str(dt), str(ours_out)], check=True)
            rows.append(compare(ours_out, lib_out, f"{case} (ours dt={dt} min)"))
    df = pd.DataFrame(rows).set_index("case")
    pd.set_option("display.width", 250)
    print(df.T.to_string(float_format=lambda v: f"{v:.4g}"))
    (work / f"crosscheck_summary_{'_'.join(cases)}.csv").write_text(df.to_csv())


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "ours":
        run_ours(sys.argv[2], float(sys.argv[3]), Path(sys.argv[4]))
    elif mode == "lib":
        run_lib(sys.argv[2], Path(sys.argv[3]))
    elif mode == "drive":
        drive(sys.argv[2], sys.argv[3], Path(sys.argv[4]), tuple(sys.argv[5:]) or ("a", "b"))
    elif mode == "annexc":                      # Basel's Python, needs HOTSPOTLV_SRC
        annex_c_report()
    else:
        raise SystemExit(__doc__)
