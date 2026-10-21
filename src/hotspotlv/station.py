"""
Mode B — a real station from the DSO's own data.

Since 1 January 2025 every Swedish electricity meter delivers at least hourly
(mostly quarter-hour) values. Summing the meters behind a nätstation gives its
load profile — what Karlstad Elnät did by hand in 2018. This module takes
such a profile (or a transformer-side measurement) and runs it through the
thermal model and the verdict. No feeder model is needed, so the voltage and
cable rules are reported as "not checked" rather than silently passed.

    CSV ──► read_station_csv ──► StationSeries (P, Q, S per step, UTC)
                                       │
          SMHI ambient for the place ──┤
                                       ▼
                  s_from_meters: S at the transformer = √((P + losses)² + (Q + q_loss)²)
                                       ▼
                   IEC 60076-7 thermal (kiosk by default) ──► verdict

Accepted CSV shapes (header names are matched case-insensitively):
  * timestamp + `kw` / `p_kw` / `kW`                 active power per step
  * timestamp + `kwh` / `energy`                     energy per step (kWh → kW = kWh / step hours)
  * optionally `kvar` / `q_kvar`, or `kva` / `s_kva`  (measured S wins over the estimate)
  * timestamps tz-naive are taken as Europe/Stockholm local time (DST handled);
    tz-aware timestamps are converted to UTC
Gaps of at most `max_gap_steps` consecutive steps are interpolated; longer
gaps raise with the position, never filled silently.

Future overlays: `with_overlay` adds synthetic EV / heat-pump houses from the
scenario models on top of the measured profile ("what if 20 more EVs").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from hotspotlv.catalogue import transformer
from hotspotlv.feeder import POWER_FACTOR
from hotspotlv.loads.ev import EVParams, ev_charging_profile
from hotspotlv.loads.heatpump import HeatPumpParams, heat_pump_profile
from hotspotlv.thermal import ThermalParams, ThermalResult, simulate
from hotspotlv.timeseries import YearResult
from hotspotlv.verdict import Verdict, judge
from hotspotlv.weather import SMHI_STATION, load_smhi_temperature

LOCAL_TZ = "Europe/Stockholm"
TIME_NAMES = ("timestamp", "time", "datetime", "date", "tid", "tidpunkt", "datum")
P_NAMES = ("p_kw", "kw", "p", "effekt", "aktiv_effekt", "active_power_kw")
E_NAMES = ("kwh", "energy_kwh", "energy", "energi", "e_kwh")
Q_NAMES = ("q_kvar", "kvar", "q", "reaktiv_effekt")
S_NAMES = ("s_kva", "kva", "s", "skenbar_effekt")
MAX_GAP_STEPS_DEFAULT = 6
CABLE_LOSS_SHARE_DEFAULT = 0.02          # LV cable losses as a share of P when P is the sum of meters


@dataclass
class StationSeries:
    """Cleaned station measurements on a uniform UTC index."""
    p_kw: pd.Series
    q_kvar: pd.Series
    s_kva_measured: pd.Series | None     # only if the CSV had S
    step_min: float
    measured_at: str                     # "meters" (sum of customer meters) or "transformer"
    source: str = ""
    gaps_filled: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.p_kw.index

    @property
    def years(self) -> list[int]:
        return sorted(set(self.index.year))


def _find_column(columns, names) -> str | None:
    lower = {c.lower().strip(): c for c in columns}
    for n in names:
        if n in lower:
            return lower[n]
    return None


ALLOWED_STEPS_MIN = (5.0, 10.0, 15.0, 30.0, 60.0)


def _uniform_step_min(index: pd.DatetimeIndex) -> float:
    """The meter interval: the most common spacing, which must dominate and be a real interval."""
    if len(index) < 3:
        raise ValueError("need at least 3 timestamps")
    d = np.diff(index.to_numpy()) / np.timedelta64(1, "m")
    values, counts = np.unique(d, return_counts=True)
    step = float(values[np.argmax(counts)])
    if step not in ALLOWED_STEPS_MIN:
        raise ValueError(f"timestamp spacing {step:g} min is not a meter interval {ALLOWED_STEPS_MIN}")
    if counts.max() / len(d) < 0.9:
        raise ValueError(f"irregular timestamps: only {100 * counts.max() / len(d):.0f} % of the steps are {step:g} min")
    return step


def read_station_csv(path, *, timestamp_col: str | None = None, p_col: str | None = None,
                     e_col: str | None = None, q_col: str | None = None, s_col: str | None = None,
                     pf: float = POWER_FACTOR, measured_at: str = "meters", tz: str = LOCAL_TZ,
                     max_gap_steps: int = MAX_GAP_STEPS_DEFAULT, sep: str | None = None) -> StationSeries:
    """Read one station's CSV into a StationSeries (see the module docstring for the shapes)."""
    if measured_at not in ("meters", "transformer"):
        raise ValueError("measured_at must be 'meters' or 'transformer'")
    df = pd.read_csv(path, sep=sep, engine="python")
    if df.shape[1] < 2:
        raise ValueError("CSV needs at least a timestamp column and a value column")
    tcol = timestamp_col or _find_column(df.columns, TIME_NAMES) or df.columns[0]
    pcol = p_col or _find_column(df.columns, P_NAMES)
    ecol = e_col or _find_column(df.columns, E_NAMES)
    qcol = q_col or _find_column(df.columns, Q_NAMES)
    scol = s_col or _find_column(df.columns, S_NAMES)
    if pcol is None and ecol is None and scol is None:
        raise ValueError(f"no power/energy column found; columns are {list(df.columns)} "
                         f"(expected one of {P_NAMES + E_NAMES + S_NAMES}, or pass p_col=/e_col=/s_col=)")

    ts = pd.to_datetime(df[tcol], errors="coerce")
    if ts.isna().any():
        bad = df[tcol][ts.isna()].iloc[0]
        raise ValueError(f"unparseable timestamp {bad!r} in column {tcol!r}")
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(tz, ambiguous="infer", nonexistent="shift_forward")
    ts = ts.dt.tz_convert("UTC")
    df = df.assign(_ts=ts).drop_duplicates("_ts").sort_values("_ts").set_index("_ts")
    step_min = _uniform_step_min(df.index)
    step_h = step_min / 60
    full = pd.date_range(df.index[0], df.index[-1], freq=f"{int(round(step_min))}min", tz="UTC")
    notes = []

    def clean(col, name):
        s = pd.to_numeric(df[col], errors="coerce").reindex(full)
        missing = int(s.isna().sum())
        if missing:
            isna = s.isna().to_numpy()
            padded = np.concatenate(([False], isna, [False]))
            edges = np.flatnonzero(padded[1:] != padded[:-1])
            longest = int((edges[1::2] - edges[::2]).max())
            if longest > max_gap_steps:
                start = full[int(edges[::2][np.argmax(edges[1::2] - edges[::2])])]
                raise ValueError(f"{name}: a gap of {longest} steps starting {start} exceeds {max_gap_steps}")
            s = s.interpolate(limit_direction="both")
            notes.append(f"{name}: {missing} missing steps interpolated")
        if (s < 0).any():
            notes.append(f"{name}: {int((s < 0).sum())} negative values (export) kept as negative")
        return s.astype(float), missing

    if pcol is not None:
        p, gaps = clean(pcol, "P")
    else:
        e, gaps = clean(ecol, "energy")
        p = e / step_h
        notes.append(f"P derived from energy per {step_min:g}-min step")
    if qcol is not None:
        q, _ = clean(qcol, "Q")
    else:
        q = p.clip(lower=0) * math.tan(math.acos(pf))
        notes.append(f"Q estimated from P at pf {pf} (consumption only)")
    s_meas = clean(scol, "S")[0] if scol is not None else None
    return StationSeries(p.rename("p_kw"), q.rename("q_kvar"), s_meas, step_min, measured_at, str(path),
                         gaps, notes)


