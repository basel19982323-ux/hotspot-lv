"""Mode B: reading a DSO's station CSV in its accepted shapes, cleaning it, estimating the
transformer loading, and reaching a verdict with the voltage rules marked as not checked."""

import math

import numpy as np
import pandas as pd
import pytest

from hotspotlv.feeder import POWER_FACTOR, build_feeder
from hotspotlv.neighbourhood import Scenario, build_neighbourhood
from hotspotlv.paths import package_data
from hotspotlv.station import (ambient_for, assess_station, export_station_csv, read_station_csv, s_from_meters,
                               with_overlay, year_result_from_station)
from hotspotlv.timeseries import run_year
from hotspotlv.weather import get_weather

EXAMPLES = package_data() / "examples"


@pytest.fixture(scope="module")
def wx():
    return get_weather(2023)


@pytest.fixture(scope="module")
def nb(wx):
    return build_neighbourhood(Scenario(n_houses=40, ev_share=0.5, pv_share=0.3, seed=5), wx)


def test_read_local_timestamps_and_kw(tmp_path, nb):
    path = export_station_csv(nb.total_kw, tmp_path / "s.csv")            # local naive timestamps, kW
    st = read_station_csv(path)
    assert st.step_min == 60 and len(st.p_kw) == 8760
    assert st.index.tz is not None and str(st.index.tz) == "UTC"
    assert st.index[0] == nb.total_kw.index[0]                             # DST handled, back on the UTC grid
    assert np.allclose(st.p_kw.to_numpy(), nb.total_kw.round(3).to_numpy())
    assert st.gaps_filled == 0 and any("Q estimated" in n for n in st.notes)


def test_read_energy_quarter_hours_and_measured_q(tmp_path):
    idx = pd.date_range("2024-01-01", periods=96 * 2, freq="15min", tz="UTC")
    p = pd.Series(np.linspace(100, 300, len(idx)), index=idx)
    df = pd.DataFrame({"Tidpunkt": idx.tz_convert("Europe/Stockholm").strftime("%Y-%m-%d %H:%M"),
                       "Energi": p.to_numpy() / 4, "kvar": 30.0})
    df.to_csv(tmp_path / "e.csv", sep=";", index=False)
    st = read_station_csv(tmp_path / "e.csv")
    assert st.step_min == 15
    assert np.allclose(st.p_kw.to_numpy(), p.to_numpy())                   # kWh per quarter → kW
    assert (st.q_kvar == 30.0).all()


def test_gaps_short_filled_long_rejected(tmp_path, nb):
    df = pd.read_csv(export_station_csv(nb.total_kw, tmp_path / "s.csv"))
    short = df.drop(index=range(100, 104))                                 # 4 missing hours → filled
    short.to_csv(tmp_path / "short.csv", index=False)
    st = read_station_csv(tmp_path / "short.csv")
    assert st.gaps_filled == 4 and len(st.p_kw) == 8760
    long = df.drop(index=range(200, 210))                                  # 10 missing hours → error
    long.to_csv(tmp_path / "long.csv", index=False)
    with pytest.raises(ValueError, match="gap of 10"):
        read_station_csv(tmp_path / "long.csv")


def test_bad_files(tmp_path):
    (tmp_path / "x.csv").write_text("timestamp,foo\n2023-01-01 00:00,1\n2023-01-01 01:00,2\n")
    with pytest.raises(ValueError, match="no power/energy column"):
        read_station_csv(tmp_path / "x.csv")
    (tmp_path / "y.csv").write_text("timestamp,kw\nnot a date,1\n2023-01-01 01:00,2\n")
    with pytest.raises(ValueError, match="unparseable timestamp"):
        read_station_csv(tmp_path / "y.csv")
    with pytest.raises(ValueError):
        read_station_csv(tmp_path / "y.csv", measured_at="somewhere")


