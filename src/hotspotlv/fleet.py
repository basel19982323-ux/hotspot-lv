"""
Mode C — screen a whole fleet of stations and rank them.

A planner never looks at one transformer: a large Swedish DSO has tens of thousands of nätstationer.
This module runs mode B (station.py) for every station in a list and returns
one ranked table — the thing a planner forwards to a colleague.

Two input shapes:
  * an INDEX CSV, one row per station:
        station_id, rated_kva, file[, smhi_station][, enclosure][, measured_at][, tz][, stamp]
                                   [, p_noload_w, p_load_w[, uk_percent]]
    where `file` is that station's profile CSV (relative to the index file). A row with p_noload_w and p_load_w
    is a custom unit (its own losses, catalogue.custom_transformer); without them rated_kva must be a catalogue
    size
  * a LONG CSV with all stations in one file — a DSO export as the station mode reads it (meterfile.py), plus a
    station column (station_id, Station, Nätstation …):
        Nätstation;Tidpunkt;Energi (kWh)[;Märkeffekt (kVA)][;p_noload_w;p_load_w[;uk_percent]]
    with each station's rating from a rating column (rated_kva, Märkeffekt …), a dict {station_id: rated_kva},
    or one default for all; read_fleet() tells the two shapes apart

Ranking: At risk before Watch before OK, errors last; within a level by days
of life used, then by peak loading. A `progress(done, total, station_id)`
callback lets the app show a bar; an error in one station becomes an error
row with the message, so one bad file does not stop a 200-station run. Every
row carries the station's notes (which SMHI station, what was assumed about
the file) and the "voltage and cable not checked" caveat in `why`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from hotspotlv import errors, meterfile
from hotspotlv.catalogue import custom_transformer
from hotspotlv.station import (CABLE_LOSS_SHARE_AT_RATED, StationResult, StationSeries, _norm, _to_number,
                               ambient_for, assess_station, read_station_csv, station_columns, station_from_frame)
from hotspotlv.weather import SMHI_STATION

LEVEL_RANK = {"At risk": 0, "Watch": 1, "OK": 2, "error": 3}
RESULT_COLUMNS = ["station_id", "kVA", "verdict", "why", "period", "step_min", "peak_loading_pct", "hours_above_80",
                  "hours_above_100", "life_days_per_year", "max_hot_spot_c", "max_load_pu", "energy_mwh",
                  "gaps_filled", "smhi_station", "notes", "error"]


@dataclass(frozen=True)
class FleetStation:
    station_id: str
    rated_kva: float
    series: StationSeries | None = None     # already-read series (long CSV) …
    file: Path | None = None                # … or a file to read
    error: str | None = None                # a station that already failed to read (long CSV) → error row
    smhi_station: int | None = None         # None → module default, noted as such in the result
    enclosure: str = "kiosk"
    measured_at: str = "meters"
    tz: str = "auto"                        # see meterfile.to_utc
    stamp: str = "start"
    losses: dict | None = None              # a custom unit's losses (catalogue.custom_transformer); None → catalogue


def _rated_kva(value) -> float:
    """'500', '500 kVA', '1 250', 500.0 → kVA as a float"""
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    m = re.search(r"[\d.]+", str(value).replace(",", ".").replace(" ", "").replace("\u00a0", ""))
    if not m:
        raise ValueError(f"cannot read a kVA rating from {value!r}")
    return float(m.group())


def _opt(row, cols, name, default):
    if name in cols and pd.notna(row[cols[name]]) and str(row[cols[name]]).strip() != "":
        return row[cols[name]]
    return default


def _custom_losses(row, cols, kva: float) -> dict | None:
    """The row's own losses, or None when it gives neither; one without the other is an error."""
    p0 = _opt(row, cols, "p_noload_w", None)
    pk = _opt(row, cols, "p_load_w", None)
    uk = _opt(row, cols, "uk_percent", None)
    if p0 is None and pk is None:
        if uk is not None:
            raise ValueError("uk_percent is given without p_noload_w and p_load_w")
        return None
    if p0 is None or pk is None:
        raise ValueError("a custom transformer needs both p_noload_w and p_load_w (in watts)")
    def num(x, name):
        v = _to_number(pd.Series([str(x)]), name).iloc[0]
        if pd.isna(v):
            raise ValueError(f"cannot read {name} {x!r} as a number")
        return float(v)
    return custom_transformer(kva, num(p0, "p_noload_w"), num(pk, "p_load_w"),
                              None if uk is None else num(uk, "uk_percent"))


