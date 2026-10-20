"""Checks on the hourly power-flow loop (run on a short slice to keep tests fast)."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.feeder import build_feeder
from hotspotlv.neighbourhood import Scenario, build_neighbourhood
from hotspotlv.timeseries import run_year
from hotspotlv.weather import get_weather


@pytest.fixture(scope="module")
def slice_result():
    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario(n_houses=30, ev_share=0.5, pv_share=0.5, seed=5), wx)
    # 6 Dec (coldest day) and 6 May (sunny) — 48 hours
    hours = nb.p_kw.loc["2023-12-06"].index.append(nb.p_kw.loc["2023-05-06"].index)
    net = build_feeder(n_houses=30)
    return nb.p_kw.loc[hours], run_year(net, nb.p_kw.loc[hours])


def test_shape_and_columns(slice_result):
    p_kw, yr = slice_result
    assert len(yr.hourly) == 48
    assert list(yr.hourly.columns) == ["s_kva", "loading_pct", "p_kw", "vmin_pu", "vmax_pu", "losses_kw", "line_max_pct"]
    assert yr.rated_kva == 500


def test_transformer_power_matches_house_sum_plus_losses(slice_result):
    p_kw, yr = slice_result
    house_sum = p_kw.sum(axis=1)
    # P through the transformer = houses + cable losses (trafo losses are on the MV side)
    diff = yr.hourly["p_kw"] - house_sum
    assert (diff >= -1e-6).all()                       # losses never negative
    assert (diff < 0.05 * house_sum.abs() + 1).all()   # and small
    # loading % consistent with S / 500 kVA
    np.testing.assert_allclose(yr.hourly["loading_pct"], yr.hourly["s_kva"] / 500 * 100, rtol=1e-6)


def test_export_hours_reverse_flow_and_raise_voltage(slice_result):
    p_kw, yr = slice_result
    export = yr.hourly[p_kw.sum(axis=1) < 0]
    assert len(export) > 0                               # sunny May noon with 50 % PV
    assert (export["p_kw"] < 0).all()
    assert (export["vmax_pu"] > 1.0).all()


def test_summary_counts(slice_result):
    _, yr = slice_result
    s = yr.summary()
    assert s["80 % rule"] == int((yr.hourly["loading_pct"] > 80).sum())
    assert s["below 0.90 pu"] == 0
    dc = yr.duration_curve()
    assert dc[0] >= dc[-1] and dc[0] == pytest.approx(s["peak_loading_pct"])


def test_wrong_house_count_rejected():
    net = build_feeder(n_houses=10)
    idx = pd.date_range("2023-01-01", periods=2, freq="h", tz="UTC")
    with pytest.raises(ValueError):
        run_year(net, pd.DataFrame(np.ones((2, 9)), index=idx))
