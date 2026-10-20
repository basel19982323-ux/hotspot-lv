"""
Heating electricity for one house from the outdoor temperature.

The picture — a house is a leaky bucket of heat:

    outdoor  -20 °C  ──────────►  heat leaks out  ∝  UA · (17 − T_out)
                                         │
                                  heat pump refills it
                                  electric = heat / COP(T_out)
                                         │
                       when it's too cold for the pump, the backup
                       immersion heater (COP = 1) takes the rest

Three ingredients, each an explicit assumption:
  * UA  — how leaky the house is, kW per K. 0.20 kW/K ≈ 150 m² Swedish villa
          from the 1960s–80s (≈ 140 kWh/m²·yr heat; post-1980 stock is tighter):
          6.8 kW at −17 °C, ~18 MWh/yr space heat in the 2023 Eskilstuna year.
          Boverket's design outdoor temperature (DVUT, 1991–2020) for Västerås
          is about −17 °C for a 1-day time constant.
  * COP — heat delivered per kWh electricity, falls as it gets colder. The
          table below is an EFFECTIVE field COP for an air/water pump on
          35–45 °C radiators, including defrost and part load; it gives a
          seasonal performance factor ≈ 2.8 incl. hot water, between the label
          SCOPs of a modern unit at W35 (≈ 3.9) and W55 (≈ 3.3). Laboratory
          EN 14511 points are higher (A7/W35 ≈ 4.6).
  * Capacity — an air/water pump gives less heat when cold; above its
          capacity the backup electric heater covers the rest. That backup is
          why Swedish winter peaks bite. Default fade 0.08 kW per K below +7 °C
          is between a modern inverter unit (NIBE F2120-8: 8.0 kW at +7 → 6.8 kW
          at −22, ≈ 0.04 kW/K) and older on/off units (≈ 0.15 kW/K).

Hot water (DHW) is a flat 0.35 kW thermal (≈ 3 MWh/yr, Sveby 20 kWh/m²·yr for
150 m²) made at ~50 °C, so at a lower COP (2.5 air/water, 3.0 ground source).
No thermal mass / no night set-back: the hourly demand follows the hourly
temperature (stated limit; a real house lags by an hour or two).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

BALANCE_TEMP_C = 17.0          # above this, internal gains + sun heat the house
DESIGN_TEMP_C = -17.0          # Boverket DVUT, Västerås, 1-day (approx.)
DHW_KW_THERMAL = 0.35          # domestic hot water, flat
DHW_COP = {"air_water": 2.5, "ground_source": 3.0, "direct_electric": 1.0}

# Effective COP of an air/water heat pump vs outdoor temperature (thermal out / electric in)
COP_TABLE_T = np.array([-25, -15, -7, 2, 7, 15])
COP_TABLE = np.array([1.5, 1.9, 2.3, 2.9, 3.5, 4.5])
GROUND_SOURCE_COP = 3.8

HEATING_KINDS = ("air_water", "ground_source", "direct_electric")


@dataclass(frozen=True)
class HeatPumpParams:
    ua_kw_per_k: float = 0.20      # house heat-loss coefficient
    q_nom_kw: float = 8.0          # heat output at +7 °C
    capacity_slope: float = 0.08   # kW thermal lost per K below +7 °C (0.04 modern inverter … 0.15 older on/off)
    q_min_kw: float = 2.0          # floor on pump heat output in extreme cold
    backup_kw_max: float = 9.0     # immersion heater size (3 × 3 kW steps)
    kind: str = "air_water"
    # "air_water"       → COP table + capacity fade + backup (most common new install)
    # "ground_source"   → flat COP 3.8, sized for the design load (bergvärme)
    # "direct_electric" → no pump: radiators/boiler, COP 1, every kWh of heat is 1 kWh electric

    def __post_init__(self):
        if self.kind not in HEATING_KINDS:
            raise ValueError(f"unknown heating kind {self.kind!r}; choose from {HEATING_KINDS}")
        if self.ua_kw_per_k <= 0 or self.q_nom_kw <= 0:
            raise ValueError("ua_kw_per_k and q_nom_kw must be positive")

    def with_(self, **changes) -> "HeatPumpParams":
        return replace(self, **changes)

    def design_load_kw(self) -> float:
        """Heat needed at the design outdoor temperature, incl. hot water."""
        return self.ua_kw_per_k * (BALANCE_TEMP_C - DESIGN_TEMP_C) + DHW_KW_THERMAL


def cop(temp_c: np.ndarray, params: HeatPumpParams) -> np.ndarray:
    if params.kind == "ground_source":
        return np.full_like(temp_c, GROUND_SOURCE_COP, dtype=float)
    if params.kind == "direct_electric":
        return np.ones_like(temp_c, dtype=float)
    return np.interp(temp_c, COP_TABLE_T, COP_TABLE)


def pump_capacity_kw(temp_c: np.ndarray, params: HeatPumpParams) -> np.ndarray:
    if params.kind == "ground_source":
        return np.full_like(temp_c, params.q_nom_kw, dtype=float)
    if params.kind == "direct_electric":
        return np.full_like(temp_c, np.inf, dtype=float)   # resistive heating has no capacity fade
    return np.maximum(params.q_min_kw, params.q_nom_kw + params.capacity_slope * (temp_c - 7.0))


def heat_pump_profile(temp_c: pd.Series, params: HeatPumpParams = HeatPumpParams()) -> pd.DataFrame:
    """Hourly electricity (kW) of one house's heating system. Columns: hp, backup, total."""
    t = temp_c.to_numpy(dtype=float)
    q_space = params.ua_kw_per_k * np.maximum(0.0, BALANCE_TEMP_C - t)
    capacity = pump_capacity_kw(t, params)
    # hot water first (it is made anyway), space heat takes what capacity is left
    q_dhw_pump = np.minimum(DHW_KW_THERMAL, capacity)
    q_space_pump = np.minimum(q_space, capacity - q_dhw_pump)
    q_backup = np.minimum(q_space + DHW_KW_THERMAL - q_dhw_pump - q_space_pump, params.backup_kw_max)
    q_backup = np.where(q_backup > 1e-9, q_backup, 0.0)      # kill floating-point dust
    p_hp = q_space_pump / cop(t, params) + q_dhw_pump / DHW_COP[params.kind]
    return pd.DataFrame({"hp": p_hp, "backup": q_backup, "total": p_hp + q_backup}, index=temp_c.index)


if __name__ == "__main__":
    from hotspotlv.weather import get_weather
    wx = get_weather(2023)
    for kind in HEATING_KINDS:
        p = HeatPumpParams(kind=kind)
        if kind == "ground_source":
            p = p.with_(q_nom_kw=p.design_load_kw())
        hp = heat_pump_profile(wx["temp_c"], p)
        print(f"{kind:16s} annual {hp['total'].sum():6.0f} kWh (backup {hp['backup'].sum():4.0f})  "
              f"peak {hp['total'].max():.2f} kW at {hp['total'].idxmax():%d %b %H:%M} UTC  "
              f"backup hours {(hp['backup'] > 0).sum()}")
    for T in (10, 0, -10, -20):
        row = heat_pump_profile(pd.Series([float(T)]))
        print(f"  air/water T={T:>4} °C → {row['total'].iloc[0]:.2f} kW electric (backup {row['backup'].iloc[0]:.2f})")
