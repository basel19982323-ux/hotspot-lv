"""
Mode B — a real station from the DSO's own data.

Since 1 January 2025 every Swedish electricity meter delivers at least hourly
(mostly quarter-hour) values. Summing the meters behind a nätstation gives its
load profile — what Karlstad Elnät did by hand in 2018. This module takes
such a profile (or a transformer-side measurement) and runs it through the
thermal model and the verdict. No feeder model is needed, so the voltage and
cable rules are reported as "not checked" rather than silently passed.

    CSV ──► read_station_csv ──► StationSeries (P, Q per step, UTC)
                                       │
          SMHI ambient for the place ──┤
                                       ▼
                  s_from_meters: S at the transformer = √((P + losses)² + (Q + q_loss)²)
                                       ▼
                   IEC 60076-7 thermal (kiosk by default) ──► verdict

What the reader accepts (headers matched after normalising case, units in
parentheses and Swedish/English synonyms):
  * the file in the shapes DSO exports take (meterfile.py): UTF-8 with or without
    BOM, UTF-16 or Windows-1252; separator ; tab , or |; lines above the header
    (metering-point ids, from/to dates), a units row under the names, empty
    ';;' lines, a ';' at the end of every line, and a sum line below the table
  * one timestamp column, or separate date + time columns (`Datum;Tid`, or
    `Datum;Timme` with hour numbers 1–24 or 0–23 or ranges '00-01');
    `2025-01-01 00:00`, `2025-01-01T00:00:00+01:00`, `01/01/2025 00:00` (day or
    month first decided from the dates, else day first and said so), `01.01.2025`,
    intervals written out ('2025-01-01 00:00 - 01:00': the start), Excel's day
    numbers; a clock in the header — '(UTC)', '(normaltid)', '(lokal tid)' — is used
  * active power per step (`kW`, `Effekt`, `Aktiv effekt`) or energy per step
    (`kWh`, `Energi`, `Förbrukning`, `Uttag`, `Wh`), or a `Värde` column with its
    unit in an `Enhet` column or in the cells; an export column (`Produktion`,
    `Inmatning`, `Export`) is subtracted → net P; `unit=` decides a column whose
    unit the file does not state
  * optional reactive power (`kvar`, `Reaktiv effekt`, `kvarh` per step) and,
    for a transformer measurement, apparent power (`kVA`)
  * a station column (`Station`, `Nätstation`): one station is read (`station=`);
    a file with several goes to the fleet mode (fleet.read_fleet_long); a rating
    column is reported, and a rating it contradicts is questioned
  * decimal point or comma, thousands dot, comma or space, decided per column
    from the cells that show it (_to_number)
Refused with the reason, never guessed: two columns for the load (phases, meters),
one row per meter, a column name twice, a value column with an unknown unit, a
cell that is not a number, a quarter-hour 'Värde' column without a unit, a unit
chosen against the one the file states, a clock name that does not exist.
  * timestamps: tz="auto" (default) — Swedish local time with summer time, unless
    the daylight-saving days of the file show normaltid (CET all year: EIFS
    2025:1 1 kap. 2 § keeps registered meter values in normaltid, while values
    made available to customers carry the current time, 12 kap. 5 §;
    meterfile.to_utc says how);
    `tz="Europe/Stockholm"` or `"normaltid"` fix it; ISO stamps with offsets or
    `Z` are read as given; `24:00` is the next day's 00:00. The spring hour that
    does not exist is dropped when it holds a placeholder (0, blank, a copy of
    its neighbour); the October hour that happens twice is told apart by file order
  * `stamp="start"` (default) means a row labels the interval that STARTS at
    the stamp; `stamp="end"` shifts interval-end stamps back one step
Nothing is dropped or realigned silently: duplicated stamps with different
values, rows off the meter grid and gaps longer than `max_gap_steps` all raise
with the position. Short gaps are interpolated and counted; every assumption made
is written to `notes`, what was found in the file to `found`, and what the file
could not tell to `unsure`.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from hotspotlv import meterfile
from hotspotlv.catalogue import transformer
from hotspotlv.feeder import HOUSE_FUSE_A, POWER_FACTOR, fuse_kw
from hotspotlv.loads.ev import EVParams, ev_charging_profile
from hotspotlv.loads.heatpump import HeatPumpParams, heat_pump_profile
from hotspotlv.thermal import K_MAX, ThermalParams, ThermalResult, simulate
from hotspotlv.timeseries import YearResult
from hotspotlv.verdict import HOURS_PER_YEAR, Verdict, judge
from hotspotlv.weather import SMHI_STATION, SMHI_STATION_NAME, load_smhi_hours

LOCAL_TZ = meterfile.LOCAL_TZ          # "Europe/Stockholm"
NORMALTID_TZ = meterfile.NORMALTID_TZ   # CET all year, no DST
DATE_NAMES = ("datum", "date", "dag")
TIME_NAMES = ("tid", "time", "klockslag", "timme")
TIMESTAMP_NAMES = ("timestamp", "datetime", "tidpunkt", "datum_tid", "date_time", "datumtid", "period", "starttid")
P_NAMES = ("p_kw", "kw", "effekt", "aktiv_effekt", "active_power", "active_power_kw", "medeleffekt", "power")
E_NAMES = ("kwh", "energy_kwh", "energy", "energi", "e_kwh", "forbrukning", "uttag", "import", "consumption",
           "konsumtion", "forbrukad_energi")
EXPORT_NAMES = ("produktion", "inmatning", "export", "generation", "feed_in", "production", "utmatning")
Q_NAMES = ("q_kvar", "kvar", "reaktiv_effekt", "reactive_power", "reactive_power_kvar", "reaktiv_energi",
           "reactive_energy", "kvarh")
S_NAMES = ("s_kva", "kva", "skenbar_effekt", "apparent_power", "apparent_power_kva")
VALUE_NAMES = ("varde", "value", "matvarde", "matvarden", "volym", "kvantitet", "quantity", "reading")
UNIT_COL_NAMES = ("enhet", "unit", "matenhet", "units")
STATION_NAMES = ("station_id", "station", "natstation", "natstation_id", "natstationsnummer", "stationsnummer",
                 "stationsnamn", "station_name", "substation", "substation_id", "secondary_substation")
RATING_NAMES = ("rated_kva", "markeffekt", "trafo_kva", "transformer_kva", "rating_kva", "transformatorstorlek",
                "trafostorlek", "rating", "nameplate_kva", "transformatoreffekt", "trafoeffekt", "trafo_storlek")
METER_NAMES = ("anlaggnings_id", "anlaggningsid", "anlaggning", "anlaggningsidentitet", "matpunkt", "matpunkt_id",
               "matpunktsid", "matarpunkt", "meter", "meter_id", "metering_point", "matar_id", "matarnummer")
CLOCK_HINTS = {"utc": "UTC", "gmt": "UTC", "z": "UTC", "normaltid": "normaltid", "utc+1": "normaltid",
               "utc+01:00": "normaltid", "gmt+1": "normaltid", "lokaltid": LOCAL_TZ, "svensktid": LOCAL_TZ,
               "cet/cest": LOCAL_TZ, "sommartid": LOCAL_TZ, "aktuelltid": LOCAL_TZ}   # a header's '(…)' after a time
SINGLE_LETTER = {"p": "p", "q": "q", "s": "s", "e": "e"}      # accepted only when nothing else matches
ALLOWED_STEPS_MIN = (5.0, 10.0, 15.0, 30.0, 60.0)
MAX_GAP_STEPS_DEFAULT = 6
CABLE_LOSS_SHARE_AT_RATED = 0.042        # LV cable losses as a share of P at rated load (I²R → share ∝ K);
                                         # fitted on the project feeder: ≈ 4.2 %·K (review of 21 Oct 2026)
OFFSET_RE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})\s*$")
DATE_RE = meterfile.DATE_RE
UNIT_SCALE = {            # → kW for power columns, kWh for energy columns
    "kw": ("power", 1.0), "mw": ("power", 1000.0), "w": ("power", 0.001),
    "kwh": ("energy", 1.0), "mwh": ("energy", 1000.0), "wh": ("energy", 0.001),
    "kvar": ("reactive", 1.0), "mvar": ("reactive", 1000.0),
    "kvarh": ("reactive_energy", 1.0), "mvarh": ("reactive_energy", 1000.0),
    "kva": ("apparent", 1.0), "mva": ("apparent", 1000.0),
}


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
@dataclass
class StationSeries:
    """Cleaned station measurements on a uniform UTC index."""
    p_kw: pd.Series
    q_kvar: pd.Series
    s_kva_measured: pd.Series | None     # only if the CSV had S and measured_at == "transformer"
    step_min: float
    measured_at: str                     # "meters" (sum of customer meters) or "transformer"
    source: str = ""
    gaps_filled: int = 0
    notes: list[str] = field(default_factory=list)
    found: list[tuple[str, str]] = field(default_factory=list)    # what was read from the file, and how
    unsure: list[str] = field(default_factory=list)               # what the file could not tell (and what was assumed)
    file_kva: float | None = None                                   # a rating the file itself gives (a rating column)

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.p_kw.index

    @property
    def years(self) -> list[int]:
        return sorted(set(self.index.tz_convert(LOCAL_TZ).year))

    def period(self) -> str:
        """From the start of the first interval to the end of the last, in Swedish time (numbers audit of 23 Oct
        2026: the dates of the first and last stamps made a full year read '2023-01-01 – 2024-01-01')."""
        start = self.index[0].tz_convert(LOCAL_TZ)
        end = (self.index[-1] + pd.Timedelta(minutes=self.step_min)).tz_convert(LOCAL_TZ)
        if start == start.normalize() and end == end.normalize():            # whole Swedish days
            return f"{start:%Y-%m-%d} – {end - pd.Timedelta(days=1):%Y-%m-%d} (Swedish time, whole days)"
        return f"{start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} (Swedish time)"


def _norm(name: str) -> tuple[str, str]:
    """('normalised_name', 'bracket') — 'Förbrukning (kWh)' → ('forbrukning', 'kwh'), 'Effekt kW' → ('effekt', 'kw'),
    'Tidpunkt (CET)' → ('tidpunkt', 'cet'). The bracket is kept as written (lower case, no spaces): a VALUE column
    whose bracket is not a known unit is refused in station_from_frame — reading 'Energi (GWh)' as kWh would be
    wrong by 10⁶, silently — while a bracket on a date, station or status column is only a label."""
    s = str(name).strip().lower()
    unit = ""
    m = re.search(r"\(([^)]*)\)|\[([^\]]*)\]", s)
    if m:
        unit = (m.group(1) or m.group(2) or "").strip().lower().replace(" ", "")
        s = s[:m.start()] + s[m.end():]
    s = (s.replace("å", "a").replace("ä", "a").replace("ö", "o").replace("é", "e"))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    tail = re.search(r"(?:^|_)(mwh|kwh|wh|mw|kw|w|kvarh|mvarh|kvar|mvar|kva|mva)$", s)   # 'forbrukning_kwh', bare 'wh'
    if tail and not unit:
        unit = tail.group(1)
        s = s[:tail.start()].strip("_") or unit
    return s, unit


def _pick(cols: dict[str, tuple[str, str]], names, unit_hints=()) -> str | None:
    """First original column whose normalised name is in `names` (or whose unit hint matches)."""
    for orig, (n, u) in cols.items():
        if n in names:
            return orig
    for orig, (n, u) in cols.items():
        if u and u in unit_hints:
            return orig
    return None


def _kind(unit: str) -> str:
    return UNIT_SCALE.get(unit, ("?",))[0] if unit else ""


def _pick_value(cols: dict, role: str, names, unit_hints, kinds) -> str | None:
    """The one column for a value role (load, energy, export, reactive, apparent). A name match whose stated unit is
    of another kind ('Effekt (kVA)') is not this role. Two or more candidates raise: the reader never silently takes
    the first of 'P L1', 'P L2', 'P L3' or of two meters' columns (v0.2 review)."""
    by_name = [o for o, (n, u) in cols.items() if n in names and _kind(u) in ("",) + kinds]
    hits = by_name or [o for o, (n, u) in cols.items() if u and u in unit_hints]
    if len(hits) > 1:
        raise ValueError(f"several columns could be the {role}: {', '.join(map(repr, hits))} — the reader adds no "
                         "columns up (phases, meters): add them up in the file, or name the one column to read "
                         "(p_col= / e_col=)")
    return hits[0] if hits else None


