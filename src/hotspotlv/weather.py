"""
Hourly weather for the study year: outdoor temperature (SMHI) and rooftop-PV
output per kWp (PVGIS). Both are open data (SMHI: CC BY 4.0; PVGIS: free, JRC).
Attribution: data/SOURCES.md.

Why both matter:
  * temperature drives the heat-pump load (cold → more kW) AND the transformer
    cooling (cold → lower hot-spot, slower ageing) — the whole point of the study
  * PV output drives midday reverse power flow

Station choice: Västerås's own SMHI station (96350) is manual — readings at
06 and 18 UTC only. The nearest *automatic* hourly station is Eskilstuna A
(96190), 26 km south across Mälaren, same inland climate. PVGIS is queried for
Västerås itself (59.61 N, 16.55 E). PVGIS covers 2005–2023, so 2023 is the
reference year.

All timestamps are UTC (what SMHI and PVGIS deliver). Convert with
`.tz_convert("Europe/Stockholm")` when something depends on local clock time
(people come home at 17 local, not 17 UTC).
"""

from __future__ import annotations

import http.client
import io
import os
import time
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

from pathlib import Path

from hotspotlv.paths import cache_dir, package_data

SMHI_STATION = 96190            # Eskilstuna A (automatic, hourly, since 1990)
SMHI_STATION_NAME = "Eskilstuna A"
SMHI_HOST = "opendata-download-metobs.smhi.se"    # the only host the program downloads from (HTTPS)
SMHI_URL = "https://" + SMHI_HOST + "/api/version/1.0/parameter/1/station/{station}/period/{period}/data.csv"

VASTERAS_LAT, VASTERAS_LON = 59.61, 16.55
PV_TILT_DEG = 30                # typical Swedish villa roof
PV_AZIMUTH_DEG = 180            # pvlib convention: 180 = facing south (pvlib sends aspect = az − 180 to PVGIS)
PV_SYSTEM_LOSS_PCT = 14         # PVGIS default (soiling, wiring, inverter)
MAX_GAP_HOURS = 6               # longer gaps than this are an error, not silently filled


