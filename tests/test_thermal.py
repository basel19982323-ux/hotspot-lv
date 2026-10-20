"""Checks on the IEC 60076-7 thermal model against things that must hold analytically."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.thermal import ThermalParams, ageing_rate, simulate, steady_state


def _idx(hours):
    return pd.date_range("2023-01-01", periods=hours, freq="h", tz="UTC")


def _const(hours, K, ta):
    i = _idx(hours)
    return pd.Series(float(K), index=i), pd.Series(float(ta), index=i)


# --- ageing law ---------------------------------------------------------------
def test_ageing_rate_doubles_every_6_k():
    assert ageing_rate(98.0) == pytest.approx(1.0)
    assert ageing_rate(104.0) == pytest.approx(2.0)
    assert ageing_rate(110.0) == pytest.approx(4.0)
    assert ageing_rate(80.0) == pytest.approx(1 / 8)
    assert ageing_rate(110.0, "upgraded") == pytest.approx(1.0)       # thermally upgraded paper: 110 °C reference
    assert ageing_rate(120.0, "upgraded") > 2.0


# --- steady state ---------------------------------------------------------------
def test_rated_load_at_20c_gives_98c_hot_spot():
    K, ta = _const(48, 1.0, 20.0)
    r = simulate(K, ta)
    assert r.hourly.theta_h.iloc[-1] == pytest.approx(98.0, abs=1e-6)
    assert r.hourly.theta_o.iloc[-1] == pytest.approx(75.0, abs=1e-6)
    assert r.hourly.V_mean.iloc[-1] == pytest.approx(1.0, abs=1e-6)
    assert r.loss_of_life_days == pytest.approx(2.0, abs=1e-6)           # 48 h at V = 1


def test_no_load_still_heats_the_oil():
    p = ThermalParams()
    d_o, d_h1, d_h2 = steady_state(0.0, 20.0, p)
    assert d_o == pytest.approx((1 / (1 + p.R)) ** p.x * p.d_theta_or)  # ≈ 9 K from no-load losses
    assert d_h1 == 0 and d_h2 == 0


def test_cold_ambient_allows_overload_without_extra_ageing():
    # at −20 °C ambient the hot-spot stays below 98 °C up to ~130 % load:
    # 55·((1+8.5·1.3²)/9.5)^0.8 + 23·1.3^1.6 ≈ 81 + 35 = 116 K rise → 96 °C
    K, ta = _const(48, 1.3, -20.0)
    assert simulate(K, ta).hourly.theta_h.iloc[-1] < 98.0
    K, ta = _const(48, 1.35, -20.0)
    assert simulate(K, ta).hourly.theta_h.iloc[-1] > 98.0
    K, ta = _const(48, 1.3, 20.0)
    assert simulate(K, ta).hourly.theta_h.iloc[-1] > 120.0   # same load in summer: way past the 120 °C limit


# --- dynamics ---------------------------------------------------------------
def test_step_response_time_constants():
    p = ThermalParams()
    i = _idx(48)
    K = pd.Series([0.0] * 24 + [1.0] * 24, index=i)
    ta = pd.Series(20.0, index=i)
    r = simulate(K, ta, p)
    d0 = steady_state(0.0, 20.0, p)[0]
    # top-oil rise after exactly τo = 3 h: 63.2 % of the way from the no-load rise to 55 K
    rise_3h = r.hourly.theta_o.iloc[26] - 20
    assert rise_3h == pytest.approx(d0 + (p.d_theta_or - d0) * (1 - np.exp(-1)), rel=0.02)
    # the winding (τw = 4 min) is at its steady gradient well within the first hour
    assert r.hourly.theta_h.iloc[24] - r.hourly.theta_o.iloc[24] == pytest.approx(p.d_theta_hr, rel=0.01)
    # after 24 h everything has settled
    assert r.hourly.theta_h.iloc[-1] == pytest.approx(98.0, abs=0.05)


def test_hot_spot_overshoot_with_k21_above_one():
    # medium-power constants (IEC Table 5: k21 = 2, k22 = 2) produce a hot-spot overshoot after a step
    p = ThermalParams(k21=2.0, k22=2.0, tau_o_min=210, tau_w_min=10, y=1.3)
    i = _idx(30)
    K = pd.Series([0.0] * 6 + [1.0] * 24, index=i)
    r = simulate(K, pd.Series(20.0, index=i), p, dt_min=5)
    grad = r.hourly.theta_h - r.hourly.theta_o
    assert grad.iloc[6] > p.d_theta_hr * 1.3          # overshoot in the first hour …
    assert grad.iloc[-1] == pytest.approx(p.d_theta_hr, rel=0.02)   # … decays to the rated gradient


def test_result_independent_of_step_size():
    i = _idx(72)
    rng = np.random.default_rng(0)
    K = pd.Series(rng.uniform(0.2, 1.3, 72), index=i)
    ta = pd.Series(rng.uniform(-20, 25, 72), index=i)
    a = simulate(K, ta, dt_min=2.0)
    b = simulate(K, ta, dt_min=0.5)
    assert np.abs(a.hourly.theta_h - b.hourly.theta_h).max() < 0.3
    assert a.loss_of_life_days == pytest.approx(b.loss_of_life_days, rel=0.02)


def test_hourly_max_and_life_bookkeeping():
    i = _idx(26)
    K = pd.Series([1.0] * 2 + [1.5] * 24, index=i)
    r = simulate(K, pd.Series(20.0, index=i))
    assert (r.hourly.theta_h_max >= r.hourly.theta_h - 1e-9).all()
    assert r.hourly.life_used_h.is_monotonic_increasing
    assert r.hourly.life_used_h.iloc[-1] == pytest.approx(r.hourly.V_mean.sum())
    assert r.summary()["hours_hot_spot_above_120"] > 0
    assert len(r.worst_week()) == min(24 * 7, 26)


def test_validation():
    K, ta = _const(5, 1.0, 20.0)
    with pytest.raises(ValueError):
        simulate(K, ta, dt_min=3.0)                       # > τw/2
    with pytest.raises(ValueError):
        simulate(K.iloc[:4], ta)
    with pytest.raises(ValueError):
        simulate(-K, ta)
    with pytest.raises(ValueError):
        ThermalParams(paper="nomex")
    p = ThermalParams.for_transformer(800)
    assert p.R == pytest.approx(6000 / 585)
