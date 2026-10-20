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

import io
import urllib.request

import numpy as np
import pandas as pd

from hotspotlv.paths import cache_dir, package_data, raw_dir

SMHI_STATION = 96190            # Eskilstuna A (automatic, hourly, since 1990)
SMHI_STATION_NAME = "Eskilstuna A"
SMHI_URL = ("https://opendata-download-metobs.smhi.se/api/version/1.0/parameter/1/"
            "station/{station}/period/corrected-archive/data.csv")

VASTERAS_LAT, VASTERAS_LON = 59.61, 16.55
PV_TILT_DEG = 30                # typical Swedish villa roof
PV_AZIMUTH_DEG = 180            # pvlib convention: 180 = facing south (pvlib sends aspect = az − 180 to PVGIS)
PV_SYSTEM_LOSS_PCT = 14         # PVGIS default (soiling, wiring, inverter)
MAX_GAP_HOURS = 6               # longer gaps than this are an error, not silently filled


def _year_index(year: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h", tz="UTC")


def _longest_nan_run(s: pd.Series) -> int:
    isna = s.isna().to_numpy()
    if not isna.any():
        return 0
    # lengths of consecutive True runs
    padded = np.concatenate(([False], isna, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return int((edges[1::2] - edges[::2]).max())


# ---------------------------------------------------------------------------
# SMHI temperature
# ---------------------------------------------------------------------------
def fetch_smhi_temperature(station: int = SMHI_STATION):
    """Download the full hourly archive (~5 MB) once into the user cache."""
    path = raw_dir() / f"smhi_{station}_temp.csv"
    if not path.exists():
        path.write_bytes(urllib.request.urlopen(SMHI_URL.format(station=station), timeout=180).read())
    return path


def load_smhi_temperature(year: int, station: int = SMHI_STATION) -> pd.Series:
    """Hourly air temperature (°C) for one year, UTC index. Gaps of at most
    MAX_GAP_HOURS consecutive hours are linearly interpolated; longer gaps raise."""
    text = fetch_smhi_temperature(station).read_text(encoding="utf-8")
    lines = text.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("Datum"))
    df = pd.read_csv(io.StringIO("\n".join(lines[header:])), sep=";", usecols=[0, 1, 2])
    df.columns = ["date", "time", "temp_c"]
    ts = pd.to_datetime(df["date"] + " " + df["time"], utc=True)      # column is "Tid (UTC)"
    temp = pd.Series(df["temp_c"].to_numpy(dtype=float), index=ts, name="temp_c").loc[str(year)]
    temp = temp[~temp.index.duplicated()].reindex(_year_index(year))
    if len(temp.dropna()) == 0:
        raise ValueError(f"SMHI {station}: no data for {year}")
    longest = _longest_nan_run(temp)
    if longest > MAX_GAP_HOURS:
        raise ValueError(f"SMHI {station} {year}: a gap of {longest} h exceeds {MAX_GAP_HOURS} h — choose another station or fill by hand")
    n_missing = int(temp.isna().sum())
    temp = temp.interpolate(limit_direction="both")
    temp.attrs["missing_hours_filled"] = n_missing
    return temp


# ---------------------------------------------------------------------------
# PVGIS photovoltaic output
# ---------------------------------------------------------------------------
def load_pvgis_pv(year: int, lat: float = VASTERAS_LAT, lon: float = VASTERAS_LON, tag: str = "vasteras") -> pd.Series:
    """Hourly PV output in kW per kWp installed (south, 30° tilt), UTC index."""
    path = raw_dir() / f"pvgis_{tag}_{year}.csv"
    if not path.exists():
        import pvlib  # imported here so the rest of the package works without it
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
    for cache in (package_data() / name, cache_dir() / name):
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
