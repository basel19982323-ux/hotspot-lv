"""
IEC 60076-7 thermal model of an oil-immersed transformer — the heart of the tool.

The picture — two buckets of heat, one inside the other:

        ambient θa (outside air, from SMHI)
        ┌──────────────────────────────────────┐
        │  OIL   θo                            │   slow bucket: τo ≈ 3 h
        │   ┌──────────────────────────────┐   │
        │   │ WINDING hot-spot             │   │   fast bucket: τw ≈ 4 min
        │   │ θh = θo + Δθh                │   │
        │   └──────────────────────────────┘   │
        └──────────────────────────────────────┘
        heat in  ∝ losses ∝ (1 + R·K²)        K = load / rated load

Load heats the oil slowly and the winding quickly; the hot-spot is where the
paper insulation ages. IEC 60076-7 (2005 clause 8.2.3 / Annex C; 2018 Annex C)
gives the "difference equation solution" that we integrate in small steps Δt:

    top-oil (absolute):  θo   += Δt/(k11·τo) · { [ (1+R·K²)/(1+R) ]^x · Δθor − (θo − θa) }
    hot-spot, fast:      Δθh1 += Δt/(k22·τw) · { k21 · K^y · Δθhr − Δθh1 }
    hot-spot, slow:      Δθh2 += Δt/(τo/k22) · { (k21−1) · K^y · Δθhr − Δθh2 }
    θh = θo + Δθh1 − Δθh2

Note the first line integrates the ABSOLUTE oil temperature with θa inside the
bracket: a change of outdoor temperature reaches the oil with the same 3-hour
lag as a change of load. (The first version integrated the rise and added θa
outside — the review of 21 Oct 2026 caught it against the standard's own
Annex C worked example, which is now a regression test.)

Ageing rate of the paper relative to 98 °C (non-thermally-upgraded kraft paper,
the normal case for distribution transformers; eq. (2)):

    V = 2^((θh − 98)/6)      → 98 °C: 1.0 ;  104 °C: 2× ;  110 °C: 4× ;  80 °C: 1/8

Loss of life L = Σ V·Δt (eq. (4)). At V = 1 the insulation uses one day of life
per day; L over a year in "days of life used" is the number we report.

Constants: IEC 60076-7 Table 5 (2005) / Table 4 (2018), column "distribution
transformer ONAN": x 0.8, y 1.6, k11 1.0, k21 1.0, k22 2.0, τo 180 min,
τw 4 min. Δθor = 55 K and Δθhr = 23 K are the standard's own example
characteristics for a distribution ONAN unit (2005 Annex E / 2018 Annex H);
the standard's example uses R = 5, we use the Ecodesign Tier-2 loss ratio of
the chosen size (500 kVA: 3900/459 = 8.5) — a deliberate, stated deviation.

Enclosure: a Swedish nätstation is normally a prefabricated kiosk. IEC 60076-7
(2005 clause 8.3.2, Table 6, eq. 11) gives the extra air-temperature rise inside
a kiosk with one transformer — 250 kVA: 10 K, 500 kVA: 15 K, 750 kVA: 20 K —
and says the extra TOP-OIL rise is about half of that. `for_transformer(kva,
enclosure="kiosk")` therefore adds Table-6/2 to Δθor (+7.5 K for 500 kVA →
ageing ≈ ×2.4). `enclosure="outdoor"` leaves the bare constants.

Stated limits: generic constants (manufacturer values are rarely published for
small units); no oil-viscosity correction in cold oil, so at −20 °C the model
is optimistic; load held constant within each step; ambient = air temperature
at the SMHI station.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from hotspotlv.catalogue import transformer

REF_HOT_SPOT_C = 98.0        # IEC: normal ageing rate 1.0 at 98 °C (kraft paper)
K_MAX = 3.0                  # above 3 × rated the model (and the transformer) is meaningless
MAX_SUBSTEPS = 10_000        # per row; guards against a dt that would run forever
IEC_LIMITS_C = {             # IEC 60076-7 Table 4 (2005) — distribution transformers
    "normal_cyclic_hot_spot": 120.0,
    "long_time_emergency_hot_spot": 140.0,   # no short-time-emergency limit is set for distribution units
    "normal_cyclic_top_oil": 105.0,
    "long_time_emergency_top_oil": 115.0,
}
IEC_LIMITS_K = {"normal_cyclic": 1.5, "long_time_emergency": 1.8, "short_time_emergency": 2.0}
PAPERS = ("kraft", "upgraded")
ENCLOSURES = ("outdoor", "kiosk")
# IEC 60076-7:2005 Table 6, "Kiosks", one transformer: extra AIR temperature rise (K) vs kVA.
# Linear between the points; held at the ends. Half of it is added to the top-oil rise (eq. 11).
KIOSK_AIR_RISE_K = {250: 10.0, 500: 15.0, 750: 20.0}


@dataclass(frozen=True)
class ThermalParams:
    """IEC 60076-7 thermal characteristics — defaults: distribution transformer, ONAN."""
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
    enclosure: str = "outdoor"
    enclosure_rise_k: float = 0.0   # extra top-oil rise already included in d_theta_or (for reporting)

    def __post_init__(self):
        if self.paper not in PAPERS:
            raise ValueError(f"paper must be one of {PAPERS}")
        vals = (self.x, self.y, self.k11, self.k21, self.k22, self.tau_o_min, self.tau_w_min,
                self.d_theta_or, self.d_theta_hr, self.R)
        if not all(math.isfinite(v) for v in vals):
            raise ValueError("all thermal parameters must be finite numbers")
        if min(self.x, self.y, self.k11, self.k22, self.tau_o_min, self.tau_w_min,
               self.d_theta_or, self.d_theta_hr, self.R) <= 0:
            raise ValueError("exponents, k11, k22, time constants, rises and R must be positive")
        if self.k21 < 1.0:
            raise ValueError("k21 must be >= 1 (IEC: 1.0 distribution … 2.0 large power)")
        if self.enclosure not in ENCLOSURES:
            raise ValueError(f"enclosure must be one of {ENCLOSURES}")

    @classmethod
    def for_transformer(cls, kva: int, enclosure: str = "kiosk", **changes) -> "ThermalParams":
        """Constants for a catalogue size; a kiosk (the Swedish norm) adds IEC Table 6 / 2 to the top-oil rise."""
        tr = transformer(kva)                 # raises a clear ValueError for unknown sizes
        rise = kiosk_extra_top_oil_rise_k(kva) if enclosure == "kiosk" else 0.0
        base = cls(R=tr["p_load_w"] / tr["p_noload_w"], enclosure=enclosure, enclosure_rise_k=rise)
        base = replace(base, d_theta_or=base.d_theta_or + rise)
        return replace(base, **changes)


def kiosk_extra_top_oil_rise_k(kva: float) -> float:
    """Half of the IEC Table 6 kiosk air rise, interpolated on kVA (eq. 11)."""
    sizes = np.array(sorted(KIOSK_AIR_RISE_K))
    return float(np.interp(kva, sizes, [KIOSK_AIR_RISE_K[k] for k in sizes]) / 2)


def ageing_rate(theta_h_c, paper: str = "kraft"):
    """Relative ageing rate V of the paper (1.0 = normal; IEC 60076-7 eq. (2) kraft, eq. (3) upgraded)."""
    if paper not in PAPERS:
        raise ValueError(f"paper must be one of {PAPERS}")
    th = np.asarray(theta_h_c, dtype=float)
    if paper == "kraft":
        return 2.0 ** ((th - REF_HOT_SPOT_C) / 6.0)
    return np.exp(15000.0 / (110.0 + 273.0) - 15000.0 / (th + 273.0))


def steady_state_rises(K: float, p: ThermalParams) -> tuple[float, float, float]:
    """(Δθo, Δθh1, Δθh2) after an infinitely long time at constant load K."""
    d_o = ((1 + p.R * K**2) / (1 + p.R)) ** p.x * p.d_theta_or
    d_h1 = p.k21 * K**p.y * p.d_theta_hr
    d_h2 = (p.k21 - 1) * K**p.y * p.d_theta_hr
    return d_o, d_h1, d_h2


def load_for_hot_spot(theta_h_c: float, theta_a_c: float, p: ThermalParams = ThermalParams()) -> float:
    """Steady-state load factor K at which the hot-spot reaches theta_h_c at ambient theta_a_c (bisection)."""
    target = theta_h_c - theta_a_c
    lo, hi = 0.0, K_MAX
    for _ in range(60):
        mid = (lo + hi) / 2
        d_o, d_h1, d_h2 = steady_state_rises(mid, p)
        if d_o + d_h1 - d_h2 < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass
class ThermalResult:
    hourly: pd.DataFrame      # per step: theta_o, theta_h (end), theta_h_max (within step), V_mean, life_used_h
    params: ThermalParams
    step_min: float
    dt_min: float

    @property
    def loss_of_life_days(self) -> float:
        """Days of insulation life used over the simulated period (365 ≈ 'one normal year')."""
        return float(self.hourly["life_used_h"].iloc[-1] / 24)

    @property
    def equivalent_ageing_factor(self) -> float:
        """Mean ageing rate over the period: 1.0 = ages exactly as designed."""
        return float(self.hourly["V_mean"].mean())

    def steps_hot_spot_above(self, limit_c: float) -> int:
        """Number of steps whose within-step MAX hot-spot exceeds the limit (IEC limits are instantaneous)."""
        return int((self.hourly["theta_h_max"] > limit_c).sum())

    def summary(self) -> dict:
        h = self.hourly
        return {
            "loss_of_life_days": self.loss_of_life_days,
            "equivalent_ageing_factor": self.equivalent_ageing_factor,
            "max_hot_spot_c": float(h["theta_h_max"].max()),
            "max_hot_spot_time": h["theta_h_max"].idxmax().isoformat(),
            "max_top_oil_c": float(h["theta_o"].max()),
            "steps_hot_spot_above_98": self.steps_hot_spot_above(98),
            "steps_hot_spot_above_110": self.steps_hot_spot_above(110),
            "steps_hot_spot_above_120": self.steps_hot_spot_above(IEC_LIMITS_C["normal_cyclic_hot_spot"]),
            "steps_hot_spot_above_140": self.steps_hot_spot_above(IEC_LIMITS_C["long_time_emergency_hot_spot"]),
            "steps_top_oil_above_105": int((h["theta_o"] > IEC_LIMITS_C["normal_cyclic_top_oil"]).sum()),
        }

    def worst_week(self) -> pd.DataFrame:
        """The 7-day window with the most life used (whole series if shorter than a week)."""
        window = min(int(round(7 * 24 * 60 / self.step_min)), len(self.hourly))
        rolling = self.hourly["V_mean"].rolling(window).sum()
        end = rolling.idxmax()
        return self.hourly.loc[:end].iloc[-window:]


def _check_index(index: pd.Index, step_min: float) -> None:
    if len(index) == 0:
        raise ValueError("empty input")
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("index must be a DatetimeIndex")
    if not (index.is_monotonic_increasing and index.is_unique):
        raise ValueError("index must be strictly increasing with no duplicates")
    if len(index) > 1:
        spacing = np.unique(np.diff(index.to_numpy()) / np.timedelta64(1, "m"))   # minutes, any time unit
        if len(spacing) != 1 or not math.isclose(spacing[0], step_min, rel_tol=1e-9):
            raise ValueError(f"index spacing must be exactly {step_min} min; got {spacing} min "
                             "(pass step_min= for other resolutions)")


def simulate(
    K: pd.Series,
    theta_a: pd.Series,
    params: ThermalParams = ThermalParams(),
    dt_min: float = 2.0,
    step_min: float = 60.0,
    start_from_steady_state: bool = True,
) -> ThermalResult:
    """Integrate the IEC 60076-7 difference equations.

    K        : load factor per step (S / S_rated)
    theta_a  : ambient temperature per step, °C, same index
    step_min : duration of one row of the input (60 for hourly data, 15 for smart-meter quarter hours)
    dt_min   : integration sub-step; IEC requires Δt ≤ τw/2 (default 2 min for τw = 4 min)

    Load and ambient are held constant within each step. Returns, per step: the
    end-of-step temperatures, the max hot-spot reached within the step (after
    the first sub-step), the mean ageing rate within the step and the
    cumulative life used in hours. With start_from_steady_state=False the
    transformer starts at ambient (cold) — allow a day of warm-up.
    """
    if not K.index.equals(theta_a.index):
        raise ValueError("K and theta_a must share the same index")
    _check_index(K.index, step_min)
    p = params
    if not (math.isfinite(step_min) and step_min > 0):
        raise ValueError("step_min must be a positive number of minutes")
    if not (math.isfinite(dt_min) and 0 < dt_min <= p.tau_w_min / 2):
        raise ValueError(f"dt_min must be within (0, tau_w/2 = {p.tau_w_min / 2}] min (IEC 60076-7)")
    n_sub = max(1, math.ceil(step_min / dt_min - 1e-9))
    if n_sub > MAX_SUBSTEPS:
        raise ValueError(f"dt_min too small: {n_sub} sub-steps per row (limit {MAX_SUBSTEPS})")
    dt = step_min / n_sub                     # the sub-step actually used (≤ dt_min)
    k = K.to_numpy(dtype=float)
    ta = theta_a.to_numpy(dtype=float)
    if not np.isfinite(k).all() or not np.isfinite(ta).all():
        raise ValueError("K and theta_a must be finite")
    if (k < 0).any() or (k > K_MAX).any():
        raise ValueError(f"K must be within 0..{K_MAX}")

    a_o = dt / (p.k11 * p.tau_o_min)
    a_h1 = dt / (p.k22 * p.tau_w_min)
    a_h2 = dt / (p.tau_o_min / p.k22)
    if max(a_o, a_h1, a_h2) > 1.0:
        raise ValueError("integration unstable: sub-step larger than a time constant — reduce dt_min")

    d_o, d_h1, d_h2 = steady_state_rises(k[0], p) if start_from_steady_state else (0.0, 0.0, 0.0)
    th_o = ta[0] + d_o                        # ABSOLUTE top-oil temperature is the state

    n = len(k)
    theta_o = np.empty(n)
    theta_h_end = np.empty(n)
    theta_h_max = np.empty(n)
    v_mean = np.empty(n)
    force_o = ((1 + p.R * k**2) / (1 + p.R)) ** p.x * p.d_theta_or   # constant within a step
    force_h = k**p.y * p.d_theta_hr
    ref = REF_HOT_SPOT_C

    th = -np.inf
    for i in range(n):
        fo, fh, t_amb = force_o[i], force_h[i], ta[i]
        h_max, v_sum = th, 0.0          # the step's max includes its starting value (dt-independent)
        for _ in range(n_sub):
            th_o += a_o * (fo - (th_o - t_amb))
            d_h1 += a_h1 * (p.k21 * fh - d_h1)
            d_h2 += a_h2 * ((p.k21 - 1) * fh - d_h2)
            th = th_o + d_h1 - d_h2
            if th > h_max:
                h_max = th
            if p.paper == "kraft":
                v_sum += 2.0 ** ((th - ref) / 6.0)
            else:
                v_sum += math.exp(15000.0 / 383.0 - 15000.0 / (th + 273.0))
        theta_o[i] = th_o
        theta_h_end[i] = th
        theta_h_max[i] = h_max
        v_mean[i] = v_sum / n_sub

    hourly = pd.DataFrame({
        "theta_o": theta_o, "theta_h": theta_h_end, "theta_h_max": theta_h_max,
        "V_mean": v_mean, "life_used_h": np.cumsum(v_mean) * step_min / 60,   # V × step length
    }, index=K.index)
    return ThermalResult(hourly, params, step_min, dt)


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
    d0 = steady_state_rises(0.0, ThermalParams())[0]
    print(f"step: no-load oil rise {d0:.1f} K; after 3 h = {r.hourly.theta_o.iloc[26] - 20:.1f} K "
          f"(expected {d0 + (55 - d0) * 0.632:.1f}), after 24 h = {r.hourly.theta_o.iloc[-1] - 20:.1f} K")
    # 3. hand numbers for the README
    for ta in (-20, 0, 20):
        print(f"  at {ta:+3d} °C ambient: 98 °C hot-spot at K = {load_for_hot_spot(98, ta):.2f}, "
              f"120 °C at K = {load_for_hot_spot(120, ta):.2f}")
    # 4. the Västerås year, "today" scenario, 500 kVA
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
    print(f"  worst week starts {ww.index[0]:%d %b}, life used {ww.V_mean.sum() / 24:.4f} days, "
          f"max hot-spot {ww.theta_h_max.max():.1f} °C")
