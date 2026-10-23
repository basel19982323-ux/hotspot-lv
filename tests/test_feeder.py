"""Checks on the feeder model. Run with `pytest`."""

import math

import numpy as np
import pandas as pd
import pytest

from hotspotlv.feeder import (TRANSFORMERS, build_feeder, fuse_kw, run_snapshot,
                              set_house_loads)


@pytest.fixture
def net():
    return build_feeder()


def test_structure(net):
    assert len(net.load) == 45            # one load per house
    assert len(net.trafo) == 1
    assert len(net.ext_grid) == 1
    assert net.trafo.sn_mva.iloc[0] == pytest.approx(0.5)
    assert len(net.line) == 90            # 45 main-cable segments + 45 service cables


def test_transformer_parameters_match_ecodesign(net):
    tr = net.trafo.iloc[0]
    # vkr = load loss / rating: 3900 W / 500 kVA = 0.78 % (a 100x slip here would pass a loose test)
    assert tr.vkr_percent == pytest.approx(0.78, abs=1e-3)
    assert tr.vk_percent == pytest.approx(6.0)
    assert tr.pfe_kw == pytest.approx(0.459)
    assert 0.1 < tr.i0_percent < 1.0
    assert tr.shift_degree == 330
    # at exactly nameplate current the copper loss must equal the Tier-2 load loss
    set_house_loads(net, 10.0)
    r = run_snapshot(net)
    k = r["trafo_loading_percent"] / 100
    expected_losses_kw = (TRANSFORMERS[500]["p_noload_w"] + TRANSFORMERS[500]["p_load_w"] * k**2) / 1000
    assert net.res_trafo.pl_mw.iloc[0] * 1000 == pytest.approx(expected_losses_kw, rel=0.03)


def test_cable_parameters(net):
    main = net.line[net.line.std_type == "AXQJ 4x240 Al"].iloc[0]
    assert main.r_ohm_per_km == pytest.approx(0.125)       # IEC 60228 class 2 Al
    assert main.x_ohm_per_km < main.r_ohm_per_km            # LV cable: R dominates X
    assert main.max_i_ka == pytest.approx(0.286)            # Prysmian AXQJ in ground
    assert main.length_km == pytest.approx(0.030)


def test_power_flow_converges_and_is_plausible(net):
    set_house_loads(net, 1.0)
    r = run_snapshot(net)
    assert 8 < r["trafo_loading_percent"] < 12            # 45 kW on 500 kVA ≈ 10 %
    assert 0.98 < r["vmin_pu"] <= 1.0
    assert r["losses_kw"] < 2


def test_reactive_power_sign_and_magnitude(net):
    set_house_loads(net, 10.0)
    assert (net.load.q_mvar > 0).all()                      # lagging consumption
    assert net.load.q_mvar.iloc[0] * 1000 == pytest.approx(10 * math.tan(math.acos(0.95)), rel=1e-6)
    # 450 kW at pf 0.95 → S ≈ 474 kVA plus losses → loading ≈ 100 %, NOT 90 %
    r = run_snapshot(net)
    assert 98 < r["trafo_loading_percent"] < 103


def test_loads_taken_by_position_not_label(net):
    labelled = pd.Series(np.full(45, 2.0), index=[f"house{j + 1}" for j in range(45)])
    set_house_loads(net, labelled)                          # must not reindex to NaN
    assert net.load.p_mw.notna().all()
    assert net.load.p_mw.iloc[0] == pytest.approx(0.002)
    per_house = np.arange(1, 46, dtype=float)
    set_house_loads(net, per_house)
    assert net.load.p_mw.iloc[44] == pytest.approx(0.045)


def test_bad_loads_rejected(net):
    with pytest.raises(ValueError):
        set_house_loads(net, np.ones(44))                   # wrong length
    with pytest.raises(ValueError):
        set_house_loads(net, [np.nan] * 45)
    with pytest.raises(ValueError):
        set_house_loads(net, -5.0)                          # PV must be given as p_gen_kw


def test_pv_export_reverses_flow_at_unity_pf(net):
    set_house_loads(net, 1.0, p_gen_kw=6.0)                 # every house exports 5 kW net at noon
    r = run_snapshot(net)
    assert r["trafo_p_lv_kw"] < -200                        # power flows LV -> MV
    assert r["vmax_pu"] > 1.01                              # voltage RISES along the feeder
    # reactive power comes from the 1 kW consumption only, not from the PV
    assert net.load.q_mvar.iloc[0] * 1000 == pytest.approx(1 * math.tan(math.acos(0.95)), rel=1e-6)


def test_fuse_kw():
    assert fuse_kw(20) == pytest.approx(13.86, abs=0.01)
    assert fuse_kw(25) == pytest.approx(17.32, abs=0.01)


def test_bad_feeder_arguments_rejected():
    with pytest.raises(ValueError):
        build_feeder(trafo_kva=630)
    with pytest.raises(ValueError):
        build_feeder(n_houses=0)
    with pytest.raises(ValueError):
        build_feeder(n_houses=2, n_feeders=3)
    net = build_feeder(n_houses=46, n_feeders=3, trafo_kva=800)
    assert len(net.load) == 46 and net.trafo.sn_mva.iloc[0] == pytest.approx(0.8)


def test_the_315_kva_unit_has_its_tier2_losses_and_4_percent_impedance():
    """Added for the study's rural street (23 Oct 2026): Ecodesign Tier-2 Ak 2 800 W / A0 324 W, uk 4 %."""
    net = build_feeder(n_houses=20, n_feeders=3, trafo_kva=315)
    tr = net.trafo.iloc[0]
    assert TRANSFORMERS[315] == dict(p_load_w=2800, p_noload_w=324, uk_percent=4.0)
    assert tr.vkr_percent == pytest.approx(100 * 2800 / 315_000, abs=1e-6) and tr.vk_percent == pytest.approx(4.0)
    assert tr.pfe_kw == pytest.approx(0.324)


def test_longer_cables_between_houses_lower_the_far_voltage():
    """The study's street types differ in the cable between house connections; the default stays 30 m."""
    volts = {}
    for seg in (0.015, 0.030, 0.060):
        net = build_feeder(n_houses=30, n_feeders=3, main_segment_km=seg)
        set_house_loads(net, 4.0)
        volts[seg] = run_snapshot(net)["vmin_pu"]
        assert net.line.length_km[net.line.name.str.contains("main")].eq(seg).all()
    assert volts[0.015] > volts[0.030] > volts[0.060]
    assert build_feeder().line.length_km[build_feeder().line.name.str.contains("main")].eq(0.030).all()
    with pytest.raises(ValueError):
        build_feeder(main_segment_km=30)                       # metres, not km
