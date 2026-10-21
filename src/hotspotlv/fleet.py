"""
Mode C — screen a whole fleet of stations and rank them.

A planner never looks at one transformer: E.ON alone has ~48 000 nätstationer.
This module runs mode B (station.py) for every station in a list and returns
one ranked table — the thing a planner forwards to a colleague.

Two input shapes:
  * an INDEX CSV, one row per station:
        station_id, rated_kva, file[, smhi_station][, enclosure][, measured_at]
    where `file` is that station's profile CSV (relative to the index file)
  * a LONG CSV with all stations in one file:
        station_id, timestamp, p_kw[, q_kvar][, s_kva]
    plus a separate dict / CSV of ratings {station_id: rated_kva}

Ranking: At risk before Watch before OK; within a level by days of life used,
then by peak loading. A `progress` callback (done, total, station_id) lets the
app show a bar; errors in one station are recorded in the table, not raised,
so one bad file does not stop a 200-station run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from hotspotlv.station import (CABLE_LOSS_SHARE_DEFAULT, StationResult, StationSeries, ambient_for,
                               assess_station, read_station_csv)
from hotspotlv.weather import SMHI_STATION

LEVEL_RANK = {"At risk": 0, "Watch": 1, "OK": 2, "error": 3}
RESULT_COLUMNS = ["station_id", "kVA", "verdict", "why", "period", "step_min", "peak_loading_pct", "hours_above_80",
                  "hours_above_100", "life_days_per_year", "max_hot_spot_c", "max_load_pu", "energy_mwh",
                  "gaps_filled", "error"]


@dataclass(frozen=True)
class FleetStation:
    station_id: str
    rated_kva: float
    series: StationSeries | None = None     # already-read series (long CSV) …
    file: Path | None = None                # … or a file to read
    smhi_station: int = SMHI_STATION
    enclosure: str = "kiosk"
    measured_at: str = "meters"


def read_fleet_index(path) -> list[FleetStation]:
    """Index CSV → list of stations (files resolved relative to the index file)."""
    path = Path(path)
    df = pd.read_csv(path, sep=None, engine="python")
    cols = {c.lower().strip(): c for c in df.columns}
    for needed in ("station_id", "rated_kva", "file"):
        if needed not in cols:
            raise ValueError(f"fleet index needs columns station_id, rated_kva, file; got {list(df.columns)}")
    out = []
    for _, r in df.iterrows():
        f = Path(str(r[cols["file"]]))
        if not f.is_absolute():
            f = path.parent / f
        out.append(FleetStation(
            station_id=str(r[cols["station_id"]]), rated_kva=float(r[cols["rated_kva"]]), file=f,
            smhi_station=int(r[cols["smhi_station"]]) if "smhi_station" in cols and pd.notna(r[cols["smhi_station"]]) else SMHI_STATION,
            enclosure=str(r[cols["enclosure"]]) if "enclosure" in cols and pd.notna(r[cols["enclosure"]]) else "kiosk",
            measured_at=str(r[cols["measured_at"]]) if "measured_at" in cols and pd.notna(r[cols["measured_at"]]) else "meters",
        ))
    if not out:
        raise ValueError("fleet index is empty")
    return out


def read_fleet_long(path, ratings: dict[str, float], smhi_station: int = SMHI_STATION, **read_kwargs) -> list[FleetStation]:
    """Long CSV (station_id, timestamp, p_kw, …) → list of stations, one series each."""
    path = Path(path)
    df = pd.read_csv(path, sep=None, engine="python")
    cols = {c.lower().strip(): c for c in df.columns}
    if "station_id" not in cols:
        raise ValueError("long CSV needs a station_id column")
    out = []
    tmp_dir = Path(path).parent
    for sid, part in df.groupby(cols["station_id"], sort=True):
        sid = str(sid)
        if sid not in ratings:
            raise ValueError(f"no rated_kva given for station {sid!r}")
        tmp = tmp_dir / f".{path.stem}__{sid}.csv"
        part.drop(columns=[cols["station_id"]]).to_csv(tmp, index=False)
        try:
            series = read_station_csv(tmp, **read_kwargs)
        finally:
            tmp.unlink(missing_ok=True)
        out.append(FleetStation(sid, float(ratings[sid]), series=series, smhi_station=smhi_station))
    return out


def screen_fleet(stations: list[FleetStation], progress: Callable[[int, int, str], None] | None = None,
                 cable_loss_share: float = CABLE_LOSS_SHARE_DEFAULT) -> tuple[pd.DataFrame, dict[str, StationResult]]:
    """Run mode B for every station. Returns the ranked table and the full results by station_id."""
    rows, results = [], {}
    ambient_cache: dict[tuple, pd.Series] = {}
    for i, fs in enumerate(stations, start=1):
        try:
            st = fs.series or read_station_csv(fs.file, measured_at=fs.measured_at)
            key = (fs.smhi_station, st.index[0], st.index[-1], st.step_min)
            if key not in ambient_cache:
                ambient_cache[key] = ambient_for(st.index, fs.smhi_station)
            res = assess_station(st, fs.rated_kva, ambient_cache[key], enclosure=fs.enclosure,
                                 cable_loss_share=cable_loss_share)
            results[fs.station_id] = res
            rows.append({"station_id": fs.station_id, **res.metrics(), "error": ""})
        except Exception as e:                       # one bad station must not stop the fleet
            rows.append({"station_id": fs.station_id, "kVA": fs.rated_kva, "verdict": "error", "why": "",
                         "error": f"{type(e).__name__}: {e}"})
        if progress:
            progress(i, len(stations), fs.station_id)
    table = pd.DataFrame(rows).reindex(columns=RESULT_COLUMNS)
    table["rank_level"] = table["verdict"].map(LEVEL_RANK).fillna(3)
    table = (table.sort_values(["rank_level", "life_days_per_year", "peak_loading_pct"], ascending=[True, False, False])
             .drop(columns="rank_level").reset_index(drop=True))
    table.index = table.index + 1
    table.index.name = "rank"
    return table, results


def export_fleet_table(table: pd.DataFrame, path) -> Path:
    path = Path(path)
    table.to_csv(path, index=True, float_format="%.3f")
    return path
