"""One sentence that pins a Case down, so a tab can say which inputs it ran on."""

from __future__ import annotations

from hotspotlv.study import Case


def charging_mode(case: Case) -> str:
    ev = case.scenario.ev
    if not ev.smart:
        return "uncontrolled charging"
    if ev.smart_start_max_h <= 25.0:
        return "night timer 22–01"
    return "spread 22–06"


def describe_case(case: Case) -> str:
    sc = case.scenario
    return (f"{sc.n_houses} houses / {case.trafo_kva} kVA · {case.n_feeders} feeders · {sc.fuse_a:g} A fuse · "
            f"{case.enclosure} · EV {sc.ev_share * 100:.0f} % · PV {sc.pv_share * 100:.0f} % · {charging_mode(case)} · "
            f"draw {sc.seed}")
