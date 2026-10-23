"""Checks on the IEC 60076-7 thermal model: analytic limits, the standard's own
worked example (Annex C), and assertions chosen to kill the mutants that
survived the first suite (review of 21 Oct 2026)."""

import json

import numpy as np
import pandas as pd
import pytest

from hotspotlv.thermal import (ThermalParams, ageing_rate, load_for_hot_spot, simulate,
                               steady_state_rises)


def _idx(n, freq="h"):
    return pd.date_range("2023-01-01", periods=n, freq=freq, tz="UTC")


def _const(n, K, ta):
    i = _idx(n)
    return pd.Series(float(K), index=i), pd.Series(float(ta), index=i)


# --- ageing law ---------------------------------------------------------------
def test_ageing_rate_doubles_every_6_k():
    assert ageing_rate(98.0) == pytest.approx(1.0)
    assert ageing_rate(104.0) == pytest.approx(2.0)
    assert ageing_rate(110.0) == pytest.approx(4.0)
    assert ageing_rate(80.0) == pytest.approx(1 / 8)
    assert ageing_rate(110.0, "upgraded") == pytest.approx(1.0)       # thermally upgraded paper: 110 °C reference
    assert ageing_rate(120.0, "upgraded") > 2.0
    with pytest.raises(ValueError):
        ageing_rate(100.0, "Kraft")


# --- IEC 60076-7:2005 Annex C worked example (Tables C.1 / C.2) ---------------
# 41 steps of 3 min, varying ambient 30.3 → 22.2 °C, load 0.81 → 1.73 → 0.86,
# medium-power ONAF-like constants, thermally upgraded paper. Transcribed from
# IS 2026-7:2009 (identical adoption of IEC 60076-7:2005).
ANNEX_C = ThermalParams(x=0.8, y=1.3, k11=0.5, k21=2.0, k22=2.0, tau_o_min=150.0, tau_w_min=7.0,
                        d_theta_or=45.0, d_theta_hr=35.0, R=8.0, paper="upgraded")
ANNEX_C_THETA_A = [30.3, 29.9, 29.8, 29.5, 29.6, 29.5, 29.5, 28.9, 29.0, 28.6, 28.0, 28.7, 27.8, 28.1,
                   27.9, 27.1, 26.9, 26.7, 27.2, 26.7, 26.9, 26.5, 26.2, 26.3, 25.4, 25.6, 25.3, 24.8,
                   24.5, 24.3, 24.1, 24.3, 24.1, 23.4, 23.6, 23.8, 23.1, 23.3, 23.1, 22.3, 22.2]
ANNEX_C_K = [0.81, 0.87, 0.88, 0.86, 0.90, 0.92, 0.95, 0.96, 0.97, 1.00, 1.70, 1.70, 1.73, 1.72,
             1.69, 1.68, 1.71, 1.69, 1.67, 1.68, 1.63, 1.59, 1.53, 1.49, 1.41, 1.38, 1.32, 1.28,
             1.21, 1.19, 0.87, 0.88, 0.87, 0.86, 0.85, 0.87, 0.83, 0.86, 0.85, 0.82, 0.86]
ANNEX_C_HOT_SPOT = [90.5, 91.6, 92.7, 93.2, 94.3, 95.6, 97.2, 98.6, 100.0, 101.6, 118.6, 132.1, 143.5,
                    152.4, 158.8, 163.6, 168.2, 171.5, 173.6, 175.7, 176.1, 175.6, 173.8, 171.5, 167.8,
                    164.3, 160.1, 156.0, 151.1, 146.8, 136.9, 129.1, 122.8, 117.5, 113.1, 110.0, 106.6,
                    104.5, 102.6, 100.4, 99.3]
ANNEX_C_LOSS_OF_LIFE_DAYS = 6.15      # Table C.2, L = 8851 min at step 40