def s_from_meters(p_kw: pd.Series, q_kvar: pd.Series, rated_kva: float, measured_at: str = "meters",
                  cable_loss_share: float = CABLE_LOSS_SHARE_DEFAULT) -> pd.Series:
    """Apparent power at the transformer's HV side from measured P and Q.

    measured_at='meters'      : P is the sum of customer meters → add LV cable losses (a share of
                                P, default 2 %) and the transformer's own losses (catalogue, by K²)
    measured_at='transformer' : P, Q already at the transformer → only the HV/LV loss difference
    The transformer's reactive consumption uk·K²·S_rated is added in both cases (one fixed-point
    pass; K changes by < 1 % on the second pass)."""
    tr = transformer(int(rated_kva))
    p = p_kw.to_numpy(dtype=float)
    q = q_kvar.to_numpy(dtype=float)
    share = cable_loss_share if measured_at == "meters" else 0.0
    p_lv = p + share * np.abs(p)
    s = np.hypot(p_lv, q)
    for _ in range(2):
        k = s / rated_kva
        # losses are always supplied from the MV side: they add to consumption and reduce export
        p_hv = p_lv + (tr["p_noload_w"] + tr["p_load_w"] * k**2) / 1000
        q_hv = q + tr["uk_percent"] / 100 * k**2 * rated_kva
        s = np.hypot(p_hv, q_hv)
    return pd.Series(s, index=p_kw.index, name="s_kva")


def ambient_for(index: pd.DatetimeIndex, smhi_station: int = SMHI_STATION) -> pd.Series:
    """Hourly SMHI air temperature for the years the index spans, resampled to the index."""
    years = sorted(set(index.year))
    temp = pd.concat([load_smhi_temperature(y, smhi_station) for y in years])
    temp = temp[~temp.index.duplicated()].sort_index()
    out = temp.reindex(temp.index.union(index)).interpolate(method="time", limit_direction="both").reindex(index)
    if out.isna().any():
        raise ValueError(f"SMHI station {smhi_station} does not cover the data period")
    return out.rename("temp_c")


