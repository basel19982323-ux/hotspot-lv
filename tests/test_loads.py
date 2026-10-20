"""Plausibility and bug-catching checks on the four load models and the neighbourhood builder.

Several assertions here are deliberately tight: a review on 20 Oct 2026 injected
16 one-line bugs (time-zone dropped, factor applied twice, charger power changed,
EV counted twice, …) and 13 slipped through the first test suite."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.feeder import fuse_kw
from hotspotlv.loads.base import SEASONAL_FACTOR, base_load_profiles
from hotspotlv.loads.ev import EVParams, ev_charging_profile
from hotspotlv.loads.heatpump import DHW_COP, DHW_KW_THERMAL, HeatPumpParams, heat_pump_profile
from hotspotlv.loads.pv import pv_profile
from hotspotlv.neighbourhood import Scenario, build_neighbourhood
from hotspotlv.weather import get_weather


@pytest.fixture(scope="module")
def wx():
    return get_weather(2023)


@pytest.fixture(scope="module")
def idx(wx):
    return wx.index


def _by_local_hour(s: pd.Series) -> pd.Series:
    loc = s.tz_convert("Europe/Stockholm")
    return loc.groupby(loc.index.hour).mean()


# --- base load -------------------------------------------------------------
def test_base_load_annual_energy_is_swedish_hushallsel(idx):
    df = base_load_profiles(idx, 20, seed=1)
    assert df.shape == (8760, 20)
    assert (df >= 0).all().all()
    annual = df.sum()
    assert 3500 <= annual.min() and annual.max() <= 8000
    assert 4800 < annual.mean() < 6500


def test_base_load_seasonal_shape_matches_factor(idx):
    df = base_load_profiles(idx, 30, seed=2)
    tot = df.sum(axis=1)
    monthly = tot.groupby(tot.index.month).mean()
    jan_over_jul = monthly[1] / monthly[7]
    assert jan_over_jul == pytest.approx(SEASONAL_FACTOR[1] / SEASONAL_FACTOR[7], rel=0.15)  # ≈ 2.0, not 4 (double) or 1 (none)


def test_base_load_daily_shape_follows_source_in_local_time(idx):
    from hotspotlv.loads.base import SUMMER_MONTHS, _load_source
    src = _load_source()
    src = src[src.index.month.isin(SUMMER_MONTHS)]
    src_shape = (src / src.mean()).mean(axis=1).groupby(src.index.hour).mean()   # houses equal-weighted
    df = base_load_profiles(idx, 40, seed=2).tz_convert("Europe/Stockholm")
    model_shape = (df / df.mean()).mean(axis=1).groupby(df.index.hour).mean()
    assert 17 <= model_shape.idxmax() <= 20              # Nordic households peak 17–20 local, not 15–16 (UTC bug)
    assert model_shape.idxmin() in (3, 4, 5)
    assert np.corrcoef(src_shape.to_numpy(), model_shape.to_numpy())[0, 1] > 0.97


def test_base_load_is_reproducible_and_per_house_stable(idx):
    a = base_load_profiles(idx, 5, seed=9)
    b = base_load_profiles(idx, 5, seed=9)
    pd.testing.assert_frame_equal(a, b)
    c = base_load_profiles(idx, 8, seed=9)
    pd.testing.assert_frame_equal(a, c.iloc[:, :5])   # adding houses does not re-roll the first ones
    assert not np.allclose(a, base_load_profiles(idx, 5, seed=10))


# --- heat pump -------------------------------------------------------------
def test_heat_pump_physics():
    cold = heat_pump_profile(pd.Series([-20.0]))
    mild = heat_pump_profile(pd.Series([5.0]))
    warm = heat_pump_profile(pd.Series([20.0]))
    assert cold["total"].iloc[0] > mild["total"].iloc[0] > warm["total"].iloc[0]
    assert cold["backup"].iloc[0] > 0           # backup heater kicks in at -20
    assert mild["backup"].iloc[0] == 0
    assert warm["hp"].iloc[0] == pytest.approx(DHW_KW_THERMAL / DHW_COP["air_water"], rel=1e-3)  # only hot water
    # backup cap: a huge house cannot draw more than pump + backup size
    huge = heat_pump_profile(pd.Series([-30.0]), HeatPumpParams(ua_kw_per_k=1.0))
    assert huge["backup"].iloc[0] == pytest.approx(9.0)


def test_heat_pump_annual_is_typical_villa(wx):
    hp = heat_pump_profile(wx["temp_c"])
    assert 5000 < hp["total"].sum() < 10000
    assert 4 < hp["total"].max() < 9
    assert 0 < hp["backup"].sum() < 0.1 * hp["total"].sum()
    assert hp["total"].idxmax() == wx["temp_c"].idxmin()   # peak at the coldest hour


def test_heating_kinds(wx):
    gs = HeatPumpParams(kind="ground_source")
    gs = gs.with_(q_nom_kw=gs.design_load_kw())
    ground = heat_pump_profile(wx["temp_c"], gs)
    assert ground["backup"].sum() < 20                     # sized for design temp; only the few hours below -17 °C
    direct = heat_pump_profile(wx["temp_c"], HeatPumpParams(kind="direct_electric"))
    assert direct["backup"].sum() == 0 and (direct["backup"] == 0).all()
    assert direct["total"].sum() == pytest.approx(
        (0.20 * (17 - wx["temp_c"]).clip(lower=0) + DHW_KW_THERMAL).sum(), rel=1e-6)
    with pytest.raises(ValueError):
        HeatPumpParams(kind="groundsource")


# --- EV --------------------------------------------------------------------
def test_ev_energy_and_timing(idx):
    ev = ev_charging_profile(idx, EVParams(), seed=4)
    assert 1200 < ev.sum() < 3000                   # kWh/yr home charging
    assert ev.max() <= 11.0 + 1e-9
    assert ev.max() > 10.0                          # full 11 kW hours exist
    by_hour = _by_local_hour(ev)
    assert by_hour.idxmax() in (17, 18, 19)         # tight: a 1-h shift or a UTC bug fails this
    assert by_hour[18] > 3 * by_hour[12]
    assert by_hour[5] < 0.1 * by_hour[18]


def test_ev_charger_power_changes_duration_not_energy(idx):
    fast = ev_charging_profile(idx, EVParams(charger_kw=11.0), seed=4)
    slow = ev_charging_profile(idx, EVParams(charger_kw=3.7), seed=4)
    assert slow.max() <= 3.7 + 1e-9
    assert slow.sum() == pytest.approx(fast.sum(), rel=0.05)   # same sessions, same energy
    assert (slow > 0).sum() > 2 * (fast > 0).sum()


def test_smart_charging_moves_load_to_night(idx):
    smart = ev_charging_profile(idx, EVParams(smart=True), seed=4)
    by_hour = _by_local_hour(smart)
    assert by_hour[18] < 0.2 * by_hour[23]
    assert by_hour.idxmax() in (23, 0, 1)


def test_ev_params_validated():
    with pytest.raises(ValueError):
        EVParams(charger_kw=0)
    with pytest.raises(ValueError):
        EVParams(p_charge_per_day=1.5)


# --- PV --------------------------------------------------------------------
def test_pv_positive_generation_and_yield(wx):
    pv = pv_profile(wx["pv_kw_per_kwp"], 10.0)
    assert (pv >= 0).all()
    assert 8000 < pv.sum() < 11000
    assert _by_local_hour(pv).idxmax() in (12, 13)   # solar noon ≈ 12:55 CEST


# --- neighbourhood ---------------------------------------------------------
def test_neighbourhood_shapes_and_bookkeeping(wx):
    nb = build_neighbourhood(Scenario(n_houses=40, heating_mix={"air_water": 1.0}, ev_share=0.0, pv_share=0.0, seed=3), wx)
    assert nb.cons_kw.shape == nb.gen_kw.shape == (8760, 40)
    assert (nb.houses.heating == "air_water").all()
    assert not nb.houses.has_ev.any() and not nb.houses.has_pv.any()
    assert (nb.gen_kw == 0).all().all()
    assert nb.houses.ua_kw_per_k.std() > 0.01                # houses really differ in size
    assert nb.houses.q_nom_kw.std() > 0.1
    assert nb.total_kw.idxmax().month in (1, 2, 12)          # winter peak
    # the parts add up to the totals (catches a component counted twice or dropped)
    parts_sum = nb.parts["base"] + nb.parts["heat"] + nb.parts["ev"] - nb.parts["fuse_curtailed"]
    pd.testing.assert_series_equal(parts_sum, nb.cons_kw.sum(axis=1), check_names=False, rtol=1e-9)
    assert nb.houses.heat_kwh.sum() == pytest.approx(nb.parts["heat"].sum(), rel=1e-3)


def test_fuse_cap_and_ev_backs_off_first(wx):
    sc = Scenario(n_houses=30, heating_mix={"direct_electric": 1.0}, ev_share=1.0, pv_share=0.0,
                  charger_mix={11.0: 1.0}, fuse_a=20, seed=1)
    nb = build_neighbourhood(sc, wx)
    cap = fuse_kw(20)
    assert nb.cons_kw.max().max() <= cap + 1e-9
    assert nb.houses.fuse_limited_hours.sum() > 0
    # with a 25 A fuse the same houses draw more at the peak
    nb25 = build_neighbourhood(sc.__class__(**{**sc.__dict__, "fuse_a": 25}), wx)
    assert nb25.total_kw.max() > nb.total_kw.max()
    # EV energy is what gets curtailed, heating is untouched
    assert nb25.houses.ev_kwh.sum() > nb.houses.ev_kwh.sum()
    assert nb25.houses.heat_kwh.sum() == pytest.approx(nb.houses.heat_kwh.sum())
    # bookkeeping must hold WITH curtailment too (round-2 finding)
    for x in (nb, nb25):
        parts_sum = x.parts["base"] + x.parts["heat"] + x.parts["ev"] - x.parts["fuse_curtailed"]
        pd.testing.assert_series_equal(parts_sum, x.cons_kw.sum(axis=1), check_names=False, rtol=1e-9)
        assert x.houses.ev_kwh.sum() < x.parts["ev"].sum()   # recorded ev_kwh is after the cap


def test_house_j_is_stable_when_neighbourhood_grows(wx):
    small = build_neighbourhood(Scenario(n_houses=6, ev_share=0.5, pv_share=0.5, seed=2), wx)
    big = build_neighbourhood(Scenario(n_houses=12, ev_share=0.5, pv_share=0.5, seed=2), wx)
    pd.testing.assert_frame_equal(small.houses, big.houses.iloc[:6])
    pd.testing.assert_frame_equal(small.cons_kw, big.cons_kw.iloc[:, :6])
    pd.testing.assert_frame_equal(small.gen_kw, big.gen_kw.iloc[:, :6])


def test_charger_mix_is_applied(wx):
    nb = build_neighbourhood(Scenario(n_houses=60, ev_share=1.0, pv_share=0, seed=5), wx)
    assert set(nb.houses.charger_kw.unique()) <= {11.0, 3.7, 7.4}
    assert nb.houses.charger_kw.nunique() == 3


def test_direct_electric_vs_heat_pump(wx):
    hp = build_neighbourhood(Scenario(heating_mix={"air_water": 1.0}, ev_share=0, pv_share=0, seed=2), wx)
    de = build_neighbourhood(Scenario(heating_mix={"direct_electric": 1.0}, ev_share=0, pv_share=0, seed=2), wx)
    assert de.parts["heat"].sum() > 2.5 * hp.parts["heat"].sum()      # energy: pump saves ~2/3
    assert 1.1 * hp.total_kw.max() < de.total_kw.max() < 1.8 * hp.total_kw.max()  # peak: backup heaters narrow the gap


def test_electrification_raises_peak_and_pv_creates_export(wx):
    none = build_neighbourhood(Scenario(heating_mix={"none": 1.0}, ev_share=0, pv_share=0, seed=1), wx)
    full = build_neighbourhood(Scenario(ev_share=0.6, pv_share=0.5, seed=1), wx)
    assert full.total_kw.max() > 2 * none.total_kw.max()
    assert full.total_kw.min() < 0                      # midsummer noon export
    assert none.total_kw.min() > 0
    assert full.parts["pv"].sum() == pytest.approx(full.houses.pv_kwh.sum(), rel=1e-3)


def test_scenario_validation():
    with pytest.raises(ValueError):
        Scenario(heating_mix={"air_water": 0.5, "none": 0.3})
    with pytest.raises(ValueError):
        Scenario(heating_mix={"groundsource": 1.0})
    with pytest.raises(ValueError):
        Scenario(heating_mix={"air_water": 1.5, "none": -0.5})
    with pytest.raises(ValueError):
        Scenario(ev_share=2)
    with pytest.raises(ValueError):
        Scenario(seed=-1)
    with pytest.raises(ValueError):
        Scenario(n_houses=0)


def test_leap_year_index_works(wx):
    idx24 = pd.date_range("2024-01-01", "2024-12-31 23:00", freq="h", tz="UTC")
    assert len(idx24) == 8784
    assert base_load_profiles(idx24, 3, seed=0).shape == (8784, 3)
    assert ev_charging_profile(idx24, seed=0).shape == (8784,)
