"""
Hourly weather for the study year: outdoor temperature (SMHI) and rooftop-PV
output per kWp (PVGIS). Both are open data (SMHI: CC BY 4.0; PVGIS: free, JRC).

Why both matter:
  * temperature drives the heat-pump load (cold → more kW) AND the transformer
    cooling (cold → lower hot-spot, slower ageing) — the whole point of the study
  * PV output drives midday reverse power flow

Station choice: Västerås's own SMHI station (96350) is manual — two readings a
day. The nearest *automatic* hourly station is Eskilstuna A (96190), 35 km
south across Mälaren, same inland climate. PVGIS is queried for Västerås itself
(59.61 N, 16.55 E). PVGIS covers 2005–2023, so 2023 is the reference year.

All timestamps are UTC (what SMHI and PVGIS deliver). Convert with
`.tz_convert("Europe/Stockholm")` when something depends on local clock time
(people come home at 17 local, not 17 UTC).
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
RAW_DIR = DATA_DIR / "raw"

SMHI_STATION = 96190            # Eskilstuna A (automatic, hourly, since 1990)
SMHI_STATION_NAME = "Eskilstuna A"
SMHI_URL = ("https://opendata-download-metobs.smhi.se/api/version/1.0/parameter/1/"
            "station/{station}/period/corrected-archive/data.csv")

VASTERAS_LAT, VASTERAS_LON = 59.61, 16.55
PV_TILT_DEG = 30                # typical Swedish villa roof
PV_AZIMUTH_DEG = 180            # pvlib convention: 180 = facing south
PV_SYSTEM_LOSS_PCT = 14         # PVGIS default (soiling, wiring, inverter)
MAX_GAP_HOURS = 6               # longer gaps than this are reported, not hidden


# ---------------------------------------------------------------------------
# SMHI temperature
# ---------------------------------------------------------------------------
def _smhi_raw_path(station: int) -> Path:
    return RAW_DIR / f"smhi_{station}_temp.csv"


def fetch_smhi_temperature(station: int = SMHI_STATION) -> Path:
    """Download the full hourly archive (~5 MB) once into data/raw/."""
    path = _smhi_raw_path(station)
    if not path.exists():
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urllib.request.urlopen(SMHI_URL.format(station=station), timeout=180).read())
    return path


def load_smhi_temperature(year: int, station: int = SMHI_STATION) -> pd.Series:
    """Hourly air temperature (°C) for one year, UTC index, gaps ≤ 6 h interpolated."""
    text = fetch_smhi_temperature(station).read_text(encoding="utf-8")
    lines = text.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("Datum"))
    df = pd.read_csv(io.StringIO("\n".join(lines[header:])), sep=";", usecols=[0, 1, 2])
    df.columns = ["date", "time", "temp_c"]
    ts = pd.to_datetime(df["date"] + " " + df["time"], utc=True)
    temp = pd.Series(df["temp_c"].to_numpy(), index=ts, name="temp_c").loc[str(year)]
    full = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h", tz="UTC")
    temp = temp[~temp.index.duplicated()].reindex(full)
    n_missing = int(temp.isna().sum())
    temp = temp.interpolate(limit=MAX_GAP_HOURS, limit_direction="both")
    if temp.isna().any():
        raise ValueError(f"SMHI {station} {year}: gap longer than {MAX_GAP_HOURS} h, fill by hand")
    temp.attrs["missing_hours_filled"] = n_missing
    return temp


# ---------------------------------------------------------------------------
# PVGIS photovoltaic output
# ---------------------------------------------------------------------------
def _pvgis_raw_path(year: int) -> Path:
    return RAW_DIR / f"pvgis_vasteras_{year}.csv"


def load_pvgis_pv(year: int) -> pd.Series:
    """Hourly PV output in kW per kWp installed (south, 30° tilt), UTC index."""
    path = _pvgis_raw_path(year)
    if not path.exists():
        import pvlib  # imported here so the rest of the package works without it
        data, _meta = pvlib.iotools.get_pvgis_hourly(
            VASTERAS_LAT, VASTERAS_LON, start=year, end=year,
            raddatabase="PVGIS-SARAH3",
            surface_tilt=PV_TILT_DEG, surface_azimuth=PV_AZIMUTH_DEG,
            pvcalculation=True, peakpower=1.0, loss=PV_SYSTEM_LOSS_PCT,
            mountingplace="building", components=True,
            url="https://re.jrc.ec.europa.eu/api/v5_3/", timeout=120,
        )
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        data.to_csv(path)
    data = pd.read_csv(path, index_col=0, parse_dates=True)
    # PVGIS stamps hours at HH:10 (satellite scan time) → snap to the full hour
    data.index = pd.DatetimeIndex(data.index, tz="UTC").floor("h")
    pv = (data["P"] / 1000).rename("pv_kw_per_kwp")  # W per 1 kWp → kW per kWp
    full = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h", tz="UTC")
    return pv.reindex(full).fillna(0.0)


# ---------------------------------------------------------------------------
# Combined, cached
# ---------------------------------------------------------------------------
def get_weather(year: int = 2023, refresh: bool = False) -> pd.DataFrame:
    """DataFrame[temp_c, pv_kw_per_kwp], 8760 hourly rows, UTC.
    Cached as data/weather_vasteras_<year>.csv (small, committed)."""
    cache = DATA_DIR / f"weather_vasteras_{year}.csv"
    if cache.exists() and not refresh:
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    wx = pd.concat([load_smhi_temperature(year), load_pvgis_pv(year)], axis=1)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    wx.to_csv(cache, float_format="%.3f")
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
