"""
Mode B — a real station from the DSO's own data.

Since 1 January 2025 every Swedish electricity meter delivers at least hourly
(mostly quarter-hour) values. Summing the meters behind a nätstation gives its
load profile — what Karlstad Elnät did by hand in 2018. This module takes
such a profile (or a transformer-side measurement) and runs it through the
thermal model and the verdict. No feeder model is needed, so the voltage and
cable rules are reported as "not checked" rather than silently passed.

    CSV ──► read_station_csv ──► StationSeries (P, Q per step, UTC)
                                       │
          SMHI ambient for the place ──┤
                                       ▼
                  s_from_meters: S at the transformer = √((P + losses)² + (Q + q_loss)²)
                                       ▼
                   IEC 60076-7 thermal (kiosk by default) ──► verdict

What the reader accepts (headers matched after normalising case, units in
parentheses and Swedish/English synonyms):
  * one timestamp column, or separate date + time columns (`Datum;Tid`)
  * active power per step (`kW`, `Effekt`, `Aktiv effekt`) or energy per step
    (`kWh`, `Energi`, `Förbrukning`, `Uttag`, `Wh`); an export column
    (`Produktion`, `Inmatning`, `Export`) is subtracted → net P
  * optional reactive power (`kvar`, `Reaktiv effekt`) and, for a transformer
    measurement, apparent power (`kVA`)
  * separators `,` `;` tab, decimal point or comma, UTF-8 or Windows-1252
  * timestamps: tz-naive = Swedish local time with DST (`tz="Europe/Stockholm"`);
    files in "normaltid" (CET all year, as EIFS 2023:1 prescribes for registered
    meter values) need `tz="normaltid"`; ISO stamps with offsets or `Z` are read
    as given; `24:00` is the next day's 00:00
  * `stamp="start"` (default) means a row labels the interval that STARTS at
    the stamp; `stamp="end"` shifts interval-end stamps back one step
Nothing is dropped or realigned silently: duplicated stamps with different
values, rows off the meter grid, impossible DST rows and gaps longer than
`max_gap_steps` all raise with the position. Short gaps are interpolated and
counted; every assumption made is written to `notes`.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from hotspotlv.catalogue import transformer
from hotspotlv.feeder import HOUSE_FUSE_A, POWER_FACTOR, fuse_kw
from hotspotlv.loads.ev import EVParams, ev_charging_profile
from hotspotlv.loads.heatpump import HeatPumpParams, heat_pump_profile
from hotspotlv.thermal import K_MAX, ThermalParams, ThermalResult, simulate
from hotspotlv.timeseries import YearResult
from hotspotlv.verdict import HOURS_PER_YEAR, Verdict, judge
from hotspotlv.weather import SMHI_STATION, SMHI_STATION_NAME, load_smhi_temperature

LOCAL_TZ = "Europe/Stockholm"
NORMALTID_TZ = "Etc/GMT-1"               # CET all year, no DST
DATE_NAMES = ("datum", "date", "dag")
TIME_NAMES = ("tid", "time", "klockslag", "timme")
TIMESTAMP_NAMES = ("timestamp", "datetime", "tidpunkt", "datum_tid", "date_time", "datumtid", "period", "starttid")
P_NAMES = ("p_kw", "kw", "effekt", "aktiv_effekt", "active_power", "active_power_kw", "medeleffekt", "power")
E_NAMES = ("kwh", "energy_kwh", "energy", "energi", "e_kwh", "forbrukning", "uttag", "import", "consumption",
           "konsumtion", "forbrukad_energi")
EXPORT_NAMES = ("produktion", "inmatning", "export", "generation", "feed_in", "production", "utmatning")
Q_NAMES = ("q_kvar", "kvar", "reaktiv_effekt", "reactive_power", "reactive_power_kvar")
S_NAMES = ("s_kva", "kva", "skenbar_effekt", "apparent_power", "apparent_power_kva")
SINGLE_LETTER = {"p": "p", "q": "q", "s": "s", "e": "e"}      # accepted only when nothing else matches
ALLOWED_STEPS_MIN = (5.0, 10.0, 15.0, 30.0, 60.0)
MAX_GAP_STEPS_DEFAULT = 6
CABLE_LOSS_SHARE_AT_RATED = 0.042        # LV cable losses as a share of P at rated load (I²R → share ∝ K);
                                         # fitted on the project feeder: ≈ 4.2 %·K (review of 21 Oct 2026)
OFFSET_RE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})\s*$")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{8}")
UNIT_SCALE = {            # → kW for power columns, kWh for energy columns
    "kw": ("power", 1.0), "mw": ("power", 1000.0), "w": ("power", 0.001),
    "kwh": ("energy", 1.0), "mwh": ("energy", 1000.0), "wh": ("energy", 0.001),
    "kvar": ("reactive", 1.0), "mvar": ("reactive", 1000.0),
    "kva": ("apparent", 1.0), "mva": ("apparent", 1000.0),
}


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
@dataclass
class StationSeries:
    """Cleaned station measurements on a uniform UTC index."""
    p_kw: pd.Series
    q_kvar: pd.Series
    s_kva_measured: pd.Series | None     # only if the CSV had S and measured_at == "transformer"
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
        return sorted(set(self.index.tz_convert(LOCAL_TZ).year))

    def period(self) -> str:
        loc = self.index.tz_convert(LOCAL_TZ)
        return f"{loc[0]:%Y-%m-%d} – {loc[-1]:%Y-%m-%d} (local)"


def _norm(name: str) -> tuple[str, str]:
    """('normalised_name', 'unit') — 'Förbrukning (kWh)' → ('forbrukning', 'kwh'), 'Effekt kW' → ('effekt', 'kw').
    An unknown unit in brackets raises: reading 'Energi (MWh)' as kWh would be 1000x wrong, silently."""
    s = str(name).strip().lower()
    unit = ""
    m = re.search(r"\(([^)]*)\)|\[([^\]]*)\]", s)
    if m:
        unit = (m.group(1) or m.group(2) or "").strip().lower().replace(" ", "")
        s = s[:m.start()] + s[m.end():]
        if unit and unit not in UNIT_SCALE and not re.fullmatch(r"[a-z]*", unit) is None and unit not in ("", "-"):
            if unit not in UNIT_SCALE:
                raise ValueError(f"column {name!r}: unit {unit!r} is not understood (known: {sorted(UNIT_SCALE)})")
    s = (s.replace("å", "a").replace("ä", "a").replace("ö", "o").replace("é", "e"))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    tail = re.search(r"(?:^|_)(mwh|kwh|wh|mw|kw|w|kvar|mvar|kva|mva)$", s)   # 'forbrukning_kwh', bare 'wh'
    if tail and not unit:
        unit = tail.group(1)
        s = s[:tail.start()].strip("_") or unit
    return s, unit


def _pick(cols: dict[str, tuple[str, str]], names, unit_hints=()) -> str | None:
    """First original column whose normalised name is in `names` (or whose unit hint matches)."""
    for orig, (n, u) in cols.items():
        if n in names:
            return orig
    for orig, (n, u) in cols.items():
        if u and u in unit_hints:
            return orig
    return None


def _read_table(path, sep) -> pd.DataFrame:
    last = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(path, sep=sep, engine="python", encoding=enc, dtype=str, skipinitialspace=True)
        except UnicodeDecodeError as e:
            last = e
    raise ValueError(f"cannot decode {path}: {last}")


def _to_number(col: pd.Series, name: str) -> pd.Series:
    """Numbers from strings, cell by cell: '12,3' → 12.3 (decimal comma), '1,226.0' → 1226.0 (thousands comma),
    '12 345,6' → 12345.6, trailing unit words ignored, blanks → NaN."""
    s = col.astype(str).str.strip().str.replace(r"\s", "", regex=True)
    s = s.str.replace(r"(?i)(kwh|kw|kvar|kva|mwh|mw|wh|w)$", "", regex=True)
    both = s.str.contains(",", regex=False) & s.str.contains(".", regex=False)
    s = s.where(~both, s.str.replace(",", "", regex=False))                       # 1,226.0 → 1226.0
    only_comma = s.str.contains(",", regex=False)
    s = s.where(~only_comma, s.str.replace(",", ".", regex=False))                # 12,3 → 12.3
    x = pd.to_numeric(s.replace({"": np.nan, "nan": np.nan, "None": np.nan, "-": np.nan, "null": np.nan}), errors="coerce")
    return x.rename(name)


def _parse_timestamps(df: pd.DataFrame, cols: dict, timestamp_col: str | None, tz: str) -> tuple[pd.DatetimeIndex, list[str]]:
    notes = []
    if timestamp_col is not None:
        raw = df[timestamp_col].astype(str).str.strip()
    else:
        tcol = _pick(cols, TIMESTAMP_NAMES)
        dcol, hcol = _pick(cols, DATE_NAMES), _pick(cols, TIME_NAMES)
        if tcol is None and dcol is not None and hcol is not None:
            raw = df[dcol].astype(str).str.strip() + " " + df[hcol].astype(str).str.strip()
            notes.append(f"timestamps joined from columns {dcol!r} + {hcol!r}")
        elif tcol is None and dcol is not None:
            raw = df[dcol].astype(str).str.strip()
        elif tcol is not None:
            raw = df[tcol].astype(str).str.strip()
        else:
            raw = df[df.columns[0]].astype(str).str.strip()
            notes.append(f"first column {df.columns[0]!r} taken as the timestamp")
    # 24:00 → next day 00:00
    is24 = raw.str.contains(r"\b24:00", regex=True)
    if is24.any():
        base = pd.to_datetime(raw[is24].str.replace(r"\b24:00(:00)?", "00:00", regex=True), errors="coerce", format="mixed")
        raw = raw.copy()
        raw[is24] = (base + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d %H:%M:%S")
        notes.append(f"{int(is24.sum())} stamps written as 24:00 read as the next day's 00:00")
    has_date = raw.str.contains(DATE_RE)
    if has_date.mean() < 0.5:
        raise ValueError("the timestamp column seems to hold only a time of day — is the date in another column? "
                         "(pass timestamp_col=, or make sure the date column is named Datum/Date)")
    offset_share = (raw.str.contains(OFFSET_RE) & has_date).mean()
    if 0 < offset_share < 1:
        raise ValueError("timestamps mix rows with and without a UTC offset — one convention per file")
    if offset_share == 1:
        ts = pd.to_datetime(raw, errors="coerce", utc=True, format="mixed")
        if ts.isna().any():
            raise ValueError(f"unparseable timestamp {raw[ts.isna()].iloc[0]!r}")
        notes.append("timestamps carry a UTC offset; read as given")
        return pd.DatetimeIndex(ts), notes
    ts = pd.to_datetime(raw, errors="coerce", format="mixed")
    if ts.isna().any():
        raise ValueError(f"unparseable timestamp {raw[ts.isna()].iloc[0]!r}")
    # DST inference needs time order, and the two October 02:00 rows (or eight quarter hours) are only
    # told apart by file order — so a newest-first file is flipped, but rows are never re-sorted here.
    steps = np.diff(ts.to_numpy().astype("datetime64[ns]").astype("int64"))
    if len(steps) and (steps < 0).sum() > (steps > 0).sum():
        ts = ts.iloc[::-1]
        notes.append("file is newest-first; read in time order")
    zone = NORMALTID_TZ if tz.lower() in ("normaltid", "cet", "etc/gmt-1") else tz
    try:
        loc = ts.dt.tz_localize(zone, ambiguous="infer", nonexistent="raise")
    except Exception as e:  # pytz AmbiguousTimeError / NonExistentTimeError and friends
        raise ValueError(f"timestamps do not fit {zone!r} daylight-saving rules ({type(e).__name__}): a file in "
                         "'normaltid' (CET all year, no DST) must be read with tz='normaltid'; a file with a missing "
                         "or doubled hour in March/October must be fixed, not guessed; rows must be in time order") from e
    notes.append("tz-naive timestamps taken as " + ("normaltid (CET = UTC+1 all year, no summer time)"
                                                      if zone == NORMALTID_TZ else f"{zone} local time"))
    out = pd.Series(loc.dt.tz_convert("UTC").to_numpy(), index=loc.index).reindex(range(len(raw)))  # back to file order
    return pd.DatetimeIndex(out), notes


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
                     export_col: str | None = None, pf: float = POWER_FACTOR, measured_at: str = "meters",
                     tz: str = LOCAL_TZ, stamp: str = "start", max_gap_steps: int = MAX_GAP_STEPS_DEFAULT,
                     sep: str | None = None) -> StationSeries:
    """Read one station's CSV into a StationSeries (see the module docstring for the shapes)."""
    if measured_at not in ("meters", "transformer"):
        raise ValueError("measured_at must be 'meters' or 'transformer'")
    if stamp not in ("start", "end"):
        raise ValueError("stamp must be 'start' or 'end'")
    if not 0 < pf <= 1:
        raise ValueError("pf must be within (0, 1]")
    if max_gap_steps < 0:
        raise ValueError("max_gap_steps must be >= 0")
    try:
        df = _read_table(path, sep)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, csv.Error) as e:
        raise ValueError(f"cannot read {path} as a CSV: {e}") from e
    if df.shape[1] < 2 or len(df) == 0:
        raise ValueError("CSV needs at least a timestamp column, a value column and some rows")
    cols = {c: _norm(c) for c in df.columns}
    pcol = p_col or _pick(cols, P_NAMES, unit_hints=("kw",))
    ecol = e_col or _pick(cols, E_NAMES, unit_hints=("kwh", "wh"))
    xcol = export_col or _pick(cols, EXPORT_NAMES)
    qcol = q_col or _pick(cols, Q_NAMES, unit_hints=("kvar",))
    scol = s_col or _pick(cols, S_NAMES, unit_hints=("kva",))
    if pcol is None and ecol is None and scol is None:                 # last resort: single letters
        single = {n: orig for orig, (n, _) in cols.items() if n in SINGLE_LETTER}
        pcol, ecol, qcol, scol = single.get("p"), single.get("e"), qcol or single.get("q"), scol or single.get("s")
    if pcol is None and ecol is None and scol is None:
        raise ValueError(f"no power/energy column found; columns are {list(df.columns)} "
                         f"(expected e.g. kW / Effekt / kWh / Energi / Förbrukning, or pass p_col= / e_col=)")

    index, notes = _parse_timestamps(df, cols, timestamp_col, tz)
    if any("24:00" in n for n in notes) and stamp == "start":
        stamp = "end"
        notes.append("24:00 stamps can only label the END of an interval — stamp='end' applied")
    order = np.argsort(index.to_numpy(), kind="stable")
    df = df.iloc[order].reset_index(drop=True)
    index = index[order]
    # duplicates: identical rows collapse with a note, differing rows are an error
    dup = index.duplicated(keep=False)
    if dup.any():
        value_cols = [c for c in (pcol, ecol, xcol, qcol, scol) if c is not None]
        rows = df[value_cols].astype(str).agg("|".join, axis=1)
        groups = pd.DataFrame({"t": index, "v": rows.to_numpy()})[dup].groupby("t")["v"].nunique()
        if (groups > 1).any():
            raise ValueError(f"duplicated timestamp {groups[groups > 1].index[0]} with different values — "
                             "an October file in local time with both 02:00 rows merged, or two exports concatenated?")
        keep = ~index.duplicated(keep="first")
        notes.append(f"{int((~keep).sum())} exactly repeated rows dropped")
        df, index = df[keep].reset_index(drop=True), index[keep]
    step_min = _uniform_step_min(index)
    step_h = step_min / 60
    if stamp == "end":
        index = index - pd.Timedelta(minutes=step_min)
        notes.append(f"stamps taken as interval END; shifted back {step_min:g} min")
    else:
        notes.append("stamps taken as interval START")
    full = pd.date_range(index[0], index[-1], freq=f"{int(round(step_min))}min", tz="UTC")
    off_grid = ~index.isin(full)
    if off_grid.any():
        raise ValueError(f"{int(off_grid.sum())} timestamps are not on the {step_min:g}-min grid "
                         f"(first: {index[off_grid][0]}) — mixed resolutions must be resampled first")
    df.index = index

    def clean(col, name):
        s = _to_number(df[col], name).reindex(full)
        missing = int(s.isna().sum())
        if missing:
            isna = s.isna().to_numpy()
            padded = np.concatenate(([False], isna, [False]))
            edges = np.flatnonzero(padded[1:] != padded[:-1])
            lengths = edges[1::2] - edges[::2]
            longest = int(lengths.max())
            if longest > max_gap_steps:
                start = full[int(edges[::2][np.argmax(lengths)])]
                raise ValueError(f"{name}: a gap of {longest} steps starting {start} exceeds {max_gap_steps} "
                                 f"(if the whole column is empty, check the decimal sign and the column choice)")
            s = s.interpolate(limit_direction="both")
            notes.append(f"{name}: {missing} missing steps interpolated")
        return s.astype(float), missing

    def as_kw(values, unit, default_kind, label):
        """Scale a column to kW using its unit (kW, MW, W, kWh, MWh, Wh); energy per step → mean kW."""
        kind, scale = UNIT_SCALE.get(unit, (default_kind, 1.0))
        if kind == "energy":
            notes.append(f"{label}: energy per {step_min:g}-min step → kW" + (f" (column unit {unit})" if unit else ""))
            return values * scale / step_h
        if unit and unit != "kw":
            notes.append(f"{label}: column unit {unit} scaled to kW")
        return values * scale

    if pcol is not None:
        p, gaps = clean(pcol, "P")
        p = as_kw(p, cols[pcol][1], "power", "P")
    else:
        e, gaps = clean(ecol, "energy")
        p = as_kw(e, cols[ecol][1] or "kwh", "energy", "P")
    if xcol is not None:
        x, _ = clean(xcol, "export")
        x = as_kw(x, cols[xcol][1] or ("kwh" if ecol is not None else "kw"), "energy" if ecol is not None else "power", "export")
        p = p - x.abs()
        notes.append(f"export column {xcol!r} subtracted → net P (negative = export)")
    if qcol is not None and _to_number(df[qcol], "q").notna().mean() > 0.5:
        q, _ = clean(qcol, "Q")
        q = q * UNIT_SCALE.get(cols[qcol][1], ("reactive", 1.0))[1]
    else:
        q = p.clip(lower=0) * math.tan(math.acos(pf))
        notes.append(f"Q estimated from P at pf {pf} (consumption only" +
                     ("; on export hours Q = 0, so the transformer loading there is under-read by a few % of "
                      "nameplate — irrelevant for ageing)" if (p < 0).any() else ")"))
    if (p < 0).any():
        notes.append(f"{int((p < 0).sum())} steps with negative P (export) kept as negative")
    s_meas = None
    if scol is not None and measured_at == "transformer":
        s_meas = clean(scol, "S")[0] * UNIT_SCALE.get(cols[scol][1], ("apparent", 1.0))[1]
        notes.append(f"measured apparent power {scol!r} used as the transformer loading")
    elif scol is not None:
        notes.append(f"column {scol!r} (kVA) ignored: measured_at='meters' — pass measured_at='transformer' to use it")
    return StationSeries(p.rename("p_kw"), q.rename("q_kvar"), s_meas, step_min, measured_at, str(path), gaps, notes)


