# base_load_norway_2021.csv.gz — hourly household electricity load, 63 detached houses without heat pump, 2021

Measured smart-meter data from Norwegian detached houses, used in this study as the
"base load" of a villa (everything except the heat pump, EV charger and PV that the
simulation adds on top). Prepared by Basel Haijar.

## Files

| File | Content |
|---|---|
| `base_load_norway_2021.csv.gz` | 8760 rows × 63 columns. Index `time_utc` = start of the hour, UTC, ISO 8601 (`2021-01-01T00:00:00Z`). Columns `h001`…`h063` = one house each. Values = **kWh consumed in that hour** (equal to the mean kW over the hour), rounded to 3 decimals. No missing values. |
| `base_load_norway_2021_meta.csv` | One row per house: `id`, `annual_kwh`, `peak_kw` (highest single hour), `heating_type`, `wood_stove`, `hot_water`, `house_type`, `floor_area_m2` (survey bracket), `n_persons`, `city`, `gaps_filled` (hours interpolated). |

Size: 1.2 MB gzip-compressed (63 houses × 8760 h).

## Source and licence

Hofmann, M., Bjarghov, S., Nessa, S. (2023). *Norwegian hourly residential electricity
demand data with consumer characteristics during the European energy crisis* (v3)
[Data set]. Zenodo. https://doi.org/10.5281/zenodo.8423312 — licence **CC BY 4.0**.

Data article: Hofmann, M., Bjarghov, S., Nessa, S. (2023). Norwegian hourly residential
electricity demand data with consumer characteristics during the European energy crisis.
*Data in Brief*, 51, 109687. https://doi.org/10.1016/j.dib.2023.109687

Files used from the record: `demand.csv` (362.6 MB, hourly meter readings of 1,136
households, Oct 2020 – Mar 2022, columns `ID, Date, Hour, Demand_kWh`),
`answers.csv` (survey answers, 4,446 respondents), `households.csv` (city, price area,
whether meter data exist) and `survey.csv` (question/option dictionary, Norwegian +
English). Raw files are kept out of the repo (`data/raw/` is git-ignored).

The derived files here are redistributed under the same CC BY 4.0 terms; the
household IDs were re-anonymised (`h001`… do not map to the Zenodo IDs in any
published order).

## How the houses were selected

Survey filter (answers.csv), applied to the 1,136 households that have meter data:

| Step | Houses left |
|---|---|
| Households with hourly meter data | 1136 |
| Detached house (Q22 = "Detached house") | 256 |
| No electric car charged at home (Q29 = No) | 127 |
| No rental unit on the same meter (not Q25 = Yes & Q26 = No) | 111 |
| Full calendar year 2021 present, ≤ 1 % missing, no gap longer than 3 h | 111 |

Heating (Q27, multiple answers) is used to label, not to filter, because nearly all
Norwegian detached houses heat with electricity:

| `heating_type` | Meaning | Houses | Ids |
|---|---|---|---|
| `non_electric` | no electric heater, no heat pump, no electric waterborne heating (district heating, wood, oil, "other") | 5 | h001–h005 |
| `direct_electric` | electric panel heaters and/or electric floor heating, **no heat pump** | 58 | h006–h063 |
| `heat_pump` | (48 houses with a heat pump were selected but are NOT shipped — this file keeps only the 63 houses without a heat pump) | 48 | — |

`wood_stove` = 1 if a fireplace/wood stove is also used. `hot_water` is the tap-water
heater type (Q28); 93 of 111 have their own electric storage tank, which is the normal
Swedish villa case as well.

**Base-load set: h001–h063 (`heating_type != "heat_pump"`), 63 houses — the only houses shipped.**
The 48 heat-pump houses of the original selection are not shipped: their demand is already heat-pump
shaped, and the simulation adds its own heat pumps.

## What the load includes and excludes

Included: lighting, appliances, cooking, electric hot-water tank, ventilation, and —
in 106 of 111 houses — **space heating by electricity** (direct resistive heaters in
h006–h063, heat pump in h064–h111). The meter measures the whole house, so heating
cannot be separated.

Excluded by selection: EV charging (no EV at home), second dwellings on the meter.
Rooftop PV: not asked in the survey for the 2020–2022 period; Norwegian residential
PV was still rare then, so net-metered PV is assumed absent (not verifiable per house).

