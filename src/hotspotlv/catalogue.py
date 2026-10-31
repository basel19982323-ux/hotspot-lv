"""Transformer catalogue — plain data, no heavy imports, shared by feeder and thermal.

Losses: Tier-2 maximum losses of Commission Regulation (EU) No 548/2014 as
amended by (EU) 2019/1783, Annex I Table I.1 (liquid-immersed medium power
transformers, ≤ 24 kV); every row is verified against the regulation text
(500 and 800 kVA in the review of 20 Oct 2026, 315 kVA in the study review of
23 Oct 2026, the v0.2 sizes on 31 Oct 2026 against the legislation.gov.uk copy
of Annex I as amended). 200 kVA is not a row of Table I.1: the regulation says
"Maximum losses for kVA ratings that fall in between the ratings given in
Table I.1 shall be obtained by linear interpolation", so it is interpolated
between 160 and 250 kVA.
uk: the Chalmers open LV-grid generator's values for the three v0.1 sizes, 4 %
up to 315 kVA and 6 % from 500 kVA (kept unchanged in v0.2, so every study
result stays the same). A Swedish DSO's own specification (Ellevio,
oil-immersed distribution transformers, rev 3.0, 2022, §11, at 75 °C) uses
3.5 % for 50, 100 and 200 kVA, 4 % for 315 and 500 kVA, 4.5 % for 800 kVA and
6 % for 1000 kVA; 6 % at 500/800 kVA gives a larger voltage drop: conservative
for undervoltage, optimistic for overvoltage in PV hours.
The v0.2 sizes keep one rule with the v0.1 rows, so that a bigger unit never
gives a larger transformer voltage drop at the same load (the v0.2 review found
that 400 → 500 and 630 → 800 kVA did, with 4 % at 400 and 630 kVA; a test now
checks the order):
  * up to 315 kVA, Ellevio's bands: 3.5 % for 50, 100 and 200 kVA as listed;
    160 kVA 3.5 % (inside the 50–200 kVA band, assumed); 250 kVA 4 % (between
    Ellevio's 3.5 % at 200 and 4 % at 315 kVA, assumed: the higher one, which is
    conservative for undervoltage);
  * from 500 kVA, the v0.1 rule of 6 %: 630 kVA 6 % (assumed, the rule of the
    sizes around it); 1000 kVA 6 % (Ellevio lists 6 % as well);
  * 400 kVA 5 % (assumed: between 4 % at 315 and 6 % at 500 kVA).
"""

TRANSFORMERS = {
    #  kVA : dict(load loss W, no-load loss W, uk %)
    # 315 kVA (added 23 Oct 2026 for the study's village road): Tier-2 Ak 2 800 W / A0 −10 % 324 W, checked in the
    # study review against the official text of Table I.1 (Publications Office XHTML; legislation.gov.uk copy as
    # amended) and the same values in Ellevio's specification.
    315: dict(p_load_w=2800, p_noload_w=324, uk_percent=4.0),
    500: dict(p_load_w=3900, p_noload_w=459, uk_percent=6.0),
    800: dict(p_load_w=6000, p_noload_w=585, uk_percent=6.0),
    # v0.2 (31 Oct 2026): the other common distribution sizes, Tier-2 Ak / A0 −10 % from Table I.1; uk as in the
    # module docstring (Ellevio §11 up to 315 kVA, the v0.1 rule of 6 % from 500 kVA, 400 kVA between).
    50: dict(p_load_w=750, p_noload_w=81, uk_percent=3.5),
    100: dict(p_load_w=1250, p_noload_w=130, uk_percent=3.5),
    160: dict(p_load_w=1750, p_noload_w=189, uk_percent=3.5),      # uk: inside Ellevio's 50–200 kVA band
    200: dict(p_load_w=2016.7, p_noload_w=225, uk_percent=3.5),    # losses interpolated 160 → 250 kVA: 1750 + 600·40/90
                                                                   # = 2016.7 W, 189 + 81·40/90 = 225 W
    250: dict(p_load_w=2350, p_noload_w=270, uk_percent=4.0),      # uk: Ellevio's 315 kVA value (the higher neighbour)
    400: dict(p_load_w=3250, p_noload_w=387, uk_percent=5.0),      # uk: between 4 % (315) and 6 % (500 kVA)
    630: dict(p_load_w=4600, p_noload_w=540, uk_percent=6.0),      # uk: the v0.1 rule of 6 % from 500 kVA
    1000: dict(p_load_w=7600, p_noload_w=693, uk_percent=6.0),
}

