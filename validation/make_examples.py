"""Build the example station files shipped with the package (src/hotspotlv/data/examples/).

The profiles are synthetic: the sum of the scenario model's houses for three
imaginary Västerås stations, written in the simplest accepted CSV shape
(local timestamps, kW). They exist so the app's "Load example" button and the
tests have something to open — they are not measurements.
"""

from pathlib import Path

import pandas as pd

from hotspotlv.neighbourhood import Scenario, build_neighbourhood
from hotspotlv.paths import package_data
from hotspotlv.station import export_station_csv
from hotspotlv.weather import get_weather

OUT = package_data() / "examples"
STATIONS = [
    ("NS-101 Lövängen", 500, Scenario("today", n_houses=45, ev_share=0.25, pv_share=0.2, seed=11)),
    ("NS-102 Björkbacken", 500, Scenario("2030", n_houses=80, ev_share=0.6, pv_share=0.4, seed=12,
                                         heating_mix={"ground_source": 0.3, "air_water": 0.3, "direct_electric": 0.2, "none": 0.2})),
    ("NS-103 Tallhöjden", 800, Scenario("2030", n_houses=110, ev_share=0.6, pv_share=0.4, seed=13,
                                        heating_mix={"ground_source": 0.3, "air_water": 0.3, "direct_electric": 0.2, "none": 0.2})),
]


def main():
    wx = get_weather(2023)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for sid, kva, sc in STATIONS:
        nb = build_neighbourhood(sc, wx)
        p = nb.total_kw
        fname = sid.split()[0].lower() + ".csv"
        export_station_csv(p, OUT / fname)
        rows.append({"station_id": sid, "rated_kva": kva, "file": fname, "smhi_station": 96190, "enclosure": "kiosk"})
        print(f"{sid}: {len(p)} rows, peak {p.max():.0f} kW, {p.clip(lower=0).sum() / 1000:.0f} MWh → {fname}")
    pd.DataFrame(rows).to_csv(OUT / "fleet_index.csv", index=False)
    (OUT / "README.md").write_text(
        "# Example station files\n\nSynthetic profiles (sum of the scenario model's houses, Västerås 2023 weather) "
        "in the simplest accepted CSV shape: `timestamp` (local time, Europe/Stockholm) and `p_kw`. "
        "`fleet_index.csv` lists them for fleet mode. They are examples, not measurements. "
        "Rebuild with `python validation/make_examples.py`.\n", encoding="utf-8")
    print("written to", OUT)


if __name__ == "__main__":
    main()
