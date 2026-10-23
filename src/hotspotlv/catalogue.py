"""Transformer catalogue — plain data, no heavy imports, shared by feeder and thermal.

Losses: Tier-2 maximum losses of Commission Regulation (EU) No 548/2014 as
amended by (EU) 2019/1783, Annex I Table I.1 (liquid-immersed medium power
transformers, ≤ 24 kV); every row is verified against the regulation text
(500 and 800 kVA in the review of 20 Oct 2026, 315 kVA in the study review of
23 Oct 2026). Other sizes are added only after the same verification.
uk: the Chalmers open LV-grid generator's values, 4 % up to 315 kVA and 6 %
from 500 kVA. A Swedish DSO's own specification (Ellevio, oil-immersed
distribution transformers, rev 3.0, 2022) uses 4 % up to 500 kVA and 4.5 % at
800 kVA, so 6 % gives a larger voltage drop: conservative for undervoltage,
optimistic for overvoltage in PV hours.
"""

TRANSFORMERS = {
    #  kVA : dict(load loss W, no-load loss W, uk %)
    # 315 kVA (added 23 Oct 2026 for the study's village road): Tier-2 Ak 2 800 W / A0 −10 % 324 W, checked in the
    # study review against the official text of Table I.1 (Publications Office XHTML; legislation.gov.uk copy as
    # amended) and the same values in Ellevio's specification.
    315: dict(p_load_w=2800, p_noload_w=324, uk_percent=4.0),
    500: dict(p_load_w=3900, p_noload_w=459, uk_percent=6.0),
    800: dict(p_load_w=6000, p_noload_w=585, uk_percent=6.0),
}


def transformer(kva: int) -> dict:
    if kva not in TRANSFORMERS:
        raise ValueError(f"no catalogue data for {kva} kVA (have {sorted(TRANSFORMERS)})")
    return TRANSFORMERS[kva]