def year_result_from_station(st: StationSeries, rated_kva: float, cable_loss_share: float = CABLE_LOSS_SHARE_DEFAULT) -> YearResult:
    """A YearResult for the verdict: transformer S per step; voltages and cable unknown → neutral values."""
    if st.s_kva_measured is not None and st.measured_at == "transformer":
        s = st.s_kva_measured.rename("s_kva")
    else:
        s = s_from_meters(st.p_kw, st.q_kvar, rated_kva, st.measured_at, cable_loss_share)
    h = pd.DataFrame({
        "s_kva": s.to_numpy(), "loading_pct": s.to_numpy() / rated_kva * 100, "p_kw": st.p_kw.to_numpy(),
        "vmin_pu": 1.0, "vmax_pu": 1.0, "losses_kw": 0.0, "line_max_pct": 0.0,
    }, index=st.index)
    return YearResult(h, float(rated_kva))


@dataclass
class StationResult:
    station: StationSeries
    rated_kva: float
    year: YearResult
    thermal: ThermalResult
    verdict: Verdict
    overlay: dict = field(default_factory=dict)

    def metrics(self) -> dict:
        v = self.verdict
        return {
            "kVA": self.rated_kva, "verdict": v.level, "why": v.headline(),
            "period": f"{self.station.index[0]:%Y-%m-%d} – {self.station.index[-1]:%Y-%m-%d}",
            "step_min": self.station.step_min,
            "peak_loading_pct": v.on_paper["peak load (% of nameplate)"],
            "hours_above_80": v.on_paper["hours above 80 % of nameplate"],
            "hours_above_100": v.on_paper["hours above 100 % of nameplate"],
            "life_days_per_year": v.really["days of life used per year"],
            "max_hot_spot_c": v.really["max hot-spot (°C)"],
            "max_load_pu": v.really["max load (× rated)"],
            "energy_mwh": float(self.station.p_kw.clip(lower=0).sum() * self.station.step_min / 60 / 1000),
            "gaps_filled": self.station.gaps_filled,
            **self.overlay,
        }


def assess_station(st: StationSeries, rated_kva: float, ambient: pd.Series | None = None,
                   smhi_station: int = SMHI_STATION, enclosure: str = "kiosk",
                   cable_loss_share: float = CABLE_LOSS_SHARE_DEFAULT, overlay: dict | None = None) -> StationResult:
    """Thermal year and verdict for one station from its measured profile."""
    if ambient is None:
        ambient = ambient_for(st.index, smhi_station)
    if not ambient.index.equals(st.index):
        raise ValueError("ambient must be on the station's index (use ambient_for)")
    year = year_result_from_station(st, rated_kva, cable_loss_share)
    params = ThermalParams.for_transformer(int(rated_kva), enclosure=enclosure)
    thermal = simulate(year.hourly["loading_pct"] / 100, ambient, params, step_min=st.step_min)
    verdict = judge(year, thermal)
    verdict.notes.append("voltage and cable rules not checked: station data carries no feeder model")
    verdict.notes.extend(st.notes)
    return StationResult(st, float(rated_kva), year, thermal, verdict, overlay or {})


def with_overlay(st: StationSeries, ambient: pd.Series, n_ev: int = 0, n_hp: int = 0,
                 ev: EVParams = EVParams(), hp: HeatPumpParams = HeatPumpParams(), seed: int = 0) -> StationSeries:
    """Add synthetic houses on top of a measured profile: n_ev EV chargers and n_hp heat pumps
    (air/water by default), drawn with the scenario models and the station's own ambient."""
    if st.step_min != 60:
        raise ValueError("overlays need an hourly profile (resample first)")
    extra = pd.Series(0.0, index=st.index)
    ss = np.random.SeedSequence(seed)
    for s in ss.spawn(n_ev):
        extra = extra + ev_charging_profile(st.index, ev, seed=s).to_numpy()
    if n_hp:
        extra = extra + n_hp * heat_pump_profile(ambient, hp)["total"].to_numpy()
    q_extra = extra * math.tan(math.acos(POWER_FACTOR))
    notes = st.notes + [f"overlay: +{n_ev} EV chargers, +{n_hp} heat pumps (synthetic, seed {seed})"]
    return replace(st, p_kw=(st.p_kw + extra).rename("p_kw"), q_kvar=(st.q_kvar + q_extra).rename("q_kvar"),
                   s_kva_measured=None, notes=notes)


def export_station_csv(p_kw: pd.Series, path, q_kvar: pd.Series | None = None, local: bool = True) -> Path:
    """Write a station profile in the simplest accepted shape (used for examples and tests)."""
    idx = p_kw.index.tz_convert(LOCAL_TZ).tz_localize(None) if local else p_kw.index
    df = pd.DataFrame({"timestamp": idx.strftime("%Y-%m-%d %H:%M"), "p_kw": np.round(p_kw.to_numpy(), 3)})
    if q_kvar is not None:
        df["q_kvar"] = np.round(q_kvar.to_numpy(), 3)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path
