# Study weather years

`weather/weather_<year>.csv`: one Swedish calendar year of hourly weather for the study, built by
`study/make_weather_years.py` with the program's own `hotspotlv.weather.get_weather(year)`.

- **Index:** the start of each hour in UTC, from 1 Jan 00:00 to 31 Dec 23:00 Swedish local time. For example,
  `2009-12-31 23:00:00+00:00` is 1 Jan 2010 00:00 CET. 8 760 rows, or 8 784 in a leap year.
- **Years:** 2009–2015, 2017, 2018 and 2020–2023.
  - 2016 and 2019 are left out: SMHI's archive has an 11-hour gap in each, and the program fills gaps of at most
    6 hours.
  - PVGIS SARAH3 ends in 2023.

| Column | Content | Source | Licence / attribution |
|---|---|---|---|
| `temp_c` | Hourly air temperature, °C, station 96190 Eskilstuna A (26 km from Västerås), gaps of up to 6 h linearly interpolated | SMHI Open Data, meteorological observations, parameter 1, period `corrected-archive` — https://opendata.smhi.se/metobs | CC BY 4.0 — "Source: SMHI". Processed as described here. |
| `pv_kw_per_kwp` | Hourly output of a 1 kWp PV system at 59.61 N 16.55 E (Västerås), 30° tilt, facing south, 14 % system losses | PVGIS 5.3, SARAH3 radiation database, European Commission Joint Research Centre | "PVGIS © European Communities, 2001–2026". Processed: stamps floored to the hour. |

The data keep their own licences. They are not under the program's MIT licence. See also
`src/hotspotlv/data/SOURCES.md`.
