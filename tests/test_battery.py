"""Peak-shaving battery: energy must balance, the kVA cap must hold while energy lasts,
and charging must never push the transformer above the cap."""

import numpy as np
import pandas as pd
import pytest

from hotspotlv.battery import BatteryParams, apply_battery, dispatch, size_for_cap
from hotspotlv.timeseries import YearResult


def _series(values):
    idx = pd.date_range("2023-01-01", periods=len(values), freq="h", tz="UTC")
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
    assert (d["p_after"].iloc[6:9] <= 400 + 1e-9).all()             # cap holds for the 3 hours (300 kWh needed)
    assert d["discharge"].iloc[6:9].sum() == pytest.approx(300)
    assert d["soc"].iloc[8] == pytest.approx(400 - 300 / np.sqrt(0.9))
    assert d["soc"].iloc[-1] == pytest.approx(400)                  # refilled from the 100 kW hours
    assert (d["p_after"] <= 400 + 1e-9).all()                       # charging never pushes above the cap
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
    b = BatteryParams(power_kw=100, energy_kwh=100, cap_kva=400)
    d = dispatch(p, q, b)
    p_limit = np.sqrt(400**2 - 300**2)                               # 264.6 kW
    assert d["p_after"].iloc[0] == pytest.approx(p_limit)
    assert np.hypot(d["p_after"].iloc[0], 300) == pytest.approx(400)
    # charging with head-room: P = 100, Q = 300 → may charge up to P = 264.6, not to 400
    p2, q2 = _series([100.0] * 3), _series([300.0] * 3)
    d2 = dispatch(p2, q2, BatteryParams(power_kw=500, energy_kwh=5000, cap_kva=400, initial_soc=0.0))
    assert d2["p_after"].iloc[0] == pytest.approx(p_limit)


def test_apply_battery_keeps_reactive_power_and_voltages():
    p = _series([100.0, 480.0, 480.0, 100.0])
    q = p * 0.3287
    yr = _year(p, q)
    yr.hourly["vmin_pu"] = [0.99, 0.95, 0.95, 0.99]
    out = apply_battery(yr, BatteryParams(power_kw=150, energy_kwh=400, cap_kva=450))
    assert out.hourly["s_kva"].iloc[1] == pytest.approx(450)                  # cap on S holds
    assert out.hourly["p_kw"].iloc[1] == pytest.approx(np.sqrt(450**2 - q.iloc[1] ** 2))
    assert out.hourly["vmin_pu"].iloc[1] == 0.95                              # busbar battery: feeders untouched
    assert (out.hourly["loading_pct"] <= 90 + 1e-9).all()
    assert "battery_kw" in out.hourly


def test_size_for_cap():
    p = _series([100] * 20 + [450, 470, 460, 420] + [100] * 24)
    yr = _year(p, _series([0] * 48))
    b = size_for_cap(yr, cap_kva=400)
    assert b.power_kw == 70                                          # max excess
    # the index is UTC; days are cut in local time, so 23:00 UTC (= 00:00 CET) falls on the next day
    assert b.energy_kwh == pytest.approx(np.ceil((50 + 70 + 60) / 0.9))
    with pytest.raises(ValueError):
        size_for_cap(yr, cap_kva=600)
    with pytest.raises(ValueError):
        BatteryParams(power_kw=0, energy_kwh=10, cap_kva=400)
