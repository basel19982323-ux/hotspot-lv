"""
What a meter-data export IS, before what it says: encoding, separator, where the table starts and ends, the
order of day and month, and the clock its stamps are written in. station.py reads the columns; this module
finds the table and turns its stamps into UTC instants — and says what it found, and what it could not tell.

    bytes ──► decode()        UTF-8 (with or without BOM), UTF-16 (Excel "Unicode text"), else Windows-1252
          ──► locate()        separator ; tab , | — the one that splits the most lines into a date + values;
                              the header is the last line above the first run of data lines with no number
                              and no date in it; lines above it (a preamble) and below the last data line
                              (a "Summa" line) are left out and counted
          ──► stamp_text()    dd/mm/yyyy, dd.mm.yyyy, mm/dd/yyyy → ISO; the order is decided by a day above 12,
                              else by which order gives a regular meter interval, else day first (and said so)
          ──► to_utc()        offsets as given; tz-naive stamps in Swedish local time (summer time) or normaltid
                              (CET all year), decided from the daylight-saving days the file contains:

    the spring hour 02:00–02:59, which does not exist in Swedish summer time
        absent, or rows with 0 / blank / a copy of the next or previous hour  → local time; such rows dropped
        rows with real values, and no October hour twice                    → normaltid (CET all year)
    the October hour 02:00–02:59, which happens twice
        twice                       → first = summer time (+02:00), second = winter time (+01:00), by file order
        once                        → taken as the summer-time hour; the winter-time hour is a gap (filled
                                      like any short gap, and said so)

Nothing here guesses silently: every decision is one line of `found` (what was detected) or `unsure` (what
the file cannot tell, with what was assumed and how to override it).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

LOCAL_TZ = "Europe/Stockholm"
NORMALTID_TZ = "Etc/GMT-1"               # CET all year, no DST
SEPARATORS = (";", "\t", ",", "|")       # tie → the first: Swedish exports are mostly ';'
SEP_NAMES = {";": "semicolon (;)", "\t": "tab", ",": "comma (,)", "|": "vertical bar (|)"}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{8}")
NUMBER_RE = re.compile(r"^[-+]?\d[\d\s.,  ]*(?:\s*[a-zA-Z]{1,5})?$")
DMY_RE = re.compile(r"^(\d{1,2})([./-])(\d{1,2})\2(\d{4}|\d{2})(?:[ T]+(.*))?$")
ALLOWED_STEPS_MIN = (5.0, 10.0, 15.0, 30.0, 60.0)
MIN_RUN = 3                              # data lines in a row that start the table


@dataclass
class Table:
    df: pd.DataFrame                     # the table as strings, with the file's own header
    sep: str
    found: list[tuple[str, str]] = field(default_factory=list)    # (what, how it was read)
    unsure: list[str] = field(default_factory=list)               # what the file could not tell


def decode(raw: bytes, name: str = "the file") -> tuple[str, str]:
    """(text, encoding name)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="strict"), "UTF-8 with BOM"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "UTF-16 (Excel 'Unicode text')"
    if raw.isascii():
        return raw.decode("ascii"), "ASCII (plain UTF-8)"
    try:
        return raw.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252"), "Windows-1252"
    except UnicodeDecodeError as e:
        raise ValueError(f"cannot decode {name}: neither UTF-8, UTF-16 nor Windows-1252 ({e})") from e


def _fields(line: str, sep: str) -> list[str]:
    try:
        return next(csv.reader([line], delimiter=sep, skipinitialspace=True))
    except (csv.Error, StopIteration):
        return [line]


def _is_data(fields: list[str]) -> bool:
    return len(fields) >= 2 and any(DATE_RE.search(f) for f in fields)


def _is_header(fields: list[str]) -> bool:
    cells = [f.strip() for f in fields if f.strip()]
    return len(fields) >= 2 and bool(cells) and not any(NUMBER_RE.match(c) or DATE_RE.search(c) for c in cells)