def _year_index(year: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h", tz="UTC")


# ---------------------------------------------------------------------------
# SMHI temperature
# ---------------------------------------------------------------------------
OFFLINE_ENV = "HOTSPOTLV_OFFLINE"   # "1": never download (the build's self-test proves the shipped data suffice)

# SMHI publishes a station's hourly temperature in two parts: the quality-checked archive, which lags about three
# months behind today, and the latest four months. Together they reach the present (packaging review round 2: a
# file of the last twelve months always failed, because only the archive was read).
ARCHIVE, LATEST = "corrected-archive", "latest-months"
_FILE = {ARCHIVE: "smhi_{station}_temp.csv", LATEST: "smhi_{station}_latest.csv"}
_SIZE = {ARCHIVE: "about 7 MB", LATEST: "about 0.1 MB"}
SMHI_HEADER = "Datum;Tid (UTC);Lufttemperatur"   # the first line of the data table in every SMHI file
ARCHIVE_REFRESH_AFTER_H = 7 * 24    # a cached archive younger than this is not downloaded again: SMHI extends
                                    # it about monthly and asks users not to fetch the same data repeatedly; a
                                    # recent file always reaches past it, the latest months (0.1 MB) cover that
_FETCHED: set[tuple[int, str]] = set()          # (station, period) downloaded in this run: never twice per run
_NOT_FOUND: set[tuple[int, str]] = set()        # (station, period) SMHI answered 404 for in this run
_PARSED: dict[tuple[str, int], pd.Series] = {}  # (file, modification time) → its series: parse 7 MB once per run


class SmhiNotFound(ValueError):
    """SMHI has no such data (HTTP 404): a wrong station number, or no latest data for a closed station."""


def _smhi_file(station: int, period: str) -> Path:
    return cache_dir(create=False) / "raw" / _FILE[period].format(station=station)


def fetch_smhi_temperature(station: int = SMHI_STATION, refresh: bool = False, period: str = ARCHIVE) -> Path:
    """The station's hourly temperature file for one SMHI period, downloaded into the user cache if it is not there.
    refresh=True downloads it again — at most once per run, and for the archive only when the cached copy is older
    than ARCHIVE_REFRESH_AFTER_H. What arrives is checked to be SMHI's file for this station before it is saved.
    A failed download raises ConnectionError with a sentence a planner can act on; HTTP 404 raises SmhiNotFound."""
    path = _smhi_file(station, period)
    if (station, period) in _NOT_FOUND:
        raise SmhiNotFound(f"SMHI has no hourly air temperatures for station {station} — check the station number "
                           "on opendata.smhi.se/metobs.")
    if path.exists():
        if not refresh or (station, period) in _FETCHED:
            return path
        age_h = (time.time() - path.stat().st_mtime) / 3600
        if period == ARCHIVE and age_h < ARCHIVE_REFRESH_AFTER_H:
            return path
    hint = (f"Outdoor temperatures for SMHI station {station} are downloaded from {SMHI_HOST} ({_SIZE[period]}, "
            f"kept in {path.parent}). ")
    if os.environ.get(OFFLINE_ENV) == "1":
        raise ConnectionError(hint + f"Downloads are switched off ({OFFLINE_ENV}=1).")
    shipped = (f" Only the 2023 temperatures of station {SMHI_STATION} {SMHI_STATION_NAME} ship with the program and "
               "work offline.")
    try:
        data = urllib.request.urlopen(SMHI_URL.format(station=station, period=period), timeout=120).read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _NOT_FOUND.add((station, period))
            raise SmhiNotFound(f"SMHI has no hourly air temperatures for station {station} — check the station "
                               "number on opendata.smhi.se/metobs.") from None
        raise ConnectionError(hint + f"SMHI answered HTTP {e.code} ({e.reason}); try again later." + shipped) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:   # no network, proxy, cut connection
        reason = getattr(e, "reason", None) or type(e).__name__
        raise ConnectionError(hint + f"The download failed ({reason}). Connect to the internet, or check the proxy "
                              "settings, and try again." + shipped) from None
    head = data[:20000].decode("utf-8-sig", errors="replace")
    if SMHI_HEADER not in head or f";{station};" not in head:
        raise ConnectionError(hint + f"{SMHI_HOST} answered, but not with the temperature file of this station (a "
                              "Wi-Fi login page or a proxy in between?). Nothing was saved. Log in to the network, or "
                              f"ask IT to allow {SMHI_HOST}, and try again." + shipped)
    _FETCHED.add((station, period))
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")              # never leave half a file behind
    part.write_bytes(data)
    part.replace(path)
    return path


def _read_smhi(path: Path) -> pd.Series:
    """One SMHI data file (either period) as an hourly series on its UTC stamps, sorted, no duplicates. A saved file
    that is not SMHI's (from before downloads were checked) is deleted, so the next attempt downloads it again."""
    key = (str(path), path.stat().st_mtime_ns)
    if key not in _PARSED:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        header = next((i for i, line in enumerate(lines) if line.startswith(SMHI_HEADER)), None)
        if header is None:
            path.unlink(missing_ok=True)
            raise ValueError(f"The saved file {path} was not SMHI's temperature file and has been deleted — try "
                             "again to download it anew.")
        df = pd.read_csv(io.StringIO("\n".join(lines[header:])), sep=";", usecols=[0, 1, 2])
        df.columns = ["date", "time", "temp_c"]
        ts = pd.to_datetime(df["date"] + " " + df["time"], utc=True)      # the column is "Tid (UTC)"
        s = pd.Series(pd.to_numeric(df["temp_c"], errors="coerce").to_numpy(dtype=float), index=ts, name="temp_c")
        s = s[s.index.notna()]                                          # a row without a date (a note line)
        _PARSED[key] = s[~s.index.duplicated()].sort_index()
    return _PARSED[key]


def _shipped_temperature(year: int, station: int) -> pd.Series | None:
    """The temperatures that ship with the program (station 96190, the weather years in package data):
    identical to the processed SMHI archive (checked to the last digit), and available offline."""
    shipped = package_data() / f"weather_vasteras_{year}.csv"
    if station != SMHI_STATION or not shipped.exists():
        return None
    t = pd.read_csv(shipped, index_col=0, parse_dates=True)["temp_c"]
    return t.rename("temp_c")


def _runs(missing: np.ndarray) -> list[tuple[int, int]]:
    """(start, stop) positions of each run of True."""
    padded = np.concatenate(([False], missing, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _long_gap(series: pd.Series) -> bool:
    return any(b - a > MAX_GAP_HOURS for a, b in _runs(series.isna().to_numpy()))


def load_smhi_hours(hours: pd.DatetimeIndex, station: int = SMHI_STATION) -> pd.Series:
    """Hourly air temperature (°C) on `hours` (whole UTC hours), from — in this order — the shipped years (station
    96190), the station's quality-checked archive, and for hours after the archive SMHI's latest four months.
    Gaps of at most MAX_GAP_HOURS consecutive hours are linearly interpolated (an edge is held at its nearest value),
    and need no download: a 2023 file in Swedish time starts at 23:00 UTC on 31 Dec 2022, and that one hour is held
    like any short SMHI gap. Longer gaps raise ValueError with their dates. Only the hours asked for are checked, so
    a file of the last twelve months is judged on those twelve months, not on its whole calendar year.
    attrs: "missing_hours_filled", and "smhi_latest_from" — the first hour taken from the latest months (None when
    every hour is from the archive or the shipped years)."""
    hours = pd.DatetimeIndex(hours)
    if hours.tz is None or str(hours.tz) != "UTC":
        raise ValueError("load_smhi_hours needs UTC hours")
    out = pd.Series(np.nan, index=hours, name="temp_c")
    for year in sorted(set(hours.year)):
        shipped = _shipped_temperature(year, station)
        if shipped is not None:
            out = out.fillna(shipped.reindex(hours))
    latest_from, offline, newest, oldest = None, None, None, None
    if _long_gap(out) or out.isna().all():
        archive = _read_smhi(fetch_smhi_temperature(station))
        if hours[out.isna().to_numpy()].max() > archive.index.max():     # SMHI extends its archive every month
            try:
                archive = _read_smhi(fetch_smhi_temperature(station, refresh=True))
            except ConnectionError as e:
                offline = e
        out = out.fillna(archive.reindex(hours))
        oldest, newest = archive.dropna().index.min(), archive.dropna().index.max()
        after = hours > archive.index.max()
        if (after & out.isna().to_numpy()).any():
            try:
                latest = _read_smhi(fetch_smhi_temperature(station, period=LATEST))
                if hours.max() > latest.index.max():
                    latest = _read_smhi(fetch_smhi_temperature(station, refresh=True, period=LATEST))
                add = latest.reindex(hours).where(after)
                if latest.notna().any():
                    newest = max(newest, latest.dropna().index.max())
                used = (add.notna() & out.isna()).to_numpy()
                out = out.fillna(add)
                if used.any():
                    latest_from = hours[used].min()
            except ConnectionError as e:
                offline = e
            except SmhiNotFound:                                 # a closed station has no latest months
                pass
    missing = out.isna().to_numpy()
    if missing.all():
        if offline is not None:
            raise offline
        if newest is not None and newest < hours[0]:
            raise ValueError(f"SMHI station {station} has temperatures up to {newest:%Y-%m-%d %H:%M} UTC; the data "
                             f"start at {hours[0]:%Y-%m-%d %H:%M} UTC — use an earlier period or another SMHI station.")
        if oldest is not None and oldest > hours[-1]:
            raise ValueError(f"SMHI station {station} has temperatures from {oldest:%Y-%m-%d %H:%M} UTC; the data "
                             f"end at {hours[-1]:%Y-%m-%d %H:%M} UTC — choose another SMHI station.")
        raise ValueError(f"SMHI station {station} has no temperatures for {hours[0]:%Y-%m-%d} to "
                         f"{hours[-1]:%Y-%m-%d} — choose another SMHI station.")
    for a, b in _runs(missing):
        if b - a <= MAX_GAP_HOURS:
            continue
        if b == len(hours) and offline is not None:          # newer data were needed and could not be downloaded
            raise offline
        first, last = hours[a], hours[b - 1]
        valid = out.dropna().index
        if b == len(hours):
            raise ValueError(f"SMHI station {station} has temperatures up to {valid.max():%Y-%m-%d %H:%M} UTC; the "
                             f"data run to {last:%Y-%m-%d %H:%M} UTC. Use a period that ends before then.")
        if a == 0:
            raise ValueError(f"SMHI station {station} has temperatures from {valid.min():%Y-%m-%d %H:%M} UTC; the "
                             f"data start at {first:%Y-%m-%d %H:%M} UTC — choose another SMHI station or a later "
                             "period.")
        raise ValueError(f"SMHI station {station} has no temperatures from {first:%Y-%m-%d %H:%M} to "
                         f"{last:%Y-%m-%d %H:%M} UTC ({b - a} h; at most {MAX_GAP_HOURS} h are filled in) — "
                         "choose another SMHI station.")
    n_missing = int(missing.sum())
    out = out.interpolate(limit_direction="both")
    out.attrs["missing_hours_filled"] = n_missing
    out.attrs["smhi_latest_from"] = latest_from
    return out


def load_smhi_temperature(year: int, station: int = SMHI_STATION) -> pd.Series:
    """Hourly air temperature (°C) for one calendar year, UTC index (see load_smhi_hours)."""
    return load_smhi_hours(_year_index(year), station)


def _year_from_archive(path, year: int) -> pd.Series:
    """One calendar year of an SMHI file, raw (with its gaps) — how the shipped weather file was made."""
    return _read_smhi(Path(path)).loc[str(year):str(year)].reindex(_year_index(year))


# ---------------------------------------------------------------------------
# PVGIS photovoltaic output
# ---------------------------------------------------------------------------
def load_pvgis_pv(year: int, lat: float = VASTERAS_LAT, lon: float = VASTERAS_LON, tag: str = "vasteras") -> pd.Series:
    """Hourly PV output in kW per kWp installed (south, 30° tilt), UTC index."""
    path = cache_dir(create=False) / "raw" / f"pvgis_{tag}_{year}.csv"
    if not path.exists():
        if os.environ.get(OFFLINE_ENV) == "1":
            raise ConnectionError(f"PV output for {year} needs a PVGIS download; downloads are switched off "
                                  f"({OFFLINE_ENV}=1).")
        import pvlib  # imported here so the rest of the package works without it
        path.parent.mkdir(parents=True, exist_ok=True)
        data, _meta = pvlib.iotools.get_pvgis_hourly(
            lat, lon, start=year, end=year,
            raddatabase="PVGIS-SARAH3",
            surface_tilt=PV_TILT_DEG, surface_azimuth=PV_AZIMUTH_DEG,
            pvcalculation=True, peakpower=1.0, loss=PV_SYSTEM_LOSS_PCT,
            mountingplace="building", components=True,
            url="https://re.jrc.ec.europa.eu/api/v5_3/", timeout=120,
        )
        data.to_csv(path)
    data = pd.read_csv(path, index_col=0, parse_dates=True)
    # PVGIS stamps each hour at HH:11 (satellite scan time) and gives P as the
    # instantaneous value at that time, in W per kWp. We use it as the mean of
    # the hour starting HH:00 (the 10:00 bucket contains solar noon, as expected).
    data.index = pd.DatetimeIndex(data.index, tz="UTC").floor("h")
    pv = (data["P"] / 1000).rename("pv_kw_per_kwp")
    return pv.reindex(_year_index(year)).fillna(0.0)


# ---------------------------------------------------------------------------
# Combined, cached
# ---------------------------------------------------------------------------
def get_weather(year: int = 2023, refresh: bool = False) -> pd.DataFrame:
    """DataFrame[temp_c, pv_kw_per_kwp], hourly for the whole year, UTC.

    Looks for a shipped file (package data), then the user cache, else builds it."""
    name = f"weather_vasteras_{year}.csv"
    for cache in (package_data() / name, cache_dir(create=False) / name):     # no folder made just to look
        if cache.exists() and not refresh:
            return pd.read_csv(cache, index_col=0, parse_dates=True)
    wx = pd.concat([load_smhi_temperature(year), load_pvgis_pv(year)], axis=1)
    wx.to_csv(cache_dir() / name, float_format="%.3f")
    return wx


if __name__ == "__main__":
    wx = get_weather(2023)
    t = wx["temp_c"]
    print(f"rows {len(wx)}  temp min {t.min():.1f} °C ({t.idxmin():%d %b %H:%M} UTC)  "
          f"mean {t.mean():.1f}  max {t.max():.1f}")
    print(f"hours below -10 °C: {(t < -10).sum()}   below 0 °C: {(t < 0).sum()}")
    print(f"PV yield {wx['pv_kw_per_kwp'].sum():.0f} kWh per kWp per year, peak {wx['pv_kw_per_kwp'].max():.2f} kW/kWp")
    local = wx.tz_convert("Europe/Stockholm")
    print("coldest local day:", local["temp_c"].resample("D").mean().idxmin().date())