def read_fleet_index(path) -> list[FleetStation]:
    """Index CSV → list of stations (files resolved relative to the index file). A row whose custom losses are
    wrong becomes an error row, like a station whose file cannot be read."""
    path = Path(path)
    df = _read_index(path)
    cols = {c.lower().strip(): c for c in df.columns}
    for needed in ("station_id", "rated_kva", "file"):
        if needed not in cols:
            raise ValueError(f"fleet index needs columns station_id, rated_kva, file; got {list(df.columns)}")
    out, seen = [], set()
    for _, r in df.iterrows():
        sid = str(r[cols["station_id"]]).strip()
        if not sid or sid.lower() == "nan":
            raise ValueError("fleet index has a row without station_id")
        if sid in seen:
            raise ValueError(f"duplicate station_id {sid!r} in the fleet index")
        seen.add(sid)
        f = Path(str(r[cols["file"]]).strip())
        if not f.is_absolute():
            f = path.parent / f
        smhi = _opt(r, cols, "smhi_station", None)
        kva = _rated_kva(r[cols["rated_kva"]])
        try:
            losses, error = _custom_losses(r, cols, kva), None
        except ValueError as e:
            losses, error = None, f"ValueError: {e}"
        out.append(FleetStation(
            station_id=sid, rated_kva=kva, file=f, losses=losses, error=error,
            smhi_station=int(float(smhi)) if smhi is not None else None,
            enclosure=str(_opt(r, cols, "enclosure", "kiosk")),
            measured_at=str(_opt(r, cols, "measured_at", "meters")),
            tz=str(_opt(r, cols, "tz", "auto")),
            stamp=str(_opt(r, cols, "stamp", "start")),
        ))
    if not out:
        raise ValueError("fleet index is empty")
    return out


def _read_index(path: Path) -> pd.DataFrame:
    last = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(path, sep=None, engine="python", dtype=str, encoding=enc)
        except UnicodeDecodeError as e:
            last = e
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as e:
            raise ValueError(f"cannot read {path} as a CSV: {e}") from e
    raise ValueError(f"cannot decode {path}: {last}")