def test_iec_annex_c_worked_example():
    idx = pd.date_range("2000-01-01 14:00", periods=len(ANNEX_C_K), freq="3min")
    r = simulate(pd.Series(ANNEX_C_K, index=idx), pd.Series(ANNEX_C_THETA_A, index=idx),
                 ANNEX_C, dt_min=3.0, step_min=3.0)
    diff = np.abs(r.hourly["theta_h"].to_numpy() - np.array(ANNEX_C_HOT_SPOT))
    assert diff.max() < 0.15                                          # Table C.2 is printed to 0.1 °C
    # life used over steps 1..40 (step 0 is the initial state in the table)
    lol_days = r.hourly["V_mean"].iloc[1:].sum() * 3 / 1440
    assert lol_days == pytest.approx(ANNEX_C_LOSS_OF_LIFE_DAYS, rel=0.01)
    assert r.hourly["theta_h_max"].max() == pytest.approx(176.1, abs=0.15)


# --- steady state ---------------------------------------------------------------
def test_rated_load_at_20c_gives_98c_hot_spot():
    K, ta = _const(48, 1.0, 20.0)
    r = simulate(K, ta)
    assert r.hourly.theta_h.iloc[-1] == pytest.approx(98.0, abs=1e-6)
    assert r.hourly.theta_o.iloc[-1] == pytest.approx(75.0, abs=1e-6)
    assert r.hourly.V_mean.iloc[-1] == pytest.approx(1.0, abs=1e-6)
    assert r.loss_of_life_days == pytest.approx(2.0, abs=1e-6)           # 48 h at V = 1
    assert r.equivalent_ageing_factor == pytest.approx(r.loss_of_life_days * 24 / 48)


def test_no_load_still_heats_the_oil():
    p = ThermalParams()
    d_o, d_h1, d_h2 = steady_state_rises(0.0, p)
    assert d_o == pytest.approx((1 / (1 + p.R)) ** p.x * p.d_theta_or)  # ≈ 9 K from no-load losses
    assert d_h1 == 0 and d_h2 == 0
    K, ta = _const(30, 1.0, -20.0)
    r = simulate(K, ta)
    assert r.hourly.theta_o.iloc[-1] == pytest.approx(35.0, abs=1e-6)   # −20 + 55: ambient is in θo
    assert r.summary()["max_top_oil_c"] == pytest.approx(35.0, abs=1e-6)


def test_cold_ambient_allows_overload_without_extra_ageing():
    # at −20 °C ambient the hot-spot stays below 98 °C up to ~130 % load:
    # 55·((1+8.5·1.3²)/9.5)^0.8 + 23·1.3^1.6 ≈ 81 + 35 = 116 K rise → 96 °C
    K, ta = _const(48, 1.3, -20.0)
    assert simulate(K, ta).hourly.theta_h.iloc[-1] < 98.0
    K, ta = _const(48, 1.35, -20.0)
    assert simulate(K, ta).hourly.theta_h.iloc[-1] > 98.0
    K, ta = _const(48, 1.3, 20.0)
    assert simulate(K, ta).hourly.theta_h.iloc[-1] > 120.0   # same load in summer: way past the 120 °C limit
    assert load_for_hot_spot(98, -20) == pytest.approx(1.32, abs=0.01)
    assert load_for_hot_spot(98, 20) == pytest.approx(1.00, abs=0.005)


# --- dynamics ---------------------------------------------------------------
def test_step_response_time_constants():
    p = ThermalParams()
    i = _idx(48)
    K = pd.Series([0.0] * 24 + [1.0] * 24, index=i)
    r = simulate(K, pd.Series(20.0, index=i), p)
    d0 = steady_state_rises(0.0, p)[0]
    rise_3h = r.hourly.theta_o.iloc[26] - 20            # after exactly τo = 3 h
    assert rise_3h == pytest.approx(d0 + (p.d_theta_or - d0) * (1 - np.exp(-1)), rel=0.02)
    assert r.hourly.theta_h.iloc[24] - r.hourly.theta_o.iloc[24] == pytest.approx(p.d_theta_hr, rel=0.01)
    assert r.hourly.theta_h.iloc[-1] == pytest.approx(98.0, abs=0.05)
    # the initial state comes from the FIRST hour (K = 0), not the last
    assert r.hourly.theta_h.iloc[0] == pytest.approx(20 + d0, abs=0.01)


