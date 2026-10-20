"""Sanity checks on the feeder model. Run with `pytest`."""

import pytest

from hotspotlv.feeder import build_feeder, run_snapshot, set_house_loads


@pytest.fixture
def net():
    return build_feeder()


def test_structure(net):
    assert len(net.load) == 45            # one load per house
    assert len(net.trafo) == 1
    assert len(net.ext_grid) == 1
    assert net.trafo.sn_mva.iloc[0] == pytest.approx(0.5)
    # 45 main-cable segments + 45 service cables
    assert len(net.line) == 90


def test_power_flow_converges_and_is_plausible(net):
    set_house_loads(net, 1.0)
    r = run_snapshot(net)
    # 45 kW on a 500 kVA transformer is ~10 % loading (plus losses / reactive)
    assert 8 < r["trafo_loading_percent"] < 12
    assert 0.98 < r["vmin_pu"] <= 1.0
    assert r["losses_kw"] < 2


def test_loading_scales_with_load(net):
    set_house_loads(net, 1.0)
    low = run_snapshot(net)["trafo_loading_percent"]
    set_house_loads(net, 10.0)
    high = run_snapshot(net)["trafo_loading_percent"]
    assert high > 9 * low  # roughly linear (losses make it slightly more)


def test_pv_export_reverses_flow(net):
    set_house_loads(net, -5.0)  # every house exports 5 kW at noon
    r = run_snapshot(net)
    assert r["trafo_p_lv_kw"] < 0       # power flows LV -> MV
    assert r["vmax_pu"] > 1.0           # voltage RISES along the feeder


def test_unknown_transformer_rejected():
    with pytest.raises(ValueError):
        build_feeder(trafo_kva=630)