_TIER2 = "Ecodesign Tier 2, Table I.1 (Ak, A0 − 10 %)"
_ELLEVIO = "Ellevio rev 3.0 §11"
SOURCES = {          # kVA: (where the losses come from, where uk comes from) — printed on the Assumptions page
    50: (_TIER2, _ELLEVIO),
    100: (_TIER2, _ELLEVIO),
    160: (_TIER2, "assumed: inside Ellevio's 50–200 kVA band of 3.5 % (Ellevio lists no 160 kVA)"),
    200: ("Ecodesign Tier 2, Table I.1, interpolated between 160 and 250 kVA as the regulation prescribes",
          _ELLEVIO),
    250: (_TIER2, "assumed: between Ellevio's 3.5 % (200 kVA) and 4 % (315 kVA), the higher one — conservative "
                  "for undervoltage (Ellevio lists no 250 kVA)"),
    315: (_TIER2, "Chalmers LV-grid generator (4 % up to 315 kVA); Ellevio also 4 %"),
    400: (_TIER2, "assumed: between 4 % at 315 and 6 % at 500 kVA, so the voltage drop falls with size"),
    500: (_TIER2, "Chalmers LV-grid generator (6 % from 500 kVA); Ellevio 4 %"),
    630: (_TIER2, "assumed: the v0.1 rule of 6 % from 500 kVA (Ellevio lists no 630 kVA)"),
    800: (_TIER2, "Chalmers LV-grid generator (6 % from 500 kVA); Ellevio 4.5 %"),
    1000: (_TIER2, "Ellevio rev 3.0 §11; also the v0.1 rule of 6 % from 500 kVA"),
}


def catalogue_markdown() -> str:
    """The catalogue as a Markdown table, every number with its source (the app's Assumptions page)."""
    rows = ["| kVA | load loss (W) | no-load loss (W) | uk (%) | losses from | uk from |",
            "|---:|---:|---:|---:|---|---|"]
    for kva in sorted(TRANSFORMERS):
        t = TRANSFORMERS[kva]
        rows.append(f"| {kva} | {t['p_load_w']:g} | {t['p_noload_w']:g} | {t['uk_percent']:g} | "
                    f"{SOURCES[kva][0]} | {SOURCES[kva][1]} |")
    return "\n".join(rows) + "\n"


# A unit the catalogue does not hold — or a known unit with its own test-report losses.
CUSTOM_KVA_RANGE = (25.0, 2500.0)       # 25 kVA: Table I.1's smallest row; 2 500 kVA: the top of EN 50464-1's
                                        # three-phase distribution range — the thermal constants and Table 4 limits
                                        # used are IEC 60076-7's distribution-transformer ones
CUSTOM_UK_PERCENT = 4.0                 # Ellevio's value for 315–500 kVA, used when no uk is given
P0_SHARE_RANGE = (0.0001, 0.01)         # no-load loss 0.01–1 % of the rating (Table I.1, 25–1 250 kVA: 0.07–0.25 %)
PK_SHARE_RANGE = (0.001, 0.05)          # load loss 0.1–5 % of the rating (Table I.1, 25–1 250 kVA: 0.76–2.4 %)


def transformer(kva: float) -> dict:
    if kva not in TRANSFORMERS:
        raise ValueError(f"no catalogue data for {kva:g} kVA (have {sorted(TRANSFORMERS)}); for another size give the "
                         "unit's no-load and load losses (a custom transformer)")
    return TRANSFORMERS[kva]


def custom_transformer(kva: float, p_noload_w: float, p_load_w: float, uk_percent: float | None = None) -> dict:
    """Losses entered by the user, checked for the mistakes that are easy to make: kW typed where watts are asked
    (a factor of 1 000), the two losses swapped, a size outside the distribution range. uk is optional: without it
    CUSTOM_UK_PERCENT is used, and the result says so ("uk_given": False). The losses are in watts: the error
    messages say so (in the report command: --p0 and --pk)."""
    try:
        kva, p0, pk = float(kva), float(p_noload_w), float(p_load_w)
        uk = None if uk_percent is None else float(uk_percent)
    except (TypeError, ValueError):
        raise ValueError("custom transformer: kVA, no-load loss and load loss must be numbers") from None
    lo, hi = CUSTOM_KVA_RANGE
    if not lo <= kva <= hi:
        raise ValueError(f"custom transformer: {kva:g} kVA is outside {lo:g}–{hi:g} kVA — the tool uses the IEC "
                         "60076-7 constants and limits of a distribution transformer, which do not fit outside it")
    s_w = kva * 1000
    for name, value, (a, b) in (("no-load loss", p0, P0_SHARE_RANGE), ("load loss", pk, PK_SHARE_RANGE)):
        if not a * s_w <= value <= b * s_w:
            raise ValueError(f"custom transformer: a {name} of {value:g} W is not {a:.2%}–{b:.0%} of {kva:g} kVA "
                             f"({a * s_w:g}–{b * s_w:g} W) — losses are in watts, from the test report")
    if pk <= p0:
        raise ValueError(f"custom transformer: the load loss ({pk:g} W) must be larger than the no-load loss "
                         f"({p0:g} W) — are they swapped?")
    if uk is not None and not 2.0 <= uk <= 10.0:
        raise ValueError(f"custom transformer: uk {uk:g} % is outside 2–10 %")
    return dict(p_load_w=pk, p_noload_w=p0, uk_percent=CUSTOM_UK_PERCENT if uk is None else uk, uk_given=uk is not None)
