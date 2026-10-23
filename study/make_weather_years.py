"""Build the weather years of the study: study/data/weather/weather_<year>.csv (temp_c, pv_kw_per_kwp).

    python study/make_weather_years.py

Each file holds one Swedish calendar year (1 Jan 00:00 – 31 Dec 23:00 Swedish time, as UTC stamps), built the way
the program builds any year (hotspotlv.weather.get_weather):
- temp_c: SMHI open data, station 96190 Eskilstuna A, hourly air temperature, corrected archive (Source: SMHI,
  CC BY 4.0); gaps of at most 6 h linearly interpolated.
- pv_kw_per_kwp: PVGIS 5.3 (SARAH3) hourly PV output for Västerås, 1 kWp, 30° south (PVGIS © European
  Communities, 2001–2026).

Years: 2009–2023 except 2016 and 2019, where SMHI's archive has gaps longer than 6 h (2016: four of 11 h; 2019:
7 h and 11 h) — the program's own rule refuses to fill those, and the study follows it. 2008 has a 434-h gap (the
record restarts on 19 Jan 2008); PVGIS SARAH3 ends in 2023.
Downloads go to study/.cache (not committed); an SMHI archive already in the user cache is copied, not re-fetched.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
OUT = HERE / "data" / "weather"
YEARS = [2009, 2010, 2011, 2012, 2013, 2014, 2015, 2017, 2018, 2020, 2021, 2022, 2023]
SKIPPED = {2016: "SMHI gaps of 11 h (four)", 2019: "SMHI gaps of 7 h and 11 h"}


def main() -> int:
    os.environ["HOTSPOTLV_CACHE"] = str(CACHE)                  # before hotspotlv reads it
    from hotspotlv import weather as w
    (CACHE / "raw").mkdir(parents=True, exist_ok=True)
    mine = Path.home() / ".hotspotlv" / "raw" / "smhi_96190_temp.csv"
    if mine.exists() and not (CACHE / "raw" / mine.name).exists():
        shutil.copy2(mine, CACHE / "raw" / mine.name)
    OUT.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        wx = w.get_weather(year)
        wx.to_csv(OUT / f"weather_{year}.csv", float_format="%.3f", lineterminator="\n")
        t = wx["temp_c"]
        print(f"{year}: {len(wx)} h, temp {t.min():.1f} … {t.max():.1f} °C (mean {t.mean():.2f}), "
              f"hours below −10 °C {int((t < -10).sum())}, PV {wx['pv_kw_per_kwp'].sum():.0f} kWh/kWp")
    print("skipped:", SKIPPED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
