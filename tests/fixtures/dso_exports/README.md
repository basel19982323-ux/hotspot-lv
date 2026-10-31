# DSO-export test files

Synthetic files, not real exports. Each one copies a way a Swedish meter-data export can look, and all are made
from one known load (`kw_at` in `make_fixtures.py`), so `tests/test_dso_exports.py` can check every value read
back at its UTC instant. Regenerate them with `python tests/fixtures/dso_exports/make_fixtures.py`.

| File | Encoding, separator | What it is meant to catch |
|---|---|---|
| `hourly_kwh_cp1252_preamble_spring.csv` | Windows-1252, `;` | Four lines above the header; hourly kWh with decimal commas; the spring change in local time (no 02:00 row) |
| `quarter_hour_kw_utf8bom_autumn.csv` | UTF-8 with BOM, `;` | Quarter-hour kW; the October hour 02:00–02:45 twice, summer time first |
| `hourly_kw_slash_dates_dayfirst.csv` | UTF-8, `,` | Dates `dd/mm/yyyy` (13/01 shows the order); decimal points |
| `quarter_hour_kwh_iso_offsets.csv` | UTF-8, `;` | ISO 8601 stamps with `+01:00` / `+02:00`; kWh per 15 minutes |
| `hourly_kwh_spring_zero_row.csv` | UTF-8, `;` | The spring hour that does not exist written as a 0 row |
| `hourly_kwh_spring_duplicate_row.csv` | UTF-8, `;` | The same hour written as a copy of 03:00 (a duplicate in March) |
| `hourly_kw_autumn_hour_once.csv` | UTF-8, `;` | The October hour written once (the exporter dropped the second) |
| `hourly_kwh_normaltid_spring.csv` | UTF-8, `;` | Normaltid (CET all year): the spring 02:00 hour carries a measured value |
| `multi_station_natstation.csv` | Windows-1252, `;` | Three stations in one file: `Nätstation` and `Märkeffekt (kVA)` columns |
| `quarter_hour_wh_utf16_tab_value_unit.txt` | UTF-16 with BOM, tab | Excel "Unicode text": `Datum` + `Tid` columns, a `Värde` column with its unit in `Enhet` (Wh), a sum line below |
| `hourly_kwh_thousands_dots_footer.csv` | UTF-8, `;` | Dates `dd.mm.yyyy`; `1.226,5` thousands dots; a `Summa` line below the table |