def to_hourly(st: StationSeries) -> StationSeries:
    """Average a sub-hourly series to hours (needed for overlays)."""
    if st.step_min == 60:
        return st
    p = st.p_kw.resample("h").mean()
    q = st.q_kvar.resample("h").mean()
    s = st.s_kva_measured.resample("h").mean() if st.s_kva_measured is not None else None
    return replace(st, p_kw=p, q_kvar=q, s_kva_measured=s, step_min=60.0,
                   notes=st.notes + [f"averaged from {st.step_min:g}-min to hourly values"])


# ---------------------------------------------------------------------------
# physics: from the sum of meters to the transformer
# ---------------------------------------------------------------------------
def s_from_meters(p_kw: pd.Series, q_kvar: pd.Series, rated_kva: float, measured_at: str = "meters",
                  cable_loss_share_at_rated: float = CABLE_LOSS_SHARE_AT_RATED) -> pd.Series:
    """Apparent power at the transformer's HV side from measured P and Q.

    measured_at='meters'      : P is the sum of customer meters → add LV cable losses (I²R, so a share
                                of P proportional to the loading: share = share_at_rated × K) and the
                                transformer's own losses (catalogue: no-load + load·K²)
    measured_at='transformer' : P, Q already at the LV terminals → only the transformer's losses
    The transformer's reactive consumption uk·K²·S_rated is added in both cases. Six fixed-point
    passes (K → losses → S → K): converged to < 0.2 % of nameplate up to K ≈ 3.5."""
    tr = transformer(int(rated_kva))
    p = p_kw.to_numpy(dtype=float)
    q = q_kvar.to_numpy(dtype=float)
    share = cable_loss_share_at_rated if measured_at == "meters" else 0.0
    s = np.hypot(p, q)
    for _ in range(6):
        k = s / rated_kva
        p_lv = p + share * k * np.abs(p)                                   # cable I²R: share grows with loading
        p_hv = p_lv + (tr["p_noload_w"] + tr["p_load_w"] * k**2) / 1000  # losses always supplied from the MV side
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


