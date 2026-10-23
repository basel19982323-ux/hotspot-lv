# Data sources and licences

Every dataset shipped inside the package (`hotspotlv/data/`) or downloaded by the code into the user cache (`~/.hotspotlv/raw/`, override with HOTSPOTLV_CACHE), with
its licence and the attribution it requires. Processed files are small and
shipped with the package; raw downloads are fetched on demand.

**The data are not under the program's MIT licence.** Each dataset keeps its own licence, listed below;
the CC BY 4.0 datasets were processed for this tool by Basel Haijar as described in each row (the original
data are unchanged at their source).

**Network use.** The program works offline with what it ships (Västerås / Eskilstuna A weather 2023, the
example stations). Choosing another SMHI station, or a station file outside 2023, needs that station's hourly
temperatures from `opendata-download-metobs.smhi.se` (HTTPS): the quality-checked archive (about 7 MB) and, for
the most recent months, SMHI's latest-months data (about 0.1 MB). Both are kept in `%USERPROFILE%\.hotspotlv\raw`
and used again from there; a newer copy is fetched only when the data reach past the saved one (the archive at
most once a day, and only when the data fall into a gap between it and the latest months). SMHI publishes its checked archive about three months late; hours after it come from the
latest months, and the result's notes say from when. Nothing else is sent or fetched.

| File | Content | Source | Licence / terms | Attribution |
|---|---|---|---|---|
| `weather_vasteras_2023.csv` (column `temp_c`) | Hourly air temperature 2023, station 96190 Eskilstuna A (26 km from Västerås; Västerås's own station 96350 is manual, 06/18 UTC only) | SMHI Open Data, meteorological observations, parameter 1, period `corrected-archive` — https://opendata.smhi.se/metobs | CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/ (SMHI's terms: https://www.smhi.se/data/om-smhis-data/villkor-for-anvandning) | "Source: SMHI" — processed: the hours 2022-12-31 23:00 – 2023-12-31 23:00 UTC (the Swedish calendar year 2023 plus the last UTC hour of 2023), gaps of up to 6 h linearly interpolated (4 hours); built by `validation/make_weather.py` |
| `weather_vasteras_2023.csv` (column `pv_kw_per_kwp`) | Hourly PV output of a 1 kWp south-facing 30° building-mounted system at 59.61 N 16.55 E, 2023, SARAH3 radiation database, 14 % system losses | PVGIS 5.3, European Commission Joint Research Centre, via `pvlib.iotools.get_pvgis_hourly` — https://joint-research-centre.ec.europa.eu/pvgis-online-tool_en | Free for any use; cite PVGIS — https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/api-non-interactive-service_en | "PVGIS © European Communities, 2001–2026"; cite Huld, T., Müller, R., Gambardella, A. (2012), *A new solar radiation database for estimating PV performance in Europe and Africa*, Solar Energy 86, 1803–1815 — processed: stamps floored to the hour, W per kWp → kW per kWp |
| `base_load_norway_2021.csv.gz`, `…_meta.csv`, `…_README.md` | Hourly whole-house consumption 2021 of 63 Norwegian detached houses without heat pump, anonymised | Hofmann, M., Bjarghov, S., Nessa, S. (2023). *Norwegian hourly residential electricity demand data with consumer characteristics during the European energy crisis.* Zenodo, v3. https://doi.org/10.5281/zenodo.8423312 ; data article: Data in Brief 51 (2023) 109687, https://doi.org/10.1016/j.dib.2023.109687 | CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/ | cite as above — processed: a subset (the 63 houses without a heat pump), renamed h001–h063, gaps filled as described in the README |
| `ev_arrival_hist.csv`, `ev_energy_hist.csv` | Plug-in-hour and energy-per-session histograms built from 35,336 of the dataset's 35,377 residential charging sessions (12 Norwegian residential locations, 2018–2021). Reference only: the EV model uses the parametric fits documented in `ev_charging_parameters.md`; the code does not read these two files | Sørensen, Å. L. (2024). *Data files: Electric vehicle charging dataset with 35,000 charging sessions from 12 residential locations in Norway* (v1). Zenodo. https://doi.org/10.5281/zenodo.12730566 ; data article: Sørensen, Å. L., Sartori, I., Lindberg, K. B., Andresen, I. (2024), Data in Brief 57, 110883, https://doi.org/10.1016/j.dib.2024.110883 | CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/ | cite as above — processed: aggregated to histograms |
| `ev_charging_parameters.md` | Parameter table for the EV model with per-row sources (Trafikanalys, Chalmers, Energiforsk, Power Circle) | listed inside the file | public reports / open statistics | see file |
| `~/.hotspotlv/raw/smhi_*_temp.csv` | Full SMHI hourly archive for one station, period `corrected-archive` (fetched by `weather.py` when needed) | SMHI, as above | CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/ | "Source: SMHI" — processed as above |
| `~/.hotspotlv/raw/smhi_*_latest.csv` | SMHI's latest four months for one station, period `latest-months`, not yet in the checked archive (fetched only for data after the archive's end) | SMHI, as above | CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/ | "Source: SMHI" — processed as above; the result's notes name the first hour taken from it |
| `~/.hotspotlv/raw/pvgis_<tag>_<year>.csv` | Raw PVGIS hourly response (fetched by `weather.py`) | PVGIS, as above | as above | as above |
| repo `data/raw/norway/`, `data/raw/ev/` | Original Zenodo downloads used to build the processed files (not committed) | as above | CC BY 4.0 | as above |

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

