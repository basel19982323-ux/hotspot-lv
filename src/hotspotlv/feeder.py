"""
Build the low-voltage (LV) feeder: one secondary-substation transformer feeding
~45 houses on three radial aluminium cables.

Picture (one of the three cables shown):

    MV grid 10 kV ──[ 500 kVA Dyn11 ]── LV busbar 0.4 kV
                                           │
                     main cable (Al 240) ──┼──┬──────┬──────┬── ... ──┐
                                              │      │      │         │
                                            house1 house2 house3 ... house15
                                            (each hangs on a short service cable)

Everything is a Swedish "typical", not a real street:
  * 500 kVA, 10/0.4 kV, Dyn11, uk = 6 %  (Chalmers open LV-grid generator)
  * Ecodesign Tier 2 losses (EU 548/2014 Annex I Table I.1, 2021 column):
    500 kVA 3 900 W load / 459 W no-load; 800 kVA 6 000 W / 585 W
  * main cables AXQJ 4x240 Al, service cables AXQJ 4x50 Al (own std types,
    IEC 60228 resistance, Prysmian in-ground current ratings)
  * ~30 m of main cable between house connection points, 15 m service cable
  * every house behind a 3-phase fuse (default 20 A); the neighbourhood model
    caps each house's hourly kW at the fuse rating (see neighbourhood.py)
"""

from __future__ import annotations

import math

import numpy as np
import pandapower as pp
import pandas as pd

from hotspotlv.catalogue import TRANSFORMERS, transformer

# Transformer catalogue (Ecodesign Tier-2 losses) lives in catalogue.py, shared with thermal.py.
I0_PERCENT_ASSUMED = 0.3  # no-load current, assumption (not in Ecodesign; typical 0.2–0.5 %)
VECTOR_GROUP = "Dyn11"
SHIFT_DEGREE = 330  # Dyn11 = 11 x 30 deg (pandapower convention; irrelevant to a balanced power flow)

# ---------------------------------------------------------------------------
# Cable catalogue. pandapower has no 240 mm² LV type built in, so we define our own.
#   R: IEC 60228 class 2 max DC resistance at 20 °C (Al). At 90 °C conductor
#      temperature R is ~28 % higher — not modelled, stated as a limit.
#   X: typical 4-core 0.6/1 kV Al cable (sources 0.069–0.078 Ω/km).
#   max_i: Prysmian AXQJ Pure datasheet, in ground, 20 °C soil, 2.5 K·m/W,
#      0.7 m depth, 90 °C conductor (SS 436 40 00 table 52C.2): 286 A / 116 A.
#      Chalmers Hartvigsson 2021 Table 5 is more conservative (230 A / 94 A).
# ---------------------------------------------------------------------------
CABLE_TYPES = {
    "AXQJ 4x240 Al": dict(r_ohm_per_km=0.125, x_ohm_per_km=0.080, c_nf_per_km=0, max_i_ka=0.286, type="cs"),
    "AXQJ 4x50 Al":  dict(r_ohm_per_km=0.641, x_ohm_per_km=0.085, c_nf_per_km=0, max_i_ka=0.116, type="cs"),
}
MAIN_CABLE_STD = "AXQJ 4x240 Al"
SERVICE_CABLE_STD = "AXQJ 4x50 Al"
MAIN_SEGMENT_KM = 0.030             # 30 m between house connection points
SERVICE_KM = 0.015                  # 15 m from street to meter
POWER_FACTOR = 0.95                 # household consumption, lagging; PV inverters at unity
HOUSE_FUSE_A = 20                   # 3-phase main fuse per house (16/20/25 A common in Sweden)


def fuse_kw(fuse_a: float, v_ll: float = 400.0) -> float:
    """Max 3-phase power through a fuse: √3 · U · I. 20 A → 13.9 kW."""
    return math.sqrt(3) * v_ll * fuse_a / 1000


def _q_from_p(p_kw, pf: float = POWER_FACTOR):
    """Reactive power (kvar) that goes with p_kw of consumption at power factor pf."""
    return np.asarray(p_kw, dtype=float) * math.tan(math.acos(pf))


