# Study results

The CSV files here are the study's simulation results. They are produced by the scripts in `study/` (see
STUDY.md section 8) and are derived from:
- SMHI temperatures (CC BY 4.0, "Source: SMHI");
- PVGIS solar output (PVGIS © European Communities, 2001–2026);
- the Norwegian household data of Hofmann, Bjarghov & Nessa (2023), Zenodo 8423312 (CC BY 4.0).

The charts in `docs/figures/` are drawn from these results. Sources, licences and processing are described in
`study/data/README.md` and `src/hotspotlv/data/SOURCES.md`.

| File | Content |
|---|---|
| `runs.csv` | one row per street, weather year and house count on the grid (741 runs) |
| `runs_fine.csv` | the same, house by house in the step below each first limit (refine.py) |
| `meter_sums.csv`, `meter_sums_fine.csv` | the summed meter values that the 80 % rule looks at |
| `sensitivity.csv` | one constant at a time, at a reference station per street |
| `sensitivity_limits.csv`, `sensitivity_limits_fine.csv` | one constant at a time, for every house count |
| `first_limits.csv`, `summary_*.csv`, `winters.csv` | the tables behind STUDY.md, written by `figures.py` |

Simulation results, not measurements.
