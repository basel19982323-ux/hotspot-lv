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
import tempfile
import threading
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
    """The hours of one Swedish calendar year as UTC stamps — 2023: 2022-12-31 23:00 … 2023-12-31 22:00 UTC, i.e.
    1 Jan 00:00 – 31 Dec 23:00 Swedish time; 8 760 hours (8 784 in a leap year; summer time does not change the
    count). The year a Swedish planner means (numbers audit of 23 Oct 2026: the UTC year showed as 1 Jan 01:00 –
    1 Jan 00:00 of the next year)."""
    start = pd.Timestamp(f"{year}-01-01", tz="Europe/Stockholm").tz_convert("UTC")
    end = pd.Timestamp(f"{year + 1}-01-01", tz="Europe/Stockholm").tz_convert("UTC")
    return pd.date_range(start, end, freq="h", inclusive="left")


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
ARCHIVE_REFRESH_AFTER_H = 24        # the archive (7 MB) is fetched again only when the data fall into a gap between
                                    # the saved archive and the latest months, and the saved copy is older than
                                    # this — never just because a recent file reaches past it (round-3 review: a
                                    # weekly 7 MB download, byte for byte the same; SMHI asks users not to fetch
                                    # the same data repeatedly)
SMHI_STATIONS_URL = "https://www.smhi.se/data/hitta-data-for-en-plats/ladda-ner-vaderobservationer"
_FETCHED: set[tuple[int, str]] = set()          # (station, period) downloaded in this run: never twice per run
_NOT_FOUND: set[tuple[int, str]] = set()        # (station, period) SMHI answered 404 for in this run
_PARSED: dict[tuple[str, int], pd.Series] = {}  # (file, modification time) → its series: parse 7 MB once per run
_DOWNLOAD = threading.Lock()                    # the station and the fleet tab can both need the same file at once


class SmhiNotFound(ValueError):
    """SMHI has no such data (HTTP 404): a wrong station number, or no latest data for a closed station."""


def _smhi_file(station: int, period: str) -> Path:
    return cache_dir(create=False) / "raw" / _FILE[period].format(station=station)


def fetch_smhi_temperature(station: int = SMHI_STATION, refresh: bool = False, period: str = ARCHIVE) -> Path:
    """The station's hourly temperature file for one SMHI period, downloaded into the user cache if it is not there.
    refresh=True downloads it again — at most once per run, and for the archive only when the cached copy is older
    than ARCHIVE_REFRESH_AFTER_H. What arrives is checked to be SMHI's file for this station before it is saved.
    A failed download raises ConnectionError with a sentence a planner can act on; HTTP 404 raises SmhiNotFound.
    One download at a time: a second caller waits and then finds the file."""
    with _DOWNLOAD:
        return _fetch(station, refresh, period)


def _not_found(station: int) -> SmhiNotFound:
    return SmhiNotFound(f"SMHI has no hourly air temperatures for station {station} — check the station number on "
                        f"SMHI's station map: {SMHI_STATIONS_URL}")


def _fetch(station: int, refresh: bool, period: str) -> Path:
    path = _smhi_file(station, period)
    if (station, period) in _NOT_FOUND:
        raise _not_found(station)
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
            raise _not_found(station) from None
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
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".part", delete=False) as part:
        tmp = Path(part.name)
        try:
            part.write(data)                                # never leave half a file behind under the real name
        except OSError:
            part.close()
            tmp.unlink(missing_ok=True)
            raise
    tmp.replace(path)
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


def _needs(hours: pd.DatetimeIndex, station: int) -> str:
    """The first words of a download error: which hours of the data needed SMHI and are not on this PC."""
    return (f"The data need SMHI temperatures of station {station} for {hours.min():%Y-%m-%d %H:%M} to "
            f"{hours.max():%Y-%m-%d %H:%M} UTC, which are not saved on this PC. ")


