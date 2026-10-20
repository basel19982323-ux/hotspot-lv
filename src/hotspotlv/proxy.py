"""
Fast mode: a fitted stand-in for the 8 760 power flows.

Why: one full year of power flows takes ~100 s. A hosting-capacity sweep
(20 house counts) × Monte Carlo (10 seeds) × 4 fixes would take a day. But
the transformer only sees the SUM of the houses, and for a fixed feeder the
relation "sum of house power → transformer kVA, losses and worst voltage" is
smooth. So we run a few hundred real power flows spanning the load range,
fit three small curves, and evaluate every hour from the curves.

    P_net = Σ consumption − Σ generation   (kW)      Q = Σ consumption · tan φ
         │
         ▼  fitted from ~300 real power flows on THIS feeder
    losses  ≈ a·P_net² + b·|P_net|               (active, cables + transformer)
    q_loss  ≈ f·(P_net² + Q²)                    (reactive, mostly the transformer's uk = 6 %)
                                                 → S_hv = √((P_net + losses)² + (Q + q_loss)²)
    vmin    ≈ 1 + c₁·P_net + c₂·P_feeder_max     (transformer drop + the heaviest feeder's drop)
    vmax    ≈ 1 + d₁·P_net + d₂·P_feeder_min     (export side: the most-exporting feeder)
    cable % ≈ max( e·max_f |P_feeder_f| ,        (first segment of the heaviest feeder)
                   max_j S_house_j / S_service )  (a single house on its service cable — exact)

The feeder sums matter: the worst cable and the lowest voltage sit on the
heaviest of the three feeders, not on the total. The proxy reads which house
hangs on which feeder from the pandapower net.

Honesty: the proxy does not know WHICH houses carry the load (it assumes the
scenario's own spatial pattern, because it is fitted on hours of that very
scenario). Transformer loading is reproduced to within ~0.5 % of nameplate; voltages to
about 0.01 p.u. (good enough for screening against a ±10 % limit, not for a
voltage study). The validation test in tests/test_proxy.py holds the
proxy to those numbers against a full year, and `fast=True` results are
labelled as such wherever they are shown.
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


@dataclass
class FeederProxy:
    rated_kva: float
    feeder_of_house: np.ndarray  # feeder index per house (position), from the net's bus names
    loss_coef: np.ndarray      # [a, b]   losses_kw = a·P² + b·|P|
    qloss_coef: float          # f        q_loss_kvar = f·(P² + Q²)
    vmin_coef: np.ndarray      # [c1, c2] vmin = 1 + c1·P + c2·P_feeder_max
    vmax_coef: np.ndarray      # [d1, d2] vmax = 1 + d1·P + d2·P_feeder_min
    cable_coef: float          # main-cable % = e·max_f |P_feeder_f|
    service_kva: float         # rating of one service cable, kVA (√3 · 0.4 kV · I_max)
    fit_error: dict            # max abs errors on the fitting sample
    n_samples: int

    def evaluate(self, cons_kw: pd.DataFrame, gen_kw: pd.DataFrame, pf: float = POWER_FACTOR) -> YearResult:
        p_cons = cons_kw.sum(axis=1).to_numpy(dtype=float)
        p_gen = gen_kw.sum(axis=1).to_numpy(dtype=float)
        p = p_cons - p_gen
        q = p_cons * math.tan(math.acos(pf))
        p_f = _feeder_sums(cons_kw.to_numpy(dtype=float) - gen_kw.to_numpy(dtype=float), self.feeder_of_house)
        pf_max, pf_min, pf_abs = p_f.max(axis=1), p_f.min(axis=1), np.abs(p_f).max(axis=1)
        service_pct = _service_pct(cons_kw.to_numpy(dtype=float), gen_kw.to_numpy(dtype=float), self.service_kva, pf)
        losses = self.loss_coef[0] * p**2 + self.loss_coef[1] * np.abs(p)
        q_loss = self.qloss_coef * (p**2 + q**2)
        s = np.hypot(p + losses, q + q_loss)
        hourly = pd.DataFrame({
            "s_kva": s,
            "loading_pct": s / self.rated_kva * 100,
            "p_kw": p + losses,
            "vmin_pu": 1 + self.vmin_coef[0] * p + self.vmin_coef[1] * pf_max,
            "vmax_pu": 1 + self.vmax_coef[0] * p + self.vmax_coef[1] * pf_min,
            "losses_kw": losses,
            "line_max_pct": np.maximum(self.cable_coef * pf_abs, service_pct),
        }, index=cons_kw.index)
        return YearResult(hourly, self.rated_kva)


def _feeder_of_house(net: pp.pandapowerNet) -> np.ndarray:
    """Feeder index of each load, read from the house bus names 'House 7 (F2)'."""
    names = net.bus.loc[net.load.bus, "name"].to_numpy()
    return np.array([int(n.split("(F")[1].rstrip(")")) - 1 for n in names])


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


def fit_proxy(net: pp.pandapowerNet, cons_kw: pd.DataFrame, gen_kw: pd.DataFrame,
              n_samples: int = N_SAMPLES_DEFAULT, seed: int = 0, pf: float = POWER_FACTOR) -> FeederProxy:
    """Run `n_samples` real power flows on hours of this scenario and fit the proxy curves."""
    if cons_kw.shape[1] != len(net.load):
        raise ValueError(f"cons_kw has {cons_kw.shape[1]} houses, the net has {len(net.load)} loads")
    rated_kva = float(net.trafo.sn_mva.iloc[0] * 1000)
    load_buses = net.load.bus.to_numpy()
    feeder_of_house = _feeder_of_house(net)
    service = net.line[net.line.name.str.startswith("service")]
    service_kva = float(math.sqrt(3) * 0.4 * service.max_i_ka.iloc[0] * 1000)
    main_mask = ~net.line.name.str.startswith("service").to_numpy()
    cons = cons_kw.to_numpy(dtype=float)
    gen = gen_kw.to_numpy(dtype=float)
    p_net = cons.sum(axis=1) - gen.sum(axis=1)
    p_f = _feeder_sums(cons - gen, feeder_of_house)
    rows = _stratified_rows(p_net, n_samples, np.random.default_rng(seed))

    P, losses, qloss, vmin, vmax, cable, s_true = [], [], [], [], [], [], []
    tan_phi = math.tan(math.acos(pf))
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
        P.append(p_net[i])
        losses.append((net.res_line.pl_mw.sum() + tr.pl_mw) * 1000)
        qloss.append(tr.q_hv_mvar * 1000 - cons[i].sum() * tan_phi)   # reactive drawn from MV beyond the houses' Q
        vmin.append(house_v.min())
        vmax.append(house_v.max())
        cable.append(net.res_line.loading_percent.to_numpy()[main_mask].max())   # fit the MAIN cables only
        s_true.append(tr.loading_percent / 100 * rated_kva)
    P, losses, qloss, vmin, vmax, cable, s_true = map(np.asarray, (P, losses, qloss, vmin, vmax, cable, s_true))
    Q = cons[rows].sum(axis=1) * tan_phi
    pf_max, pf_min, pf_abs = p_f[rows].max(axis=1), p_f[rows].min(axis=1), np.abs(p_f[rows]).max(axis=1)

    # least squares, no intercept where physics says zero at P = 0
    loss_coef = np.linalg.lstsq(np.c_[P**2, np.abs(P)], losses, rcond=None)[0]
    vmin_coef = np.linalg.lstsq(np.c_[P, pf_max], vmin - 1, rcond=None)[0]
    vmax_coef = np.linalg.lstsq(np.c_[P, pf_min], vmax - 1, rcond=None)[0]
    cable_coef = float(np.linalg.lstsq(np.c_[pf_abs], cable, rcond=None)[0][0])
    qloss_coef = float(np.linalg.lstsq(np.c_[P**2 + Q**2], qloss, rcond=None)[0][0])

    s_fit = np.hypot(P + loss_coef[0] * P**2 + loss_coef[1] * np.abs(P), Q + qloss_coef * (P**2 + Q**2))
    fit_error = {
        "s_kva_max_abs": float(np.abs(s_fit - s_true).max()),
        "loading_pct_max_abs": float((np.abs(s_fit - s_true) / rated_kva * 100).max()),
        "vmin_max_abs": float(np.abs(1 + vmin_coef[0] * P + vmin_coef[1] * pf_max - vmin).max()),
        "vmax_max_abs": float(np.abs(1 + vmax_coef[0] * P + vmax_coef[1] * pf_min - vmax).max()),
        "cable_pct_max_abs": float(np.abs(cable_coef * pf_abs - cable).max()),
    }
    return FeederProxy(rated_kva, feeder_of_house, loss_coef, qloss_coef, vmin_coef, vmax_coef, cable_coef,
                       service_kva, fit_error, len(rows))


if __name__ == "__main__":
    import time
    from hotspotlv.feeder import build_feeder
    from hotspotlv.neighbourhood import Scenario, build_neighbourhood
    from hotspotlv.timeseries import run_year
    from hotspotlv.weather import get_weather

    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario("2030", ev_share=0.6, pv_share=0.4, n_houses=60), wx)
    net = build_feeder(n_houses=60)
    t0 = time.perf_counter()
    proxy = fit_proxy(net, nb.cons_kw, nb.gen_kw)
    t_fit = time.perf_counter() - t0
    fast = proxy.evaluate(nb.cons_kw, nb.gen_kw)
    print(f"proxy fitted on {proxy.n_samples} power flows in {t_fit:.1f} s; fit errors {proxy.fit_error}")
    t0 = time.perf_counter()
    full = run_year(build_feeder(n_houses=60), nb.cons_kw, nb.gen_kw)
    print(f"full year in {time.perf_counter() - t0:.0f} s")
    d = fast.hourly - full.hourly
    print("max |fast − full|:", {c: round(float(d[c].abs().max()), 4) for c in d.columns})
    print("hours > 80 %: fast", fast.hours_above, "full", full.hours_above)
