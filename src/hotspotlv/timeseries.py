"""
Run the power flow for every hour of the year.

    hour 0     hour 1     hour 2        ...      hour 8759
    ┌────┐     ┌────┐     ┌────┐                 ┌────┐
    │ PF │ ──► │ PF │ ──► │ PF │ ──►   ...   ──► │ PF │
    └────┘     └────┘     └────┘                 └────┘
       │          │          │                      │
       ▼          ▼          ▼                      ▼
    S_kVA, loading %, Vmin, losses   → one row per hour → YearResult

Each hour is an independent snapshot (a power flow has no memory), so we just
loop: put this hour's 45 house loads into the net, solve, read the results.
The transformer's apparent power S (kVA) per hour is the input for the
thermal model later; loading % = S / rated kVA.

Warm start: each solve starts from the previous hour's voltages
(`init="results"`), which roughly halves the time.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandapower as pp
import pandas as pd

from hotspotlv.feeder import POWER_FACTOR, set_house_loads

NAMEPLATE_RULE_PCT = 80.0     # the Swedish DSO rule of thumb (Energiforsk 2018:552)
VMIN_LIMIT_PU, VMAX_LIMIT_PU = 0.90, 1.10   # EN 50160 ±10 %


@dataclass
class YearResult:
    hourly: pd.DataFrame          # index = hours; s_kva, loading_pct, p_kw, vmin_pu, vmax_pu, losses_kw, line_max_pct
    rated_kva: float

    @property
    def hours_above(self) -> dict[str, int]:
        lp = self.hourly["loading_pct"]
        return {
            "80 % rule": int((lp > NAMEPLATE_RULE_PCT).sum()),
            "100 % nameplate": int((lp > 100.0).sum()),
            "120 %": int((lp > 120.0).sum()),
        }

    @property
    def voltage_violations(self) -> dict[str, int]:
        return {
            "below 0.90 pu": int((self.hourly["vmin_pu"] < VMIN_LIMIT_PU).sum()),
            "above 1.10 pu": int((self.hourly["vmax_pu"] > VMAX_LIMIT_PU).sum()),
        }

    def duration_curve(self) -> np.ndarray:
        """Loading % sorted high → low: 'for how many hours is loading above x'."""
        return np.sort(self.hourly["loading_pct"].to_numpy())[::-1]

    def summary(self) -> dict:
        h = self.hourly
        return {
            "peak_kva": float(h["s_kva"].max()),
            "peak_loading_pct": float(h["loading_pct"].max()),
            "peak_time": h["s_kva"].idxmax(),
            "min_p_kw": float(h["p_kw"].min()),
            "energy_mwh": float(h["p_kw"].clip(lower=0).sum() / 1000),
            "export_mwh": float(-h["p_kw"].clip(upper=0).sum() / 1000),
            "losses_mwh": float(h["losses_kw"].sum() / 1000),
            "vmin_pu": float(h["vmin_pu"].min()),
            "vmax_pu": float(h["vmax_pu"].max()),
            **self.hours_above, **self.voltage_violations,
        }


def run_year(net: pp.pandapowerNet, cons_kw: pd.DataFrame, gen_kw: pd.DataFrame | None = None,
             pf: float = POWER_FACTOR) -> YearResult:
    """Power flow for every row (hour) of `cons_kw` (consumption per house) and
    `gen_kw` (PV generation per house, positive; None = no PV).
    Returns hourly transformer and voltage results."""
    if cons_kw.shape[1] != len(net.load):
        raise ValueError(f"cons_kw has {cons_kw.shape[1]} houses, the net has {len(net.load)} loads")
    if gen_kw is None:
        gen_kw = pd.DataFrame(0.0, index=cons_kw.index, columns=cons_kw.columns)
    if gen_kw.shape != cons_kw.shape or not gen_kw.index.equals(cons_kw.index):
        raise ValueError("gen_kw must have the same shape and index as cons_kw")
    rated_kva = float(net.trafo.sn_mva.iloc[0] * 1000)
    load_buses = net.load.bus.to_numpy()
    cons = cons_kw.to_numpy(dtype=float)
    gen = gen_kw.to_numpy(dtype=float)
    out = np.empty((len(cons_kw), 7))

    init = "flat"
    for i in range(len(cons)):
        set_house_loads(net, cons[i], gen[i], pf)
        try:
            pp.runpp(net, numba=False, init=init)
        except pp.LoadflowNotConverged:
            pp.runpp(net, numba=False, init="flat")       # retry cold; raises again if truly infeasible
        init = "results"
        tr = net.res_trafo.iloc[0]
        # pandapower's loading % = max(S_hv, S_lv) / S_rated; the HV side carries the
        # transformer's own losses too, so it is ~0.5 % above S_lv. We keep that
        # (conservative) definition and derive S from it, so both columns agree.
        s_kva = float(tr.loading_percent / 100 * rated_kva)
        house_v = net.res_bus.vm_pu.to_numpy()[load_buses]
        out[i] = (
            s_kva,
            tr.loading_percent,
            -tr.p_lv_mw * 1000,                       # + = MV→LV (houses consume), − = export
            house_v.min(),
            house_v.max(),
            (net.res_line.pl_mw.sum() + tr.pl_mw) * 1000,
            net.res_line.loading_percent.max(),
        )

    hourly = pd.DataFrame(out, index=cons_kw.index,
                          columns=["s_kva", "loading_pct", "p_kw", "vmin_pu", "vmax_pu", "losses_kw", "line_max_pct"])
    return YearResult(hourly, rated_kva)


if __name__ == "__main__":
    import time
    from hotspotlv.feeder import build_feeder
    from hotspotlv.neighbourhood import Scenario, build_neighbourhood
    from hotspotlv.weather import get_weather

    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario("today", n_houses=45, ev_share=0.25, pv_share=0.20, seed=0), wx)
    net = build_feeder(n_houses=45, trafo_kva=500)
    t0 = time.perf_counter()
    yr = run_year(net, nb.cons_kw, nb.gen_kw)
    dt = time.perf_counter() - t0
    print(f"8760 power flows in {dt:.1f} s ({dt / 8760 * 1000:.1f} ms each)")
    for k, v in yr.summary().items():
        print(f"  {k:18s} {v}")
    dc = yr.duration_curve()
    print("  duration curve: top 10 h ≥ {:.0f} %, top 100 h ≥ {:.0f} %, top 1000 h ≥ {:.0f} %".format(dc[9], dc[99], dc[999]))