UNIT_SUFFIX_RE = re.compile(r"(?i)\s*(?:kvarh|kwh|mwh|wh|kvar|mvar|kva|mva|kw|mw|w)\s*$")
THOUSANDS_SPACE_RE = re.compile(r"[\s\u00a0\u202f]")      # space, no-break space, narrow no-break space
DOT_GROUPED_RE = re.compile(r"^[-+]?[1-9]\d{0,2}(?:\.\d{3})+$")   # 1.226 / 12.345.678: groups of three, no leading 0
BLANKS = {"", "nan", "none", "-", "null", "n/a", "na"}


COMMA_GROUPED_RE = re.compile(r"^[-+]?[1-9]\d{0,2}(?:,\d{3})+$")   # 1,226 / 12,345: groups of three, no leading 0


def _cells(col: pd.Series) -> pd.Series:
    """The cells as number text: unit after the number, spaces (also no-break) and a leading '+' removed, the
    minus sign '−' as '-', blanks as ''."""
    s = col.astype(str).str.strip().str.replace("−", "-", regex=False)
    s = s.str.replace(UNIT_SUFFIX_RE, "", regex=True).str.replace(THOUSANDS_SPACE_RE, "", regex=True).str.lstrip("+")
    return s.mask(s.str.lower().isin(BLANKS), "")


def _decimal_shown(s: pd.Series) -> tuple[str | None, str]:
    """The decimal sign the cells of a column show, and a cell that shows it: (',' or '.', cell), ('mixed', 'a and b')
    when cells show both, or (None, '') when none does. A cell shows ',' by '1.226,5', '12,5', '0,500' (not three
    digits after it, or a leading 0) or '1.226.500' (two dots: thousands); '.' by '1,226.5', '12.5', '0.125' or
    '1,226,500'. '1.226' and '23,000' show nothing: three digits after the sign can be decimals or a thousands group."""
    dots, commas = s.str.count(r"\."), s.str.count(",")
    both = (dots > 0) & (commas > 0)
    comma_last = s.str.rfind(",") > s.str.rfind(".")
    only_comma, only_dot = (commas > 0) & (dots == 0), (dots > 0) & (commas == 0)
    comma = (both & comma_last) | (only_comma & (commas == 1) & ~s.str.match(COMMA_GROUPED_RE)) | (only_dot & (dots > 1))
    dot = (both & ~comma_last) | (only_dot & (dots == 1) & ~s.str.match(DOT_GROUPED_RE)) | (only_comma & (commas > 1))
    if comma.any() and dot.any():
        return "mixed", f"{s[comma].iloc[0]!r} and {s[dot].iloc[0]!r}"
    if comma.any():
        return ",", s[comma].iloc[0]
    if dot.any():
        return ".", s[dot].iloc[0]
    return None, ""


