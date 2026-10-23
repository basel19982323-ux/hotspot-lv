"""Build the weather file shipped with the program: src/hotspotlv/data/weather_vasteras_2023.csv.

    python validation/make_weather.py

Hours: 2022-12-31 23:00 … 2023-12-31 23:00 UTC (8 761) — the Swedish calendar year 2023 (1 Jan 00:00 – 31 Dec
23:00 Swedish time) plus the last hour of the UTC year, so a station file in either convention is covered without a
download. `get_weather(2023)` returns the 8 760 hours of the Swedish year.

- temp_c: SMHI open data, station 96190 Eskilstuna A, air temperature, period corrected-archive (Source: SMHI,
  CC BY 4.0). Missing hours (at most 6 in a row) linearly interpolated; the script stops on a longer gap.
- pv_kw_per_kwp: PVGIS 5.3 hourly PV output for 59.61 N 16.55 E (see weather.load_pvgis_pv), stamps floored to the
  hour, W → kW per kWp. Hours PVGIS does not return (the night hour 2022-12-31 23:00 UTC) are 0.

Uses the user cache (~/.hotspotlv/raw) and downloads what is missing. Prints a comparison with the previous file.
"""

from __future__ import annotations

import pandas as pd

from hotspotlv import weather as w
from hotspotlv.paths import package_data

OUT = package_data() / "weather_vasteras_2023.csv"
HOURS = pd.date_range("2022-12-31 23:00", "2023-12-31 23:00", freq="h", tz="UTC")


def main() -> None:
    raw = w._read_smhi(w.fetch_smhi_temperature(w.SMHI_STATION)).reindex(HOURS)
    longest = max((b - a for a, b in w._runs(raw.isna().to_numpy())), default=0)
    if longest > w.MAX_GAP_HOURS:
        raise SystemExit(f"a gap of {longest} h in SMHI's archive — not filled")
    filled = raw.isna().sum()
    temp = raw.interpolate(limit_direction="both").rename("temp_c")
    pv = w.load_pvgis_pv(2023).reindex(HOURS).fillna(0.0).rename("pv_kw_per_kwp")
    wx = pd.concat([temp, pv], axis=1)
    if OUT.exists():
        old = pd.read_csv(OUT, index_col=0, parse_dates=True)
        both = old.index.intersection(wx.index)
        print(f"previous file: {len(old)} rows; {len(both)} in common, largest difference "
              f"{(old.loc[both] - wx.loc[both]).abs().max().to_dict()}")
    wx.to_csv(OUT, float_format="%.3f", lineterminator="\n")
    print(f"{OUT.name}: {len(wx)} hours {wx.index[0]} … {wx.index[-1]}, {filled} SMHI hours interpolated; "
          f"first hour {wx.iloc[0].to_dict()}")


if __name__ == "__main__":
    main()