def locate(text: str, sep: str | None = None) -> tuple[str, int, int, int] | None:
    """(separator, header line, first line after the table, data lines) — 0-based line numbers — or None when no
    separator splits any line into a date and values (the caller then reads the file as it is)."""
    lines = text.splitlines()
    best = None
    for s in ([sep] if sep else SEPARATORS):
        data = [_is_data(_fields(l, s)) if l.strip() else False for l in lines]
        n = sum(data)
        if n and (best is None or n > best[1]):
            best = (s, n, data)
    if best is None:
        return None
    s, n, data = best
    run = min(MIN_RUN, n)                                  # the first run of data lines starts the table (a dated
    starts = [i for i in range(len(lines) - run + 1) if all(data[i:i + run])]   # preamble line is not a run)
    start = starts[0] if starts else data.index(True)
    header = start - 1                                     # up over blank lines and broken data rows (a number
    while header >= 0:                                     # but no date: kept, so the reader names them) to the
        f = _fields(lines[header], s)                      # first line of names
        if lines[header].strip() and (_is_header(f) or len(f) < 2):
            break
        header -= 1
    if header < 0 or not _is_header(_fields(lines[header], s)):
        raise ValueError(f"no header row above the first data row (line {start + 1}: {lines[start][:60]!r}) — the "
                         "table needs column names such as 'Tidpunkt;Energi (kWh)'")
    last = max(i for i, d in enumerate(data) if d)
    return s, header, last + 1, n


def read_table(source, sep: str | None = None) -> Table:
    """The table of a file (a path, or an open text/binary file) as strings, and what was found on the way."""
    raw = source.read() if hasattr(source, "read") else Path(source).read_bytes()
    name = getattr(source, "name", None) or str(source)
    if isinstance(raw, bytes):
        text, encoding = decode(raw, name)
    else:
        text, encoding = raw.lstrip("﻿"), "text (already decoded)"
    found = [("encoding", encoding)]
    unsure: list[str] = []
    spot = locate(text, sep)
    if spot is None:                                       # no date anywhere: let pandas read it, the column
        df = pd.read_csv(io.StringIO(text), sep=sep, engine="python", dtype=str, skipinitialspace=True)   # checks
        head = text.split("\n", 1)[0]                                                                    # explain
        sep = sep or (";" if ";" in head else ("\t" if "\t" in head else ","))
        found.append(("separator", SEP_NAMES.get(sep, repr(sep))))
        return Table(df, sep, found, unsure)
    sep, header, end, n_data = spot
    lines = text.splitlines()
    body = "\n".join(lines[header:end])
    df = pd.read_csv(io.StringIO(body), sep=sep, engine="python", dtype=str, skipinitialspace=True)
    found.append(("separator", SEP_NAMES.get(sep, repr(sep))))
    above = sum(1 for l in lines[:header] if l.strip())
    found.append(("header", f"line {header + 1}: {lines[header].strip()[:80]}"
                  + (f" ({above} line{'s' if above != 1 else ''} above it left out)" if above else "")))
    below = [l for l in lines[end:] if l.strip()]
    if below:
        found.append(("below the table", f"{len(below)} line{'s' if len(below) != 1 else ''} left out "
                                          f"(first: {below[0].strip()[:60]!r})"))
    return Table(df, sep, found, unsure)


# ---------------------------------------------------------------------------
# stamps
# ---------------------------------------------------------------------------
def _regular(ts: pd.Series) -> bool:
    """Do these stamps fall on one meter interval with no jump — the span about (rows − 1) steps, allowing an hour
    for daylight saving and a few missing steps? A wrong day/month order jumps by weeks at every change of day."""
    t = pd.to_datetime(ts, errors="coerce", format="ISO8601").dropna().drop_duplicates().sort_values()
    if len(t) < 3:
        return True
    d = np.diff(t.to_numpy().astype("datetime64[m]").astype("int64"))
    values, counts = np.unique(d, return_counts=True)
    step = float(values[np.argmax(counts)])
    return (step in ALLOWED_STEPS_MIN and counts.max() / len(d) >= 0.9
            and d.sum() <= (len(d) + 6) * step + 60)


def _iso(m: pd.DataFrame, dayfirst: bool) -> pd.Series:
    day, month = (m[0], m[2]) if dayfirst else (m[2], m[0])
    year = m[3].where(m[3].str.len() == 4, "20" + m[3])
    time = m[4].fillna("").str.strip()
    return (year + "-" + month.str.zfill(2) + "-" + day.str.zfill(2) + (" " + time).where(time != "", ""))


