# Home EV-charging model — statistical inputs (Swedish villas)

Author: Basel Haijar. Status: literature values for a *simulation*; every row says how far its source is from "Swedish villa home charging".
Model: each EV-owning house, each day → (a) charge today? (b) plug-in time (c) energy (d) charger power. Uncontrolled = start at plug-in, constant power until done, 11 kW cap. Smart variant = shift start to 22:00.

## Parameter table

| # | Parameter | Value to use | Distribution for the simulation | Source | Distance from "Swedish villa" |
|---|---|---|---|---|---|
| 1 | Charge today? (prob. per day) | **0.40** (sens. 0.30–0.50) | Bernoulli(0.40); optional: weekdays 0.42, weekend 0.36 | Chalmers 334 GPS-logged EVs in Sweden [1]: home charging every 2.5 driving days (large battery) / 1.6 (small); 74 % of EVs drive on a given day → p ≈ 0.30–0.46. Norwegian residential data [3]: 3.2 sessions/user/week (median 2.6) → 0.46 | [1] is Swedish, 70 % of sample lives in houses [2]; [3] is Nordic but apartment garages |
| 2 | Plug-in (arrival) time | peak 16–17 h, mean 17.5 h | **Normal(17.5 h, 2.0 h) truncated 13–23**, or draw from `ev_arrival_hist.csv` (hourly shares, weekday/weekend) | [1]: EVs arrive home 16:00–20:00, peak 17:00; Energiforsk 2023:966 fig 6.2 [2]: most start charging on arrival (evening). Histogram from [3]: weekday mean 17.55 h, sd 4.2, p10 12.1, p90 22.5 | Peak hour Swedish-verified; the histogram shape is Norwegian apartments (wider tail than villas, sd 4.2 → use 2.0 for villas) |
| 3 | Energy per session (kWh) | median 10, mean 14 | **lognormal(mu = ln 10 = 2.30, sigma = 0.82)**, cap at 60 kWh (typical battery) | [3]: mean 12.7, median 9.0, p90 26.8, lognormal fit mu 2.16 / sigma 0.94 (see `ev_energy_hist.csv`). Scaled up so that p × mean = 0.7 × weekly driving energy of a Swedish chargeable car (row 8) | Shape Norwegian apartments; level calibrated to Swedish km/yr and 70 % home share [1] |
| 4 | Charger power (kW) | 11 kW 3-ph 16 A main case | Categorical: **11 kW 3-ph 16 A: 55 %, 3.7 kW 1-ph 16 A: 30 %, 7.4 kW 1-ph 32 A: 15 %** (assumption); delivered power = min(charger, car) | [1]: measured home power clusters 3–4 kW and 9–10 kW (at battery, after losses, i.e. 1-ph 16 A vs 3-ph 16 A); [2]: most sessions 3–7 kW; Elsäkerhetsverket [6]: laddbox recommended, lower power usually enough overnight. No Swedish market share statistic found → split is an assumption | Swedish; share split is an assumption, say so in the write-up |
| 5 | Charging efficiency (wall → battery) | 0.90 | constant | Standard assumption; [1] measures at battery (3.68 kW nominal for 1-ph 16 A) | generic |
| 6 | Smart-charging variant | start 22:00 | shift start to Uniform(22:00, 01:00) to avoid a synthetic 22:00 spike; same energy | [2] fig 6.2: hourly-price customers (~25 %) start 22–01; [1]: 50 % of detached-house owners delay start > 1 h | Swedish, measured |
| 7 | Annual driving, chargeable car | **17 000 km/yr** (1 700 mil) | fixed per house, or Normal(17 000, 4 000) | Trafikanalys Körsträckor 2025 [4]: all cars 1 243 mil; BEV+PHEV ≈ 1 700 mil; private owners 1 155 mil. 2024: all 1 141, BEV 1 390 mil [10]. [1]: BEV median 14 442, mean 16 530 km/yr | Swedish, official |
| 8 | Weekly home-charged energy (check) | ≈ 41 kWh/wk/car | 17 000 km × 0.18 kWh/km (at wall, annual) / 52 × 0.70 home share | [1]: 70 % of energy charged at home, 86 % of that overnight | Swedish, measured |
| 9 | Consumption, annual mean | 0.16 kWh/km at battery, ≈ 0.18 at wall | constant | [1]: 0.156 kWh/km measured; [2] assumes 0.16 | Swedish, measured |
| 10 | Consumption, winter | **0.22–0.25 kWh/km at wall** (sens. 0.27) | winter months Dec–Feb: multiply row 9 by 1.3 | Finnish test at −9 °C: 27–28 kWh/100 km, +31–42 % vs cycle [8]; NAF 2026 test at −30 °C: 9 of 24 cars lost ≥ 40 % range [9]; [3]: Jan/Feb sessions only +5 % kWh (apartment users) | Nordic tests, not Swedish villas; big spread by car and temperature |
| 11 | Chargeable cars in fleet, 2025-12-31 | **16 %** (806 521 of 5 042 919); BEV 8.6 % (432 709), PHEV 373 812 | — | Trafikanalys press 2026-02-12 [5]; Fordonsåret 2025 [7] | Swedish, official |
| 12 | New registrations 2025 | 283 276 cars; **BEV 35 %** (100 365), PHEV 26 % (73 223), chargeable 61 % | — | [7] | Swedish, official |
| 13 | Forecast | 2027: 1 million chargeable; 2028: 23 % of fleet, 71 % of new regs chargeable | — | Trafikanalys prognos 2025-05-21 [11]. Power Circle June 2026: BEV share of new sales 50 % in 2027, ~98 % 2034 [12]; older Power Circle 2019: 2.5 M chargeable by 2030 [13]. No public 2030 fleet number found (ELIS is paid) | Swedish; 2030 ≈ 30 % of fleet by extrapolating [11] — label as own extrapolation |
| 14 | Share of villas with ≥ 1 chargeable car | 2025: **25 %**; 2030: **45 %** (assumption) | Bernoulli per house | Above fleet share because villa households own more cars and 70 % of EV owners in [2] live in houses; Stockholm county already 30 % [5] | Assumption built on Swedish numbers — state it |

