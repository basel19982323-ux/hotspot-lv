"""Plausibility checks on the four load models and the neighbourhood builder."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.loads.base import base_load_profiles
from hotspotlv.loads.ev import EVParams, ev_charging_profile
from hotspotlv.loads.heatpump import HeatPumpParams, heat_pump_profile
from hotspotlv.loads.pv import pv_profile
from hotspotlv.neighbourhood import Scenario, build_neighbourhood
from hotspotlv.weather import get_weather


@pytest.fixture(scope="module")
def wx():
    return get_weather(2023)


@pytest.fixture(scope="module")
def idx(wx):
    return wx.index


# --- base load -------------------------------------------------------------
def test_base_load_annual_energy_is_swedish_hushallsel(idx):
    df = base_load_profiles(idx, 20, seed=1)
    assert df.shape == (8760, 20)
    assert (df >= 0).all().all()
    annual = df.sum()
    assert 3500 <= annual.min() and annual.max() <= 8000
    assert 4500 < annual.mean() < 6500


def test_base_load_has_winter_and_evening(idx):
    df = base_load_profiles(idx, 20, seed=2)
    tot = df.sum(axis=1)
    assert tot[tot.index.month == 1].mean() > 1.5 * tot[tot.index.month == 7].mean()
    loc = tot.tz_convert("Europe/Stockholm")
    by_hour = loc.groupby(loc.index.hour).mean()
    assert by_hour[18] > 1.2 * by_hour[3]


def test_base_load_is_reproducible(idx):
    a = base_load_profiles(idx, 5, seed=9)
    b = base_load_profiles(idx, 5, seed=9)
    pd.testing.assert_frame_equal(a, b)
    assert not np.allclose(a, base_load_profiles(idx, 5, seed=10))


# --- heat pump -------------------------------------------------------------
def test_heat_pump_physics():
    cold = heat_pump_profile(pd.Series([-20.0]))
    mild = heat_pump_profile(pd.Series([5.0]))
    warm = heat_pump_profile(pd.Series([20.0]))
    assert cold["total"].iloc[0] > mild["total"].iloc[0] > warm["total"].iloc[0]
    assert cold["backup"].iloc[0] > 0           # backup heater kicks in at -20
    assert mild["backup"].iloc[0] == 0
    assert warm["hp"].iloc[0] == pytest.approx(0.35 / 4.5, rel=1e-3)  # only hot water


def test_heat_pump_annual_is_typical_villa(wx):
    hp = heat_pump_profile(wx["temp_c"])
    assert 5000 < hp["total"].sum() < 10000
    assert 4 < hp["total"].max() < 9
    assert hp["backup"].sum() < 0.1 * hp["total"].sum()


def test_ground_source_has_no_backup(wx):
    hp = heat_pump_profile(wx["temp_c"], HeatPumpParams(kind="ground_source", q_nom_kw=12))
    assert hp["backup"].sum() == 0


# --- EV --------------------------------------------------------------------
def test_ev_energy_and_timing(idx):
    ev = ev_charging_profile(idx, EVParams(), seed=4)
    assert 1200 < ev.sum() < 3000                   # kWh/yr home charging
    assert ev.max() <= 11.0 + 1e-9
    loc = ev.tz_convert("Europe/Stockholm")
    by_hour = loc.groupby(loc.index.hour).mean()
    assert 16 <= by_hour.idxmax() <= 20             # evening peak


def test_smart_charging_moves_load_to_night(idx):
    smart = ev_charging_profile(idx, EVParams(smart=True), seed=4)
    loc = smart.tz_convert("Europe/Stockholm")
    by_hour = loc.groupby(loc.index.hour).mean()
    assert by_hour[18] < 0.2 * by_hour[23]


# --- PV --------------------------------------------------------------------
def test_pv_sign_and_yield(wx):
    pv = pv_profile(wx["pv_kw_per_kwp"], 10.0)
    assert (pv <= 0).all()
    assert 8000 < -pv.sum() < 11000


# --- neighbourhood ---------------------------------------------------------
def test_neighbourhood_shapes_and_shares(wx):
    nb = build_neighbourhood(Scenario(n_houses=40, hp_share=1.0, ev_share=0.0, pv_share=0.0, seed=3), wx)
    assert nb.p_kw.shape == (8760, 40)
    assert nb.houses.has_hp.all() and not nb.houses.has_ev.any() and not nb.houses.has_pv.any()
    assert nb.total_kw.idxmax().month in (1, 2, 12)      # winter peak


def test_electrification_raises_peak_and_pv_creates_export(wx):
    none = build_neighbourhood(Scenario(hp_share=0, ev_share=0, pv_share=0, seed=1), wx)
    full = build_neighbourhood(Scenario(hp_share=0.8, ev_share=0.6, pv_share=0.5, seed=1), wx)
    assert full.total_kw.max() > 2 * none.total_kw.max()
    assert full.total_kw.min() < 0                      # midsummer noon export
    assert none.total_kw.min() > 0
