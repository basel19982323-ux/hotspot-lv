"""
IEC 60076-7 thermal model of an oil-immersed transformer — the heart of the tool.

The picture — two buckets of heat, one inside the other:

        ambient θa (outside air, from SMHI)
        ┌──────────────────────────────────────┐
        │  OIL   θo = θa + Δθo                 │   slow bucket: τo ≈ 3 h
        │   ┌──────────────────────────────┐   │
        │   │ WINDING hot-spot             │   │   fast bucket: τw ≈ 4 min
        │   │ θh = θo + Δθh                │   │
        │   └──────────────────────────────┘   │
        └──────────────────────────────────────┘
        heat in  ∝ losses ∝ (1 + R·K²)        K = load / rated load

Load heats the oil slowly and the winding quickly; the hot-spot is where the
paper insulation ages. IEC 60076-7 (2018), clause 8.2.3 "difference equation
solution", gives three update rules that we integrate in small steps Δt:

    top-oil rise:      Δθo  += Δt/(k11·τo) · { [ (1+R·K²)/(1+R) ]^x · Δθor − Δθo }
    hot-spot, fast:    Δθh1 += Δt/(k22·τw) · { k21 · K^y · Δθhr − Δθh1 }
    hot-spot, slow:    Δθh2 += Δt/(τo/k22) · { (k21−1) · K^y · Δθhr − Δθh2 }
    θo = θa + Δθo ;  θh = θo + Δθh1 − Δθh2

and the ageing rate of the paper relative to 98 °C (non-thermally-upgraded
kraft paper, the normal case for distribution transformers):

    V = 2^((θh − 98)/6)      → 98 °C: 1.0 ;  104 °C: 2× ;  110 °C: 4× ;  80 °C: 1/8

Loss of life L = Σ V·Δt. At V = 1 the insulation uses one day of life per day;
L over a year expressed in "days of life used" is the number we report.

Constants: IEC 60076-7:2018 Table 5, column "Distribution transformer ONAN".
Δθor (top-oil rise at rated load) and Δθhr (hot-spot-to-top-oil gradient at
rated load) are the IEC 60076-2 design limits (60 K top-oil rise is the limit;
55 K + 23 K = 78 K hot-spot rise) — a real unit usually runs cooler; both are
parameters. R = load loss / no-load loss at rated load (Ecodesign Tier 2).

Stated limits: generic constants (manufacturer values are rarely published for
small units); no oil-viscosity correction in cold oil; hourly load held
constant within each hour; ambient = air temperature at the SMHI station
(a kiosk substation runs a few K warmer in summer).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from hotspotlv.feeder import TRANSFORMERS

REF_HOT_SPOT_C = 98.0        # IEC: normal ageing rate 1.0 at 98 °C (kraft paper)
IEC_LIMITS_C = {             # IEC 60076-7 Table 2/3, distribution transformers
    "normal_cyclic_hot_spot": 120.0,
    "long_time_emergency_hot_spot": 140.0,
    "short_time_emergency_hot_spot": 160.0,
    "top_oil": 105.0,
}


@dataclass(frozen=True)
class ThermalParams:
    """IEC 60076-7 Table 5 — distribution transformer, ONAN."""
    x: float = 0.8            # oil exponent
    y: float = 1.6            # winding exponent
    k11: float = 1.0
    k21: float = 1.0
    k22: float = 2.0
    tau_o_min: float = 180.0  # oil time constant
    tau_w_min: float = 4.0    # winding time constant
    d_theta_or: float = 55.0  # top-oil rise at rated load, K
    d_theta_hr: float = 23.0  # hot-spot-to-top-oil gradient at rated load, K
    R: float = 3900 / 459     # load loss / no-load loss at rated load (500 kVA Tier 2)
    paper: str = "kraft"      # "kraft" (V = 2^((θh−98)/6)) or "upgraded" (Arrhenius, 110 °C ref)

    def __post_init__(self):
        if self.paper not in ("kraft", "upgraded"):
            raise ValueError("paper must be 'kraft' or 'upgraded'")
        if min(self.tau_o_min, self.tau_w_min, self.k11, self.k22, self.d_theta_or, self.d_theta_hr, self.R) <= 0:
            raise ValueError("time constants, k11, k22, rises and R must be positive")

    @classmethod
    def for_transformer(cls, kva: int, **changes) -> "ThermalParams":
        tr = TRANSFORMERS[kva]
        return replace(cls(R=tr["p_load_w"] / tr["p_noload_w"]), **changes)


def ageing_rate(theta_h_c, paper: str = "kraft"):
    """Relative ageing rate V of the paper (1.0 = normal, IEC 60076-7 eq. 2 / 3)."""
    th = np.asarray(theta_h_c, dtype=float)
    if paper == "kraft":
        return 2.0 ** ((th - REF_HOT_SPOT_C) / 6.0)
    return np.exp(15000.0 / (110.0 + 273.0) - 15000.0 / (th + 273.0))   # thermally upgraded


def steady_state(K: float, theta_a: float, p: ThermalParams) -> tuple[float, float, float]:
    """(Δθo, Δθh1, Δθh2) after an infinitely long time at constant load and ambient."""
    d_o = ((1 + p.R * K**2) / (1 + p.R)) ** p.x * p.d_theta_or
    d_h1 = p.k21 * K**p.y * p.d_theta_hr
    d_h2 = (p.k21 - 1) * K**p.y * p.d_theta_hr
    return d_o, d_h1, d_h2


@dataclass
class ThermalResult:
    hourly: pd.DataFrame      # theta_o, theta_h (end of hour), theta_h_max, V_mean, life_used_h (cumulative)
    params: ThermalParams
    dt_min: float

    @property
    def loss_of_life_days(self) -> float:
        """Days of insulation life used over the simulated period (365 ≈ 'one normal year')."""
        return float(self.hourly["life_used_h"].iloc[-1] / 24)

    @property
    def equivalent_ageing_factor(self) -> float:
        """Mean ageing rate over the period: 1.0 = ages exactly as designed."""
        return float(self.hourly["V_mean"].mean())

    def hours_hot_spot_above(self, limit_c: float) -> int:
        return int((self.hourly["theta_h_max"] > limit_c).sum())

    def summary(self) -> dict:
        h = self.hourly
        return {
            "loss_of_life_days": self.loss_of_life_days,
            "equivalent_ageing_factor": self.equivalent_ageing_factor,
            "max_hot_spot_c": float(h["theta_h_max"].max()),
            "max_hot_spot_time": h["theta_h_max"].idxmax(),
            "max_top_oil_c": float(h["theta_o"].max()),
            "hours_hot_spot_above_98": self.hours_hot_spot_above(98),
            "hours_hot_spot_above_110": self.hours_hot_spot_above(110),
            "hours_hot_spot_above_120": self.hours_hot_spot_above(IEC_LIMITS_C["normal_cyclic_hot_spot"]),
            "hours_hot_spot_above_140": self.hours_hot_spot_above(IEC_LIMITS_C["long_time_emergency_hot_spot"]),
        }

    def worst_week(self) -> pd.DataFrame:
        """The 7-day window with the most life used."""
        window = min(24 * 7, len(self.hourly))
        rolling = self.hourly["V_mean"].rolling(window).sum()   # life used per window, hours
        end = rolling.idxmax()
        return self.hourly.loc[:end].iloc[-window:]


def simulate(
    K: pd.Series,
    theta_a: pd.Series,
    params: ThermalParams = ThermalParams(),
    dt_min: float = 2.0,
    start_from_steady_state: bool = True,
) -> ThermalResult:
    """Integrate the IEC 60076-7 difference equations.

    K       : load factor per hour (S / S_rated), hourly index
    theta_a : ambient temperature per hour, °C, same index
    dt_min  : integration step; IEC requires Δt ≤ τw/2 (default 2 min for τw = 4 min)

    Load and ambient are held constant within each hour. Returns hourly results:
    the end-of-hour temperatures, the max hot-spot within the hour, the mean
    ageing rate within the hour and the cumulative life used (hours).
    """
    if not K.index.equals(theta_a.index):
        raise ValueError("K and theta_a must share the same hourly index")
    if dt_min > params.tau_w_min / 2:
        raise ValueError(f"dt_min must be <= tau_w/2 = {params.tau_w_min / 2} min (IEC 60076-7)")
    k = K.to_numpy(dtype=float)
    ta = theta_a.to_numpy(dtype=float)
    if (k < 0).any() or not np.isfinite(k).all() or not np.isfinite(ta).all():
        raise ValueError("K must be >= 0 and finite, theta_a finite")

    n_sub = int(round(60 / dt_min))
    dt = 60.0 / n_sub                      # exact sub-step in minutes
    p = params
    a_o = dt / (p.k11 * p.tau_o_min)
    a_h1 = dt / (p.k22 * p.tau_w_min)
    a_h2 = dt / (p.tau_o_min / p.k22)

    if start_from_steady_state:
        d_o, d_h1, d_h2 = steady_state(k[0], ta[0], p)
    else:
        d_o, d_h1, d_h2 = 0.0, 0.0, 0.0

    n = len(k)
    theta_o = np.empty(n)
    theta_h_end = np.empty(n)
    theta_h_max = np.empty(n)
    v_mean = np.empty(n)
    # precompute the hourly forcing terms (constant within the hour)
    force_o = ((1 + p.R * k**2) / (1 + p.R)) ** p.x * p.d_theta_or
    force_h = k**p.y * p.d_theta_hr

    for i in range(n):
        fo, fh, t_amb = force_o[i], force_h[i], ta[i]
        h_max, v_sum = -np.inf, 0.0
        for _ in range(n_sub):
            d_o += a_o * (fo - d_o)
            d_h1 += a_h1 * (p.k21 * fh - d_h1)
            d_h2 += a_h2 * ((p.k21 - 1) * fh - d_h2)
            th = t_amb + d_o + d_h1 - d_h2
            h_max = th if th > h_max else h_max
            v_sum += float(ageing_rate(th, p.paper))
        theta_o[i] = t_amb + d_o
        theta_h_end[i] = th
        theta_h_max[i] = h_max
        v_mean[i] = v_sum / n_sub

    hourly = pd.DataFrame({
        "theta_o": theta_o, "theta_h": theta_h_end, "theta_h_max": theta_h_max,
        "V_mean": v_mean, "life_used_h": np.cumsum(v_mean),   # each hour at rate V uses V hours
    }, index=K.index)
    return ThermalResult(hourly, params, dt)


if __name__ == "__main__":
    import time
    # 1. sanity: rated load at 20 °C ambient → hot-spot 98 °C → ageing rate 1
    idx = pd.date_range("2023-01-01", periods=48, freq="h", tz="UTC")
    r = simulate(pd.Series(1.0, index=idx), pd.Series(20.0, index=idx))
    print(f"K=1, 20 °C: hot-spot {r.hourly.theta_h.iloc[-1]:.1f} °C, V {r.hourly.V_mean.iloc[-1]:.3f}")
    # 2. step 0 → 1 at 20 °C: after τo (3 h) the oil has done 63 % of the way from
    #    its no-load rise (≈ 9 K — no-load losses heat the oil even at zero load) to 55 K
    K = pd.Series([0.0] * 24 + [1.0] * 24, index=idx)
    r = simulate(K, pd.Series(20.0, index=idx))
    d0 = steady_state(0.0, 20.0, ThermalParams())[0]
    print(f"step: no-load oil rise {d0:.1f} K; after 3 h = {r.hourly.theta_o.iloc[26] - 20:.1f} K "
          f"(expected {d0 + (55 - d0) * 0.632:.1f}), after 24 h = {r.hourly.theta_o.iloc[-1] - 20:.1f} K")
    # 3. the Västerås year, "today" scenario, 500 kVA
    from hotspotlv.feeder import build_feeder
    from hotspotlv.neighbourhood import Scenario, build_neighbourhood
    from hotspotlv.timeseries import run_year
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario("today"), wx)
    yr = run_year(build_feeder(), nb.cons_kw, nb.gen_kw)
    t0 = time.perf_counter()
    th = simulate(yr.hourly["loading_pct"] / 100, wx["temp_c"], ThermalParams.for_transformer(500))
    print(f"thermal year in {time.perf_counter() - t0:.1f} s")
    for kname, v in th.summary().items():
        print(f"  {kname:28s} {v}")
    ww = th.worst_week()
    print(f"  worst week starts {ww.index[0]:%d %b}, life used {ww.V_mean.sum() / 24:.2f} days, "
          f"max hot-spot {ww.theta_h_max.max():.1f} °C")