def _to_number(col: pd.Series, name: str, decimal: str | None = None, sep: str | None = None,
               notes: list | None = None, info: dict | None = None) -> pd.Series:
    """Numbers from strings. Spaces (also no-break) are thousands separators and a unit after the number (kW, kWh …)
    is ignored. The decimal sign is decided for the whole COLUMN by the cells that show it (_decimal_shown): in a
    decimal-comma column '1.300' is 1300, in a decimal-point column '1,300' is 1300 (v0.2 review: a lone '1.300'
    among '950,5' was read as 1.3). A cell with both signs always takes the last as its decimal sign. Where no cell
    shows the sign — or cells show both, which is then said — each cell is read by itself:
      * a comma is a decimal comma ('23,000' → 23.0); outside a ';' file a note says so when every comma has three
        digits after it (a ';' file has ';' because ',' is its decimal sign);
      * dots are thousands separators when every one-dot value has groups of three (none starting with '0.') and
        there are two or more of them, or one among two or more whole numbers ('980', '1.005', '990'); else
        decimals. In a comma-separated file a dot is the decimal sign. Read as thousands, a note says so: '12.345'
        with three decimals looks the same — decimal='.' (or ',') decides it instead.
    The notes go to `notes` (the caller adds them to what the file could not tell); `info["numbers"]` gets a line
    for the File tab. Blanks, '-', 'null' → NaN; any other cell that is not a number raises with its row — it would
    otherwise become a gap and be filled (v0.2 review: '400,0*' at a peak was interpolated away)."""
    if decimal not in (None, ".", ","):
        raise ValueError("decimal must be '.' or ','")
    s = _cells(col)
    sign, example = (decimal, "") if decimal else _decimal_shown(s)
    dots, commas = s.str.count(r"\."), s.str.count(",")
    both = (dots > 0) & (commas > 0)
    comma_last = s.str.rfind(",") > s.str.rfind(".")
    only_comma, only_dot = (commas > 0) & (dots == 0), (dots > 0) & (commas == 0)
    one_dot = s[only_dot & (dots == 1)]
    whole = s.str.fullmatch(r"-?\d+")
    per_cell = sign in (None, "mixed")
    dot_rule = per_cell and sep != "," and len(one_dot) > 0 and bool(one_dot.str.match(DOT_GROUPED_RE).all()) \
        and (len(one_dot) >= 2 or int(whole.sum()) >= 2)
    comma_thousands = only_comma & ((commas > 1) | (sign == "."))
    dot_thousands = only_dot & ((dots > 1) | (sign == ",") | dot_rule)
    out = s.copy()
    eu, us = both & comma_last, both & ~comma_last
    out[eu] = s[eu].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)      # 1.226,5
    out[us] = s[us].str.replace(",", "", regex=False)                                         # 1,226.5
    out[comma_thousands] = s[comma_thousands].str.replace(",", "", regex=False)               # 1,226,500
    decimal_comma = only_comma & ~comma_thousands
    out[decimal_comma] = s[decimal_comma].str.replace(",", ".", regex=False)                  # 12,5
    out[dot_thousands] = s[dot_thousands].str.replace(".", "", regex=False)                   # 1.226.500

    if sign == ",":
        what = f"decimal comma (shown by {example!r})" if example else "decimal comma (given)"
    elif sign == ".":
        what = f"decimal point (shown by {example!r})" if example else "decimal point (given)"
    elif not (commas.any() or dots.any()):
        what = "whole numbers"
    else:
        what = "no cell shows the decimal sign" if sign is None else f"both decimal signs appear ({example})"
        if sign == "mixed" and notes is not None:
            notes.append(f"{name}: the column shows both decimal signs ({example}) — each cell read by itself (its "
                         "last sign is the decimal one); check the numbers")
        if dot_rule:
            ex = one_dot.iloc[0]
            what += f" — dots read as thousands ({ex!r} → {ex.replace('.', '')})"
            if notes is not None:
                notes.append(f"{name}: values like {ex!r} read with '.' as a thousands separator ({ex!r} → "
                             f"{ex.replace('.', '')}): no value shows a decimal sign and every dot has three digits "
                             "after it — if they are decimals, choose the decimal point (decimal='.'; report command: "
                             "--decimal .)")
        elif only_dot.any():
            what += f" — dots read as decimals ({s[only_dot].iloc[0]!r})"
        if decimal_comma.any():
            ex = s[decimal_comma].iloc[0]
            what += f" — commas read as decimals ({ex!r})"
            if sign is None and sep != ";" and decimal_comma.sum() >= 2 and notes is not None:
                notes.append(f"{name}: every value with a comma has three digits after it ({ex!r}) — read as a "
                             f"decimal comma ({ex!r} → {ex.replace(',', '.')}); if the commas are thousands "
                             "separators, choose the decimal point (decimal='.'; report command: --decimal .)")
    x = pd.to_numeric(out.replace({"": np.nan}), errors="coerce")
    bad = x.isna() & (out != "")
    if bad.any():
        i = int(np.flatnonzero(bad.to_numpy())[0])
        raise ValueError(f"{name}: {int(bad.sum())} value{'s' if bad.sum() != 1 else ''} cannot be read as a number "
                         f"(first: {col.iloc[i]!r}, table row {i + 1}) — mark a missing value with an empty cell or "
                         "'-', never with text")
    if info is not None:
        info["numbers"] = what
    return x.rename(name)