def read_fleet_long(path, ratings: dict | None = None, smhi_station: int | None = None, losses: dict | None = None,
                    default_kva: float | None = None, **read_kwargs) -> list[FleetStation]:
    """One file with every station (a station column) → list of stations, one series each. The file is read once
    as a DSO export (encoding, separator, header, dates: meterfile.py) and split by station; each station's rows go
    through the station mode's own reader. A station's rating: `ratings` {station_id: kVA}, else the file's rating
    column (one value per station), else `default_kva`. losses: {station_id: catalogue.custom_transformer(…)}, or
    p_noload_w / p_load_w [/ uk_percent] columns in the file. A station whose rows cannot be read becomes an error
    entry (its error row appears in the table) instead of aborting the whole import."""
    path = Path(path)
    try:
        table = meterfile.read_table(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as e:
        raise ValueError(f"cannot read {path} as a CSV: {e}") from e
    df = table.df
    scol, rcol = station_columns(df)
    if scol is None:
        raise ValueError(f"a fleet file needs a station column (station_id, Station, Nätstation …); got {list(df.columns)}")
    ids = df[scol].astype(str).str.strip()
    if (ids == "").any() or ids.str.lower().eq("nan").any():
        raise ValueError(f"the fleet file has rows without a station id (column {scol!r})")
    loss_cols = {c.lower().strip(): c for c in df.columns if c.lower().strip() in ("p_noload_w", "p_load_w", "uk_percent")}
    ratings = {str(k).strip(): v for k, v in (ratings or {}).items()}
    losses = {str(k).strip(): v for k, v in (losses or {}).items()}
    found = list(table.found) + [("stations", f"{ids.nunique()} in column {scol!r}")]
    out = []
    for sid, part in df.groupby(ids, sort=True):
        if sid in ratings:
            kva = _rated_kva(ratings[sid])
        elif rcol is not None and part[rcol].astype(str).str.strip().replace("", np.nan).dropna().nunique() == 1:
            kva = _rated_kva(part[rcol].dropna().iloc[0])
        elif rcol is not None and part[rcol].astype(str).str.strip().replace("", np.nan).dropna().nunique() > 1:
            raise ValueError(f"station {sid!r} has more than one rating in column {rcol!r}")
        elif default_kva is not None:
            kva = float(default_kva)
        else:
            raise ValueError(f"no rated_kva for station {sid!r}: add a rating column (rated_kva / Märkeffekt), "
                             "give ratings, or a default rating for the whole file")
        error, unit = None, losses.get(sid)
        try:
            if unit is None and loss_cols:
                unit = _custom_losses(part.iloc[0], loss_cols, kva)
            frame = part.drop(columns=[c for c in [scol, rcol, *loss_cols.values()] if c is not None])
            series = station_from_frame(frame.reset_index(drop=True), table.sep, source=f"{path} [{sid}]",
                                        found=found + [("station", f"{sid!r} (column {scol!r})")],
                                        unsure=list(table.unsure), **read_kwargs)
        except ValueError as e:
            series, error = None, f"ValueError: {e}"
        out.append(FleetStation(sid, kva, series=series, smhi_station=smhi_station, error=error, losses=unit))
    return out


def read_fleet(path, default_kva: float | None = None, **read_kwargs) -> list[FleetStation]:
    """Either shape: an index (a `file` column, one profile file per station) or one long file with a station
    column. default_kva: the rating of a long file's stations when it has no rating column."""
    path = Path(path)
    head = meterfile.read_table(path).df
    names = {_norm(c)[0] for c in head.columns}
    if "file" in {c.lower().strip() for c in head.columns}:
        return read_fleet_index(path)
    if station_columns(head)[0] is not None:
        return read_fleet_long(path, default_kva=default_kva, **read_kwargs)
    raise ValueError(f"not a fleet file: it needs either a 'file' column (an index of station files) or a station "
                     f"column (station_id, Station, Nätstation …) with the stations' values; got {sorted(names)}")


def screen_fleet(stations: list[FleetStation], progress: Callable[[int, int, str], None] | None = None,
                 cable_loss_share_at_rated: float = CABLE_LOSS_SHARE_AT_RATED) -> tuple[pd.DataFrame, dict[str, StationResult]]:
    """Run mode B for every station. Returns the ranked table and the full results by station_id."""
    ids = [s.station_id for s in stations]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate station_id in the fleet")
    rows, results = [], {}
    temp_cache: dict[tuple, pd.Series] = {}      # SMHI hourly series per (station, years) — reindexed per station
    for i, fs in enumerate(stations, start=1):
        try:
            if fs.error:
                raise ValueError(fs.error)
            st = fs.series or read_station_csv(fs.file, measured_at=fs.measured_at, tz=fs.tz, stamp=fs.stamp)
            smhi = fs.smhi_station if fs.smhi_station is not None else SMHI_STATION
            key = (smhi, tuple(sorted(set(st.index.year))))
            if key not in temp_cache:
                temp_cache[key] = ambient_for(st.index, smhi)
            amb = temp_cache[key]
            if not amb.index.equals(st.index):
                amb = ambient_for(st.index, smhi)
            res = assess_station(st, fs.rated_kva, amb, smhi_station=fs.smhi_station, enclosure=fs.enclosure,
                                 cable_loss_share_at_rated=cable_loss_share_at_rated, losses=fs.losses)
            results[fs.station_id] = res
            rows.append({"station_id": fs.station_id, **res.metrics(), "error": ""})
        except Exception as e:                       # one bad station must not stop the fleet
            rows.append({"station_id": fs.station_id, "kVA": fs.rated_kva, "verdict": "error", "why": "",
                         "notes": "", "error": errors.of(e)})
        if progress:
            try:
                progress(i, len(stations), fs.station_id)
            except Exception:
                pass                                 # a broken progress bar must not stop the fleet either
    table = pd.DataFrame(rows).reindex(columns=RESULT_COLUMNS)
    table["rank_level"] = table["verdict"].map(LEVEL_RANK).fillna(3)
    # a withdrawn life (NaN: loading above 2 × rated) is beyond every IEC loading limit — it ranks first in its level
    assessed = table["verdict"] != "error"
    table["life_rank"] = table["life_days_per_year"].where(~(assessed & table["life_days_per_year"].isna()), np.inf)
    table = (table.sort_values(["rank_level", "life_rank", "peak_loading_pct"], ascending=[True, False, False],
                               na_position="last")
             .drop(columns=["rank_level", "life_rank"]).reset_index(drop=True))
    table.index = table.index + 1
    table.index.name = "rank"
    return table, results


def export_fleet_table(table: pd.DataFrame, path) -> Path:
    path = Path(path)
    table.to_csv(path, index=True, float_format="%.3f", encoding="utf-8-sig")
    return path
