"""Transformer catalogue — plain data, no heavy imports, shared by feeder and thermal.

Losses: Tier-2 maximum losses of Commission Regulation (EU) No 548/2014 as
amended by (EU) 2019/1783, Annex I Table I.1 (oil-immersed, ≤ 24 kV); the
500 and 800 kVA rows were verified against the regulation text in the review
of 20 Oct 2026. Other sizes are added only after the same verification.
uk: Swedish practice (Chalmers open LV-grid generator): 4 % up to 315 kVA,
6 % from 500 kVA.
"""

TRANSFORMERS = {
    #  kVA : dict(load loss W, no-load loss W, uk %)
    # 315 kVA (added 23 Oct 2026 for the rural street type of the study): Tier-2 Ak 2 800 W / A0 −10 % 324 W, read
    # from SGB-SMIT's reproduction of Table I.1 (brochure 21-04-20, the same table whose 500 and 800 kVA rows match
    # the verified values above); the EUR-Lex text was not reachable that day — the study review checks it.
    315: dict(p_load_w=2800, p_noload_w=324, uk_percent=4.0),
    500: dict(p_load_w=3900, p_noload_w=459, uk_percent=6.0),
    800: dict(p_load_w=6000, p_noload_w=585, uk_percent=6.0),
}


def transformer(kva: int) -> dict:
    if kva not in TRANSFORMERS:
        raise ValueError(f"no catalogue data for {kva} kVA (have {sorted(TRANSFORMERS)})")
    return TRANSFORMERS[kva]
