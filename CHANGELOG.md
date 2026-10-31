# Changelog

What changed in each version of Hotspot LV. It is a simulation tool: its results are indicative, with generic
transformer constants, and not validated on a real station.

## 0.2.0 — not yet released

For grid companies' own meter exports: the *Own station data* and *Fleet screening* modes read real export
shapes, take every common transformer size, and a report can be written with one command. The thermal model and
every study result are unchanged: a test re-runs a 315 kVA study case and checks it against its committed row.

### Fixed

- **Swedish number formats.** `1.226,5` was read as 1.2265. Now:
  - a cell with both `.` and `,` takes the last one as the decimal sign (`1.226,5` and `1,226.5` are 1226.5);
  - a comma alone is a decimal comma (`12,5`);
  - a dot alone is a decimal point, unless every dot-only value in the column has groups of three digits after
    it. Then the dots are thousands separators, the notes say so, and `decimal='.'` overrides it;
  - spaces, no-break spaces and a trailing unit (`12,5 kWh`) are accepted.

### Added

- **More transformer sizes:** 50, 100, 160, 200, 250, 400, 630 and 1000 kVA, with the Ecodesign Tier-2 maximum
  losses (EU 548/2014 as amended by 2019/1783, Table I.1). 200 kVA is interpolated between 160 and 250 kVA, as
  the regulation prescribes.
  - uk is Ellevio's specified value where it lists the size.
  - Where it does not (160, 250, 400 and 630 kVA), the nearest Ellevio band is used, and this is stated as an
    assumption.
  - The *Assumptions & sources* page lists every size with its sources.
- **Custom transformer** in *Own station data* and *Fleet screening* (index columns `p_noload_w`, `p_load_w`,
  `uk_percent`): any rating from 25 to 3 150 kVA, with the losses from the unit's test report.
  - The losses set the loss ratio R and the transformer's own losses; every other thermal constant stays the IEC
    default.
  - Entries that cannot be right are refused: kW typed where watts are asked, swapped losses, a uk outside 2–10 %.
- **A reader for real DSO exports** (`meterfile.py`):
  - encodings: UTF-8 with or without BOM, UTF-16 (Excel "Unicode text") and Windows-1252;
  - separators: `;`, tab, `,` or `|`;
  - lines above the header and a sum line below the table are left out, and counted;
  - dates `2025-01-01 00:00`, `2025-01-01T00:00:00+01:00`, `01/01/2025` and `01.01.2025`. The order of day and
    month is decided from the dates; when they cannot show it, day first is assumed and said so;
  - a `Värde` column with its unit in an `Enhet` column or in the cells;
  - one station of a file with a `Station` / `Nätstation` column.
- **Daylight-saving days:**
  - Swedish local time or normaltid is detected from the days of the change.
  - A placeholder row in the spring hour that does not exist (0, blank or a copy of a neighbour) is dropped.
  - The October hour that happens twice is told apart by file order.
  - When that hour is written only once, it is taken as summer time, and the other hour is filled as a gap.
- **What was read, and what the file could not tell:**
  - shown on the new *File* tab, in the station card and in the report;
  - in code: `StationSeries.found` and `.unsure`;
  - every point the file could not settle is also in the notes.
- **Fleet screening of one export with every station:**
  - a station column, and a rating column (`rated_kva`, `Märkeffekt`) or one rating for the whole file;
  - `read_fleet()` tells such a file from an index.
- **`hotspot-lv report <csv> --kva 500 --out report.pdf`** (or `.html`) writes one station's report without a
  window. It has:
  - what was read from the file;
  - peak load against the rating;
  - a hot-spot and top-oil temperature duration curve;
  - hours above the IEC 60076-7 normal cyclic limits;
  - the relative ageing rate;
  - the verdict;
  - the note "Indicative result, generic transformer constants, not validated on a real station."
- **Test files shaped like DSO exports** in `tests/fixtures/dso_exports/`, all made from one known load.

### Changed

- **Clock detection.** Timestamps are read with `tz="auto"` by default. A file in normaltid that holds a measured
  value in the spring hour was refused in 0.1 with a hint; it is now read as normaltid, and the notes say so.
  `tz="Europe/Stockholm"` keeps the strict behaviour.
- **Bigger-transformer fix.** The default of the *Fixes* comparison stays 800 kVA, although 630 kVA now exists.
- **Station card.** It has a section "What was read from the file".

## 0.1.0 — 23 October 2026

First public version: the desktop app (own station data, fleet screening, synthetic street with fixes, sweep and
spread) and the thermal ageing & dynamic loading study (STUDY.md).
