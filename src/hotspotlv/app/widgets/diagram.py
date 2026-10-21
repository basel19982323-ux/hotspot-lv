"""The live single-line diagram — the drawing a power engineer expects.

    MV 10 kV ──( )( )── busbar ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                 ▲         ├─■─■─■─■─■─■─■─■─■─■─■─■─■─■─■   feeder 1
            transformer    ├─■─■─■─■─■─■─■─■─■─■─■─■─■─■─■   feeder 2
         coloured by       └─■─■─■─■─■─■─■─■─■─■─■─■─■─■─■   feeder 3
         loading %            houses coloured by their net kW at the scrubbed hour
                              (blue = exporting PV), badges: HP · EV · PV

`set_hour(i)` recolours everything for row i of the year; the timeline's
scrubber drives it. Pure drawing — all numbers come from the engine results.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from hotspotlv.app.theme import ACCENT, BORDER, EXPORT_COLOR, INK, MUTED, VERDICT_COLORS

HOUSE = 16          # px
GAP = 26
FEEDER_Y0 = 150
FEEDER_DY = 70
X0 = 240


def loading_color(pct: float) -> QColor:
    """grey → amber → red as the transformer loading rises."""
    if pct < 60:
        return QColor("#9aa5b1")
    if pct < 80:
        return QColor("#7fb069")
    if pct < 100:
        return QColor(VERDICT_COLORS["Watch"])
    return QColor(VERDICT_COLORS["At risk"])


def house_color(kw: float, cap_kw: float) -> QColor:
    if kw < -0.1:
        return QColor(EXPORT_COLOR)
    f = min(max(kw, 0.0) / max(cap_kw, 1e-9), 1.0)
    r = int(230 - 30 * f); g = int(230 - 150 * f); b = int(230 - 190 * f)
    return QColor(r, g, b)


class FeederDiagram(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(Qt.white))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._houses: pd.DataFrame | None = None
        self._p_house: np.ndarray | None = None      # hours × houses net kW
        self._loading: np.ndarray | None = None
        self._cap_kw = 13.9
        self._items = {}

    def set_case(self, houses: pd.DataFrame, p_house_kw: pd.DataFrame, loading_pct: pd.Series,
                 rated_kva: float, n_feeders: int = 3, cap_kw: float = 13.9):
        self._houses = houses
        self._p_house = p_house_kw.to_numpy(dtype=float)
        self._loading = loading_pct.to_numpy(dtype=float)
        self._cap_kw = cap_kw
        self._draw(rated_kva, n_feeders)
        self.set_hour(int(np.argmax(self._loading)))
        self._fit()

    def _fit(self):
        if self.scene().items():
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def clear_case(self):
        self.scene().clear()
        self._items = {}
        self._houses = None

    def _draw(self, rated_kva: float, n_feeders: int):
        sc = self.scene()
        sc.clear()
        self._items = {}
        pen = QPen(QColor(INK), 2)
        font = QFont("Segoe UI", 9)
        small = QFont("Segoe UI", 7)
        # MV source
        sc.addEllipse(20, FEEDER_Y0 + FEEDER_DY - 18, 36, 36, pen)
        t = sc.addText("MV\n10 kV", font); t.setPos(14, FEEDER_Y0 + FEEDER_DY + 20)
        sc.addLine(56, FEEDER_Y0 + FEEDER_DY, 95, FEEDER_Y0 + FEEDER_DY, pen)
        # transformer: two overlapping circles, filled by loading
        self._items["trafo1"] = sc.addEllipse(95, FEEDER_Y0 + FEEDER_DY - 22, 44, 44, pen, QBrush(QColor("#dddddd")))
        self._items["trafo2"] = sc.addEllipse(121, FEEDER_Y0 + FEEDER_DY - 22, 44, 44, pen, QBrush(QColor("#dddddd")))
        self._items["trafo_label"] = sc.addText(f"{rated_kva:.0f} kVA", font)
        self._items["trafo_label"].setPos(96, FEEDER_Y0 + FEEDER_DY + 24)
        sc.addLine(165, FEEDER_Y0 + FEEDER_DY, 200, FEEDER_Y0 + FEEDER_DY, pen)
        # busbar
        y_top, y_bot = FEEDER_Y0 - 10, FEEDER_Y0 + (n_feeders - 1) * FEEDER_DY + 10
        sc.addLine(200, y_top, 200, y_bot, QPen(QColor(INK), 6))
        t = sc.addText("0.4 kV busbar", small); t.setPos(170, y_top - 22)
        # feeders and houses
        n = len(self._houses)
        per = [n // n_feeders + (1 if i < n % n_feeders else 0) for i in range(n_feeders)]
        j = 0
        self._items["houses"] = []
        for f, count in enumerate(per):
            y = FEEDER_Y0 + f * FEEDER_DY
            sc.addLine(200, y, X0 + count * GAP, y, pen)
            t = sc.addText(f"F{f + 1}", small); t.setPos(206, y - 20)
            for k in range(count):
                x = X0 + k * GAP
                sc.addLine(x, y, x, y + 14, QPen(QColor(INK), 1))
                rect = sc.addRect(QRectF(x - HOUSE / 2, y + 14, HOUSE, HOUSE), QPen(QColor(BORDER), 1), QBrush(QColor("#e6e6e6")))
                rect.setToolTip(self._house_tip(j))
                badges = []
                row = self._houses.iloc[j]
                if row.get("heating", "none") != "none":
                    badges.append("HP" if row["heating"] != "direct_electric" else "EL")
                if row.get("has_ev", False):
                    badges.append("EV")
                if row.get("has_pv", False):
                    badges.append("PV")
                b = sc.addText("·".join(badges), QFont("Segoe UI", 6))
                b.setDefaultTextColor(QColor(MUTED))
                b.setPos(x - 14, y + 30)
                self._items["houses"].append(rect)
                j += 1
        self._items["hour_label"] = sc.addText("", font)
        self._items["hour_label"].setDefaultTextColor(QColor(ACCENT))
        self._items["hour_label"].setPos(20, 20)
        self._items["legend"] = sc.addText("house colour = net kW this hour (blue = exporting PV) · transformer colour = loading %", small)
        self._items["legend"].setDefaultTextColor(QColor(MUTED))
        self._items["legend"].setPos(20, y_bot + 40)
        sc.setSceneRect(sc.itemsBoundingRect().adjusted(-10, -10, 30, 30))

    def _house_tip(self, j: int) -> str:
        r = self._houses.iloc[j]
        return (f"house {j + 1}: heating {r.get('heating', '?')}, EV {'yes' if r.get('has_ev') else 'no'}"
                f"{' (' + str(r.get('charger_kw')) + ' kW)' if r.get('has_ev') else ''}, "
                f"PV {str(r.get('pv_kwp')) + ' kWp' if r.get('has_pv') else 'no'}")

    def set_hour(self, i: int, label: str = ""):
        if self._houses is None or not self._items:
            return
        i = int(np.clip(i, 0, len(self._loading) - 1))
        lp = float(self._loading[i])
        c = loading_color(lp)
        for key in ("trafo1", "trafo2"):
            self._items[key].setBrush(QBrush(c))
        self._items["trafo_label"].setPlainText(f"{lp:.0f} %")
        for j, rect in enumerate(self._items["houses"]):
            kw = float(self._p_house[i, j])
            rect.setBrush(QBrush(house_color(kw, self._cap_kw)))
            rect.setToolTip(self._house_tip(j) + f"\n{kw:+.1f} kW this hour")
        self._items["hour_label"].setPlainText(label or f"hour {i} of the period")