def stamp_text(raw: pd.Series, dayfirst: bool | None = None) -> tuple[pd.Series, str, str | None]:
    """Stamps rewritten so pandas reads them unambiguously: (text, the format found, what could not be told).
    Only day-month-year shapes are rewritten (to ISO); ISO stamps, with or without offsets, pass unchanged."""
    s = raw.astype(str).str.strip()
    m = s.str.extract(DMY_RE)
    if not m[0].notna().all():
        first = s.iloc[0]
        if re.match(r"^\d{4}-\d{2}-\d{2}T", first):
            shape = "ISO 8601 (YYYY-MM-DDThh:mm:ss" + ("±hh:mm" if re.search(r"[+-]\d{2}:?\d{2}$|Z$", first) else "") + ")"
        elif re.match(r"^\d{4}-\d{2}-\d{2}", first):
            shape = "YYYY-MM-DD hh:mm" + (":ss" if re.match(r"^\S+ \d{1,2}:\d{2}:\d{2}", first) else "")
        else:
            shape = f"as written (first: {first!r})"
        return s, shape, None
    a, b = m[0].astype(int), m[2].astype(int)
    mark = m[1].iloc[0]
    unsure = None
    if a.max() > 12 and b.max() > 12:
        raise ValueError(f"dates such as {s[(a > 12) | (b > 12)].iloc[0]!r}: neither the first nor the second number "
                         "can be the month")
    if a.max() > 12:
        order = True
    elif b.max() > 12:
        order = False
    elif dayfirst is not None:
        order = dayfirst
    elif mark == ".":
        order = True                                       # dd.mm.yyyy: dots are never month first
    else:
        fits = {o: _regular(_iso(m, o)) for o in (True, False)}
        if fits[True] != fits[False]:
            order = fits[True]
        else:
            order = True
            unsure = (f"day or month first cannot be told from these dates ({s.iloc[0]!r} …): day first assumed, "
                      "the Swedish order — pass dayfirst=False if the file is month first")
    if dayfirst is not None and dayfirst != order:
        raise ValueError(f"dayfirst={dayfirst} does not fit dates such as {s[(a > 12) | (b > 12)].iloc[0]!r}")
    shape = (f"DD{mark}MM{mark}YYYY hh:mm (day first)" if order else f"MM{mark}DD{mark}YYYY hh:mm (month first)")
    return _iso(m, order), shape, unsure


def _placeholder(values: np.ndarray, i: int, j: int | None) -> bool:
    """Is row i blank, zero, or a copy of row j?"""
    v = values[i]
    if np.all(np.isnan(v)) or np.all(np.nan_to_num(v) == 0):
        return True
    return j is not None and np.allclose(np.nan_to_num(v), np.nan_to_num(values[j]), rtol=0, atol=1e-9)


