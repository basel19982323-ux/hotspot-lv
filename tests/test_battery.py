"""Peak-shaving battery: energy must balance, the kVA cap must hold while energy lasts,
charging must never push the transformer above the cap, sizing is by simulation."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.battery import BatteryParams, apply_battery, dispatch, hours_above, size_by_simulation
from hotspotlv.timeseries import YearResult


def _series(values, freq="h"):
    idx = pd.date_range("2023-01-01", periods=len(values), freq=freq, tz="UTC")
    return pd.Series(np.asarray(values, float), index=idx)


def _year(p, q):
    s = np.hypot(p, q)
    h = pd.DataFrame({"s_kva": s, "loading_pct": s / 500 * 100, "p_kw": p, "vmin_pu": 1.0, "vmax_pu": 1.0,
                      "losses_kw": 0.0, "line_max_pct": 0.0}, index=p.index)
    return YearResult(h, 500.0)


def test_dispatch_shaves_peak_and_recharges():
    p = _series([100] * 6 + [500, 500, 500] + [100] * 15)          # 3-hour peak of 500 kW, no Q
    q = _series([0] * 24)
    b = BatteryParams(power_kw=150, energy_kwh=400, cap_kva=400)
    d = dispatch(p, q, b)
    assert (d["p_after"].iloc[6:9] <= 400).all()                    # cap holds for the 3 hours (300 kWh needed)
    assert d["discharge"].iloc[6:9].sum() == pytest.approx(300, rel=1e-6)
    assert d["soc"].iloc[8] == pytest.approx(400 - 300 / np.sqrt(0.9), rel=1e-6)
    assert d["soc"].iloc[-1] == pytest.approx(400)                  # refilled from the 100 kW hours
    assert (d["p_after"] < 400).all()                               # charging stays strictly under the cap
    assert d["discharge"].sum() / d["charge"].sum() == pytest.approx(0.9, rel=1e-6)   # round trip


def test_battery_runs_empty():
    p = _series([100] * 2 + [500] * 10 + [100] * 12)                 # 10 h × 100 kW above a 400 kVA cap
    q = _series([0] * 24)
    b = BatteryParams(power_kw=100, energy_kwh=200, cap_kva=400)
    d = dispatch(p, q, b)
    assert d["discharge"].sum() == pytest.approx(200 * np.sqrt(0.9))  # all stored energy, minus discharge losses
    assert d["p_after"].iloc[2:12].max() == pytest.approx(500)        # cap is lost once empty
    assert d["soc"].min() >= -1e-9


def test_cap_is_on_apparent_power():
    # P = 300 kW with Q = 300 kvar → S = 424 kVA > cap 400 although P is far below it
    p = _series([300.0] * 3)
    q = _series([300.0] * 3)
    d = dispatch(p, q, BatteryParams(power_kw=100, energy_kwh=100, cap_kva=400))
    p_limit = np.sqrt(400**2 - 300**2)                               # 264.6 kW
    assert d["p_after"].iloc[0] == pytest.approx(p_limit, rel=1e-6)
    assert np.hypot(d["p_after"].iloc[0], 300) <= 400
    # charging with head-room: P = 100, Q = 300 → may charge up to P = 264.6, not to 400
    p2, q2 = _series([100.0] * 3), _series([300.0] * 3)
    d2 = dispatch(p2, q2, BatteryParams(power_kw=500, energy_kwh=5000, cap_kva=400, initial_soc=0.0))
    assert d2["p_after"].iloc[0] == pytest.approx(p_limit, rel=1e-6)
    assert np.hypot(d2["p_after"].iloc[0], 300) <= 400


def test_step_length_comes_from_the_index():
    # the same 3-hour peak in quarter hours must need the same energy
    p_h = _series([100] * 6 + [500] * 3 + [100] * 15)
    p_q = _series(np.repeat(p_h.to_numpy(), 4), freq="15min")
    b = BatteryParams(power_kw=150, energy_kwh=400, cap_kva=400)
    d_h = dispatch(p_h, _series([0] * 24), b)
    d_q = dispatch(p_q, _series([0] * 96, freq="15min"), b)
    assert d_q["discharge"].sum() * 0.25 == pytest.approx(d_h["discharge"].sum(), rel=1e-6)   # kWh equal
    assert d_q["soc"].min() == pytest.approx(d_h["soc"].min(), rel=1e-6)


def test_apply_battery_keeps_reactive_power_and_voltages():
    p = _series([100.0, 480.0, 480.0, 100.0])
    q = p * 0.3287
    yr = _year(p, q)
    yr.hourly["vmin_pu"] = [0.99, 0.95, 0.95, 0.99]
    out = apply_battery(yr, BatteryParams(power_kw=150, energy_kwh=400, cap_kva=450))
    assert out.hourly["s_kva"].iloc[1] == pytest.approx(450, rel=1e-6)   # cap on S holds
    assert out.hourly["s_kva"].iloc[1] <= 450
    assert out.hourly["p_kw"].iloc[1] == pytest.approx(np.sqrt(450**2 - q.iloc[1] ** 2), rel=1e-6)
    assert out.hourly["vmin_pu"].iloc[1] == 0.95                              # busbar battery: feeders untouched
    assert (out.hourly["loading_pct"] <= 90).all()
    assert "battery_kw" in out.hourly


def test_size_by_simulation_single_peak():
    p = _series([100] * 20 + [450, 470, 460, 420] + [100] * 24)
    yr = _year(p, _series([0] * 48))
    s = size_by_simulation(yr, cap_kva=400, energy_tolerance_kwh=1.0)
    assert s.feasible and s.hours_above_cap_after == 0
    assert s.params.power_kw == 70                                   # max excess
    # 200 kWh of excess, delivered through √0.9 discharge efficiency → ≈ 211 kWh stored, within tolerance
    assert s.params.energy_kwh == pytest.approx(200 / np.sqrt(0.9), abs=2)
    assert hours_above(apply_battery(yr, s.params), 400) == 0
    with pytest.raises(ValueError):
        size_by_simulation(yr, cap_kva=600)
    for bad in (dict(energy_tolerance_kwh=0), dict(energy_tolerance_kwh=float("nan")), dict(energy_max_kwh=0)):
        with pytest.raises(ValueError):
            size_by_simulation(yr, cap_kva=400, **bad)                 # would loop forever / do nothing
    q = _year(_series(np.repeat(p.to_numpy(), 4), freq="15min"), _series([0] * 192, freq="15min"))
    assert hours_above(q, 400) == pytest.approx(4.0)                 # hours, not quarter-hour rows


def test_size_by_simulation_reports_infeasible_cold_spell():
    # three consecutive days with 12 h above the cap and only 12 h to recharge at a small head-room
    day = [380] * 12 + [500] * 12                                    # cap 400: head-room 20 kW × 12 h = 240 kWh/day
    p = _series(day * 3 + [100] * 24)
    yr = _year(p, _series([0] * 96))
    s = size_by_simulation(yr, cap_kva=400)                          # default ceiling 1000 kWh
    assert not s.feasible                                            # 1200 kWh/day excess vs 240 kWh/day recharge
    assert s.params.energy_kwh == 1000 and s.hours_above_cap_after > 0
    big = size_by_simulation(yr, cap_kva=400, energy_max_kwh=5000)   # allowed to be huge: starts full, holds
    assert big.feasible and big.params.energy_kwh < 5000
    assert s.discharged_kwh_per_year > 0 and s.full_cycles_per_year > 0


def test_validation():
    for bad in (dict(power_kw=0, energy_kwh=10, cap_kva=400), dict(power_kw=float("nan"), energy_kwh=10, cap_kva=400),
                dict(power_kw=10, energy_kwh=10, cap_kva=400, initial_soc=2.0),
                dict(power_kw=10, energy_kwh=10, cap_kva=400, initial_soc=-1.0)):
        with pytest.raises(ValueError):
            BatteryParams(**bad)
    b = BatteryParams(power_kw=10, energy_kwh=10, cap_kva=400)
    with pytest.raises(ValueError):
        dispatch(_series([1, 2, 3]), _series([0, 0]), b)             # length mismatch
    with pytest.raises(ValueError):
        dispatch(_series([1, np.nan, 3]), _series([0, 0, 0]), b)