def test_s_from_meters_matches_the_power_flow(nb):
    # the estimate (cable share + transformer losses + uk·K²) vs the real power flow on 5 winter days
    hours = nb.cons_kw.loc["2023-12-04":"2023-12-08"].index
    full = run_year(build_feeder(n_houses=40), nb.cons_kw.loc[hours], nb.gen_kw.loc[hours])
    p = nb.p_kw.loc[hours].sum(axis=1)
    q = nb.cons_kw.loc[hours].sum(axis=1) * math.tan(math.acos(POWER_FACTOR))
    s_est = s_from_meters(p, q, 500)
    err_pp = (s_est - full.hourly["s_kva"]).abs() / 500 * 100
    assert err_pp.max() < 2.0                                              # within 2 % of nameplate without a feeder model
    assert (s_est >= np.hypot(p, q) - 1e-9).all()                          # losses only add


def test_assess_station_end_to_end(tmp_path, nb, wx):
    st = read_station_csv(export_station_csv(nb.total_kw, tmp_path / "s.csv"))
    amb = ambient_for(st.index, 96190)
    assert amb.index.equals(st.index) and amb.min() < -15
    res = assess_station(st, 500, amb)
    assert res.verdict.level in ("OK", "Watch", "At risk")
    assert any("not checked" in n for n in res.verdict.notes)
    assert "not checked" in res.verdict.headline() and "EIFS" not in res.verdict.headline()
    assert res.verdict.really["hours with voltage outside ±10 %"] == 0      # neutral, never a reason
    m = res.metrics()
    assert m["period"].startswith("2023-01-01") and m["step_min"] == 60
    assert 0 < m["life_days_per_year"] < 5 and m["energy_mwh"] > 300
    # a 15-min version of the same station gives the same physics
    q = st.p_kw.resample("15min").ffill()
    df = pd.DataFrame({"timestamp": q.index.tz_convert("Europe/Stockholm").strftime("%Y-%m-%d %H:%M"), "kw": q.to_numpy()})
    df.to_csv(tmp_path / "q.csv", index=False)
    st15 = read_station_csv(tmp_path / "q.csv")
    res15 = assess_station(st15, 500, ambient_for(st15.index, 96190))
    assert res15.metrics()["life_days_per_year"] == pytest.approx(m["life_days_per_year"], rel=0.05)
    assert res15.metrics()["hours_above_80"] == pytest.approx(m["hours_above_80"], abs=1)


def test_overlay_adds_load(tmp_path, nb):
    st = read_station_csv(export_station_csv(nb.total_kw, tmp_path / "s.csv"))
    amb = ambient_for(st.index)
    more = with_overlay(st, amb, n_ev=20, n_hp=10, seed=1)
    assert more.p_kw.sum() > st.p_kw.sum() + 20 * 1000 + 10 * 5000       # ≥ 1 MWh per EV, ≥ 5 MWh per pump
    assert (more.q_kvar >= st.q_kvar - 1e-9).all()
    assert more.s_kva_measured is None and any("overlay" in n for n in more.notes)
    base = assess_station(st, 500, amb); over = assess_station(more, 500, amb, overlay={"n_ev": 20})
    assert over.metrics()["peak_loading_pct"] > base.metrics()["peak_loading_pct"]
    assert over.metrics()["n_ev"] == 20


def test_measured_s_at_transformer_is_used(tmp_path):
    idx = pd.date_range("2023-06-01", periods=48, freq="h", tz="UTC")
    df = pd.DataFrame({"timestamp": idx.strftime("%Y-%m-%dT%H:%M:%SZ"), "p_kw": 300.0, "s_kva": 420.0})
    df.to_csv(tmp_path / "t.csv", index=False)
    st = read_station_csv(tmp_path / "t.csv", measured_at="transformer")
    yr = year_result_from_station(st, 500)
    assert (yr.hourly["s_kva"] == 420.0).all()


def test_examples_are_readable():
    st = read_station_csv(EXAMPLES / "ns-101.csv")
    assert len(st.p_kw) == 8760 and st.p_kw.max() > 150