Consequence for the simulation: a `direct_electric` house already contains its
resistive heating. When the simulation adds a heat pump to such a house, the resistive
heating in the base load must be removed first (e.g. regress the temperature-dependent
part against outdoor temperature, or subtract a modelled heating demand), otherwise
heating is counted twice. The five `non_electric` houses (median 9.9 MWh/yr) show what
a Nordic villa looks like without any electric space heating.

## Time handling

The source gives local Norwegian wall-clock time (`Date` + `Hour` 1–24, every day
24 rows; the authors filled the missing spring DST hour by averaging and dropped the
duplicate autumn hour). This was converted to a continuous UTC index:
the synthetic 02:00 row on 28 March 2021 was dropped, the missing second 02:00 row on
31 October 2021 (= 01:00 UTC) was linearly interpolated. That is the single
`gaps_filled` hour per house; no other hours were missing in 2021.
Norway and Sweden share the same time zone (CET/CEST), so "local time" below applies
to Västerås unchanged.

## Summary statistics (2021)

| Statistic | All 111 | No heat pump (h001–h063) | `non_electric` (h001–h005) |
|---|---|---|---|
| Median annual consumption | 21 122 kWh | 20 236 kWh | 9 942 kWh |
| Mean annual consumption | 21 569 kWh | 20 903 kWh | 9 311 kWh |
| Min / max annual | 4 665 / 46 377 kWh | 4 665 / 46 377 kWh | 5 069 / 12 699 kWh |
| After-diversity maximum demand (coincident peak ÷ n) | 5.19 kW/house | 5.08 kW/house | 2.62 kW/house |
| Time of coincident peak (local) | 6 Jan 2021 18:00 | 10 Feb 2021 21:00 | 5 Jan 2021 17:00 |
| Mean individual annual peak | 8.62 kW | 8.32 kW | 4.51 kW |
| Diversity factor (sum of individual peaks ÷ coincident peak) | 1.66 | 1.64 | 1.72 |
| Hour of day when the average aggregate peaks (local) | 19:00 (2.77 kW/house) | 19:00 (2.66 kW/house) | 18:00 (1.32 kW/house) |
| Night minimum (local) | 03:00 (2.08 kW/house) | 03:00 (2.02 kW/house) | 06:00 (0.82 kW/house) |

Average monthly energy per house, all 111 (kWh): Jan 3138, Feb 2723, Mar 2329,
Apr 1910, May 1521, Jun 1008, Jul 890, Aug 967, Sep 1098, Oct 1547, Nov 1930,
Dec 2508 — the 3.5× winter/summer ratio is the electric heating.

## Caveats

- **Norwegian, not Swedish.** Both are Nordic with a similar appliance stock, climate
  and villa size, but Norway heats far more with direct electricity; Swedish villas use
  more heat pumps, district heating and pellets. Treat the `direct_electric` profiles as
  an upper bound for a Swedish villa's base load and remove the heating part before
  adding a heat pump model.
- Oslo/Bergen area houses (Bergen = milder, wetter coast; Oslo ≈ Västerås climate).
- 2021 started with a cold January/February and ended with the start of the price
  crisis; some households reported reducing consumption in winter 2021/22 (see the
  survey), so December may be slightly depressed. 2021 weekdays do not line up
  with 2023 weekdays (1 Jan 2021 = Friday, 1 Jan 2023 = Sunday) if combined with 2023
  weather data; align by weekday if the daily shape matters.
- Only 5 houses are free of electric space heating — too few for statistics on their own.
- The survey is self-reported; heating type and EV ownership refer to the time of the
  survey (spring 2022), not necessarily to all of 2021.
- Sample: households that answered a survey and consented to data sharing; the
  original authors removed meters with > 1 % zero readings, peaks > 20 kWh/h or
  averages outside 0.1–6 kWh/h.
- This is input data for a simulation study; it is not a measurement of any Swedish grid.

## Note added 20 Oct 2026 (review)
The shipped file contains only the 63 houses **without** a heat pump (h001–h063);
the "All 111" statistics above describe the full selection before that cut.
The seasonal factor used in `loads/base.py` (`SEASONAL_FACTOR`, monthly energy
relative to July, mean of h001–h005) was recomputed from this file and is:
Jan 2.03, Feb 1.89, Mar 1.69, Apr 1.42, May 1.26, Jun 1.00, Jul 1.00, Aug 1.08,
Sep 1.33, Oct 1.39, Nov 1.50, Dec 1.74. Only June–August days are used as
appliance texture (May and September still contain electric space heating in
the direct-electric houses).