def year_result_from_station(st: StationSeries, rated_kva: float,
                             cable_loss_share_at_rated: float = CABLE_LOSS_SHARE_AT_RATED) -> YearResult:
    """A YearResult for the verdict: transformer S per step; voltages and cable unknown → neutral values
    (the verdict marks them as not checked)."""
    if st.s_kva_measured is not None:
        s = st.s_kva_measured.rename("s_kva")
    else:
        s = s_from_meters(st.p_kw, st.q_kvar, rated_kva, st.measured_at, cable_loss_share_at_rated)
    h = pd.DataFrame({
        "s_kva": s.to_numpy(), "loading_pct": s.to_numpy() / rated_kva * 100, "p_kw": st.p_kw.to_numpy(),
        "vmin_pu": 1.0, "vmax_pu": 1.0, "losses_kw": 0.0, "line_max_pct": 0.0,
    }, index=st.index)
    return YearResult(h, float(rated_kva))


def scale_text(factor: float) -> str:
    """A scaling factor in plain digits: '12', '11.8', '365' — never '3.6e+02'."""
    return f"{factor:,.0f}" if factor >= 100 else f"{factor:,.1f}"


# ---------------------------------------------------------------------------
# assessment
# ---------------------------------------------------------------------------
NOT_CHECKED_KEYS = ("hours with voltage outside ±10 %", "hours with voltage outside the planning band",
                    "max cable loading (%)")


