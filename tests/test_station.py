"""Mode B: reading a DSO's station CSV in the shapes real Swedish exports take, cleaning it without
silent realignment, estimating the transformer loading, and reaching a verdict that says what it
did not check. Cases follow the review of 21 Oct 2026 (18 realistic files)."""

import math

import numpy as np
import pandas as pd
import pytest

from hotspotlv.feeder import POWER_FACTOR, build_feeder
from hotspotlv.neighbourhood import Scenario, build_neighbourhood
from hotspotlv.paths import package_data
from hotspotlv.station import (ambient_for, assess_station, export_station_csv, read_station_csv, s_from_meters,
                               to_hourly, with_overlay, year_result_from_station)
from hotspotlv.timeseries import run_year
from hotspotlv.weather import get_weather

EXAMPLES = package_data() / "examples"


@pytest.fixture(scope="module")
def wx():
    return get_weather(2023)


@pytest.fixture(scope="module")
def nb(wx):
    return build_neighbourhood(Scenario(n_houses=40, ev_share=0.5, pv_share=0.3, seed=5), wx)


@pytest.fixture(scope="module")
def ref(nb):
    """The reference: hourly local-time kW file and the UTC series it must come back as."""
    return nb.total_kw.round(3)


def _write(path, df, sep=",", **kw):
    df.to_csv(path, sep=sep, index=False, **kw)
    return path


def _local(index):
    return index.tz_convert("Europe/Stockholm")


# --- shapes that must read correctly ------------------------------------------
def test_local_timestamps_and_kw(tmp_path, ref):
    st = read_station_csv(export_station_csv(ref, tmp_path / "s.csv"))
    assert st.step_min == 60 and len(st.p_kw) == 8760 and str(st.index.tz) == "UTC"
    assert st.index.equals(ref.index)                                       # DST handled, back on the UTC grid
    assert np.allclose(st.p_kw.to_numpy(), ref.to_numpy())
    assert st.gaps_filled == 0
    assert any("Q estimated" in n for n in st.notes) and any("interval START" in n for n in st.notes)
    assert st.period().startswith("2023-01-01") and st.period().endswith("(Swedish time, whole days)")


def test_datum_tid_semicolon_decimal_comma_kwh(tmp_path, ref):
    loc = _local(ref.index)
    df = pd.DataFrame({"Datum": loc.strftime("%Y-%m-%d"), "Tid": loc.strftime("%H:%M"),
                       "Förbrukning (kWh)": [f"{v:.3f}".replace(".", ",") for v in ref.to_numpy()]})
    st = read_station_csv(_write(tmp_path / "e.csv", df, sep=";", encoding="cp1252"))
    assert st.index.equals(ref.index)
    assert np.allclose(st.p_kw.to_numpy(), ref.to_numpy())                  # kWh per hour = kW, decimal comma read
    assert any("joined" in n for n in st.notes)


def test_wh_and_quarter_hours_and_measured_q(tmp_path):
    idx = pd.date_range("2024-01-01", periods=96 * 2, freq="15min", tz="UTC")
    p = pd.Series(np.linspace(100, 300, len(idx)), index=idx)
    df = pd.DataFrame({"Tidpunkt": _local(idx).strftime("%Y-%m-%d %H:%M"), "Energi (Wh)": p.to_numpy() / 4 * 1000,
                       "Reaktiv effekt (kvar)": 30.0})
    st = read_station_csv(_write(tmp_path / "w.csv", df, sep="\t"))
    assert st.step_min == 15
    assert np.allclose(st.p_kw.to_numpy(), p.to_numpy())                    # Wh per quarter → kW
    assert (st.q_kvar == 30.0).all()


def test_import_export_columns(tmp_path, ref):
    loc = _local(ref.index)
    cons = ref.clip(lower=0); gen = (-ref).clip(lower=0) + 2.0                 # some export every hour
    df = pd.DataFrame({"Datum": loc.strftime("%Y-%m-%d %H:%M"), "Uttag (kWh)": cons.to_numpy(), "Inmatning (kWh)": gen.to_numpy()})
    st = read_station_csv(_write(tmp_path / "x.csv", df))
    assert np.allclose(st.p_kw.to_numpy(), (cons - gen).to_numpy())
    assert any("export column" in n for n in st.notes) and (st.p_kw < 0).any()


