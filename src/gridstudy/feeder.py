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
  * Ecodesign Tier 2 losses for 500 kVA: load loss 3900 W, no-load loss 459 W
  * main cables AXQJ 4x240 Al (our own std type, IEC 60228 resistance)
  * ~30 m of main cable between house connection points
  * 15 m service cable AXQJ 4x50 Al per house
  * every house behind a 20 A / 25 A 3-phase fuse (used later for load caps)
"""

from __future__ import annotations

import math

import pandapower as pp
import pandas as pd

# ---------------------------------------------------------------------------
# Transformer catalogue (Ecodesign Tier 2 losses, EU 2019/1783, oil-immersed)
# Values that are assumptions (not in the regulation) are marked.
# ---------------------------------------------------------------------------
TRANSFORMERS = {
    #  kVA : dict(load loss W, no-load loss W, uk %)
    500: dict(p_load_w=3900, p_noload_w=459, uk_percent=6.0),
    800: dict(p_load_w=6000, p_noload_w=585, uk_percent=6.0),
}
I0_PERCENT_ASSUMED = 0.3  # no-load current, assumption (typical 0.2–0.5 %)
VECTOR_GROUP = "Dyn11"
SHIFT_DEGREE = 330  # Dyn11 = 11 x 30 deg (irrelevant to a balanced power flow)

# ---------------------------------------------------------------------------
# Feeder geometry defaults
# ---------------------------------------------------------------------------
# Our own cable catalogue (pandapower has no 240 mm² LV type built in).
# R = IEC 60228 max DC resistance at 20 °C for Al; X and max current typical
# for 4-core 0.6/1 kV Al cables laid in ground (manufacturer data sheets vary ±10 %).
CABLE_TYPES = {
    "AXQJ 4x240 Al": dict(r_ohm_per_km=0.125, x_ohm_per_km=0.080, c_nf_per_km=0, max_i_ka=0.400, type="cs"),
    "AXQJ 4x50 Al":  dict(r_ohm_per_km=0.641, x_ohm_per_km=0.085, c_nf_per_km=0, max_i_ka=0.145, type="cs"),
}
MAIN_CABLE_STD = "AXQJ 4x240 Al"
SERVICE_CABLE_STD = "AXQJ 4x50 Al"
MAIN_SEGMENT_KM = 0.030             # 30 m between house connection points
SERVICE_KM = 0.015                  # 15 m from street to meter
POWER_FACTOR = 0.95                 # households, lagging


def _q_from_p(p_kw: float, pf: float = POWER_FACTOR) -> float:
    """Reactive power (kvar) that goes with p_kw at power factor pf."""
    return p_kw * math.tan(math.acos(pf))


def build_feeder(
    n_houses: int = 45,
    n_feeders: int = 3,
    trafo_kva: int = 500,
    house_p_kw: float = 1.0,
) -> pp.pandapowerNet:
    """Create the pandapower network.

    Returns a net whose house loads are all `house_p_kw` (placeholder).
    Later sessions overwrite `net.load.p_mw` every hour of the year.
    """
    if trafo_kva not in TRANSFORMERS:
        raise ValueError(f"no catalogue data for {trafo_kva} kVA")
    tr = TRANSFORMERS[trafo_kva]

    net = pp.create_empty_network(name=f"LV feeder {n_houses} houses / {trafo_kva} kVA")
    for cable_name, params in CABLE_TYPES.items():
        pp.create_std_type(net, params, cable_name, element="line")

    # --- medium-voltage side: the "big lake" -----------------------------------
    mv_bus = pp.create_bus(net, vn_kv=10.0, name="MV 10 kV")
    pp.create_ext_grid(net, bus=mv_bus, vm_pu=1.0, name="MV grid")

    # --- the transformer: the "pump" ---------------------------------------------
    lv_bus = pp.create_bus(net, vn_kv=0.4, name="LV busbar 0.4 kV")
    sn_mva = trafo_kva / 1000
    pp.create_transformer_from_parameters(
        net,
        hv_bus=mv_bus,
        lv_bus=lv_bus,
        sn_mva=sn_mva,
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

    # --- three radial cables, houses spread as evenly as possible ----------------
    houses_per_feeder = [n_houses // n_feeders] * n_feeders
    for i in range(n_houses % n_feeders):  # leftover houses go to the first feeders
        houses_per_feeder[i] += 1

    house_no = 0
    for f, n_on_this in enumerate(houses_per_feeder, start=1):
        upstream = lv_bus
        for k in range(1, n_on_this + 1):
            house_no += 1
            # connection point on the street cable
            node = pp.create_bus(net, vn_kv=0.4, name=f"F{f} node {k}")
            pp.create_line(
                net, from_bus=upstream, to_bus=node,
                length_km=MAIN_SEGMENT_KM, std_type=MAIN_CABLE_STD,
                name=f"F{f} main {k}",
            )
            # the house itself, at the end of its service cable
            house = pp.create_bus(net, vn_kv=0.4, name=f"House {house_no} (F{f})")
            pp.create_line(
                net, from_bus=node, to_bus=house,
                length_km=SERVICE_KM, std_type=SERVICE_CABLE_STD,
                name=f"service {house_no}",
            )
            pp.create_load(
                net, bus=house,
                p_mw=house_p_kw / 1000, q_mvar=_q_from_p(house_p_kw) / 1000,
                name=f"House {house_no}",
            )
            upstream = node

    return net


def set_house_loads(net: pp.pandapowerNet, p_kw, pf: float = POWER_FACTOR) -> None:
    """Overwrite every house load. `p_kw` is a scalar or a sequence (one per house).
    Negative values mean the house exports (rooftop PV)."""
    p = pd.Series(p_kw, index=net.load.index, dtype=float)
    net.load["p_mw"] = p / 1000
    net.load["q_mvar"] = p.abs() * math.tan(math.acos(pf)) / 1000


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
            f"losses {r['losses_kw']:.1f} kW"
        )