def to_utc(ts: pd.Series, values: np.ndarray, tz: str = "auto") -> tuple[pd.Series, np.ndarray, list, list, list]:
    """tz-naive stamps (file order, time order) → (UTC stamps, rows to drop, notes, found, unsure).
    tz: 'auto' (Swedish local time unless the daylight-saving days prove normaltid), 'Europe/Stockholm' (local
    time, never switched), 'normaltid' (CET all year), or any other zone name. values: the numbers of each row
    (rows × value columns, NaN for blank), to tell a placeholder row from a measured one."""
    notes, found, unsure = [], [], []
    auto = tz.lower() == "auto"
    zone = LOCAL_TZ if auto else (NORMALTID_TZ if tz.lower() in ("normaltid", "cet", "etc/gmt-1") else tz)
    n = len(ts)
    drop = np.zeros(n, bool)
    if zone != LOCAL_TZ:
        try:
            loc = ts.dt.tz_localize(zone, ambiguous="infer", nonexistent="raise")
        except Exception as e:  # pytz AmbiguousTimeError / NonExistentTimeError and friends
            if not (isinstance(e, ValueError) or type(e).__name__.endswith("TimeError")):
                raise
            raise ValueError(f"timestamps do not fit {zone!r} ({type(e).__name__}); rows must be in time order") from e
        what = ("normaltid (CET = UTC+1 all year, no summer time)" if zone == NORMALTID_TZ else f"{zone} local time")
        notes.append(f"tz-naive timestamps taken as {what}")
        found.append(("clock", what + " — as chosen"))
        return loc.dt.tz_convert("UTC"), drop, notes, found, unsure

    nonexist = ts.dt.tz_localize(zone, ambiguous=np.ones(n, bool), nonexistent="NaT").isna().to_numpy()
    amb = ts.dt.tz_localize(zone, ambiguous="NaT", nonexistent="shift_forward").isna().to_numpy()
    row_of = {}
    for i, t in enumerate(ts):
        row_of.setdefault(t, i)
    hour = pd.Timedelta(hours=1)
    spring = np.flatnonzero(nonexist)
    fake = [i for i in spring if _placeholder(values, i, row_of.get(ts.iloc[i] + hour)) or
            _placeholder(values, i, row_of.get(ts.iloc[i] - hour))]
    real = sorted(set(spring) - set(fake))
    occ = np.full(n, -1)                                   # 0 for the first row of an October stamp, 1 for the next
    seen: dict = {}
    for i in np.flatnonzero(amb):
        occ[i] = seen.get(ts.iloc[i], 0)
        seen[ts.iloc[i]] = occ[i] + 1
    twice = amb & (occ >= 1)
    once_stamps = sorted({ts.iloc[i] for i in np.flatnonzero(amb)} - {ts.iloc[i] for i in np.flatnonzero(twice)})
    day = lambda i: f"{ts.iloc[i]:%Y-%m-%d}"

    if real and not twice.any():
        if not auto:
            raise ValueError(f"{len(real)} rows are stamped in the spring hour that does not exist in Swedish summer "
                             f"time ({ts.iloc[real[0]]:%Y-%m-%d %H:%M}) and carry real values: the file is in "
                             "normaltid (CET all year) — read it with tz='normaltid' (or 'auto')")
        loc = ts.dt.tz_localize(NORMALTID_TZ)
        notes.append("tz-naive timestamps taken as normaltid (CET = UTC+1 all year, no summer time)")
        found.append(("clock", f"normaltid (CET all year) — detected: the hour {ts.iloc[real[0]]:%Y-%m-%d %H:%M}, "
                               "which does not exist in Swedish summer time, carries measured values"
                      + ("" if not amb.any() else ", and the October hour is not repeated")))
        if not amb.any():
            unsure.append("the clock was decided from the spring change alone (the file has no October change)")
        return loc.dt.tz_convert("UTC"), drop, notes, found, unsure

    for i in spring:                                       # local time: a spring row is a placeholder (or, with
        drop[i] = True                                     # the October hour repeated, a fill) — never a measurement
    if len(spring):
        kind = "a copy of the next or previous hour, 0 or blank" if not real else "filled values"
        notes.append(f"{len(spring)} row{'s' if len(spring) != 1 else ''} stamped in the spring hour that does not "
                     f"exist in Swedish summer time ({day(spring[0])} 02:00–02:59) dropped ({kind})")
    ambiguous = np.where(occ == 0, True, False)            # first occurrence = summer time, later = winter time
    loc = ts.dt.tz_localize(zone, ambiguous=ambiguous, nonexistent="NaT")
    notes.append(f"tz-naive timestamps taken as {LOCAL_TZ} local time")
    how = ["Swedish local time with summer time" + (" (detected: no row in the spring hour that does not exist)"
                                                    if (spring.size == 0 and _has_spring(ts)) else "")]
    if twice.any():
        how.append(f"the October hour {day(np.flatnonzero(twice)[0])} 02:00–02:59 appears twice: the first is "
                   "summer time (+02:00), the second winter time (+01:00)")
    if once_stamps:
        how.append(f"the October hour {once_stamps[0]:%Y-%m-%d} 02:00–02:59 appears only once: taken as summer time; "
                   "the winter-time hour is missing and filled like a gap")
        notes.append(how[-1] + " — if the file adds both hours into one row, that row is about twice too high")
    found.append(("clock", "; ".join(how)))
    if not (_has_spring(ts) or amb.any()):
        unsure.append("clock: the file has no daylight-saving change, so Swedish local time and normaltid cannot be "
                      "told apart; local time assumed — choose normaltid if the export says CET / UTC+1")
    elif once_stamps and not _has_spring(ts):
        unsure.append("clock: a single October hour fits local time with a dropped hour and normaltid alike; local "
                      "time assumed — choose normaltid if the export says CET / UTC+1")
    return loc.dt.tz_convert("UTC"), drop, notes, found, unsure


def _has_spring(ts: pd.Series) -> bool:
    """Does the file cover a spring change (some last Sunday of March, 01:00 and 03:00 both present)?"""
    t = pd.DatetimeIndex(ts)
    for y in sorted(set(t.year)):
        d = pd.Timestamp(f"{y}-03-31")
        sunday = d - pd.Timedelta(days=(d.weekday() + 1) % 7)
        if (t == sunday + pd.Timedelta(hours=1)).any() and (t == sunday + pd.Timedelta(hours=3)).any():
            return True
    return False