## Cost assumptions (indicative, `costs.py`)
| Item | Default | Source / status |
|---|---|---|
| Transformer unit 800 kVA 12/0.4 kV (swap inside the existing kiosk) | 209 kSEK | Ei Normvärdeslista elnät 2024–2027 v1.1, code NG15921, value year 2022 — https://ei.se/download/18.61d7efeb18b048db98c4e48/1697184391450/Normv%C3%A4rdeslista-2024-2027.xlsx (method: Sweco report https://ei.se/download/18.61d7efeb18b048db98c4e5a/1697035558170/Konsultrapport-Normv%C3%A4rdeslista-2024%E2%80%932027-SWECO.pdf). 500 kVA unit NG15922: 156 kSEK. |
| Complete new 800 kVA station (kiosk + unit) | 490–634 kSEK | Ei codes NG15223 (sheet-metal kiosk, 281 kSEK) / NG15223B (concrete, 425 kSEK), "exklusive transformator", + unit 209 kSEK; value year 2022 |
| Small LV substation battery, turnkey | 4 000–6 000 SEK/kWh (slider 2 000–8 000) | assumption; anchors: Europe utility-scale turnkey ≈ 1 900 SEK/kWh (BNEF energy-storage price survey Dec 2025, Europe US$ 177/kWh; Dec 2024 Europe US$ 275/kWh), Swedish residential installed 6 000–7 500 SEK/kWh (installer guides 2025/26); no public price for a ≤ 1 MWh LV-station unit found |
| Coordinated (spread) EV charging per charger | 0–5 000 SEK | assumption; house load balancers 1 200–2 000 SEK (Zaptec Sense, Easee Equalizer) plus electrician; a tariff/DSO signal can be free |
| Night-timer charging | 0 SEK | optimistic bound: Energiforsk 2023:966 survey — ≈ 25 % on hourly contracts control charging; price-optimised charging "kan skapa nya effekttoppar" |
| Reinforcement-equivalent value of peak kW avoided | 4.4 SEK/kW/yr ≈ 220 SEK/kW one-off | Energiforsk 2024:1043 §6.2 (≈ 100 kSEK per ~450 kW station step over ~50 yr, undiscounted; flexibility most likely defers reinforcement) — https://energiforsk.se/media/33660/2024-1043-analys-av-na-tutvecklingsplaner-och-flexibilitet-mo-jligheter-fo-r-digitalisering.pdf |
| Service lives | transformer 40 yr, battery 15 yr, charging control 10 yr | assumptions, shown so capex alone does not favour short-lived assets |

## Example station files (`data/examples/`)
Synthetic: the sum of the scenario model's houses for three imaginary Västerås stations. Adapted from the
Norwegian household data above (the base loads; CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/), with SMHI 96190 temperatures (CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/) and PVGIS
2023 PV output (PVGIS terms) — attributions as above. Not measurements; the station names say "(synthetic)".
Rebuilt by `validation/make_examples.py`.