def test_ambient_change_reaches_the_oil_with_the_oil_time_constant():
    # load constant, ambient steps −20 → +10 °C: the oil must NOT jump 30 K instantly
    i = _idx(48)
    K = pd.Series(0.5, index=i)
    ta = pd.Series([-20.0] * 24 + [10.0] * 24, index=i)
    r = simulate(K, ta)
    jump_1h = r.hourly.theta_o.iloc[24] - r.hourly.theta_o.iloc[23]
    assert 7 < jump_1h < 10                                              # 1 − e^(−1/3) = 28 % of 30 K
    assert r.hourly.theta_o.iloc[-1] - r.hourly.theta_o.iloc[23] == pytest.approx(30.0, abs=0.05)


def test_winding_time_constant_uses_k22():
    # freeze the oil (huge τo) and watch only the winding: after 1 h with τw = 60 min and
    # k22 = 2 the gradient has done 1 − e^(−1/2) = 39 % of the way, not 63 %
    p = ThermalParams(tau_o_min=1e9, tau_w_min=60.0, k22=2.0)
    i = _idx(26)
    K = pd.Series([0.0] * 2 + [1.0] * 24, index=i)
    r = simulate(K, pd.Series(20.0, index=i), p, dt_min=1.0)
    grad_1h = r.hourly.theta_h.iloc[2] - r.hourly.theta_o.iloc[2]
    assert grad_1h == pytest.approx(p.d_theta_hr * (1 - np.exp(-0.5)), rel=0.02)


def test_hot_spot_overshoot_with_k21_above_one():
    # medium-power constants (k21 = 2, k22 = 2) produce an overshoot whose decay follows τo/k22
    p = ThermalParams(k21=2.0, k22=2.0, tau_o_min=210, tau_w_min=10, y=1.3)
    i = _idx(30)
    K = pd.Series([0.0] * 6 + [1.0] * 24, index=i)
    r = simulate(K, pd.Series(20.0, index=i), p, dt_min=5)
    grad = r.hourly.theta_h - r.hourly.theta_o
    assert grad.iloc[6] > p.d_theta_hr * 1.3                           # overshoot in the first hour …
    t = 3 * 60.0                                                       # … and 3 h after the step the slow term has
    expected = p.d_theta_hr * (p.k21 - (p.k21 - 1) * (1 - np.exp(-t / (p.tau_o_min / p.k22))))
    assert grad.iloc[8] == pytest.approx(expected, rel=0.03)            # end of hour 8 = 3 h after the step; τo/k22 = 105 min
    assert grad.iloc[-1] == pytest.approx(p.d_theta_hr, rel=0.02)


def test_result_independent_of_step_size():
    i = _idx(72)
    rng = np.random.default_rng(0)
    K = pd.Series(rng.uniform(0.2, 1.3, 72), index=i)
    ta = pd.Series(rng.uniform(-20, 25, 72), index=i)
    a = simulate(K, ta, dt_min=2.0)
    b = simulate(K, ta, dt_min=0.5)
    assert np.abs(a.hourly.theta_h - b.hourly.theta_h).max() < 0.3
    assert a.loss_of_life_days == pytest.approx(b.loss_of_life_days, rel=0.02)


def test_quarter_hour_input_gives_the_same_physics():
    hourly = simulate(*_const(48, 1.0, 20.0))
    i15 = _idx(48 * 4, "15min")
    q = simulate(pd.Series(1.0, index=i15), pd.Series(20.0, index=i15), step_min=15.0)
    assert q.hourly.theta_h.iloc[-1] == pytest.approx(hourly.hourly.theta_h.iloc[-1], abs=1e-6)
    assert q.loss_of_life_days == pytest.approx(hourly.loss_of_life_days, abs=1e-6)
    assert len(q.worst_week()) == min(7 * 24 * 4, len(q.hourly))       # the window follows step_min (whole series if shorter)
    # a sub-step that does not divide the row is rounded DOWN (ceil on the count), never up
    r = simulate(*_const(3, 1.0, 20.0), dt_min=1.7)
    assert r.dt_min <= 1.7 and 60 / r.dt_min == pytest.approx(round(60 / r.dt_min))


