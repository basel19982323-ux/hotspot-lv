# Validation scripts

Independent cross-checks that are not part of the unit-test suite because they
need a second environment.

## `thermal_crosscheck_alliander.py`
Runs `hotspotlv.thermal.simulate` and the open-source
[alliander-opensource/transformer-thermal-model](https://github.com/alliander-opensource/transformer-thermal-model)
(MPL-2.0, Alliander, Dutch DSO) on identical inputs and identical IEC 60076-7
constants, and prints the differences. The library requires pandas 3 while
pandapower requires pandas < 3, so the library runs in its own virtual
environment:

```
python -m venv ttm_venv
ttm_venv\Scripts\pip install transformer-thermal-model
python validation\thermal_crosscheck_alliander.py   # see the file header for modes
```

Results recorded 21 Oct 2026 (after the ambient-lag fix, commit 79003ec):

| case | max abs. hot-spot difference | loss of life |
|---|---|---|
| 48 h step 0 to 1.0 pu at 20 C | < 0.1 K (explicit Euler vs exact exponential) | +0.2 % |
| 72 h random K 0.2-1.3, ambient -20 to 25 C | see research/reviews/2026-10-21-thermal-round2.md | |
| Vasteras 2023, 45 houses "today" | 36.7 vs 36.7 C max hot-spot | 0.0603 vs 0.0603 days |

The script also contains the IEC 60076-7:2005 Annex C worked example
(Tables C.1/C.2) with a straight transcription of equations C.6-C.14; that
example is additionally a permanent unit test (`tests/test_thermal.py`).