def test_iso_with_mixed_offsets_and_z(tmp_path, ref):
    iso = ref.index.tz_convert("Europe/Stockholm").strftime("%Y-%m-%dT%H:%M:%S%z")  # +0100 / +0200
    st = read_station_csv(_write(tmp_path / "iso.csv", pd.DataFrame({"timestamp": iso, "kW": ref.to_numpy()})))
    assert st.index.equals(ref.index)
    z = ref.index.strftime("%Y-%m-%dT%H:%M:%SZ")
    st = read_station_csv(_write(tmp_path / "z.csv", pd.DataFrame({"timestamp": z, "kW": ref.to_numpy()})))
    assert st.index.equals(ref.index)


def test_normaltid_file(tmp_path, ref):
    cet = ref.index.tz_convert("Etc/GMT-1").tz_localize(None).strftime("%Y-%m-%d %H:%M")   # CET all year
    df = pd.DataFrame({"timestamp": cet, "kW": ref.to_numpy()})
    with pytest.raises(ValueError, match="normaltid"):                       # read as local time it cannot fit
        read_station_csv(_write(tmp_path / "n.csv", df))
    st = read_station_csv(tmp_path / "n.csv", tz="normaltid")
    assert st.index.equals(ref.index) and np.allclose(st.p_kw.to_numpy(), ref.to_numpy())