@dataclass
class StationResult:
    station: StationSeries
    rated_kva: float
    year: YearResult
    thermal: ThermalResult
    verdict: Verdict
    smhi_station: int = SMHI_STATION
    overlay: dict = field(default_factory=dict)

    def metrics(self) -> dict:
        v = self.verdict
        base = {
            "kVA": self.rated_kva, "verdict": v.level, "why": v.headline(),
            "period": self.station.period(), "step_min": self.station.step_min,
            "peak_loading_pct": v.on_paper["peak load (% of nameplate)"],
            "hours_above_80": v.on_paper["hours above 80 % of nameplate"],
            "hours_above_100": v.on_paper["hours above 100 % of nameplate"],
            "life_days_per_year": v.really["days of life used per year"],
            "max_hot_spot_c": v.really["max hot-spot (°C)"],
            "max_load_pu": v.really["max load (× rated)"],
            "energy_mwh": float(self.station.p_kw.clip(lower=0).sum() * self.station.step_min / 60 / 1000),
            "gaps_filled": self.station.gaps_filled,
            "smhi_station": self.smhi_station,
            "notes": "; ".join(v.notes),
        }
        for k, val in self.overlay.items():
            base[f"overlay_{k}"] = val
        return base


def assess_station(st: StationSeries, rated_kva: float, ambient: pd.Series | None = None,
                   smhi_station: int | None = None, enclosure: str = "kiosk",
                   cable_loss_share_at_rated: float = CABLE_LOSS_SHARE_AT_RATED,
                   overlay: dict | None = None) -> StationResult:
    """Thermal year and verdict for one station from its measured profile."""
    notes = []
    if smhi_station is None:
        smhi_station = SMHI_STATION
        notes.append(f"ambient: SMHI station {SMHI_STATION} {SMHI_STATION_NAME} — the default; choose the SMHI station "
                     "nearest the place")
    else:
        notes.append(f"ambient: SMHI station {smhi_station}")
    if ambient is None:
        ambient = ambient_for(st.index, smhi_station)
    if not ambient.index.equals(st.index):
        raise ValueError("ambient must be on the station's index (use ambient_for)")
    year = year_result_from_station(st, rated_kva, cable_loss_share_at_rated)
    k = year.hourly["loading_pct"] / 100
    clipped = int((k > K_MAX).sum())
    if clipped:
        notes.append(f"loading above {K_MAX:g} × rated in {clipped} steps — the thermal run was clipped there (its "
                     "figures are withdrawn anyway above 2 × rated); the current-limit rule sees the full loading")
        k = k.clip(upper=K_MAX)
    hours = len(st.index) * st.step_min / 60
    if abs(HOURS_PER_YEAR / hours - 1) > 0.1:
        days = hours / 24
        span = f"{hours:,.0f} hours" if days < 1.5 else f"{days:,.0f} days"
        notes.append(f"the data cover {span}: life used per year is scaled "
                     f"×{scale_text(HOURS_PER_YEAR / hours)} as if this period repeated all year — a winter-only file "
                     "overstates the yearly figure, a summer-only file understates it")
    params = ThermalParams.for_transformer(int(rated_kva), enclosure=enclosure)
    thermal = simulate(k, ambient, params, step_min=st.step_min)
    verdict = judge(year, thermal)                           # withdraws the thermal figures above 2 × rated
    from hotspotlv.study import enclosure_note
    notes.append(enclosure_note(params, rated_kva))
    for key in NOT_CHECKED_KEYS:
        verdict.really[key] = None                            # a 0 would read as "checked and fine"
    verdict.notes.append("voltage and cable rules not checked: station data carries no feeder model")
    verdict.notes.extend(notes)
    verdict.notes.extend(st.notes)
    return StationResult(st, float(rated_kva), year, thermal, verdict, smhi_station, overlay or {})


