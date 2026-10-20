"""
Base load = "hushållsel": lighting, appliances, cooking, hot water — everything
EXCEPT space heating and EV charging. Heat pumps and EVs are added separately,
so the base must not already contain them.

Source: 63 measured Norwegian detached houses, hourly 2021 (Zenodo 8423312,
CC BY 4.0). Problem: 58 of them heat with direct electricity, so their winter
hours are dominated by heating. Only 5 houses have no electric heating.

How we strip the heating (the idea):

    winter day of a direct-electric house:   ████████████████  (heating + base)
    summer day of the same house:            ███               (base only)

    1. Take only SUMMER days (May–Sep) of each house → appliance "texture"
       with the house's own habits (when they cook, wash, etc.).
    2. Multiply by a SEASONAL factor taken from the 5 houses that have no
       electric heating (more lighting and hot water in winter: Jan ≈ 2 × Jul).
    3. Scale each house's year to a Swedish annual hushållsel target.

Result: realistic, diverse, heating-free profiles. Caveats stated in the README:
Norwegian habits, 2021 weekdays mapped onto the study year by weekday type.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
PROFILES_FILE = DATA_DIR / "base_load_norway_2021.csv.gz"
META_FILE = DATA_DIR / "base_load_norway_2021_meta.csv"

SUMMER_MONTHS = (5, 6, 7, 8, 9)          # months used for the appliance texture
# Monthly energy relative to July, mean of the 5 non-electric-heating houses
# (computed from the dataset; see data/base_load_norway_2021_README.md).
SEASONAL_FACTOR = {1: 2.03, 2: 1.89, 3: 1.69, 4: 1.42, 5: 1.26, 6: 1.00,
                   7: 1.00, 8: 1.08, 9: 1.33, 10: 1.39, 11: 1.50, 12: 1.74}
# Swedish single-family hushållsel (excl. heating), kWh/yr: typical 4 000–7 000.
ANNUAL_KWH_MEAN, ANNUAL_KWH_SD = 5500.0, 1000.0
ANNUAL_KWH_MIN, ANNUAL_KWH_MAX = 3500.0, 8000.0


def _load_source() -> pd.DataFrame:
    """The 63 measured houses, index = naive LOCAL time (wall clock), kWh/h."""
    df = pd.read_csv(PROFILES_FILE, index_col=0)
    # The file is stamped "…Z" but the values are local wall clock flattened to
    # 24 h/day (see dataset README); treat the stamps as local, drop the Z.
    df.index = pd.to_datetime(df.index.str.replace("Z", "", regex=False))
    return df


def _summer_days(house: pd.Series) -> dict[str, list[np.ndarray]]:
    """Split a house's summer hours into whole days, keyed 'weekday'/'weekend'."""
    s = house[house.index.month.isin(SUMMER_MONTHS)]
    days: dict[str, list[np.ndarray]] = {"weekday": [], "weekend": []}
    for date, day in s.groupby(s.index.date):
        if len(day) != 24:
            continue
        kind = "weekend" if pd.Timestamp(date).weekday() >= 5 else "weekday"
        days[kind].append(day.to_numpy() / SEASONAL_FACTOR[date.month])  # de-season
    return days


def base_load_profiles(
    index_utc: pd.DatetimeIndex,
    n_houses: int,
    seed: int = 0,
) -> pd.DataFrame:
    """Hourly base load (kW) for `n_houses` houses on the study-year UTC index.

    Each synthetic house borrows the summer-day texture of one measured house,
    re-seasoned with SEASONAL_FACTOR, and is scaled to a random annual target.
    Deterministic for a given seed.
    """
    rng = np.random.default_rng(seed)
    source = _load_source()
    source_ids = rng.choice(source.columns, size=n_houses, replace=n_houses > len(source.columns))

    local = index_utc.tz_convert("Europe/Stockholm")
    local_dates = pd.Series(local.date, index=index_utc)
    local_hours = np.asarray(local.hour)
    unique_dates = sorted(set(local_dates))

    out = np.zeros((len(index_utc), n_houses))
    for j, sid in enumerate(source_ids):
        days = _summer_days(source[sid])
        # pick one source day per study-year date, matching weekday/weekend
        picked = {}
        for d in unique_dates:
            kind = "weekend" if pd.Timestamp(d).weekday() >= 5 else "weekday"
            picked[d] = days[kind][rng.integers(len(days[kind]))] * SEASONAL_FACTOR[d.month]
        col = np.array([picked[d][h] for d, h in zip(local_dates, local_hours)])
        target = float(np.clip(rng.normal(ANNUAL_KWH_MEAN, ANNUAL_KWH_SD), ANNUAL_KWH_MIN, ANNUAL_KWH_MAX))
        out[:, j] = col * target / col.sum()

    return pd.DataFrame(out, index=index_utc, columns=[f"house{j + 1}" for j in range(n_houses)])


if __name__ == "__main__":
    idx = pd.date_range("2023-01-01", "2023-12-31 23:00", freq="h", tz="UTC")
    df = base_load_profiles(idx, 45, seed=1)
    total = df.sum(axis=1)
    print(f"{df.shape[1]} houses, annual kWh per house: mean {df.sum().mean():.0f}, "
          f"min {df.sum().min():.0f}, max {df.sum().max():.0f}")
    print(f"coincident peak {total.max():.1f} kW at {total.idxmax().tz_convert('Europe/Stockholm'):%d %b %H:%M} local "
          f"→ {total.max() / df.shape[1]:.2f} kW per house (ADMD)")
    loc = total.tz_convert("Europe/Stockholm")
    print("mean kW per house by local hour:", (loc.groupby(loc.index.hour).mean() / df.shape[1]).round(2).tolist())