## Files
- `data/ev_arrival_hist.csv` — hour, weekday_share, weekend_share (35 336 sessions, [3]).
- `data/ev_energy_hist.csv` — kwh_bin (2 kWh bins), share ([3]).
- `data/raw/ev/norway_residential_Dataset1_charging_reports.csv` — raw sessions, CC BY 4.0 (Sørensen et al. 2024).

## Caveats
- ElaadNL (https://elaad.nl/en/?p=3630): open data = aggregated 2018–2020 profiles for public, private and workplace chargers in the Netherlands; the dashboard moved to https://data.elaad.nl/ and could not be downloaded here (TLS certificate error). It is mostly Dutch public charging, so the Norwegian residential dataset [3] was used instead for the histogram shapes.
- [3] is apartment-building garages in Norway (mostly 7.4 kW 32 A chargers) — shapes only; levels (rows 1, 3, 7) are calibrated to Swedish measurements.
- Rows 4 and 14 are assumptions; rows 1–3 are "best estimates" from two sources, not a fitted Swedish villa dataset. Energiforsk 2023:966 figures 6.1–6.3 contain Swedish home arrival/SOC histograms but only as images (no numbers).

## Sources
[1] Kobayashi, Taljegård, Johnsson (2025) Applied Energy 401:126608 — https://research.chalmers.se/publication/547964
[2] Energiforsk 2023:966 (Chalmers/Profu, 220 logged EVs 2023) — https://energiforsk.se/media/33922/2023-966-paverkan-pa-elsystemet-och-elnatet-av-en-storskalig-elektrifiering-av-fordonsflottan.pdf
[3] Sørensen et al. (2024) Data in Brief, 35 377 residential sessions, 12 Norwegian sites 2018–2021 — https://doi.org/10.5281/zenodo.12730566 ; article https://pmc.ncbi.nlm.nih.gov/articles/PMC11404051/
[4] Trafikanalys, Körsträckor 2025 (2026-04-17) — https://www.trafa.se/globalassets/statistik/vagtrafik/korstrackor/2025/korstrackor-2025---2026-04-17.pdf
[5] Trafikanalys press release 2026-02-12 — https://www.mynewsdesk.com/se/trafikanalys/pressreleases/laddbara-personbilar-oekar-en-av-sex-gaar-att-ladda-3431768
[6] Elsäkerhetsverket, Planera din laddningspunkt — https://www.elsakerhetsverket.se/privatpersoner/din-elanlaggning/bygga-och-renovera/installation-av-elbilsladdare/planera-din-laddningspunkt/
[7] Trafikanalys, Fordonsåret 2025 (2026-01-12) — https://www.trafa.se/globalassets/webbinarier/fordon/fordonsaret-2025.pdf
[8] Vi Bilägare 2020-03-17 (Tekniikan Maailma winter test) — https://www.vibilagare.se/nyheter/sa-mycket-drar-elbilarna-pa-vintern-tesla-och-mercedes-i-botten
[9] Vi Bilägare 2026-01-29 (NAF winter test) — https://www.vibilagare.se/nyheter/rackviddstest-2026-sa-klarar-elbilarna-vinterkylan
[10] Vi Bilägare 2025-04-10 (Trafikanalys 2024 km by fuel) — https://www.vibilagare.se/nyheter/nya-siffror-elbilarnas-korstracka-okar-kraftigt
[11] Trafikanalys, Korttidsprognoser 2025–2028 (2025-05-21) — https://www.trafa.se/globalassets/webbinarier/fordon/prognoser-2025---webbinarium.pdf
[12] TV4/TT 2026-06-09 on Power Circle scenario — https://www.tv4.se/artikel/tt-260609-elbilar1-a0af9b31/ny-prognos-da-tar-elbilen-over-i-sverige
[13] Vi Bilägare 2019-01-29 (Power Circle 2030 forecast) — https://www.vibilagare.se/nyheter/da-tar-elbilarna-over