def _latest_months(station: int, until: pd.Timestamp) -> tuple[pd.Series | None, ConnectionError | None]:
    """SMHI's latest months for the station: the saved copy, fetched again (once per run) when `until` lies past it.
    A failed refresh keeps the saved copy (round-3 review: it was thrown away, so a file that assessed in one run
    failed in the next); the error comes back for the message if hours are still missing."""
    try:
        latest = _read_smhi(fetch_smhi_temperature(station, period=LATEST))
    except SmhiNotFound:                                     # a closed station has no latest months
        return None, None
    except ConnectionError as e:
        return None, e
    if until > latest.index.max():
        try:
            latest = _read_smhi(fetch_smhi_temperature(station, refresh=True, period=LATEST))
        except SmhiNotFound:
            pass
        except ConnectionError as e:
            return latest, e
    return latest, None


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
    latest_from, offline, newest, oldest, archive_end = None, None, None, None, None
    if _long_gap(out) or out.isna().all():
        try:
            archive = _read_smhi(fetch_smhi_temperature(station))
        except ConnectionError as e:
            raise ConnectionError(_needs(hours[out.isna().to_numpy()], station) + str(e)) from None
        latest = None
        after = hours > archive.index.max()
        if (after & out.fillna(archive.reindex(hours)).isna().to_numpy()).any():   # hours after the checked archive
            latest, offline = _latest_months(station, hours.max())
            # a long gap between the saved archive's end and the latest months' start: the saved archive is old
            # and SMHI has extended it since — fetch it again (at most once per run, and only if a day old)
            reach = latest.index.max() if latest is not None else hours.max()
            covered = out.fillna(archive.reindex(hours))
            if latest is not None:
                covered = covered.fillna(latest.reindex(hours))
            if _long_gap(covered.where(after & (hours <= reach), 0.0)):   # only that stretch; elsewhere "covered"
                try:
                    archive = _read_smhi(fetch_smhi_temperature(station, refresh=True))
                except ConnectionError as e:
                    offline = offline or e
        out = out.fillna(archive.reindex(hours))             # checked data first …
        archive_end = archive.index.max()
        oldest, newest = archive.dropna().index.min(), archive.dropna().index.max()
        if latest is not None:                               # … then the latest months, only after the archive
            add = latest.reindex(hours).where(hours > archive_end)
            used = (add.notna() & out.isna()).to_numpy()
            out = out.fillna(add)
            if used.any():
                latest_from = hours[used].min()
            if latest.notna().any():
                newest = max(newest, latest.dropna().index.max())
    missing = out.isna().to_numpy()
    if missing.all():
        if offline is not None:
            raise ConnectionError(_needs(hours, station) + str(offline)) from None
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
        first, last = hours[a], hours[b - 1]
        if offline is not None and archive_end is not None and last > archive_end:
            raise ConnectionError(_needs(hours[a:b], station) + str(offline)) from None   # newer data could not
        valid = out.dropna().index                                                          # be fetched
        if b == len(hours):
            raise ValueError(f"SMHI station {station} has temperatures up to {valid.max():%Y-%m-%d %H:%M} UTC; the "
                             f"data run to {last:%Y-%m-%d %H:%M} UTC. Use a period that ends before then. (SMHI adds "
                             "an hour every hour; the program fetches the latest months at most once per run, so a "
                             "later run reaches further.)")
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
    return _read_smhi(Path(path)).reindex(_year_index(year))


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
    """DataFrame[temp_c, pv_kw_per_kwp], hourly for the Swedish calendar year (UTC stamps, see _year_index).

    Looks for a shipped file (package data), then the user cache, else builds it. The shipped 2023 file holds one
    hour more (to 2023-12-31 23:00 UTC) so a file stamped in UTC is covered too; this returns the 8 760 hours."""
    name = f"weather_vasteras_{year}.csv"
    for cache in (package_data() / name, cache_dir(create=False) / name):     # no folder made just to look
        if cache.exists() and not refresh:
            wx = pd.read_csv(cache, index_col=0, parse_dates=True).reindex(_year_index(year))
            if wx.isna().any().any():                     # a cache built on the UTC year by an older version
                continue
            return wx
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
