"""A pandas DataFrame shown in a QTableView, with planner-friendly column names, sensible
number formats (hours and ratings as whole numbers) and the verdict column coloured."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from hotspotlv.app.theme import VERDICT_COLORS

FRIENDLY = {
    "station_id": "station", "kVA": "rated kVA", "verdict": "verdict", "why": "why", "period": "period",
    "step_min": "step (min)", "peak_loading_pct": "peak load (%)", "hours_above_80": "h > 80 %",
    "hours_above_100": "h > 100 %", "life_days_per_year": "life used (days/yr)", "max_hot_spot_c": "max hot-spot (°C)",
    "max_load_pu": "max load (× rated)", "energy_mwh": "energy (MWh/yr)", "gaps_filled": "gaps filled",
    "smhi_station": "SMHI station", "notes": "notes", "error": "error", "rank": "rank",
    "house": "house", "heating": "heating", "ua_kw_per_k": "heat loss (kW/K)", "q_nom_kw": "pump size (kW)",
    "has_ev": "EV", "charger_kw": "charger (kW)", "has_pv": "PV", "pv_kwp": "PV (kWp)", "base_kwh": "base (kWh/yr)",
    "heat_kwh": "heating (kWh/yr)", "ev_kwh": "EV (kWh/yr)", "pv_kwh": "PV (kWh/yr)", "fuse_limited_hours": "fuse-limited h",
    "voltage_bad_hours": "voltage outside ±10 % (h)", "voltage_plan_hours": "outside planning band (h)",
    "max_cable_pct": "max cable (%)", "exact_hours": "exact power-flow hours", "battery_hours_above_cap": "battery h above cap",
    "hours_hot_spot_above_98": "hot-spot > 98 °C (h)", "max_load_pu": "max load (× rated)",
}
WHOLE = ("hours", "h > ", "kVA", "gaps", "step", "kWh/yr", "fuse", "kSEK", "(yr)", "(h)", "bad h", "h above")
IDS = ("SMHI", "rank", "house", "seed")                  # whole numbers without a thousands separator
ONE_DECIMAL = ("%", "°C", "MWh", "kW", "kWp")


def _is_bool(v) -> bool:
    return isinstance(v, (bool, np.bool_))


def _looks_numeric(v) -> bool:
    """A pre-formatted number such as '1,032' or '0.054' (the Spread tab formats its summary per row)."""
    try:
        float(str(v).replace(",", "").replace("–", "-"))
        return True
    except ValueError:
        return False


def _is_number(v) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not _is_bool(v)


class DataFrameModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame | None = None, decimals: int = 2, header_tips: dict | None = None):
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()
        self._decimals = decimals
        self._header_tips = header_tips or {}

    def set_df(self, df: pd.DataFrame):
        self.beginResetModel()
        self._df = df.reset_index() if df.index.name else df.copy()
        self.endResetModel()

    @property
    def df(self) -> pd.DataFrame:
        return self._df

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._df.shape[1]

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole:
            return self._header_tips.get(str(self._df.columns[section]))
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            name = str(self._df.columns[section])
            return FRIENDLY.get(name, name)
        return str(section + 1)

    def _format(self, value, header: str) -> str:
        if _is_bool(value):
            return "yes" if bool(value) else "no"
        if _is_number(value):
            v = float(value)
            if math.isnan(v):
                return "–"
            if any(w in header for w in IDS):
                return f"{v:.0f}"
            if isinstance(value, (int, np.integer)) or any(w in header for w in WHOLE):
                return f"{v:,.0f}"
            if "days" in header:
                return f"{v:.3f}" if v < 1 else f"{v:.2f}"
            if "× rated" in header:
                return f"{v:.2f}"
            if any(w in header for w in ONE_DECIMAL):
                return f"{v:,.1f}"
            return f"{v:,.{self._decimals}f}" if abs(v) < 1e6 else f"{v:.3g}"
        return str(value)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self._df.iat[index.row(), index.column()]
        col = str(self._df.columns[index.column()])
        header = FRIENDLY.get(col, col)
        if role == Qt.DisplayRole:
            return self._format(value, header)
        if role == Qt.ForegroundRole and col == "verdict" and str(value) in VERDICT_COLORS:
            return QBrush(QColor(VERDICT_COLORS[str(value)]))
        if role == Qt.FontRole and col == "verdict":
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole and (_is_number(value) or _looks_numeric(value)):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and col in ("why", "notes", "error", "period"):
            return str(value)
        return None
