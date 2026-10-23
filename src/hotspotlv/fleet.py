"""
Mode C — screen a whole fleet of stations and rank them.

A planner never looks at one transformer: a large Swedish DSO has tens of thousands of nätstationer.
This module runs mode B (station.py) for every station in a list and returns
one ranked table — the thing a planner forwards to a colleague.

Two input shapes:
  * an INDEX CSV, one row per station:
        station_id, rated_kva, file[, smhi_station][, enclosure][, measured_at][, tz][, stamp]
    where `file` is that station's profile CSV (relative to the index file)
  * a LONG CSV with all stations in one file:
        station_id, timestamp, p_kw[, q_kvar][, s_kva]
    plus a dict of ratings {station_id: rated_kva}

Ranking: At risk before Watch before OK, errors last; within a level by days
of life used, then by peak loading. A `progress(done, total, station_id)`
callback lets the app show a bar; an error in one station becomes an error
row with the message, so one bad file does not stop a 200-station run. Every
row carries the station's notes (which SMHI station, what was assumed about
the file) and the "voltage and cable not checked" caveat in `why`.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from hotspotlv import errors
from hotspotlv.station import (CABLE_LOSS_SHARE_AT_RATED, LOCAL_TZ, StationResult, StationSeries,
                               ambient_for, assess_station, read_station_csv)
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
    tz: str = LOCAL_TZ
    stamp: str = "start"


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


def read_fleet_index(path) -> list[FleetStation]:
    """Index CSV → list of stations (files resolved relative to the index file)."""
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
        out.append(FleetStation(
            station_id=sid, rated_kva=_rated_kva(r[cols["rated_kva"]]), file=f,
            smhi_station=int(float(smhi)) if smhi is not None else None,
            enclosure=str(_opt(r, cols, "enclosure", "kiosk")),
            measured_at=str(_opt(r, cols, "measured_at", "meters")),
            tz=str(_opt(r, cols, "tz", LOCAL_TZ)),
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


def read_fleet_long(path, ratings: dict, smhi_station: int | None = None, **read_kwargs) -> list[FleetStation]:
    """Long CSV (station_id, timestamp, p_kw, …) → list of stations, one series each. A station whose
    rows cannot be read becomes an error entry (its error row appears in the table) instead of
    aborting the whole import."""
    path = Path(path)
    df = _read_index(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "station_id" not in cols:
        raise ValueError("long CSV needs a station_id column")
    ids = df[cols["station_id"]].astype(str).str.strip()
    if (ids == "") .any() or ids.str.lower().eq("nan").any():
        raise ValueError("long CSV has rows without station_id")
    ratings = {str(k).strip(): v for k, v in ratings.items()}
    out = []
    for sid, part in df.groupby(ids, sort=True):
        if sid not in ratings:
            raise ValueError(f"no rated_kva given for station {sid!r}")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8",
                                         dir=tempfile.gettempdir()) as tmp:
            part.drop(columns=[cols["station_id"]]).to_csv(tmp, index=False)
            tmp_path = Path(tmp.name)
        try:
            series = read_station_csv(tmp_path, **read_kwargs)
            out.append(FleetStation(sid, _rated_kva(ratings[sid]), series=series, smhi_station=smhi_station))
        except ValueError as e:
            out.append(FleetStation(sid, _rated_kva(ratings[sid]), smhi_station=smhi_station, error=f"ValueError: {e}"))
        finally:
            tmp_path.unlink(missing_ok=True)
    return out


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
                                 cable_loss_share_at_rated=cable_loss_share_at_rated)
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