def _parse_timestamps(df: pd.DataFrame, cols: dict, timestamp_col: str | None, tz: str, values: np.ndarray,
                      dayfirst: bool | None = None):
    """→ (UTC stamps in file order, NaT where a row is dropped; notes; found; unsure; rows dropped).
    values: the rows' numbers (rows × value columns), so a placeholder row in the spring hour can be told apart."""
    notes, found, unsure = [], [], []
    if timestamp_col is not None:
        raw = df[timestamp_col].astype(str).str.strip()
        where = f"column {timestamp_col!r}"
    else:
        tcol = _pick(cols, TIMESTAMP_NAMES)
        dcol, hcol = _pick(cols, DATE_NAMES), _pick(cols, TIME_NAMES)
        if tcol is None and dcol is not None and hcol is not None:
            hours, hour_note = _time_of_day(df[hcol].astype(str).str.strip())
            raw = df[dcol].astype(str).str.strip() + " " + hours
            notes.append(f"timestamps joined from columns {dcol!r} + {hcol!r}")
            where = f"columns {dcol!r} + {hcol!r}" + (f" ({hour_note})" if hour_note else "")
        elif tcol is None and dcol is not None:
            raw = df[dcol].astype(str).str.strip()
            where = f"column {dcol!r}"
        elif tcol is not None:
            raw = df[tcol].astype(str).str.strip()
            where = f"column {tcol!r}"
        else:
            raw = df[df.columns[0]].astype(str).str.strip()
            notes.append(f"first column {df.columns[0]!r} taken as the timestamp")
            where = f"first column {df.columns[0]!r} (no timestamp name found)"
    raw, range_note = _start_of_ranges(raw)
    raw, serial_note = _excel_days(raw)
    raw, shape, order_unsure = meterfile.stamp_text(raw, dayfirst)
    found.append(("timestamps", f"{where}: {shape}" + "".join(f" ({n})" for n in (range_note, serial_note) if n)))
    if range_note:
        notes.append(f"timestamps: {range_note}")
    if order_unsure:
        unsure.append(order_unsure)
    stamp_cols = [c for c in (timestamp_col, _pick(cols, TIMESTAMP_NAMES), _pick(cols, DATE_NAMES),
                              _pick(cols, TIME_NAMES)) if c is not None and c in cols]
    hint = next((CLOCK_HINTS[cols[c][1]] for c in stamp_cols if cols[c][1] in CLOCK_HINTS), None)
    if hint and tz.lower() == "auto":
        tz = hint
        found.append(("clock hint", f"the header says ({next(cols[c][1] for c in stamp_cols if cols[c][1] in CLOCK_HINTS)})"
                                    f" — read as {'normaltid' if hint == 'normaltid' else hint}"))
    elif any(cols[c][1] == "cet" for c in stamp_cols):
        found.append(("clock hint", "the header says (CET), which can mean CET all year or Swedish time with summer "
                                    "time — decided from the daylight-saving days instead"))
    # 24:00 → next day 00:00
    is24 = raw.str.contains(r"\b24:00", regex=True)
    if is24.any():
        base = pd.to_datetime(raw[is24].str.replace(r"\b24:00(:00)?", "00:00", regex=True), errors="coerce", format="mixed")
        raw = raw.copy()
        raw[is24] = (base + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d %H:%M:%S")
        notes.append(f"{int(is24.sum())} stamps written as 24:00 read as the next day's 00:00")
    has_date = raw.str.contains(DATE_RE)
    if has_date.mean() < 0.5:
        raise ValueError("the timestamp column seems to hold only a time of day — is the date in another column? "
                         "(pass timestamp_col=, or make sure the date column is named Datum/Date)")
    offset_share = (raw.str.contains(OFFSET_RE) & has_date).mean()
    if 0 < offset_share < 1:
        raise ValueError("timestamps mix rows with and without a UTC offset — one convention per file")
    if offset_share == 1:
        ts = pd.to_datetime(raw, errors="coerce", utc=True, format="mixed")
        if ts.isna().any():
            raise ValueError(f"unparseable timestamp {raw[ts.isna()].iloc[0]!r}")
        notes.append("timestamps carry a UTC offset; read as given")
        found.append(("clock", "UTC offsets in the stamps, read as given"))
        return pd.DatetimeIndex(ts), notes, found, unsure, np.zeros(len(raw), bool)
    ts = pd.to_datetime(raw, errors="coerce", format="mixed")
    if ts.isna().any():
        raise ValueError(f"unparseable timestamp {raw[ts.isna()].iloc[0]!r}")
    # DST inference needs time order, and the two October 02:00 rows (or eight quarter hours) are only
    # told apart by file order — so a newest-first file is flipped, but rows are never re-sorted here.
    steps = np.diff(ts.to_numpy().astype("datetime64[ns]").astype("int64"))
    if len(steps) and (steps < 0).sum() > (steps > 0).sum():
        ts = ts.iloc[::-1]
        notes.append("file is newest-first; read in time order")
    labels = ts.index.to_numpy()
    utc, drop, n2, f2, u2 = meterfile.to_utc(ts.reset_index(drop=True), values[labels], tz)
    notes += n2; found += f2; unsure += u2
    out = pd.Series(utc.to_numpy(), index=labels).reindex(range(len(raw)))       # back to file order
    dropped = np.zeros(len(raw), bool)
    dropped[labels[drop]] = True
    return pd.DatetimeIndex(out), notes, found, unsure, dropped


RANGE_RE = re.compile(r"^(?P<a>.*?\d{1,2}(?::\d{2}){0,2})\s*[-–]\s*(?P<b>(?:\d{4}-\d{2}-\d{2}[ T]|\d{1,2}[./]\d{1,2}[./]\d{2,4} )?"
                      r"\d{1,2}(?::\d{2}){0,2})$")


def _time_of_day(t: pd.Series) -> tuple[pd.Series, str]:
    """A time column as hh:mm. Hour numbers: 1–24 (the hour that ENDS at the number: 1 = 00:00–01:00, the Swedish
    'timme 1') or 0–23 (the hour that starts there); '00-01' → its start. Clock times pass unchanged."""
    if t.str.fullmatch(r"\d{1,2}\s*[-–]\s*\d{1,2}").all():
        return t.str.extract(r"^(\d{1,2})")[0].astype(int).map("{:02d}:00".format), "hour ranges, the start taken"
    if t.str.fullmatch(r"\d{1,2}").all():
        h = t.astype(int)
        if h.min() >= 1 and h.max() == 24:
            return (h - 1).map("{:02d}:00".format), "hour numbers 1–24 read as the hour ending there: 1 = 00:00–01:00"
        if h.min() == 0 and h.max() <= 23:
            return h.map("{:02d}:00".format), "hour numbers 0–23 read as the hour starting there"
        raise ValueError(f"the hour numbers run {h.min()}–{h.max()}: neither 1–24 nor 0–23 — write clock times "
                         "(hh:mm) instead")
    return t, ""


def _start_of_ranges(raw: pd.Series) -> tuple[pd.Series, str]:
    """'2025-01-13 00:00 - 01:00' or '… - 2025-01-13 01:00' (an interval written out) → its start. Only when every
    stamp is a range: a lone '-hh:mm' is a UTC offset."""
    m = raw.str.extract(RANGE_RE)
    if m["a"].notna().all() and m["a"].str.contains(DATE_RE).all() and (m["b"].str.len() < m["a"].str.len() + 1).all() \
            and not raw.str.contains("T", regex=False).any():
        return m["a"].str.strip(), "intervals written 'from – to': the start of each is the stamp"
    return raw, ""


def _excel_days(raw: pd.Series) -> tuple[pd.Series, str]:
    """Excel's day numbers (45658.04167 = 2025-01-01 01:00) → stamps; days counted from 1899-12-30, read as the
    local clock the sheet showed, to the minute."""
    if not raw.str.fullmatch(r"[2-7]\d{4}(?:[.,]\d+)?").all():
        return raw, ""
    days = pd.to_numeric(raw.str.replace(",", ".", regex=False))
    t = (pd.Timestamp("1899-12-30") + pd.to_timedelta(days, unit="D")).dt.round("min")
    return t.dt.strftime("%Y-%m-%d %H:%M"), "Excel day numbers (days since 1899-12-30), rounded to the minute"


def _local_text(t) -> str:
    """A UTC stamp as the file's reader sees it: Swedish time first."""
    t = pd.Timestamp(t)
    return f"{t.tz_convert(LOCAL_TZ):%Y-%m-%d %H:%M} Swedish time ({t:%H:%M} UTC)"


def _uniform_step_min(index: pd.DatetimeIndex) -> float:
    """The meter interval: the most common spacing, which must dominate and be a real interval."""
    if len(index) < 3:
        raise ValueError("need at least 3 timestamps")
    d = np.diff(index.to_numpy()) / np.timedelta64(1, "m")
    values, counts = np.unique(d, return_counts=True)
    step = float(values[np.argmax(counts)])
    if step not in ALLOWED_STEPS_MIN:
        raise ValueError(f"timestamp spacing {step:g} min is not a meter interval {ALLOWED_STEPS_MIN}")
    if counts.max() / len(d) < 0.9:
        raise ValueError(f"irregular timestamps: only {100 * counts.max() / len(d):.0f} % of the steps are {step:g} min")
    return step


def _names(values, most: int = 6) -> str:
    values = list(values)
    return ", ".join(map(str, values[:most])) + (f" … ({len(values)} in all)" if len(values) > most else "")


class ManyStations(ValueError):
    """A file of several stations and none chosen; .column and .stations (in file order) let the caller ask."""

    def __init__(self, column: str, stations: list[str]):
        self.column, self.stations = column, list(stations)
        super().__init__(f"the file holds {len(self.stations)} stations in column {column!r} ({_names(self.stations)}): "
                         "choose one (station=…, or --station in the report command), or screen them all in Fleet "
                         "screening")


def station_columns(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """(the station column, the rating column) of a table, each None if absent."""
    cols = {c: _norm(c) for c in df.columns}
    return _pick(cols, STATION_NAMES), _pick(cols, RATING_NAMES)


def read_station_csv(path, *, station: str | None = None, sep: str | None = None, **kwargs) -> StationSeries:
    """Read one station's CSV into a StationSeries (see the module docstring for the shapes). A file with a
    station column holds one station, or `station=` picks one; keyword arguments as station_from_frame."""
    try:
        table = meterfile.read_table(path, sep)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, csv.Error) as e:
        raise ValueError(f"cannot read {path} as a CSV: {e}") from e
    df, found = table.df, list(table.found)
    scol, rcol = station_columns(df)
    file_kva = None
    if scol is not None and df[scol].isna().all():
        found.append(("station", f"column {scol!r} is empty — ignored"))
        df, scol = df.drop(columns=[scol]), None
    if scol is not None:
        ids = df[scol].fillna("").astype(str).str.strip()
        if (ids == "").any():
            raise ValueError(f"column {scol!r}: {int((ids == '').sum())} rows have no station id (first: table row "
                             f"{int(np.flatnonzero((ids == '').to_numpy())[0]) + 1})")
        names = list(dict.fromkeys(ids))
        if station is not None:
            keep = ids == str(station).strip()
            if not keep.any():
                raise ValueError(f"station {station!r} is not in column {scol!r} (the file has {_names(names)})")
            df = df[keep]
        elif len(names) > 1:
            raise ManyStations(scol, names)
        found.append(("station", f"{df[scol].iloc[0].strip()!r} (column {scol!r})"))
        df = df.drop(columns=[scol]).reset_index(drop=True)
    if rcol is not None:
        ratings = df[rcol].dropna().astype(str).str.strip().replace("", np.nan).dropna().unique()
        if len(ratings) == 1:
            file_kva = float(_to_number(pd.Series(ratings), "rating").iloc[0])
            found.append(("rating in the file", f"{file_kva:g} kVA (column {rcol!r}) — the assessment uses the "
                                                "rating you set"))
        elif len(ratings) > 1:
            raise ValueError(f"column {rcol!r} gives more than one rating ({_names(ratings)}) for one station")
        df = df.drop(columns=[rcol]).reset_index(drop=True)
    elif station is not None:
        raise ValueError(f"station={station!r} was given, but the file has no station column")
    source = str(path) if isinstance(path, (str, Path)) else str(getattr(path, "name", "") or "")
    st = station_from_frame(df, table.sep, source=source, found=found, unsure=list(table.unsure), **kwargs)
    return replace(st, file_kva=file_kva)


def station_from_frame(df: pd.DataFrame, sep: str, *, source: str = "", found: list | None = None,
                       unsure: list | None = None, timestamp_col: str | None = None, p_col: str | None = None,
                       e_col: str | None = None, q_col: str | None = None, s_col: str | None = None,
                       export_col: str | None = None, pf: float = POWER_FACTOR, measured_at: str = "meters",
                       tz: str = "auto", stamp: str = "start", max_gap_steps: int = MAX_GAP_STEPS_DEFAULT,
                       decimal: str | None = None, unit: str | None = None,
                       dayfirst: bool | None = None) -> StationSeries:
    """One station's table (strings, the file's own header) → StationSeries. `decimal` ('.' or ',') fixes the
    decimal sign when the numbers alone cannot tell (see _to_number); `unit` ('kW', 'kWh', 'W', 'Wh', 'MW',
    'MWh') the unit of the value column when the file does not state it; `dayfirst` the date order when the
    dates cannot show it; `tz` see meterfile.to_utc."""
    found = list(found or [])
    unsure = list(unsure or [])
    if unit is not None and UNIT_SCALE.get(unit.lower(), ("",))[0] not in ("power", "energy"):
        raise ValueError("unit must be one of kW, kWh, W, Wh, MW, MWh")
    meterfile.check_tz(tz)
    if measured_at not in ("meters", "transformer"):
        raise ValueError("measured_at must be 'meters' or 'transformer'")
    if stamp not in ("start", "end"):
        raise ValueError("stamp must be 'start' or 'end'")
    if not 0 < pf <= 1:
        raise ValueError("pf must be within (0, 1]")
    if max_gap_steps < 0:
        raise ValueError("max_gap_steps must be >= 0")
    if df.shape[1] < 2 or len(df) == 0:
        raise ValueError("CSV needs at least a timestamp column, a value column and some rows")
    twice = [str(c).rsplit(".", 1)[0] for c in df.columns
             if re.fullmatch(r".+\.\d+", str(c)) and str(c).rsplit(".", 1)[0] in df.columns]   # pandas' 'X.1'
    if twice:
        raise ValueError(f"the header has the column name {twice[0]!r} twice — name each column once")
    cols = {c: _norm(c) for c in df.columns}
    for c in [c for c, (n, _) in cols.items() if n in RATING_NAMES + STATION_NAMES]:
        del cols[c]                                   # a rating (kVA) or a station id is never a measured value
    meter = _pick(cols, METER_NAMES)
    if meter is not None and df[meter].nunique() > 1:
        raise ValueError(f"the file has one row per meter ({df[meter].nunique()} values in column {meter!r}): the "
                         "reader takes one series per station and does not add meters up yet — add them up per "
                         "timestamp first (one total per stamp)")
    value_names = P_NAMES + E_NAMES + EXPORT_NAMES + Q_NAMES + S_NAMES + VALUE_NAMES
    for c, (n, u) in cols.items():
        if n in value_names and u and u not in UNIT_SCALE:
            raise ValueError(f"column {c!r}: unit {u!r} is not understood (known: {sorted(UNIT_SCALE)})")
    pcol = p_col or _pick_value(cols, "load (power)", P_NAMES, ("kw", "mw"), ("power",))
    ecol = e_col or _pick_value(cols, "load (energy)", E_NAMES, ("kwh", "wh", "mwh"), ("energy",))
    xcol = export_col or _pick_value(cols, "export", EXPORT_NAMES, (), ("power", "energy"))
    qcol = q_col or _pick_value(cols, "reactive power", Q_NAMES, ("kvar", "kvarh"), ("reactive", "reactive_energy"))
    scol = s_col or _pick_value(cols, "apparent power", S_NAMES, ("kva", "mva"), ("apparent",))
    if pcol is not None and ecol is not None:
        found.append(("not used", f"column {ecol!r}: the power column {pcol!r} is read instead"))
        ecol = None
    if pcol is None and ecol is None and scol is None:                 # last resort: single letters
        single = {n: orig for orig, (n, _) in cols.items() if n in SINGLE_LETTER}
        pcol, ecol, qcol, scol = single.get("p"), single.get("e"), qcol or single.get("q"), scol or single.get("s")
    stated = True                                     # does the file state the main column's unit?
    bare = False                                      # a 'Värde' column whose unit nothing states
    if pcol is None and ecol is None and scol is None:                 # a value column, its unit elsewhere
        vcol = _pick_value(cols, "value", VALUE_NAMES, (), ("power", "energy"))
        if vcol is not None:
            u, where = _unit_of_values(df, cols, vcol)
            cols[vcol] = (cols[vcol][0], u or "")
            if u and UNIT_SCALE[u][0] == "power":
                pcol = vcol
            else:
                ecol, bare = vcol, not u
            if u:
                found.append(("unit", f"{u} — {where}"))
    if pcol is None and ecol is None and scol is None:
        raise ValueError(f"no power/energy column found; columns are {list(df.columns)} "
                         f"(expected e.g. kW / Effekt / kWh / Energi / Förbrukning, or pass p_col= / e_col=)")
    if pcol is None and ecol is None:
        raise ValueError(f"only an apparent-power column ({scol!r}, kVA): the reader needs the active power or energy "
                         "as well (kW / Effekt / kWh / Energi)")
    main = pcol or ecol
    if unit is not None and main is not None and cols[main][1] and cols[main][1] != unit.lower():
        raise ValueError(f"the column {main!r} states its unit ({cols[main][1]}), but the unit chosen is {unit}: set "
                         "the unit to 'from the file' (a chosen unit is only for a column that does not state one)")
    if unit is not None and main is not None:         # the user's unit decides the main column's meaning
        u = unit.lower()
        cols[main] = (cols[main][0], u)
        pcol, ecol = (main, None) if UNIT_SCALE[u][0] == "power" else (None, main)
        bare = False
        found.append(("unit", f"{unit} — as chosen"))
    elif main is not None and not cols[main][1]:
        stated = False                                # 'Effekt' / 'Förbrukning' / 'Värde' without a unit
    value_cols = [c for c in (pcol, ecol, xcol, qcol, scol) if c is not None]
    values = np.column_stack([_to_number(df[c], c, decimal, sep).to_numpy(dtype=float) for c in value_cols])

    index, notes, f_t, u_t, dropped = _parse_timestamps(df, cols, timestamp_col, tz, values, dayfirst)
    found += f_t
    unsure += u_t
    if dropped.any():
        df, index = df[~dropped].reset_index(drop=True), index[~dropped]
    if any("24:00" in n for n in notes) and stamp == "start":
        stamp = "end"
        notes.append("24:00 stamps can only label the END of an interval — stamp='end' applied")
    order = np.argsort(index.to_numpy(), kind="stable")
    df = df.iloc[order].reset_index(drop=True)
    index = index[order]
    # duplicates: identical rows collapse with a note, differing rows are an error
    dup = index.duplicated(keep=False)
    if dup.any():
        rows = df[value_cols].astype(str).agg("|".join, axis=1)
        groups = pd.DataFrame({"t": index, "v": rows.to_numpy()})[dup].groupby("t")["v"].nunique()
        if (groups > 1).any():
            share = groups.size / max(len(set(index)), 1)
            if share > 0.5:
                raise ValueError(f"{100 * share:.0f} % of the timestamps appear more than once with different values — "
                                 "one row per meter or per phase? The reader takes one total per stamp: add them up "
                                 "first")
            raise ValueError(f"duplicated timestamp {_local_text(groups[groups > 1].index[0])} with different values "
                             "— two exports concatenated?")
        keep = ~index.duplicated(keep="first")
        notes.append(f"{int((~keep).sum())} exactly repeated rows dropped")
        df, index = df[keep].reset_index(drop=True), index[keep]
    step_min = _uniform_step_min(index)
    step_h = step_min / 60
    found.append(("resolution", f"{step_min:g}-min values"))
    if not stated:
        what = "kW (mean power)" if pcol is not None else "kWh per step (energy)"
        if bare and step_min != 60:
            raise ValueError(f"the column {main!r} does not say its unit and the values are {step_min:g}-min: kW and "
                             f"kWh per {step_min:g} min differ by ×{60 / step_min:g} — choose the unit (unit='kWh' or "
                             "'kW'; in the app: Unit)")
        unsure.append(f"unit: the column {main!r} does not state one — {what} assumed"
                      + (" (for hourly values kWh per hour and mean kW are the same number)" if step_min == 60 else "")
                      + "; choose the unit if this is wrong")
    if stamp == "start":
        loc_first, loc_last = index[0].tz_convert(LOCAL_TZ), index[-1].tz_convert(LOCAL_TZ)
        if (loc_first - loc_first.normalize()) == pd.Timedelta(minutes=step_min) and loc_last == loc_last.normalize():
            unsure.append(f"the stamps run from {loc_first:%H:%M} to {loc_last:%H:%M} — typical of stamps at the "
                          "interval END; they are read as interval START, as chosen: choose 'end' if the export says so")
    if any(n.startswith("timestamps: intervals written") for n in notes) and stamp == "end":
        stamp = "start"
        notes.append("the intervals are written out, so their start is the stamp — stamp='end' not applied")
    if stamp == "end":
        index = index - pd.Timedelta(minutes=step_min)
        notes.append(f"stamps taken as interval END; shifted back {step_min:g} min")
    else:
        notes.append("stamps taken as interval START")
    found.append(("interval", f"each stamp is the {stamp.upper()} of its interval — "
                  + ("read from the file" if any("24:00" in n or "intervals written" in n for n in notes)
                     else "your setting: a file cannot show it")))
    full = pd.date_range(index[0], index[-1], freq=f"{int(round(step_min))}min", tz="UTC")
    off_grid = ~index.isin(full)
    if off_grid.any():
        raise ValueError(f"{int(off_grid.sum())} timestamps are not on the {step_min:g}-min grid "
                         f"(first: {_local_text(index[off_grid][0])}) — mixed resolutions must be resampled first")
    df.index = index

    number_info: dict = {}

    def clean(col, name):
        told: list = []
        s = _to_number(df[col], name, decimal, sep, told, number_info if col == main else None)
        unsure.extend(u for u in told if u not in unsure)
        if col in (main, scol) and len(s) >= 24 and s.nunique() == 1:
            unsure.append(f"the column {col!r} has the same value ({s.iloc[0]:g}) in every row — a rating or a "
                          "placeholder rather than a measured load?")
        s = s.reindex(full)
        missing = int(s.isna().sum())
        if missing:
            isna = s.isna().to_numpy()
            padded = np.concatenate(([False], isna, [False]))
            edges = np.flatnonzero(padded[1:] != padded[:-1])
            lengths = edges[1::2] - edges[::2]
            longest = int(lengths.max())
            if longest > max_gap_steps:
                start = full[int(edges[::2][np.argmax(lengths)])]
                raise ValueError(f"{name}: a gap of {longest} steps from {_local_text(start)} is longer than "
                                 f"{max_gap_steps} steps — shorten the period, or fill the gap in the file (report "
                                 "command: --max-gap); if the whole column is empty, check the column choice")
            s = s.interpolate(limit_direction="both")
            notes.append(f"{name}: {missing} missing steps interpolated")
        return s.astype(float), missing

    def as_kw(values, unit, default_kind, label):
        """Scale a column to kW using its unit (kW, MW, W, kWh, MWh, Wh); energy per step → mean kW."""
        kind, scale = UNIT_SCALE.get(unit, (default_kind, 1.0))
        if kind == "energy":
            notes.append(f"{label}: energy per {step_min:g}-min step → kW" + (f" (column unit {unit})" if unit else ""))
            return values * scale / step_h
        if unit and unit != "kw":
            notes.append(f"{label}: column unit {unit} scaled to kW")
        return values * scale

    if pcol is not None:
        p, gaps = clean(pcol, "P")
        p = as_kw(p, cols[pcol][1], "power", "P")
    else:
        e, gaps = clean(ecol, "energy")
        p = as_kw(e, cols[ecol][1] or "kwh", "energy", "P")
    if xcol is not None:                              # an export column without a unit takes the load column's
        x, _ = clean(xcol, "export")
        x = as_kw(x, cols[xcol][1] or cols[main][1] or ("kwh" if ecol is not None else "kw"),
                  "energy" if ecol is not None else "power", "export")
        p = p - x.abs()
        notes.append(f"export column {xcol!r} subtracted → net P (negative = export)")
    if qcol is not None and _to_number(df[qcol], "q", decimal, sep).notna().mean() > 0.5:
        q, _ = clean(qcol, "Q")
        kind, scale = UNIT_SCALE.get(cols[qcol][1], ("reactive", 1.0))
        q = q * scale / (step_h if kind == "reactive_energy" else 1.0)            # kvarh per step → mean kvar
    else:
        q = p.clip(lower=0) * math.tan(math.acos(pf))
        notes.append(f"Q estimated from P at pf {pf} (consumption only" +
                     ("; on export hours Q = 0, so the transformer loading there is under-read by a few % of "
                      "nameplate — irrelevant for ageing)" if (p < 0).any() else ")"))
    if (p < 0).any():
        notes.append(f"{int((p < 0).sum())} steps with negative P (export) kept as negative")
    s_meas = None
    if scol is not None and measured_at == "transformer":
        s_meas = clean(scol, "S")[0] * UNIT_SCALE.get(cols[scol][1], ("apparent", 1.0))[1]
        notes.append(f"measured apparent power {scol!r} used as the transformer loading")
    elif scol is not None:
        notes.append(f"column {scol!r} (kVA) ignored: measured_at='meters' — pass measured_at='transformer' to use it")
    found.append(("values", _values_text(pcol, ecol, cols, step_min)))
    found.append(("numbers", number_info.get("numbers", "")))
    found.append(("reactive power", f"column {qcol!r}" if qcol is not None and not any(n.startswith("Q estimated")
                                                                                         for n in notes)
                  else f"not in the file — estimated from P at power factor {pf}"))
    if xcol is not None:
        found.append(("export", f"column {xcol!r}, subtracted → net P"))
    notes += [f"not told by the file — {u}" for u in unsure]
    out = StationSeries(p.rename("p_kw"), q.rename("q_kvar"), s_meas, step_min, measured_at, source, gaps, notes,
                        found, unsure)
    found.append(("period", f"{out.period()}, {len(index):,} rows read, {gaps} missing steps filled"))
    return out


def _unit_of_values(df: pd.DataFrame, cols: dict, vcol: str) -> tuple[str | None, str]:
    """The unit of a bare value column: from a unit column (Enhet), else from the cells ('12,5 kWh')."""
    ucol = _pick(cols, UNIT_COL_NAMES)
    if ucol is not None:
        units = set(df[ucol].dropna().astype(str).str.strip().str.lower()) - {""}
        if len(units) > 1:
            raise ValueError(f"column {ucol!r} mixes units {sorted(units)} — one unit per file")
        if units and next(iter(units)) in UNIT_SCALE:
            if UNIT_SCALE[next(iter(units))][0] not in ("power", "energy"):
                raise ValueError(f"column {ucol!r} says {next(iter(units))!r}: not active power or energy")
            return next(iter(units)), f"from column {ucol!r}"
        if units:
            raise ValueError(f"column {ucol!r} says {next(iter(units))!r}, which is not a unit the reader knows "
                             f"(known: {sorted(k for k, (kind, _) in UNIT_SCALE.items() if kind in ('power', 'energy'))})")
    cells = df[vcol].dropna().astype(str).str.strip()
    m = cells.str.extract(r"(?i)\d\s*([a-z]+)\s*$")[0].dropna().str.lower()
    if len(m) and m.nunique() == 1 and m.iloc[0] in UNIT_SCALE:
        return m.iloc[0], f"from the cells of {vcol!r}"
    return None, ""


def _values_text(pcol, ecol, cols, step_min) -> str:
    col = pcol or ecol
    unit = cols[col][1] or ("kw" if pcol else "kwh")
    kind = UNIT_SCALE.get(unit, ("power", 1.0))[0]
    name = {"kw": "kW", "mw": "MW", "w": "W", "kwh": "kWh", "mwh": "MWh", "wh": "Wh"}.get(unit, unit)
    if kind == "energy":
        return f"column {col!r}: {name} per {step_min:g}-min step → mean kW (÷ {step_min / 60:g} h)"
    return f"column {col!r}: {name} (mean power per step)" + ("" if unit == "kw" else " → kW")


def to_hourly(st: StationSeries) -> StationSeries:
    """Average a sub-hourly series to hours (needed for overlays)."""
    if st.step_min == 60:
        return st
    p = st.p_kw.resample("h").mean()
    q = st.q_kvar.resample("h").mean()
    s = st.s_kva_measured.resample("h").mean() if st.s_kva_measured is not None else None
    return replace(st, p_kw=p, q_kvar=q, s_kva_measured=s, step_min=60.0,
                   notes=st.notes + [f"averaged from {st.step_min:g}-min to hourly values"])


# ---------------------------------------------------------------------------
# physics: from the sum of meters to the transformer
# ---------------------------------------------------------------------------
def s_from_meters(p_kw: pd.Series, q_kvar: pd.Series, rated_kva: float, measured_at: str = "meters",
                  cable_loss_share_at_rated: float = CABLE_LOSS_SHARE_AT_RATED, losses: dict | None = None) -> pd.Series:
    """Apparent power at the transformer's HV side from measured P and Q.

    measured_at='meters'      : P is the sum of customer meters → add LV cable losses (I²R, so a share
                                of P proportional to the loading: share = share_at_rated × K) and the
                                transformer's own losses (catalogue: no-load + load·K²)
    measured_at='transformer' : P, Q already at the LV terminals → only the transformer's losses
    The transformer's reactive consumption uk·K²·S_rated is added in both cases. Six fixed-point
    passes (K → losses → S → K): converged to < 0.2 % of nameplate up to K ≈ 3.5.
    losses: a custom unit's losses and uk (catalogue.custom_transformer); None → the catalogue size."""
    tr = losses or transformer(rated_kva)
    p = p_kw.to_numpy(dtype=float)
    q = q_kvar.to_numpy(dtype=float)
    share = cable_loss_share_at_rated if measured_at == "meters" else 0.0
    s = np.hypot(p, q)
    for _ in range(6):
        k = s / rated_kva
        p_lv = p + share * k * np.abs(p)                                   # cable I²R: share grows with loading
        p_hv = p_lv + (tr["p_noload_w"] + tr["p_load_w"] * k**2) / 1000  # losses always supplied from the MV side
        q_hv = q + tr["uk_percent"] / 100 * k**2 * rated_kva
        s = np.hypot(p_hv, q_hv)
    return pd.Series(s, index=p_kw.index, name="s_kva")


def ambient_for(index: pd.DatetimeIndex, smhi_station: int = SMHI_STATION) -> pd.Series:
    """SMHI air temperature on the station's index: the whole UTC hours the data span (and only those — packaging
    review round 2: whole calendar years made a 2023 file in Swedish time ask for 2022, and a recent file fail on
    the months SMHI has not published yet), interpolated in time to the index. attrs["smhi_latest_from"]: the
    first hour taken from SMHI's latest months instead of its checked archive, or None."""
    utc = index.tz_convert("UTC")
    hours = pd.date_range(utc.min().floor("h"), utc.max().floor("h"), freq="h")
    temp = load_smhi_hours(hours, smhi_station)
    out = temp.reindex(temp.index.union(utc)).interpolate(method="time", limit_direction="both").reindex(utc)
    if out.isna().any():
        raise ValueError(f"SMHI station {smhi_station} does not cover the data period")
    out = pd.Series(out.to_numpy(), index=index, name="temp_c")
    out.attrs.update(temp.attrs)
    return out


def year_result_from_station(st: StationSeries, rated_kva: float,
                             cable_loss_share_at_rated: float = CABLE_LOSS_SHARE_AT_RATED,
                             losses: dict | None = None) -> YearResult:
    """A YearResult for the verdict: transformer S per step; voltages and cable unknown → neutral values
    (the verdict marks them as not checked)."""
    if st.s_kva_measured is not None:
        s = st.s_kva_measured.rename("s_kva")
    else:
        s = s_from_meters(st.p_kw, st.q_kvar, rated_kva, st.measured_at, cable_loss_share_at_rated, losses)
    h = pd.DataFrame({
        "s_kva": s.to_numpy(), "loading_pct": s.to_numpy() / rated_kva * 100, "p_kw": st.p_kw.to_numpy(),
        "vmin_pu": 1.0, "vmax_pu": 1.0, "losses_kw": 0.0, "line_max_pct": 0.0,
    }, index=st.index)
    return YearResult(h, float(rated_kva))


def scale_text(factor: float) -> str:
    """A scaling factor in plain digits: '12', '11.8', '365' — never '3.6e+02'."""
    return f"{factor:,.0f}" if factor >= 100 else f"{factor:,.1f}"


# ---------------------------------------------------------------------------
# assessment
# ---------------------------------------------------------------------------
NOT_CHECKED_KEYS = ("hours with voltage outside ±10 %", "hours with voltage outside the planning band",
                    "max cable loading (%)")


@dataclass
class StationResult:
    station: StationSeries
    rated_kva: float
    year: YearResult
    thermal: ThermalResult
    verdict: Verdict
    smhi_station: int = SMHI_STATION
    overlay: dict = field(default_factory=dict)
    losses: dict | None = None                  # a custom unit's losses; None → the catalogue size

    def metrics(self) -> dict:
        v = self.verdict
        base = {
            "kVA": self.rated_kva, "verdict": v.level, "why": v.headline(),
            "period": self.station.period(), "step_min": self.station.step_min,
            "peak_loading_pct": v.on_paper["peak load (% of nameplate)"],
            "hours_above_80": v.on_paper["hours above 80 % of nameplate"],
            "hours_above_100": v.on_paper["hours above 100 % of nameplate"],
            "life_days_per_year": v.really["days of life used per year"],
            "max_hot_spot_c": v.really["max hot-spot (°C)"],
            "max_load_pu": v.really["max load (× rated)"],
            "energy_mwh": float(self.station.p_kw.clip(lower=0).sum() * self.station.step_min / 60 / 1000),
            "gaps_filled": self.station.gaps_filled,
            "smhi_station": self.smhi_station,
            "notes": "; ".join(v.notes),
        }
        for k, val in self.overlay.items():
            base[f"overlay_{k}"] = val
        return base


def assess_station(st: StationSeries, rated_kva: float, ambient: pd.Series | None = None,
                   smhi_station: int | None = None, enclosure: str = "kiosk",
                   cable_loss_share_at_rated: float = CABLE_LOSS_SHARE_AT_RATED,
                   overlay: dict | None = None, losses: dict | None = None) -> StationResult:
    """Thermal year and verdict for one station from its measured profile. `losses` (catalogue.custom_transformer)
    makes it a custom unit: its own losses and uk, the same generic IEC thermal constants."""
    notes = []
    if losses is None:
        transformer(rated_kva)                          # an unknown size fails here, before SMHI is fetched
    else:
        uk = (f"uk {losses['uk_percent']:g} %" if losses.get("uk_given", True) else
              f"uk {losses['uk_percent']:g} % (assumed: none given)")
        notes.append(f"transformer losses entered by the user: no-load {losses['p_noload_w']:g} W, load "
                     f"{losses['p_load_w']:g} W, {uk} — not the catalogue's Ecodesign values; the thermal "
                     "constants are still the generic IEC 60076-7 ones")
    if smhi_station is None:
        smhi_station = SMHI_STATION
        notes.append(f"ambient: SMHI station {SMHI_STATION} {SMHI_STATION_NAME} — the default; choose the SMHI station "
                     "nearest the place")
    else:
        notes.append(f"ambient: SMHI station {smhi_station}")
    if ambient is None:
        ambient = ambient_for(st.index, smhi_station)
    if not ambient.index.equals(st.index):
        raise ValueError("ambient must be on the station's index (use ambient_for)")
    filled = int(ambient.attrs.get("missing_hours_filled") or 0)
    if filled:                                          # round-3 review: computed, but never shown
        notes.append(f"outdoor temperature: {filled} hour{'s' if filled != 1 else ''} with no value in the temperature "
                     "data used (an SMHI gap, or an hour just outside the data), filled from the neighbouring hours "
                     "(gaps of at most 6 h; at the start or end, held at the nearest value)")
    latest = ambient.attrs.get("smhi_latest_from")
    if latest is not None:
        notes.append(f"outdoor temperature from {latest:%Y-%m-%d %H:%M} UTC on: SMHI's latest-months data, not yet in "
                     "SMHI's quality-checked archive (which follows about three months later)")
    year = year_result_from_station(st, rated_kva, cable_loss_share_at_rated, losses)
    k = year.hourly["loading_pct"] / 100
    peak = float(k.max())
    if not 0.01 <= peak <= 3.0:                       # v0.2 review: a decimal sign or unit 1000× off looks like this
        notes.append(f"check the file: the peak load is {100 * peak:,.1f} % of {rated_kva:g} kVA — far from any "
                     "station's; check the decimal sign, the unit and the rating (the File tab says how they were read)")
    if st.file_kva is not None and abs(st.file_kva - float(rated_kva)) > 0.5:
        notes.append(f"check the rating: the file gives {st.file_kva:g} kVA, the assessment uses {rated_kva:g} kVA")
    clipped = int((k > K_MAX).sum())
    if clipped:
        notes.append(f"loading above {K_MAX:g} × rated in {clipped} steps — the thermal run was clipped there (its "
                     "figures are withdrawn anyway above 2 × rated); the current-limit rule sees the full loading")
        k = k.clip(upper=K_MAX)
    hours = len(st.index) * st.step_min / 60
    if abs(HOURS_PER_YEAR / hours - 1) > 0.1:
        days = hours / 24
        span = f"{hours:,.0f} hours" if days < 1.5 else f"{days:,.0f} days"
        notes.append(f"the data cover {span}: life used per year is scaled "
                     f"×{scale_text(HOURS_PER_YEAR / hours)} as if this period repeated all year — a winter-only file "
                     "overstates the yearly figure, a summer-only file understates it")
    params = ThermalParams.for_transformer(rated_kva, enclosure=enclosure, losses=losses)
    thermal = simulate(k, ambient, params, step_min=st.step_min)
    verdict = judge(year, thermal)                           # withdraws the thermal figures above 2 × rated
    from hotspotlv.study import enclosure_note
    notes.append(enclosure_note(params, rated_kva))
    for key in NOT_CHECKED_KEYS:
        verdict.really[key] = None                            # a 0 would read as "checked and fine"
    verdict.notes.append("voltage and cable rules not checked: station data carries no feeder model")
    verdict.notes.extend(notes)
    verdict.notes.extend(st.notes)
    return StationResult(st, float(rated_kva), year, thermal, verdict, smhi_station, overlay or {}, losses)


def with_overlay(st: StationSeries, ambient: pd.Series, n_ev: int = 0, n_hp: int = 0,
                 ev: EVParams = EVParams(), hp: HeatPumpParams = HeatPumpParams(), seed: int = 0,
                 fuse_a: float = HOUSE_FUSE_A) -> StationSeries:
    """Add synthetic houses on top of a measured profile: n_ev EV chargers and n_hp heat pumps
    (air/water by default, sizes jittered ±20 % like the scenario model), each synthetic house
    capped at its main fuse. An UPPER BOUND: no base load is added, every charger has the
    scenario's default power, and nothing else in the station changes."""
    if st.step_min != 60:
        raise ValueError("overlays need an hourly profile (use to_hourly first)")
    if n_ev < 0 or n_hp < 0 or int(n_ev) != n_ev or int(n_hp) != n_hp:
        raise ValueError("n_ev and n_hp must be non-negative integers")
    if not ambient.index.equals(st.index):
        raise ValueError("ambient must be on the station's index (use ambient_for)")
    n = max(n_ev, n_hp)
    extra = np.zeros(len(st.index))
    cap = fuse_kw(fuse_a)
    for j, s in enumerate(np.random.SeedSequence(seed).spawn(n)):
        ss_hp, ss_ev = s.spawn(2)
        house = np.zeros(len(st.index))
        if j < n_hp:
            rng = np.random.default_rng(ss_hp)
            p = hp.with_(ua_kw_per_k=max(0.08, hp.ua_kw_per_k * rng.normal(1.0, 0.2)),
                         q_nom_kw=max(3.0, hp.q_nom_kw * rng.normal(1.0, 0.15)))
            house += heat_pump_profile(ambient, p)["total"].to_numpy()
        if j < n_ev:
            house += ev_charging_profile(st.index, ev, seed=ss_ev).to_numpy()
        extra += np.minimum(house, cap)
    q_extra = extra * math.tan(math.acos(POWER_FACTOR))
    notes = st.notes + [f"overlay: +{n_ev} EV chargers ({ev.charger_kw:g} kW), +{n_hp} heat pumps — synthetic upper bound "
                        f"(no base load, fuse {fuse_a:g} A per synthetic house, random draw {seed})"]
    return replace(st, p_kw=(st.p_kw + extra).rename("p_kw"), q_kvar=(st.q_kvar + q_extra).rename("q_kvar"),
                   s_kva_measured=None, notes=notes)


def export_station_csv(p_kw: pd.Series, path, q_kvar: pd.Series | None = None, local: bool = True) -> Path:
    """Write a station profile in the simplest accepted shape (used for examples and tests)."""
    idx = p_kw.index.tz_convert(LOCAL_TZ).tz_localize(None) if local else p_kw.index
    df = pd.DataFrame({"timestamp": idx.strftime("%Y-%m-%d %H:%M"), "p_kw": np.round(p_kw.to_numpy(), 3)})
    if q_kvar is not None:
        df["q_kvar"] = np.round(q_kvar.to_numpy(), 3)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path