# --- bookkeeping ---------------------------------------------------------------
def test_within_hour_max_is_above_end_of_hour_after_a_step_down():
    i = _idx(26)
    K = pd.Series([1.5] * 24 + [0.5] * 2, index=i)
    r = simulate(K, pd.Series(20.0, index=i))
    # the hour the load drops: the hot-spot starts high and falls → max ≫ end value,
    # and the max is the START value (end of the previous hour), whatever the sub-step
    assert r.hourly.theta_h_max.iloc[24] > r.hourly.theta_h.iloc[24] + 10
    assert r.hourly.theta_h_max.iloc[24] == pytest.approx(r.hourly.theta_h.iloc[23], abs=1e-9)
    fine = simulate(K, pd.Series(20.0, index=i), dt_min=0.5)
    assert fine.hourly.theta_h_max.iloc[24] == pytest.approx(r.hourly.theta_h_max.iloc[24], abs=0.05)
    assert (r.hourly.theta_h_max >= r.hourly.theta_h - 1e-9).all()
    # EAF is the MEAN ageing rate, not the median (the two differ for a varying load)
    assert r.equivalent_ageing_factor == pytest.approx(r.hourly.V_mean.mean())
    assert r.equivalent_ageing_factor != pytest.approx(r.hourly.V_mean.median())
    # strict '>' in the limit counts: a step whose max equals the limit does not count
    assert r.steps_hot_spot_above(r.hourly.theta_h_max.max()) == 0
    assert r.summary()["max_hot_spot_time"] == r.hourly.theta_h_max.idxmax().isoformat()


def test_time_above_a_limit_is_time_not_steps_touched():
    """Catches: counting the steps whose max (start value included) is above — an excursion ending on a step
    boundary was counted one extra hour (round-2 study review, 23 Oct 2026: 2 h above 140 °C were 14 min)."""
    i = _idx(26)
    K = pd.Series([1.5] * 24 + [0.5] * 2, index=i)
    r = simulate(K, pd.Series(20.0, index=i))
    # the hour the load drops starts above 120 °C (its max says so) but falls below within minutes
    assert r.hourly.theta_h_max.iloc[24] > 120 and r.hourly.theta_h.iloc[24] < 120
    assert r.steps_hot_spot_above(120) == r.hours_hot_spot_above(120) // 1 + 1      # one step touched, not timed
    assert 0 < r.hourly.h_above_120.iloc[24] < 1                                     # part of that hour only
    assert r.hours_hot_spot_above(120) < r.steps_hot_spot_above(120)
    fine = simulate(K, pd.Series(20.0, index=i), dt_min=0.25)
    assert fine.hours_hot_spot_above(120) == pytest.approx(r.hours_hot_spot_above(120), abs=2 / 60)
    assert r.hours_hot_spot_above(98) >= r.hours_hot_spot_above(120) >= r.hours_hot_spot_above(140)
    with pytest.raises(ValueError, match="counted"):
        r.hours_hot_spot_above(110)
    # constant above: every minute counts
    c = simulate(*_const(24, 1.5, 20.0))
    assert c.hours_hot_spot_above(98) == pytest.approx(24.0)


def test_life_bookkeeping_and_limits():
    i = _idx(26)
    K = pd.Series([1.0] * 2 + [1.5] * 24, index=i)
    r = simulate(K, pd.Series(20.0, index=i))
    assert r.hourly.life_used_h.is_monotonic_increasing
    assert r.hourly.life_used_h.iloc[-1] == pytest.approx(r.hourly.V_mean.sum())
    s = r.summary()
    assert s["steps_hot_spot_above_120"] > 0
    assert s["steps_hot_spot_above_120"] == int((r.hourly.theta_h_max > 120).sum())   # strict >
    assert s["max_hot_spot_c"] == pytest.approx(r.hourly.theta_h_max.max())
    json.dumps(s)                                                      # app-friendly


