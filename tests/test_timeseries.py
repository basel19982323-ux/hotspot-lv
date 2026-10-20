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
    hours = nb.cons_kw.loc["2023-12-06"].index.append(nb.cons_kw.loc["2023-05-06"].index)
    net = build_feeder(n_houses=30)
    cons, gen = nb.cons_kw.loc[hours], nb.gen_kw.loc[hours]
    return cons, gen, run_year(net, cons, gen)


def test_shape_and_columns(slice_result):
    cons, gen, yr = slice_result
    assert len(yr.hourly) == 48
    assert yr.hourly.index.equals(cons.index)
    assert list(yr.hourly.columns) == ["s_kva", "loading_pct", "p_kw", "vmin_pu", "vmax_pu", "losses_kw", "line_max_pct"]
    assert yr.rated_kva == 500


def test_transformer_power_matches_house_sum_plus_losses(slice_result):
    cons, gen, yr = slice_result
    net_houses = (cons - gen).sum(axis=1)
    diff = yr.hourly["p_kw"] - net_houses          # = cable losses (trafo losses sit on the MV side)
    assert (diff >= -1e-6).all()
    assert (diff < 0.05 * net_houses.abs() + 1).all()
    np.testing.assert_allclose(yr.hourly["loading_pct"], yr.hourly["s_kva"] / 500 * 100, rtol=1e-6)
    # loading never below the pure active-power loading (Q and losses only add)
    assert (yr.hourly["s_kva"] >= net_houses.abs() - 1e-6).all()


def test_coldest_evening_is_the_peak(slice_result):
    cons, gen, yr = slice_result
    peak = yr.hourly["loading_pct"].idxmax().tz_convert("Europe/Stockholm")
    assert peak.month == 12 and 16 <= peak.hour <= 21


def test_export_hours_reverse_flow_and_raise_voltage(slice_result):
    cons, gen, yr = slice_result
    export = yr.hourly[(cons - gen).sum(axis=1) < -40]  # export must dominate: with mild export the lagging Q of the
                                                        # remaining consumption still holds the busbar just under 1.0
    assert len(export) > 0                                # sunny May noon with 50 % PV
    assert (export["p_kw"] < 0).all()
    assert (export["vmax_pu"] > 1.0).all()                # voltage rises at the exporting houses
    assert (export["vmax_pu"] < 1.05).all()


def test_summary_counts(slice_result):
    _, _, yr = slice_result
    s = yr.summary()
    assert s["80 % rule"] == int((yr.hourly["loading_pct"] > 80).sum())
    assert s["below 0.90 pu"] == 0 and s["above 1.10 pu"] == 0
    dc = yr.duration_curve()
    assert dc[0] >= dc[-1] and dc[0] == pytest.approx(s["peak_loading_pct"])
    assert s["energy_mwh"] > 0 and s["export_mwh"] > 0


def test_without_generation_argument():
    net = build_feeder(n_houses=10)
    idx = pd.date_range("2023-01-01", periods=3, freq="h", tz="UTC")
    cons = pd.DataFrame(np.full((3, 10), 2.0), index=idx)
    yr = run_year(net, cons)
    assert (yr.hourly["p_kw"] > 20).all() and (yr.hourly["p_kw"] < 21).all()


def test_bad_inputs_rejected():
    net = build_feeder(n_houses=10)
    idx = pd.date_range("2023-01-01", periods=2, freq="h", tz="UTC")
    with pytest.raises(ValueError):
        run_year(net, pd.DataFrame(np.ones((2, 9)), index=idx))
    with pytest.raises(ValueError):
        run_year(net, pd.DataFrame(np.ones((2, 10)), index=idx), pd.DataFrame(np.ones((2, 9)), index=idx))