def test_interval_end_stamps_and_24h(tmp_path, ref):
    loc = _local(ref.index) + pd.Timedelta(hours=1)
    end = loc.strftime("%Y-%m-%d %H:%M")
    df = pd.DataFrame({"timestamp": end, "kW": ref.to_numpy()})
    st = read_station_csv(_write(tmp_path / "end.csv", df), stamp="end")
    assert st.index.equals(ref.index) and np.allclose(st.p_kw.to_numpy(), ref.to_numpy())
    # the same file with the "24:00" notation for midnight
    df24 = df.copy()
    at_midnight = loc.hour == 0
    prev_day = (loc[at_midnight].tz_localize(None) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")   # calendar day, not 24 h
    df24.loc[at_midnight, "timestamp"] = prev_day + " 24:00"
    st = read_station_csv(_write(tmp_path / "24.csv", df24), stamp="end")
    assert st.index.equals(ref.index) and np.allclose(st.p_kw.to_numpy(), ref.to_numpy())


# --- what must NOT be read silently -------------------------------------------
def test_newest_first_october_file_keeps_the_02h_pair_in_order(tmp_path, ref):
    oct = ref.loc["2023-10-28":"2023-10-30"]
    df = pd.DataFrame({"timestamp": _local(oct.index).strftime("%Y-%m-%d %H:%M"), "kW": oct.to_numpy()}).iloc[::-1]
    st = read_station_csv(_write(tmp_path / "rev.csv", df))
    assert st.index.equals(oct.index) and np.allclose(st.p_kw.to_numpy(), oct.to_numpy())   # not swapped


def test_units_are_scaled_or_refused(tmp_path, ref):
    loc = _local(ref.index).strftime("%Y-%m-%d %H:%M")
    st = read_station_csv(_write(tmp_path / "mw.csv", pd.DataFrame({"timestamp": loc, "Effekt (MW)": ref.to_numpy() / 1000})))
    assert np.allclose(st.p_kw.to_numpy(), ref.to_numpy())
    st = read_station_csv(_write(tmp_path / "w.csv", pd.DataFrame({"timestamp": loc, "Energi Wh": ref.to_numpy() * 1000})))
    assert np.allclose(st.p_kw.to_numpy(), ref.to_numpy())                   # unit word without brackets
    st = read_station_csv(_write(tmp_path / "fk.csv", pd.DataFrame({"timestamp": loc, "Förbrukning kWh": ref.to_numpy()})))
    assert np.allclose(st.p_kw.to_numpy(), ref.to_numpy())
    with pytest.raises(ValueError, match="unit"):
        read_station_csv(_write(tmp_path / "gj.csv", pd.DataFrame({"timestamp": loc, "Energi (GJ)": ref.to_numpy()})))
    cells = [f"{v * 100:,.1f}" if i % 2 else f"{v:.1f}".replace(".", ",") for i, v in enumerate(ref.to_numpy()[:100])]
    expected = [float(c.replace(",", "")) if i % 2 else float(c.replace(",", ".")) for i, c in enumerate(cells)]
    st = read_station_csv(_write(tmp_path / "mix.csv", pd.DataFrame({"timestamp": loc[:100], "kW": cells})))
    assert np.allclose(st.p_kw.to_numpy(), expected) and st.gaps_filled == 0    # '1,226.0' and '12,3' in one column


def test_24h_stamps_imply_end_stamping(tmp_path, ref):
    loc = _local(ref.index) + pd.Timedelta(hours=1)
    end = pd.Series(loc.strftime("%Y-%m-%d %H:%M"))
    at_mid = loc.hour == 0
    end[at_mid] = (loc[at_mid].tz_localize(None) - pd.Timedelta(days=1)).strftime("%Y-%m-%d") + " 24:00"
    st = read_station_csv(_write(tmp_path / "24d.csv", pd.DataFrame({"timestamp": end, "kW": ref.to_numpy()})))   # default stamp
    assert st.index.equals(ref.index) and any("stamp='end' applied" in n for n in st.notes)


def test_one_day_quarter_hour_file_is_fine(tmp_path):
    idx = pd.date_range("2024-02-01", periods=96, freq="15min", tz="UTC")
    st = read_station_csv(_write(tmp_path / "day.csv", pd.DataFrame({"timestamp": _local(idx).strftime("%Y-%m-%d %H:%M"), "kW": 50.0})))
    assert len(st.p_kw) == 96


def test_mixed_offset_rows_and_tid_ranges_are_refused(tmp_path):
    (tmp_path / "m.csv").write_text("timestamp,kW\n2023-01-01T00:00:00+01:00,1\n2023-01-01 01:00,2\n2023-01-01 02:00,3\n")
    with pytest.raises(ValueError, match="mix"):
        read_station_csv(tmp_path / "m.csv")
    (tmp_path / "r.csv").write_text("Tid,kWh\n00:00-01:00,1\n01:00-02:00,2\n02:00-03:00,3\n")
    with pytest.raises(ValueError, match="time of day"):
        read_station_csv(tmp_path / "r.csv")
    (tmp_path / "empty.csv").write_text("")
    with pytest.raises(ValueError):
        read_station_csv(tmp_path / "empty.csv")


def test_time_only_column_is_rejected(tmp_path, ref):
    loc = _local(ref.index)[:168]
    df = pd.DataFrame({"Tid": loc.strftime("%H:%M"), "kWh": ref.to_numpy()[:168]})
    with pytest.raises(ValueError, match="time of day"):
        read_station_csv(_write(tmp_path / "t.csv", df))


def test_duplicates_and_off_grid(tmp_path, ref):
    df = pd.read_csv(export_station_csv(ref, tmp_path / "s.csv"))
    dup_same = pd.concat([df, df.iloc[[10]]])
    st = read_station_csv(_write(tmp_path / "d1.csv", dup_same))
    assert len(st.p_kw) == 8760 and any("repeated rows dropped" in n for n in st.notes)
    dup_diff = pd.concat([df, df.iloc[[10]].assign(p_kw=999.0)])
    with pytest.raises(ValueError, match="different values"):
        read_station_csv(_write(tmp_path / "d2.csv", dup_diff))
    off = df.copy(); off.loc[500, "timestamp"] = "2023-01-21 20:30"
    with pytest.raises(ValueError, match="not on the 60-min grid"):
        read_station_csv(_write(tmp_path / "o.csv", off))


def test_gaps_short_filled_long_rejected(tmp_path, ref):
    df = pd.read_csv(export_station_csv(ref, tmp_path / "s.csv"))
    short = df.drop(index=range(100, 104))                                   # 4 missing hours → filled
    st = read_station_csv(_write(tmp_path / "short.csv", short))
    assert st.gaps_filled == 4 and len(st.p_kw) == 8760
    long = df.drop(index=range(200, 210))                                    # 10 missing hours → error
    with pytest.raises(ValueError, match="gap of 10"):
        read_station_csv(_write(tmp_path / "long.csv", long))


def test_bad_files(tmp_path):
    (tmp_path / "x.csv").write_text("timestamp,foo\n2023-01-01 00:00,1\n2023-01-01 01:00,2\n2023-01-01 02:00,3\n")
    with pytest.raises(ValueError, match="no power/energy column"):
        read_station_csv(tmp_path / "x.csv")
    (tmp_path / "y.csv").write_text("timestamp,kw\nnot a date,1\n2023-01-01 01:00,2\n2023-01-01 02:00,3\n")
    with pytest.raises(ValueError, match="unparseable timestamp"):
        read_station_csv(tmp_path / "y.csv")
    (tmp_path / "ok.csv").write_text("timestamp,kw\n2023-01-01 00:00,1\n2023-01-01 01:00,2\n2023-01-01 02:00,3\n")
    for bad in (dict(measured_at="somewhere"), dict(stamp="middle"), dict(pf=0), dict(max_gap_steps=-1)):
        with pytest.raises(ValueError):
            read_station_csv(tmp_path / "ok.csv", **bad)
    (tmp_path / "bad.csv").write_text("timestamp,kw\n2023-01-01 00:00,1\n2023-01-01 02:00,2\n2023-01-02 00:00,3\n")
    with pytest.raises(ValueError):
        read_station_csv(tmp_path / "bad.csv")                               # not a meter interval


# --- physics -------------------------------------------------------------------
@pytest.mark.parametrize("n_houses, tol_pp", [(40, 0.6), (90, 2.0)])
def test_s_from_meters_matches_the_power_flow(wx, n_houses, tol_pp):
    nb = build_neighbourhood(Scenario(n_houses=n_houses, ev_share=0.6, pv_share=0.3, seed=5), wx)
    hours = nb.cons_kw.loc["2023-12-04":"2023-12-08"].index.append(nb.cons_kw.loc["2023-06-10":"2023-06-12"].index)
    full = run_year(build_feeder(n_houses=n_houses), nb.cons_kw.loc[hours], nb.gen_kw.loc[hours])
    p = nb.p_kw.loc[hours].sum(axis=1)
    q = nb.cons_kw.loc[hours].sum(axis=1) * math.tan(math.acos(POWER_FACTOR))
    s_est = s_from_meters(p, q, 500)
    err_pp = (s_est - full.hourly["s_kva"]).abs() / 500 * 100
    assert err_pp.max() < tol_pp                                             # loading-dependent losses, no feeder model
    cons = p > 0
    assert (s_est[cons] >= np.hypot(p, q)[cons] - 1e-9).all()                # on consumption hours losses only add
    # each loss term matters: dropping the uk term or the cable share must be visible
    no_cable = s_from_meters(p, q, 500, cable_loss_share_at_rated=0.0)
    assert (s_est - no_cable).max() > 2.0


def test_s_from_meters_at_the_transformer_and_high_k():
    idx = pd.date_range("2023-01-01", periods=3, freq="h", tz="UTC")
    p = pd.Series([0.0, 500.0, 1400.0], index=idx); q = pd.Series([0.0, 160.0, 460.0], index=idx)
    s_m = s_from_meters(p, q, 500, "meters"); s_t = s_from_meters(p, q, 500, "transformer")
    assert s_m.iloc[0] == pytest.approx(0.459, abs=1e-3)                    # no-load losses only
    assert (s_m.iloc[1:] > s_t.iloc[1:]).all()                               # cable share only for meter sums
    assert np.isfinite(s_m.iloc[2]) and np.hypot(1400, 460) < s_m.iloc[2] < 2 * np.hypot(1400, 460)   # converged at K ≈ 3.8


# --- assessment ----------------------------------------------------------------
def test_assess_station_end_to_end(tmp_path, ref):
    st = read_station_csv(export_station_csv(ref, tmp_path / "s.csv"))
    amb = ambient_for(st.index, 96190)
    assert amb.index.equals(st.index) and amb.min() < -15
    res = assess_station(st, 500, amb, smhi_station=96190)
    assert res.verdict.level in ("OK", "Watch", "At risk")
    assert any("not checked" in n for n in res.verdict.notes)
    assert "not checked" in res.verdict.headline() and "EIFS" not in res.verdict.headline()
    assert res.verdict.really["hours with voltage outside ±10 %"] is None   # not 0: unknown
    m = res.metrics()
    assert m["period"].startswith("2023-01-01") and m["step_min"] == 60 and m["smhi_station"] == 96190
    assert "SMHI station 96190" in m["notes"] and "the default" not in m["notes"]
    assert 0 < m["life_days_per_year"] < 5 and m["energy_mwh"] > 300
    default = assess_station(st, 500, amb)
    assert "the default; choose the SMHI station nearest the place" in default.metrics()["notes"]
    # a 15-min version of the same station gives the same physics
    q = st.p_kw.resample("15min").ffill()
    df = pd.DataFrame({"timestamp": _local(q.index).strftime("%Y-%m-%d %H:%M"), "kw": q.to_numpy()})
    st15 = read_station_csv(_write(tmp_path / "q.csv", df))
    res15 = assess_station(st15, 500, ambient_for(st15.index, 96190), smhi_station=96190)
    assert res15.metrics()["life_days_per_year"] == pytest.approx(m["life_days_per_year"], rel=0.05)
    assert res15.metrics()["hours_above_80"] == pytest.approx(m["hours_above_80"], abs=1)
    assert res15.metrics()["energy_mwh"] == pytest.approx(m["energy_mwh"], rel=0.01)
    assert to_hourly(st15).p_kw.sum() == pytest.approx(st.p_kw.sum(), rel=0.01)


def test_loading_above_3x_is_at_risk_not_an_error(tmp_path, ref):
    st = read_station_csv(export_station_csv(ref * 7, tmp_path / "big.csv"))     # wrong rating or huge station
    res = assess_station(st, 500, ambient_for(st.index), smhi_station=96190)
    assert res.verdict.level == "At risk" and any("current limit" in r.rule for r in res.verdict.at_risk)
    assert any("clipped" in n for n in res.verdict.notes)


def test_overlay_adds_load_as_upper_bound(tmp_path, ref):
    st = read_station_csv(export_station_csv(ref, tmp_path / "s.csv"))
    amb = ambient_for(st.index)
    more = with_overlay(st, amb, n_ev=20, n_hp=10, seed=1)
    assert more.p_kw.sum() > st.p_kw.sum() + 20 * 1000 + 10 * 5000         # ≥ 1 MWh per EV, ≥ 5 MWh per pump
    assert (more.p_kw - st.p_kw).max() <= 20 * 13.86 + 1e-6                 # fuse cap per synthetic house
    assert (more.q_kvar >= st.q_kvar - 1e-9).all()
    assert more.s_kva_measured is None and any("upper bound" in n for n in more.notes)
    base = assess_station(st, 500, amb); over = assess_station(more, 500, amb, overlay={"n_ev": 20, "verdict": "x"})
    assert over.metrics()["peak_loading_pct"] > base.metrics()["peak_loading_pct"]
    assert over.metrics()["overlay_n_ev"] == 20 and over.metrics()["verdict"] in ("OK", "Watch", "At risk")
    for bad in (dict(n_ev=-1), dict(n_hp=2.5)):
        with pytest.raises(ValueError):
            with_overlay(st, amb, **bad)
    with pytest.raises(ValueError):
        with_overlay(st, amb.iloc[:-1], n_ev=1)


def test_measured_s_at_transformer_is_used(tmp_path):
    idx = pd.date_range("2023-06-01", periods=48, freq="h", tz="UTC")
    df = pd.DataFrame({"timestamp": idx.strftime("%Y-%m-%dT%H:%M:%SZ"), "p_kw": 300.0, "s_kva": 420.0})
    _write(tmp_path / "t.csv", df)
    st = read_station_csv(tmp_path / "t.csv", measured_at="transformer")
    assert (year_result_from_station(st, 500).hourly["s_kva"] == 420.0).all()
    st2 = read_station_csv(tmp_path / "t.csv")                                # meters: the kVA column is ignored, with a note
    assert st2.s_kva_measured is None and any("ignored" in n for n in st2.notes)


def test_examples_are_readable():
    st = read_station_csv(EXAMPLES / "ns-101.csv")
    assert len(st.p_kw) == 8760 and st.p_kw.max() > 150
