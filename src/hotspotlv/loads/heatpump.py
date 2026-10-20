"""
Heat-pump electricity for one house from the outdoor temperature.

The picture — a house is a leaky bucket of heat:

    outdoor  -20 °C  ──────────►  heat leaks out  ∝  UA · (17 − T_out)
                                         │
                                  heat pump refills it
                                  electric = heat / COP(T_out)
                                         │
                       when it's too cold for the pump, the backup
                       immersion heater (COP = 1) takes the rest

Three ingredients, each an explicit assumption:
  * UA  — how leaky the house is, kW per K.  0.20 kW/K ≈ 150 m² Swedish villa
          from the 1970s–90s: 6.8 kW at the Västerås design temperature −17 °C,
          ~19 MWh/yr space heat in a normal year.
  * COP — heat delivered per kWh electricity, falls as it gets colder
          (air/water pump, 35–45 °C radiators; piecewise-linear table below,
          typical of manufacturer data at EN 14511 test points).
  * Capacity — an air/water pump gives less heat when cold; above its
          capacity the backup electric heater covers the rest. That backup is
          why Swedish winter peaks bite.

Hot water (DHW) is added as a flat 0.35 kW thermal (≈ 3 MWh/yr) through the
same pump. No thermal mass / no night set-back: the hourly demand follows the
hourly temperature. (Stated limit; a real house lags by an hour or two.)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BALANCE_TEMP_C = 17.0          # above this, internal gains + sun heat the house
DHW_KW_THERMAL = 0.35          # domestic hot water, flat

# COP of an air/water heat pump vs outdoor temperature (thermal out / electric in)
COP_TABLE_T = np.array([-25, -15, -7, 2, 7, 15])
COP_TABLE = np.array([1.5, 1.9, 2.3, 2.9, 3.5, 4.5])


@dataclass(frozen=True)
class HeatPumpParams:
    ua_kw_per_k: float = 0.20      # house heat-loss coefficient
    q_nom_kw: float = 8.0          # heat output at +7 °C
    capacity_slope: float = 0.15   # kW thermal lost per K below +7 °C
    q_min_kw: float = 2.0          # floor on pump heat output in extreme cold
    backup_kw_max: float = 9.0     # immersion heater size (3 × 3 kW steps)
    kind: str = "air_water"        # "ground_source" → flat COP 3.8, full capacity


def cop(temp_c: np.ndarray, params: HeatPumpParams) -> np.ndarray:
    if params.kind == "ground_source":
        return np.full_like(temp_c, 3.8, dtype=float)
    return np.interp(temp_c, COP_TABLE_T, COP_TABLE)


def pump_capacity_kw(temp_c: np.ndarray, params: HeatPumpParams) -> np.ndarray:
    if params.kind == "ground_source":
        return np.full_like(temp_c, params.q_nom_kw, dtype=float)
    return np.maximum(params.q_min_kw, params.q_nom_kw + params.capacity_slope * (temp_c - 7.0))


def heat_pump_profile(temp_c: pd.Series, params: HeatPumpParams = HeatPumpParams()) -> pd.DataFrame:
    """Hourly electricity (kW) of one heat-pump house. Columns: hp, backup, total."""
    t = temp_c.to_numpy(dtype=float)
    q_need = params.ua_kw_per_k * np.maximum(0.0, BALANCE_TEMP_C - t) + DHW_KW_THERMAL
    q_pump = np.minimum(q_need, pump_capacity_kw(t, params))
    q_backup = np.minimum(q_need - q_pump, params.backup_kw_max)
    p_hp = q_pump / cop(t, params)
    return pd.DataFrame({"hp": p_hp, "backup": q_backup, "total": p_hp + q_backup}, index=temp_c.index)


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    hp = heat_pump_profile(wx["temp_c"])
    print(f"annual electricity {hp['total'].sum():.0f} kWh (pump {hp['hp'].sum():.0f}, backup {hp['backup'].sum():.0f})")
    print(f"peak {hp['total'].max():.2f} kW at {hp['total'].idxmax():%d %b %H:%M} UTC, T = {wx['temp_c'][hp['total'].idxmax()]:.1f} °C")
    print(f"hours with backup heater on: {(hp['backup'] > 0).sum()}")
    for T in (10, 0, -10, -20):
        row = heat_pump_profile(pd.Series([float(T)]))
        print(f"  T={T:>4} °C → {row['total'].iloc[0]:.2f} kW electric (backup {row['backup'].iloc[0]:.2f})")