def test_worst_week_is_the_true_maximum_window():
    i = _idx(24 * 21)
    K = pd.Series(0.5, index=i)
    K.iloc[24 * 10: 24 * 12] = 1.4                                     # hot spell in week 2
    r = simulate(K, pd.Series(20.0, index=i))
    ww = r.worst_week()
    assert len(ww) == 24 * 7
    assert ww.index[0] <= i[24 * 10] <= ww.index[-1]                   # window contains the spell
    rolling = r.hourly.V_mean.rolling(24 * 7).sum()
    assert ww.V_mean.sum() == pytest.approx(rolling.max())
    short = simulate(*_const(26, 1.0, 20.0)).worst_week()
    assert len(short) == 26


# --- validation ---------------------------------------------------------------
def test_validation():
    K, ta = _const(5, 1.0, 20.0)
    with pytest.raises(ValueError):
        simulate(K, ta, dt_min=3.0)                       # > τw/2
    with pytest.raises(ValueError):
        simulate(K, ta, dt_min=0.0)
    with pytest.raises(ValueError):
        simulate(K, ta, dt_min=1e-9)                      # would be 6e10 sub-steps → refused, not a hang
    for bad_step in (0.0, -60.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            simulate(K.iloc[:1], ta.iloc[:1], step_min=bad_step)
    # microsecond-resolution timestamps (pandas 3 default) are hourly too
    us = K.index.as_unit("us")
    simulate(pd.Series(1.0, index=us), pd.Series(20.0, index=us))
    with pytest.raises(ValueError):
        simulate(K.iloc[:4], ta)
    with pytest.raises(ValueError):
        simulate(-K, ta)
    with pytest.raises(ValueError):
        simulate(5 * K, ta)                               # absurd load
    with pytest.raises(ValueError):
        simulate(K.iloc[:0], ta.iloc[:0])                 # empty
    i15 = _idx(8, "15min")
    with pytest.raises(ValueError):
        simulate(pd.Series(1.0, index=i15), pd.Series(20.0, index=i15))   # 15-min data declared as hourly
    rev = pd.Series(1.0, index=K.index[::-1])
    with pytest.raises(ValueError):
        simulate(rev, rev)
    for bad in (dict(paper="nomex"), dict(tau_w_min=float("nan")), dict(y=-1), dict(x=0), dict(k21=0.5)):
        with pytest.raises(ValueError):
            ThermalParams(**bad)
    with pytest.raises(ValueError):
        ThermalParams.for_transformer(630)
    with pytest.raises(ValueError):
        simulate(K, ta, ThermalParams(tau_o_min=0.9))     # unstable sub-step
    assert ThermalParams.for_transformer(800).R == pytest.approx(6000 / 585)


def test_kiosk_enclosure_correction():
    from hotspotlv.thermal import kiosk_extra_top_oil_rise_k
    assert kiosk_extra_top_oil_rise_k(500) == pytest.approx(7.5)          # Table 6: 15 K air → half to top-oil
    assert kiosk_extra_top_oil_rise_k(250) == pytest.approx(5.0)
    assert kiosk_extra_top_oil_rise_k(800) == pytest.approx(10.0)         # held at the 750 kVA end
    kiosk = ThermalParams.for_transformer(500)                             # Swedish default: kiosk
    outdoor = ThermalParams.for_transformer(500, enclosure="outdoor")
    assert kiosk.d_theta_or == pytest.approx(outdoor.d_theta_or + 7.5)
    assert kiosk.enclosure_rise_k == 7.5 and outdoor.enclosure_rise_k == 0
    K, ta = _const(48, 1.0, 20.0)
    assert simulate(K, ta, kiosk).hourly.theta_h.iloc[-1] == pytest.approx(105.5, abs=1e-6)
    assert simulate(K, ta, kiosk).hourly.V_mean.iloc[-1] == pytest.approx(2 ** (7.5 / 6), rel=1e-6)   # ≈ ×2.4
    with pytest.raises(ValueError):
        ThermalParams(enclosure="garage")
