"""`hotspot-lv report <csv> --kva 500 [--out report.pdf]` — one station's assessment as a file, without the window.

    CSV ──► read_station_csv (what was found, what the file could not tell)
        ──► SMHI outdoor temperature ──► assess_station ──► station report (app/report.py, report=True)
                                                             .pdf (A4, as the station card) or .html (one file)

The same pipeline as the Own-station tab, so the report and the screen agree. Charts are drawn with the app's
own Qt charts, laid out but never shown (no window opens). Prints a five-line summary; exit code 0 on success,
2 for wrong arguments, 1 when the file cannot be assessed (the reason is printed)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

UNITS = ("kW", "kWh", "W", "Wh", "MW", "MWh")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="hotspot-lv report",
        description="Assess one station's meter export and write a report (simulation: indicative result, generic "
                    "transformer constants, not validated on a real station).")
    ap.add_argument("csv", help="the station's CSV or text export (encoding, separator, header, dates and clock are "
                                "detected; the report lists what was found)")
    ap.add_argument("--kva", type=float, required=True,
                    help="transformer rating: a catalogue size (50, 100, 160, 200, 250, 315, 400, 500, 630, 800, "
                         "1000), or any size with --p0 and --pk")
    ap.add_argument("--out", help="report file, .pdf (default) or .html; default: <csv name>-report.pdf next to the CSV")
    ap.add_argument("--p0", type=float, help="custom unit: no-load loss in W (from the test report)")
    ap.add_argument("--pk", type=float, help="custom unit: load loss in W at rated current")
    ap.add_argument("--uk", type=float, help="custom unit: short-circuit impedance in %% (else 4 %% is assumed)")
    ap.add_argument("--station", help="one station of a file with a Station / Nätstation column")
    ap.add_argument("--smhi", type=int, help="SMHI station id for the outdoor temperature (default 96190 Eskilstuna A; "
                                             "choose the one nearest the place)")
    ap.add_argument("--measured-at", choices=("meters", "transformer"), default="meters",
                    help="the values are the sum of customer meters (default) or measured at the transformer")
    ap.add_argument("--tz", default="auto", help="clock of tz-naive stamps: auto (default), Europe/Stockholm, normaltid")
    ap.add_argument("--stamp", choices=("start", "end"), default="start",
                    help="a stamp labels the interval that starts (default) or ends there")
    ap.add_argument("--unit", choices=UNITS, help="unit of the value column, when the file does not state it")
    order = ap.add_mutually_exclusive_group()
    order.add_argument("--dayfirst", dest="dayfirst", action="store_true", default=None,
                       help="dates are day first (dd/mm/yyyy), when the dates cannot show it")
    order.add_argument("--monthfirst", dest="dayfirst", action="store_false", help="dates are month first (mm/dd/yyyy)")
    ap.add_argument("--enclosure", choices=("kiosk", "outdoor"), default="kiosk")
    return ap


def _say(text: str) -> None:
    if sys.stdout is not None:                       # a windowed build has no console
        print(text, flush=True)


def run(args) -> Path:
    """Read, assess and write; returns the report's path (raises ValueError / OSError with the reason)."""
    if sys.platform != "win32":                  # no display needed (Linux: fontconfig gives the fonts). On Windows the
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # normal platform: its offscreen one has no fonts
    from PySide6.QtWidgets import QApplication                # and prints boxes; the charts are never shown

    from hotspotlv.app.report import write_report_html, write_station_card
    from hotspotlv.catalogue import custom_transformer
    from hotspotlv.station import ambient_for, assess_station, read_station_csv
    from hotspotlv.weather import SMHI_STATION

    src = Path(args.csv)
    if not src.is_file():
        raise ValueError(f"{src} is not a file")
    out = Path(args.out) if args.out else src.with_name(f"{src.stem}-report.pdf")
    if out.suffix.lower() not in (".pdf", ".html", ".htm"):
        raise ValueError(f"--out must end in .pdf or .html, not {out.suffix or 'nothing'}")
    if (args.p0 is None) != (args.pk is None):
        raise ValueError("a custom unit needs both --p0 and --pk (watts)")
    if args.uk is not None and args.p0 is None:
        raise ValueError("--uk is for a custom unit: give --p0 and --pk as well")
    losses = custom_transformer(args.kva, args.p0, args.pk, args.uk) if args.p0 is not None else None
    app = QApplication.instance() or QApplication([])          # noqa: F841 — the charts need it alive
    st = read_station_csv(src, measured_at=args.measured_at, tz=args.tz, stamp=args.stamp, unit=args.unit,
                          station=args.station, dayfirst=args.dayfirst)
    smhi = args.smhi if args.smhi is not None else SMHI_STATION
    amb = ambient_for(st.index, smhi)
    res = assess_station(st, args.kva, amb, smhi_station=args.smhi, enclosure=args.enclosure, losses=losses)
    name = src.name + (f" [{args.station}]" if args.station else "")
    if out.suffix.lower() == ".pdf":
        write_station_card(res, amb, out, source_name=name, report=True)
    else:
        write_report_html(res, amb, out, source_name=name)
    v = res.verdict
    _say(v.headline())
    _say(f"rating {res.rated_kva:g} kVA · peak {v.on_paper['peak load (% of nameplate)']:.1f} % of nameplate · "
         f"{st.period()}, {st.step_min:g}-min values")
    for u in st.unsure:
        _say(f"not told by the file: {u}")
    _say(f"report: {out.resolve()}")
    _say("Indicative result, generic transformer constants, not validated on a real station.")
    return out


def main(argv: list[str] | None = None) -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")     # a pipe in a Windows code page cannot take every '→' or '≤'
    ap = parser()
    args = ap.parse_args(argv)                       # exits with 2 and the usage on wrong arguments
    try:
        run(args)
    except (ValueError, OSError) as e:
        _say(f"hotspot-lv report: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