def with_overlay(st: StationSeries, ambient: pd.Series, n_ev: int = 0, n_hp: int = 0,
                 ev: EVParams = EVParams(), hp: HeatPumpParams = HeatPumpParams(), seed: int = 0,
                 fuse_a: float = HOUSE_FUSE_A) -> StationSeries:
    """Add synthetic houses on top of a measured profile: n_ev EV chargers and n_hp heat pumps
    (air/water by default, sizes jittered ±20 % like the scenario model), each synthetic house
    capped at its main fuse. An UPPER BOUND: no base load is added, every charger has the
    scenario's default power, and nothing else in the station changes."""
    if st.step_min != 60:
        raise ValueError("overlays need an hourly profile (use to_hourly first)")
    if n_ev < 0 or n_hp < 0 or int(n_ev) != n_ev or int(n_hp) != n_hp:
        raise ValueError("n_ev and n_hp must be non-negative integers")
    if not ambient.index.equals(st.index):
        raise ValueError("ambient must be on the station's index (use ambient_for)")
    n = max(n_ev, n_hp)
    extra = np.zeros(len(st.index))
    cap = fuse_kw(fuse_a)
    for j, s in enumerate(np.random.SeedSequence(seed).spawn(n)):
        ss_hp, ss_ev = s.spawn(2)
        house = np.zeros(len(st.index))
        if j < n_hp:
            rng = np.random.default_rng(ss_hp)
            p = hp.with_(ua_kw_per_k=max(0.08, hp.ua_kw_per_k * rng.normal(1.0, 0.2)),
                         q_nom_kw=max(3.0, hp.q_nom_kw * rng.normal(1.0, 0.15)))
            house += heat_pump_profile(ambient, p)["total"].to_numpy()
        if j < n_ev:
            house += ev_charging_profile(st.index, ev, seed=ss_ev).to_numpy()
        extra += np.minimum(house, cap)
    q_extra = extra * math.tan(math.acos(POWER_FACTOR))
    notes = st.notes + [f"overlay: +{n_ev} EV chargers ({ev.charger_kw:g} kW), +{n_hp} heat pumps — synthetic upper bound "
                        f"(no base load, fuse {fuse_a:g} A per synthetic house, random draw {seed})"]
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
