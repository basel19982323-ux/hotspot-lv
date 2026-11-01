# Changelog

What changed in each version of Hotspot LV. It is a simulation tool: its results are indicative, with generic
transformer constants, and not validated on a real station.

## 0.2.0 — 1 November 2026

For grid companies' own meter exports: the *Own station data* and *Fleet screening* modes read the shapes Swedish
DSO exports take — tested on constructed files only, not yet on a real export —, take every common transformer
size, and a report can be written with one command. The thermal model and every study result are unchanged: a test
re-runs a 315 kVA study case and checks it against its committed row. An independent review of this version (six
reviewers: physics, data, code, tests, honesty, the planner's view) led to the fixes listed under each heading.

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
  - uk: Ellevio's bands up to 315 kVA (3.5 % for 50–200, 4 % for 250), the v0.1 rule of 6 % from 500 kVA (630 and
    1000 kVA too), and 5 % at 400 kVA between them. A bigger unit thus never gives a larger voltage drop at the
    same load (the review found that 4 % at 400 and 630 kVA did); a test checks it. 160, 250, 400 and 630 kVA are
    stated as assumptions; 315, 500 and 800 kVA keep their v0.1 uk.
  - The *Assumptions & sources* page lists every size with its sources.
  - The *Scenario (synthetic street)* tab offers every size too. Its *Fixes* comparison keeps the bigger units it
    offered before (500 and 800 kVA), because its unit price is the 800 kVA unit's.
- **Custom transformer** in *Own station data* and *Fleet screening* (index columns `p_noload_w`, `p_load_w`,
  `uk_percent`): any rating from 25 to 2 500 kVA (the distribution range: the thermal constants and limits used are a
  distribution transformer's), with the losses from the unit's test report.
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
  - one station of a file with a `Station` / `Nätstation` column; a rating column is shown, and a different
    rating chosen for the assessment is questioned;
  - hour numbers (`Timme` 1–24 or 0–23), intervals written out (`00:00 - 01:00`), Excel's day numbers, a units
    row under the names, metering-point ids and from/to dates above the header, a `;` at the end of every line,
    reactive energy in kvarh, a clock written in the header (`(UTC)`, `(normaltid)`, `(lokal tid)`).
- **Numbers decided per column.** The decimal sign of a column is decided by the cells that show it, so a lone
  `1.300` among `950,5` is 1 300, not 1.3 (review finding). When no cell shows it, the rules above apply, and a
  thousands reading is stated as something the file could not tell.
- **Refused with the reason, never guessed** (review findings):
  - two columns that could both be the load, such as `P L1`, `P L2`, `P L3`, or two meters' columns;
  - one row per meter;
  - a column name used twice;
  - a value column with a unit the reader does not know, and a unit chosen against the one the file states;
  - a cell that is not a number (it was filled like a gap before);
  - a spring hour with values but no 03:00 (stamps written as start + 1 hour);
  - a clock name that does not exist.
- **A peak no station has** — below 1 % or above 300 % of the rating — asks to check the decimal sign, the unit
  and the rating.
- **Daylight-saving days:**
  - Swedish local time or normaltid is detected from the days of the change.
  - A placeholder row in the spring hour that does not exist (0, blank or a copy of a neighbour) is dropped.
  - The October hour that happens twice is told apart by file order.
  - When that hour is written only once, it is taken as summer time, and the other hour is filled as a gap; when
    that row is about twice its neighbours, the notes say both hours are probably added into it.
  - Normaltid read from the spring hour always comes with a note: a file in UTC without offsets looks the same.
- **What was read, and what the file could not tell:**
  - shown on the new *File* tab, in the station card and in the report; the interval convention is always listed,
    as your setting;
  - in code: `StationSeries.found` and `.unsure`;
  - every point the file could not settle is also in the notes.
- **Fleet screening of one export with every station:**
  - a station column, and a rating column (`rated_kva`, `Märkeffekt`) or one rating for the whole file;
  - one decimal sign for the whole file;
  - a station without a rating, or with two, is its own error row: the other stations are still screened;
  - `read_fleet()` tells such a file from an index.
- **`hotspot-lv report <csv> --kva 500 --out report.pdf`** (or `.html`) writes one station's report without a
  window (options for the station, the custom unit, the clock, the unit, the decimal sign and the longest gap to
  fill; the default file name carries the station). In the Windows build, `HotspotLV.exe report …` runs it, and
  its lines go to `<report>.log.txt` next to the report, since the program has no console. It has:
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
  `tz="Europe/Stockholm"` still refuses a measured spring hour, but now drops a placeholder spring row and takes an
  October hour written once as summer time, both stated. The app offers UTC as a clock too.
- **Station card.** It has a section "What was read from the file". The temperature chart of the report sorts
  step-end values (the review found the step maximum overstated the hours above a limit).
- **Cells that are not numbers** are refused with their row; before, they became gaps and were filled.

## 0.1.0 — 23 October 2026

First public version: the desktop app (own station data, fleet screening, synthetic street with fixes, sweep and
spread) and the thermal ageing & dynamic loading study (STUDY.md).
