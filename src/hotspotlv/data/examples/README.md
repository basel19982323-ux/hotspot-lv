# Example station files

Synthetic profiles (sum of the scenario model's houses, Västerås 2023 weather) in the simplest accepted CSV shape: `timestamp` (local time, Europe/Stockholm) and `p_kw`. `fleet_index.csv` lists them for fleet mode. They are examples, not measurements. Rebuild with `python validation/make_examples.py`.

Built from: the Norwegian household data of Hofmann, Bjarghov & Nessa (2023), https://doi.org/10.5281/zenodo.8423312
(base loads, CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/), SMHI station 96190 temperatures (Source: SMHI,
CC BY 4.0) and PVGIS 2023 PV output (PVGIS © European Communities, 2001–2026); adapted by Basel Haijar, see
`../SOURCES.md`.
