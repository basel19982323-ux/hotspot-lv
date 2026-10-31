"""Write the DSO-export test files of this folder (python tests/fixtures/dso_exports/make_fixtures.py).

Synthetic, not real exports: each file copies one way a Swedish meter-data export can look — encoding,
separator, lines above or below the table, Swedish headers, date order, clock convention at the daylight-saving
changes, resolution and unit. Every file is made from the same load, kw_at(t) below, so a test can check the
values read back against the truth at each UTC instant. README.md lists what each file is meant to catch.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
LOCAL = "Europe/Stockholm"
NORMALTID = "Etc/GMT-1"


def kw_at(t_utc: pd.DatetimeIndex, station: int = 0) -> np.ndarray:
    """The true mean kW of the interval starting at each UTC instant (0.1 kW resolution)."""
    h = t_utc.hour + t_utc.minute / 60
    day = (t_utc - pd.Timestamp("2025-01-01", tz="UTC")).days
    return np.round(150 + 60 * np.sin(2 * np.pi * (np.asarray(h) - 6) / 24) + 0.5 * np.asarray(day) + 40 * station, 1)


def utc_range(start_local: str, end_local: str, step_min: int) -> pd.DatetimeIndex:
    """Every interval start from start_local up to (not including) end_local, Swedish local time, as UTC."""
    a = pd.Timestamp(start_local, tz=LOCAL).tz_convert("UTC")
    b = pd.Timestamp(end_local, tz=LOCAL).tz_convert("UTC")
    return pd.date_range(a, b, freq=f"{step_min}min", inclusive="left")


def comma(x, decimals: int) -> str:
    """Swedish style: decimal comma, no thousands separator."""
    return f"{x:.{decimals}f}".replace(".", ",")


def write(name: str, lines: list[str], encoding: str, newline: str = "\r\n") -> None:
    (HERE / name).write_bytes((newline.join(lines) + newline).encode(encoding))


def local_text(t_utc: pd.DatetimeIndex, fmt: str, zone: str = LOCAL) -> list[str]:
    return list(t_utc.tz_convert(zone).strftime(fmt))


def main() -> None:
    # 1 — hourly kWh, Windows-1252, ';', a preamble, the spring change in local time (01:00 → 03:00, no 02:00 row)
    t = utc_range("2025-03-29 00:00", "2025-04-01 00:00", 60)
    rows = [f"{s};{comma(v, 1)}" for s, v in zip(local_text(t, "%Y-%m-%d %H:%M"), kw_at(t))]
    write("hourly_kwh_cp1252_preamble_spring.csv",
          ["Mätvärden för nätstation", "Anläggning;NS 4711 Björkvägen", "Period;2025-03-29 - 2025-03-31",
           "Enhet;kWh", "", "Tidpunkt;Energi (kWh)"] + rows, "cp1252")

    # 2 — quarter-hour kW, UTF-8 with BOM, ';', the autumn change: 02:00–02:45 twice (summer time first)
    t = utc_range("2025-10-25 00:00", "2025-10-28 00:00", 15)
    rows = [f"{s};{comma(v, 1)}" for s, v in zip(local_text(t, "%Y-%m-%d %H:%M"), kw_at(t))]
    write("quarter_hour_kw_utf8bom_autumn.csv", ["Datum;Effekt (kW)"] + rows, "utf-8-sig")

    # 3 — hourly kW, ',', decimal point, day-first dates dd/mm/yyyy over 16 days (13/01 shows the order)
    t = utc_range("2025-01-01 00:00", "2025-01-17 00:00", 60)
    rows = [f"{s},{v:.1f}" for s, v in zip(local_text(t, "%d/%m/%Y %H:%M"), kw_at(t))]
    write("hourly_kw_slash_dates_dayfirst.csv", ["Datum,Aktiv effekt (kW)"] + rows, "utf-8")

    # 4 — quarter-hour kWh per interval, ISO 8601 with UTC offsets across the spring change
    t = utc_range("2025-03-29 00:00", "2025-03-31 00:00", 15)
    stamps = [s[:-2] + ":" + s[-2:] for s in local_text(t, "%Y-%m-%dT%H:%M:%S%z")]
    rows = [f"{s};{comma(v * 0.25, 3)}" for s, v in zip(stamps, kw_at(t))]
    write("quarter_hour_kwh_iso_offsets.csv", ["Tidpunkt;Energi (kWh)"] + rows, "utf-8")

    # 5 — hourly kWh, local time, the exporter writes the spring hour that does not exist as a 0 row
    t = utc_range("2025-03-29 00:00", "2025-04-01 00:00", 60)
    loc = local_text(t, "%Y-%m-%d %H:%M")
    rows = [f"{s};{comma(v, 1)}" for s, v in zip(loc, kw_at(t))]
    i = loc.index("2025-03-30 03:00")
    rows.insert(i, "2025-03-30 02:00;0,0")
    write("hourly_kwh_spring_zero_row.csv", ["Tidpunkt;Energi (kWh)"] + rows, "utf-8")

    # 6 — the same, but the spring row repeats the 03:00 value (a duplicate in March)
    rows = [f"{s};{comma(v, 1)}" for s, v in zip(loc, kw_at(t))]
    rows.insert(i, "2025-03-30 02:00;" + rows[i].split(";")[1])
    write("hourly_kwh_spring_duplicate_row.csv", ["Tidpunkt;Energi (kWh)"] + rows, "utf-8")

    # 7 — hourly kW, local time, the autumn hour 02:00 written once (the exporter dropped the second one)
    t = utc_range("2025-10-25 00:00", "2025-10-28 00:00", 60)
    loc = local_text(t, "%Y-%m-%d %H:%M")
    keep = [k for k in range(len(t)) if not (loc[k] == "2025-10-26 02:00" and k > 0 and loc[k - 1] == loc[k])]
    rows = [f"{loc[k]};{comma(kw_at(t)[k], 1)}" for k in keep]
    write("hourly_kw_autumn_hour_once.csv", ["Tidpunkt;Effekt (kW)"] + rows, "utf-8")

    # 8 — hourly kWh in normaltid (CET all year): the spring 02:00 hour carries a real value
    t = pd.date_range(pd.Timestamp("2025-03-29 00:00", tz=NORMALTID), periods=72, freq="h").tz_convert("UTC")
    rows = [f"{s};{comma(v, 1)}" for s, v in zip(local_text(t, "%Y-%m-%d %H:%M", NORMALTID), kw_at(t))]
    write("hourly_kwh_normaltid_spring.csv", ["Tidpunkt;Energi (kWh)"] + rows, "utf-8")

    # 9 — three stations in one file, Windows-1252, ';', a Nätstation column and a rating column
    t = utc_range("2025-01-13 00:00", "2025-01-16 00:00", 60)
    loc = local_text(t, "%Y-%m-%d %H:%M")
    rows = []
    for n, (sid, kva) in enumerate((("NS 101 Åkervägen", 315), ("NS 102 Ängen", 500), ("NS 103 Öster", 800))):
        rows += [f"{sid};{kva};{s};{comma(v, 1)}" for s, v in zip(loc, kw_at(t, n))]
    write("multi_station_natstation.csv", ["Nätstation;Märkeffekt (kVA);Tidpunkt;Energi (kWh)"] + rows, "cp1252")

    # 10 — Excel "Unicode text": UTF-16 with BOM, tab-separated, date and time apart, a value and a unit column,
    #      Wh per quarter hour, and a sum line under the table
    t = utc_range("2025-02-03 00:00", "2025-02-05 00:00", 15)
    d, h = local_text(t, "%Y-%m-%d"), local_text(t, "%H:%M")
    wh = np.round(kw_at(t) * 250).astype(int)                     # kW · 0.25 h · 1000 = Wh
    rows = [f"{a}\t{b}\t{v}\tWh" for a, b, v in zip(d, h, wh)]
    write("quarter_hour_wh_utf16_tab_value_unit.txt",
          ["Datum\tTid\tVärde\tEnhet"] + rows + ["", f"Summa\t\t{wh.sum()}\tWh"], "utf-16")

    # 11 — hourly kWh with Swedish thousands dots and decimal commas, a footer, dates dd.mm.yyyy
    t = utc_range("2025-01-01 00:00", "2025-01-04 00:00", 60)
    big = kw_at(t) * 10                                           # a large station: 1 000 – 2 300 kWh per hour
    rows = [f"{s};{f'{v:,.1f}'.replace(',', ' ').replace('.', ',').replace(' ', '.')}"
            for s, v in zip(local_text(t, "%d.%m.%Y %H:%M"), big)]
    write("hourly_kwh_thousands_dots_footer.csv",
          ["Tidpunkt;Förbrukning (kWh)"] + rows + ["Summa;" + comma(big.sum(), 1)], "utf-8")


if __name__ == "__main__":
    main()
