# Data sources and licences

Every dataset shipped in `data/` or downloaded into `data/raw/` by the code, with
its licence and the attribution it requires. Processed files are small and
committed; raw downloads are git-ignored and re-fetched on demand.

| File | Content | Source | Licence / terms | Attribution |
|---|---|---|---|---|
| `weather_vasteras_2023.csv` (column `temp_c`) | Hourly air temperature 2023, station 96190 Eskilstuna A (26 km from Västerås; Västerås's own station 96350 is manual, 06/18 UTC only) | SMHI Open Data, meteorological observations, parameter 1, period `corrected-archive` — https://opendata.smhi.se/apidocs/metobs | Creative Commons Attribution 4.0 (CC BY 4.0) — https://www.smhi.se/data/oppna-data/villkor-for-anvandning | "Source: SMHI" |
| `weather_vasteras_2023.csv` (column `pv_kw_per_kwp`) | Hourly PV output of a 1 kWp south-facing 30° building-mounted system at 59.61 N 16.55 E, 2023, SARAH3 radiation database, 14 % system losses | PVGIS 5.3, European Commission Joint Research Centre, via `pvlib.iotools.get_pvgis_hourly` — https://joint-research-centre.ec.europa.eu/pvgis-online-tool_en | Free for any use; cite PVGIS — https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/api-non-interactive-service_en | "© European Union, 2001–2026, PVGIS" |
| `base_load_norway_2021.csv.gz`, `…_meta.csv`, `…_README.md` | Hourly whole-house consumption 2021 of 63 Norwegian detached houses without heat pump, anonymised | Hofmann, M., Bjarghov, S., Nessa, S. (2023). *Norwegian hourly residential electricity demand data with consumer characteristics during the European energy crisis.* Zenodo, v3. https://doi.org/10.5281/zenodo.8423312 ; data article: Data in Brief 51 (2023) 109687, https://doi.org/10.1016/j.dib.2023.109687 | CC BY 4.0 | cite as above |
| `ev_arrival_hist.csv`, `ev_energy_hist.csv` | Plug-in-hour and energy-per-session histograms of 35,377 residential charging sessions (Norwegian apartment garages, 2018–2021) | Sørensen, Å. L., et al. (2024). *Residential electric vehicle charging datasets from apartment buildings.* Zenodo. https://doi.org/10.5281/zenodo.12730566 ; Data in Brief (2021) https://doi.org/10.1016/j.dib.2021.107105 | CC BY 4.0 | cite as above |
| `ev_charging_parameters.md` | Parameter table for the EV model with per-row sources (Trafikanalys, Chalmers, Energiforsk, Power Circle) | listed inside the file | public reports / open statistics | see file |
| `raw/smhi_*_temp.csv` | Full SMHI hourly archive for one station (fetched by `weather.py`) | SMHI, as above | CC BY 4.0 | "Source: SMHI" |
| `raw/pvgis_vasteras_<year>.csv` | Raw PVGIS hourly response (fetched by `weather.py`) | PVGIS, as above | as above | as above |
| `raw/norway/`, `raw/ev/` | Original Zenodo downloads used to build the processed files (not committed) | as above | CC BY 4.0 | as above |

Transformer loss figures in `feeder.py` are the Tier-2 maximum losses of
Commission Regulation (EU) No 548/2014 as amended by (EU) 2019/1783, Annex I
Table I.1 (oil-immersed, 500 kVA: 3 900 W load / 459 W no-load; 800 kVA:
6 000 W / 585 W). Cable conductor resistances are IEC 60228 class 2 (Al, 20 °C);
current ratings follow the Prysmian AXQJ datasheet conditions (in ground, 20 °C
soil, 2.5 K·m/W, 0.7 m, 90 °C conductor).

Swedish household statistics used for defaults (annual hushållsel, heating
system shares) come from Energimyndigheten, *Energistatistik för småhus*, tables
EN0102 (official statistics, free to use with attribution):
https://pxexternal.energimyndigheten.se/pxweb/sv/Energimyndighetens_statistikdatabas/
