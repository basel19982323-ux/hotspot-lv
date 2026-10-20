# Overloaded on paper vs really ageing

**A thermal ageing & dynamic loading study of a Swedish secondary-substation
transformer under electrification — simulation only.**

> Work in progress. The question: a 500 kVA transformer feeds ~45 houses that
> are adding heat pumps, EV charging and rooftop PV. How many hours per year is
> it above its nameplate rating — and does the IEC 60076-7 thermal model say it
> actually ages faster, given a Swedish (Västerås) winter?

## Status

| Part | State |
|---|---|
| LV feeder model (pandapower) | in progress |
| Load models (base, heat pump, EV, PV) | planned |
| Full-year hourly power flow | planned |
| IEC 60076-7 thermal / ageing model | planned |
| Scenarios + fixes (800 kVA, smart charging, battery) | planned |
| Report, plots, tests | planned |

## Quick start

```bash
pip install -e ".[dev]"
pytest
```

## Licence

MIT — see `LICENSE`. Simulation study; all assumptions and limits are stated in
the report section. Not affiliated with any manufacturer or grid operator.
