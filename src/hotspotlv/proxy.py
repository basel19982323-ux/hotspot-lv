"""
Fast mode: a fitted stand-in for the 8 760 power flows — plus exact re-runs
where the verdict is decided.

Why: one full year of power flows takes ~100 s. A hosting-capacity sweep
(20 house counts) × Monte Carlo (10 seeds) × 4 fixes would take a day. But
the transformer only sees the SUM of the houses, and for a fixed feeder the
relation "sum of house power → transformer kVA, losses and worst voltage" is
smooth. So we run a few hundred real power flows spanning the load range,
fit small curves, and evaluate every hour from the curves:

    P_net = Σ consumption − Σ generation   (kW)      Q = Σ consumption · tan φ
         │
         ▼  fitted from ~300 real power flows on THIS feeder
    cable losses ≈ a·P_net² + b·|P_net|                      → p_kw (LV side, like run_year)
    total losses ≈ a'·P_net² + b'·|P_net|, q_loss ≈ f·(P²+Q²) → S_hv = √((P+Δp)² + (Q+Δq)²)
    vmin    ≈ 1 + c₁·P_net + c₂·P_feeder_max     (transformer drop + the heaviest feeder's drop)
    vmax    ≈ 1 + d₁·P_net + d₂·P_feeder_min     (export side: the most-exporting feeder)
    cable % ≈ max( e·max_f |P_feeder_f| ,        (first segment of the heaviest feeder)
                   max_j S_house_j / S_service )  (a single house on its service cable — exact)

The feeder sums matter: the worst cable and the lowest voltage sit on the
heaviest of the feeders, not on the total. The proxy reads which house hangs
on which feeder from the pandapower net.

Honesty: one curve per quantity reproduces transformer loading to ~0.5 % of
nameplate and voltages to ~0.01 pu up to ≈ 130 % loading on a 45-house
feeder; beyond that (and for heavily loaded 100+ house feeders) the errors
grow. Therefore `refine_near_limits` re-runs the EXACT power flow for every
hour that sits near a limit the verdict uses (loading above 90 % of nameplate,
voltage near the planning band, cable near its rating) and replaces those
rows. The fitted curves then only ever decide hours that are far from any
limit. `fit_error` and the number of refined hours are reported with every
result so a table can say "fast mode, N hours exact".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandapower as pp
import pandas as pd

from hotspotlv.feeder import POWER_FACTOR, set_house_loads
from hotspotlv.timeseries import YearResult

N_SAMPLES_DEFAULT = 300
N_SAMPLES_MIN = 10
# hours that get an exact power flow: within these bands of a limit the verdict cares about
REFINE_LOADING_PCT = 90.0
REFINE_VMIN_PU, REFINE_VMAX_PU = 0.94, 1.04
REFINE_CABLE_PCT = 90.0


@dataclass
class FeederProxy:
    rated_kva: float
    feeder_of_house: np.ndarray  # feeder index per house (position), from the net's bus names
    cable_loss_coef: np.ndarray  # [a, b]   cable losses_kw = a·P² + b·|P|      (→ p_kw, LV side)
    loss_coef: np.ndarray        # [a', b'] total losses (cables + trafo) for S
    qloss_coef: float            # f        q_loss_kvar = f·(P² + Q²)
    vmin_coef: np.ndarray        # [c1, c2] vmin = 1 + c1·P + c2·P_feeder_max
    vmax_coef: np.ndarray        # [d1, d2] vmax = 1 + d1·P + d2·P_feeder_min
    cable_coef: float            # main-cable % = e·max_f |P_feeder_f|
    service_kva: float           # rating of one service cable, kVA (√3 · 0.4 kV · I_max)
    fit_error: dict              # max abs errors on the fitting sample
    n_samples: int

    def evaluate(self, cons_kw: pd.DataFrame, gen_kw: pd.DataFrame, pf: float = POWER_FACTOR) -> YearResult:
        _check_inputs(cons_kw, gen_kw, len(self.feeder_of_house))
        cons = cons_kw.to_numpy(dtype=float)
        gen = gen_kw.to_numpy(dtype=float)
        p_cons, p_gen = cons.sum(axis=1), gen.sum(axis=1)
        p = p_cons - p_gen
        q = p_cons * math.tan(math.acos(pf))
        p_f = _feeder_sums(cons - gen, self.feeder_of_house)
        pf_max, pf_min, pf_abs = p_f.max(axis=1), p_f.min(axis=1), np.abs(p_f).max(axis=1)
        cable_losses = self.cable_loss_coef[0] * p**2 + self.cable_loss_coef[1] * np.abs(p)
        losses = self.loss_coef[0] * p**2 + self.loss_coef[1] * np.abs(p)
        q_loss = self.qloss_coef * (p**2 + q**2)
        s = np.hypot(p + losses, q + q_loss)
        hourly = pd.DataFrame({
            "s_kva": s,
            "loading_pct": s / self.rated_kva * 100,
            "p_kw": p + cable_losses,
            "vmin_pu": 1 + self.vmin_coef[0] * p + self.vmin_coef[1] * pf_max,
            "vmax_pu": 1 + self.vmax_coef[0] * p + self.vmax_coef[1] * pf_min,
            "losses_kw": losses,
            "line_max_pct": np.maximum(self.cable_coef * pf_abs, _service_pct(cons, gen, self.service_kva, pf)),
        }, index=cons_kw.index)
        return YearResult(hourly, self.rated_kva)


def _check_inputs(cons_kw: pd.DataFrame, gen_kw: pd.DataFrame, n_houses: int) -> None:
    if cons_kw.shape[1] != n_houses:
        raise ValueError(f"cons_kw has {cons_kw.shape[1]} houses, expected {n_houses}")
    if gen_kw.shape != cons_kw.shape or not gen_kw.index.equals(cons_kw.index):
        raise ValueError("gen_kw must have the same shape and index as cons_kw")
    if not (np.isfinite(cons_kw.to_numpy()).all() and np.isfinite(gen_kw.to_numpy()).all()):
        raise ValueError("cons_kw / gen_kw contain NaN or inf")


def _feeder_of_house(net: pp.pandapowerNet) -> np.ndarray:
    """Feeder index of each load, read from the house bus names 'House 7 (F2)'."""
    names = net.bus.loc[net.load.bus, "name"].to_numpy()
    try:
        return np.array([int(n.split("(F")[1].rstrip(")")) - 1 for n in names])
    except (IndexError, ValueError) as e:
        raise ValueError("net was not built by feeder.build_feeder (house bus names must end in '(F<n>)')") from e


def _feeder_sums(p_house: np.ndarray, feeder_of_house: np.ndarray) -> np.ndarray:
    """hours × feeders net kW."""
    n_f = feeder_of_house.max() + 1
    return np.stack([p_house[:, feeder_of_house == f].sum(axis=1) for f in range(n_f)], axis=1)


def _service_pct(cons: np.ndarray, gen: np.ndarray, service_kva: float, pf: float) -> np.ndarray:
    """Loading % of the most-loaded service cable per hour (one house's own kVA over the cable rating)."""
    s_house = np.hypot(cons - gen, cons * math.tan(math.acos(pf)))
    return s_house.max(axis=1) / service_kva * 100


def _stratified_rows(p_net: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Pick n hours spread evenly over the range of net power (plus the extremes)."""
    order = np.argsort(p_net)
    picks = np.unique(np.linspace(0, len(order) - 1, n).round().astype(int))
    rows = order[picks]
    extra = rng.choice(len(p_net), size=max(0, n - len(rows)), replace=False)
    return np.unique(np.concatenate([rows, extra]))


def _exact_rows(net: pp.pandapowerNet, cons: np.ndarray, gen: np.ndarray, rows, pf: float) -> dict[str, np.ndarray]:
    """Run the real power flow for the given rows; return the YearResult columns plus fitting extras."""
    rated_kva = float(net.trafo.sn_mva.iloc[0] * 1000)
    load_buses = net.load.bus.to_numpy()
    main_mask = ~net.line.name.str.startswith("service").to_numpy()
    tan_phi = math.tan(math.acos(pf))
    cols = {k: [] for k in ("s_kva", "loading_pct", "p_kw", "vmin_pu", "vmax_pu", "losses_kw", "line_max_pct",
                            "cable_losses_kw", "main_cable_pct", "q_loss_kvar")}
    init = "flat"
    for i in rows:
        set_house_loads(net, cons[i], gen[i], pf)
        try:
            pp.runpp(net, numba=False, init=init)
        except pp.LoadflowNotConverged:
            pp.runpp(net, numba=False, init="flat")
        init = "results"
        tr = net.res_trafo.iloc[0]
        house_v = net.res_bus.vm_pu.to_numpy()[load_buses]
        line_pct = net.res_line.loading_percent.to_numpy()
        cable_loss = float(net.res_line.pl_mw.sum() * 1000)
        cols["s_kva"].append(tr.loading_percent / 100 * rated_kva)
        cols["loading_pct"].append(tr.loading_percent)
        cols["p_kw"].append(-tr.p_lv_mw * 1000)
        cols["vmin_pu"].append(house_v.min())
        cols["vmax_pu"].append(house_v.max())
        cols["losses_kw"].append(cable_loss + tr.pl_mw * 1000)
        cols["line_max_pct"].append(line_pct.max())
        cols["cable_losses_kw"].append(cable_loss)
        cols["main_cable_pct"].append(line_pct[main_mask].max())
        cols["q_loss_kvar"].append(tr.q_hv_mvar * 1000 - cons[i].sum() * tan_phi)
    return {k: np.asarray(v) for k, v in cols.items()}


def fit_proxy(net: pp.pandapowerNet, cons_kw: pd.DataFrame, gen_kw: pd.DataFrame,
              n_samples: int = N_SAMPLES_DEFAULT, seed: int = 0, pf: float = POWER_FACTOR) -> FeederProxy:
    """Run `n_samples` real power flows on hours of this scenario and fit the proxy curves."""
    _check_inputs(cons_kw, gen_kw, len(net.load))
    n_rows = len(cons_kw)
    if n_samples < N_SAMPLES_MIN:
        raise ValueError(f"n_samples must be >= {N_SAMPLES_MIN}")
    n_samples = min(n_samples, n_rows)
    rated_kva = float(net.trafo.sn_mva.iloc[0] * 1000)
    feeder_of_house = _feeder_of_house(net)
    service = net.line[net.line.name.str.startswith("service")]
    service_kva = float(math.sqrt(3) * 0.4 * service.max_i_ka.iloc[0] * 1000)
    cons = cons_kw.to_numpy(dtype=float)
    gen = gen_kw.to_numpy(dtype=float)
    p_net = cons.sum(axis=1) - gen.sum(axis=1)
    if np.ptp(p_net) < 1.0:
        raise ValueError("net power hardly varies; a proxy cannot be fitted on this input")
    p_f = _feeder_sums(cons - gen, feeder_of_house)
    rows = _stratified_rows(p_net, n_samples, np.random.default_rng(seed))
    ex = _exact_rows(net, cons, gen, rows, pf)

    P = p_net[rows]
    Q = cons[rows].sum(axis=1) * math.tan(math.acos(pf))
    pf_max, pf_min, pf_abs = p_f[rows].max(axis=1), p_f[rows].min(axis=1), np.abs(p_f[rows]).max(axis=1)
    # least squares, no intercept where physics says zero at P = 0
    cable_loss_coef = np.linalg.lstsq(np.c_[P**2, np.abs(P)], ex["cable_losses_kw"], rcond=None)[0]
    loss_coef = np.linalg.lstsq(np.c_[P**2, np.abs(P)], ex["losses_kw"], rcond=None)[0]
    qloss_coef = float(np.linalg.lstsq(np.c_[P**2 + Q**2], ex["q_loss_kvar"], rcond=None)[0][0])
    vmin_coef = np.linalg.lstsq(np.c_[P, pf_max], ex["vmin_pu"] - 1, rcond=None)[0]
    vmax_coef = np.linalg.lstsq(np.c_[P, pf_min], ex["vmax_pu"] - 1, rcond=None)[0]
    cable_coef = float(np.linalg.lstsq(np.c_[pf_abs], ex["main_cable_pct"], rcond=None)[0][0])

    s_fit = np.hypot(P + loss_coef[0] * P**2 + loss_coef[1] * np.abs(P), Q + qloss_coef * (P**2 + Q**2))
    fit_error = {
        "s_kva_max_abs": float(np.abs(s_fit - ex["s_kva"]).max()),
        "loading_pct_max_abs": float((np.abs(s_fit - ex["s_kva"]) / rated_kva * 100).max()),
        "vmin_max_abs": float(np.abs(1 + vmin_coef[0] * P + vmin_coef[1] * pf_max - ex["vmin_pu"]).max()),
        "vmax_max_abs": float(np.abs(1 + vmax_coef[0] * P + vmax_coef[1] * pf_min - ex["vmax_pu"]).max()),
        "cable_pct_max_abs": float(np.abs(cable_coef * pf_abs - ex["main_cable_pct"]).max()),
    }
    return FeederProxy(rated_kva, feeder_of_house, cable_loss_coef, loss_coef, qloss_coef, vmin_coef, vmax_coef,
                       cable_coef, service_kva, fit_error, len(rows))


def refine_mask(year: YearResult, fit_error: dict | None = None) -> np.ndarray:
    """Hours that sit near a verdict limit. The bands are widened by twice the proxy's own
    fit error, so an hour the exact power flow would put near a limit is never missed."""
    fe = fit_error or {}
    v_margin = max(0.0, 2 * fe.get("vmin_max_abs", 0.0) - (0.94 - 0.92))
    vmax_margin = max(0.0, 2 * fe.get("vmax_max_abs", 0.0) - (1.05 - 1.04))
    load_margin = max(0.0, 2 * fe.get("loading_pct_max_abs", 0.0) - 10.0)
    cable_margin = max(0.0, 2 * fe.get("cable_pct_max_abs", 0.0) - 10.0)
    h = year.hourly
    return ((h["loading_pct"] > REFINE_LOADING_PCT - load_margin) | (h["vmin_pu"] < REFINE_VMIN_PU + v_margin)
            | (h["vmax_pu"] > REFINE_VMAX_PU - vmax_margin) | (h["line_max_pct"] > REFINE_CABLE_PCT - cable_margin)).to_numpy()


def refine_near_limits(net: pp.pandapowerNet, year: YearResult, cons_kw: pd.DataFrame, gen_kw: pd.DataFrame,
                       pf: float = POWER_FACTOR, fit_error: dict | None = None) -> tuple[YearResult, int]:
    """Replace every proxy row that sits near a verdict limit with the exact power flow.
    Returns the refined YearResult and the number of hours re-run."""
    _check_inputs(cons_kw, gen_kw, len(net.load))
    if not cons_kw.index.equals(year.hourly.index):
        raise ValueError("cons_kw/gen_kw must share the YearResult's index")
    h = year.hourly
    rows = np.flatnonzero(refine_mask(year, fit_error))
    if len(rows) == 0:
        return year, 0
    ex = _exact_rows(net, cons_kw.to_numpy(dtype=float), gen_kw.to_numpy(dtype=float), rows, pf)
    new = h.copy()
    for col in ("s_kva", "loading_pct", "p_kw", "vmin_pu", "vmax_pu", "losses_kw", "line_max_pct"):
        new.iloc[rows, new.columns.get_loc(col)] = ex[col]
    return YearResult(new, year.rated_kva), int(len(rows))
