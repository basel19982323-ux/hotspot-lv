"""A pandas DataFrame shown in a QTableView, with the verdict column coloured."""

from __future__ import annotations

import math

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

from hotspotlv.app.theme import VERDICT_COLORS


class DataFrameModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame | None = None, decimals: int = 2):
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()
        self._decimals = decimals

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
        if role != Qt.DisplayRole:
            return None
        return str(self._df.columns[section]) if orientation == Qt.Horizontal else str(section + 1)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self._df.iat[index.row(), index.column()]
        col = str(self._df.columns[index.column()])
        if role == Qt.DisplayRole:
            if isinstance(value, float):
                if math.isnan(value):
                    return "–"
                return f"{value:,.{self._decimals}f}" if abs(value) < 1e6 else f"{value:.3g}"
            if isinstance(value, bool):
                return "yes" if value else "no"
            return str(value)
        if role == Qt.ForegroundRole and col == "verdict" and str(value) in VERDICT_COLORS:
            return QBrush(QColor(VERDICT_COLORS[str(value)]))
        if role == Qt.FontRole and col == "verdict":
            from PySide6.QtGui import QFont
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole and isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and col in ("why", "notes", "error"):
            return str(value)
        return None