def build_feeder(
    n_houses: int = 45,
    n_feeders: int = 3,
    trafo_kva: int = 500,
    house_p_kw: float = 1.0,
    main_cable_max_i_ka: float | None = None,
) -> pp.pandapowerNet:
    """Create the pandapower network.

    Returns a net whose house loads are all `house_p_kw` (placeholder).
    Later sessions overwrite the loads every hour of the year.
    `main_cable_max_i_ka` overrides the main-cable rating (default 0.286 kA, Prysmian
    AXQJ in ground at 20 °C soil; Chalmers' LV-grid generator uses 0.230 kA for a PVC
    cable — the cable-loading result depends on this choice, so it is a parameter).
    """
    tr = transformer(trafo_kva)               # clear error for sizes not in the catalogue
    if n_houses < 1 or n_feeders < 1 or n_feeders > n_houses:
        raise ValueError(f"need 1 <= n_feeders <= n_houses, got {n_feeders=}, {n_houses=}")
    if main_cable_max_i_ka is not None and not 0 < main_cable_max_i_ka < 2:
        raise ValueError("main_cable_max_i_ka must be in kA, e.g. 0.286")

    net = pp.create_empty_network(name=f"LV feeder {n_houses} houses / {trafo_kva} kVA")
    for cable_name, params in CABLE_TYPES.items():
        if cable_name == MAIN_CABLE_STD and main_cable_max_i_ka is not None:
            params = {**params, "max_i_ka": main_cable_max_i_ka}
        pp.create_std_type(net, params, cable_name, element="line")

    # --- medium-voltage side: the "big lake" -----------------------------------
    mv_bus = pp.create_bus(net, vn_kv=10.0, name="MV 10 kV")
    pp.create_ext_grid(net, bus=mv_bus, vm_pu=1.0, name="MV grid")

    # --- the transformer: the "pump" ---------------------------------------------
    lv_bus = pp.create_bus(net, vn_kv=0.4, name="LV busbar 0.4 kV")
    pp.create_transformer_from_parameters(
        net,
        hv_bus=mv_bus,
        lv_bus=lv_bus,
        sn_mva=trafo_kva / 1000,
        vn_hv_kv=10.0,
        vn_lv_kv=0.4,
        vk_percent=tr["uk_percent"],
        # resistive part of uk = load loss / rating (3900 W / 500 kVA = 0.78 %)
        vkr_percent=100 * tr["p_load_w"] / (trafo_kva * 1000),
        pfe_kw=tr["p_noload_w"] / 1000,
        i0_percent=I0_PERCENT_ASSUMED,
        shift_degree=SHIFT_DEGREE,
        vector_group=VECTOR_GROUP,
        name=f"{trafo_kva} kVA {VECTOR_GROUP}",
    )

    # --- radial cables, houses spread as evenly as possible ----------------------
    houses_per_feeder = [n_houses // n_feeders] * n_feeders
    for i in range(n_houses % n_feeders):  # leftover houses go to the first feeders
        houses_per_feeder[i] += 1

    house_no = 0
    for f, n_on_this in enumerate(houses_per_feeder, start=1):
        upstream = lv_bus
        for k in range(1, n_on_this + 1):
            house_no += 1
            node = pp.create_bus(net, vn_kv=0.4, name=f"F{f} node {k}")
            pp.create_line(
                net, from_bus=upstream, to_bus=node,
                length_km=MAIN_SEGMENT_KM, std_type=MAIN_CABLE_STD,
                name=f"F{f} main {k}",
            )
            house = pp.create_bus(net, vn_kv=0.4, name=f"House {house_no} (F{f})")
            pp.create_line(
                net, from_bus=node, to_bus=house,
                length_km=SERVICE_KM, std_type=SERVICE_CABLE_STD,
                name=f"service {house_no}",
            )
            pp.create_load(
                net, bus=house,
                p_mw=house_p_kw / 1000, q_mvar=float(_q_from_p(house_p_kw)) / 1000,
                name=f"House {house_no}",
            )
            upstream = node

    return net


def set_house_loads(net: pp.pandapowerNet, p_cons_kw, p_gen_kw=0.0, pf: float = POWER_FACTOR) -> None:
    """Overwrite every house load.

    p_cons_kw : consumption (base + heating + EV), scalar or one value per house
    p_gen_kw  : PV generation (positive numbers), scalar or one value per house
    Net active power = consumption − generation. Reactive power comes from the
    consumption only (pf 0.95 lagging); PV inverters are taken at unity pf.
    Values are taken BY POSITION (house 1 … N), never by label.
    """
    n = len(net.load)
    cons = np.broadcast_to(np.asarray(p_cons_kw, dtype=float), (n,))
    gen = np.broadcast_to(np.asarray(p_gen_kw, dtype=float), (n,))
    if not (np.isfinite(cons).all() and np.isfinite(gen).all()):
        raise ValueError("house loads contain NaN/inf")
    if (cons < 0).any() or (gen < 0).any():
        raise ValueError("consumption and generation must both be >= 0 (give PV as positive p_gen_kw)")
    net.load["p_mw"] = (cons - gen) / 1000
    net.load["q_mvar"] = _q_from_p(cons, pf) / 1000


def run_snapshot(net: pp.pandapowerNet) -> dict:
    """Run ONE balanced power flow and return the numbers we care about."""
    pp.runpp(net, numba=False)  # grid is tiny; numba JIT would only add noise
    house_v = net.res_bus.loc[net.load.bus, "vm_pu"]
    worst = house_v.idxmin()
    return {
        "trafo_loading_percent": float(net.res_trafo.loading_percent.iloc[0]),
        "trafo_p_lv_kw": float(-net.res_trafo.p_lv_mw.iloc[0] * 1000),
        "vmin_pu": float(house_v.min()),
        "vmin_bus": net.bus.loc[worst, "name"],
        "vmax_pu": float(house_v.max()),
        "line_loading_max_percent": float(net.res_line.loading_percent.max()),
        "losses_kw": float((net.res_line.pl_mw.sum() + net.res_trafo.pl_mw.sum()) * 1000),
    }


if __name__ == "__main__":
    net = build_feeder()
    print(net)
    for kw in (1, 3, 6, 10):
        set_house_loads(net, kw)
        r = run_snapshot(net)
        print(
            f"{kw:>3} kW/house -> trafo {r['trafo_loading_percent']:6.1f} %  "
            f"Vmin {r['vmin_pu']:.3f} p.u. at {r['vmin_bus']}  "
            f"main cable {r['line_loading_max_percent']:5.1f} %  losses {r['losses_kw']:.1f} kW"
        )
    set_house_loads(net, 1.0, p_gen_kw=6.0)
    r = run_snapshot(net)
    print(f"PV noon: 1 kW cons, 6 kW PV each -> trafo {r['trafo_p_lv_kw']:+.0f} kW, Vmax {r['vmax_pu']:.4f} p.u.")
