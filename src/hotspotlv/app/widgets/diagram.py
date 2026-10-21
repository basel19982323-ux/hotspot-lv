"""The live single-line diagram — the drawing a power engineer expects.

    MV 10 kV ──( )( )── busbar ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                 ▲         ├─■─■─■─■─■─■─■─■─■─■─■─■─■─■─■   feeder 1
            transformer    ├─■─■─■─■─■─■─■─■─■─■─■─■─■─■─■   feeder 2
         coloured by       └─■─■─■─■─■─■─■─■─■─■─■─■─■─■─■   feeder 3
         loading %            houses coloured by their net kW at the scrubbed hour
                              (blue = exporting PV); badges HP · EL · EV · PV

`set_hour(i, label)` recolours everything for row i of the year; the
timeline's scrubber drives it. Houses are laid on the feeders exactly as
feeder.build_feeder distributes them (first feeders get the remainder).
Pure drawing — all numbers come from the engine results.
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
BADGES_MAX_HOUSES = 60      # above this the badge letters would be unreadable; tooltips still carry them
LEGEND = ("house colour = net kW this hour (dark = near the fuse cap, blue = exporting PV) · "
          "transformer colour = loading % · badges: HP heat pump, EL direct electric heating, EV charger, PV solar")


def loading_color(pct: float) -> QColor:
    """grey → green → amber → red as the transformer loading rises."""
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


def houses_per_feeder(n_houses: int, n_feeders: int) -> list[int]:
    """Same split as feeder.build_feeder: the remainder goes to the first feeders."""
    return [n_houses // n_feeders + (1 if i < n_houses % n_feeders else 0) for i in range(n_feeders)]


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
        self._rated_kva = 0.0
        self._items = {}

    def set_case(self, houses: pd.DataFrame, p_house_kw: pd.DataFrame, loading_pct: pd.Series,
                 rated_kva: float, n_feeders: int = 3, cap_kw: float = 13.9):
        self._houses = houses
        self._p_house = p_house_kw.to_numpy(dtype=float)
        self._loading = loading_pct.to_numpy(dtype=float)
        self._cap_kw = cap_kw
        self._rated_kva = rated_kva
        self._draw(rated_kva, n_feeders)
        self.set_hour(int(np.argmax(self._loading)))
        self._fit()

    def clear_case(self):
        self.scene().clear()
        self._items = {}
        self._houses = None

    def _fit(self):
        if self.scene().items():
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _draw(self, rated_kva: float, n_feeders: int):
        sc = self.scene()
        sc.clear()
        self._items = {}
        pen = QPen(QColor(INK), 2)
        font = QFont("Segoe UI", 9)
        small = QFont("Segoe UI", 8)
        n = len(self._houses)
        show_badges = n <= BADGES_MAX_HOUSES
        # MV source
        sc.addEllipse(20, FEEDER_Y0 + FEEDER_DY - 18, 36, 36, pen)
        t = sc.addText("MV\n10 kV", font); t.setPos(14, FEEDER_Y0 + FEEDER_DY + 20)
        sc.addLine(56, FEEDER_Y0 + FEEDER_DY, 95, FEEDER_Y0 + FEEDER_DY, pen)
        # transformer: two overlapping circles, filled by loading
        self._items["trafo1"] = sc.addEllipse(95, FEEDER_Y0 + FEEDER_DY - 22, 44, 44, pen, QBrush(QColor("#dddddd")))
        self._items["trafo2"] = sc.addEllipse(121, FEEDER_Y0 + FEEDER_DY - 22, 44, 44, pen, QBrush(QColor("#dddddd")))
        self._items["trafo_label"] = sc.addText(f"{rated_kva:.0f} kVA", font)
        self._items["trafo_label"].setPos(92, FEEDER_Y0 + FEEDER_DY + 24)
        sc.addLine(165, FEEDER_Y0 + FEEDER_DY, 200, FEEDER_Y0 + FEEDER_DY, pen)
        # busbar
        y_top, y_bot = FEEDER_Y0 - 10, FEEDER_Y0 + (n_feeders - 1) * FEEDER_DY + 10
        sc.addLine(200, y_top, 200, y_bot, QPen(QColor(INK), 6))
        t = sc.addText("0.4 kV busbar", small); t.setPos(170, y_top - 22)
        # feeders and houses, in build_feeder's order
        j = 0
        self._items["houses"] = []
        for f, count in enumerate(houses_per_feeder(n, n_feeders)):
            y = FEEDER_Y0 + f * FEEDER_DY
            sc.addLine(200, y, X0 + count * GAP, y, pen)
            t = sc.addText(f"F{f + 1}", small); t.setPos(206, y - 20)
            for k in range(count):
                x = X0 + k * GAP
                sc.addLine(x, y, x, y + 14, QPen(QColor(INK), 1))
                rect = sc.addRect(QRectF(x - HOUSE / 2, y + 14, HOUSE, HOUSE), QPen(QColor(BORDER), 1), QBrush(QColor("#e6e6e6")))
                rect.setToolTip(self._house_tip(j))
                if show_badges:
                    b = sc.addText("·".join(self._badges(j)), QFont("Segoe UI", 6))
                    b.setDefaultTextColor(QColor(MUTED))
                    b.setPos(x - 14, y + 30)
                self._items["houses"].append(rect)
                j += 1
        self._items["hour_label"] = sc.addText("", font)
        self._items["hour_label"].setDefaultTextColor(QColor(ACCENT))
        self._items["hour_label"].setPos(20, 20)
        if not show_badges:
            note = sc.addText("badges hidden above 60 houses — hover a house", small)
            note.setDefaultTextColor(QColor(MUTED))
            note.setPos(X0, y_bot + 36)
        sc.setSceneRect(sc.itemsBoundingRect().adjusted(-10, -10, 30, 30))

    def _badges(self, j: int) -> list[str]:
        row = self._houses.iloc[j]
        out = []
        heating = row.get("heating", "none")
        if heating == "direct_electric":
            out.append("EL")
        elif heating != "none":
            out.append("HP")
        if row.get("has_ev", False):
            out.append("EV")
        if row.get("has_pv", False):
            out.append("PV")
        return out

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
        self._items["trafo_label"].setPlainText(f"{lp:.0f} %\n{self._rated_kva:.0f} kVA")
        for j, rect in enumerate(self._items["houses"]):
            kw = float(self._p_house[i, j])
            rect.setBrush(QBrush(house_color(kw, self._cap_kw)))
            rect.setToolTip(self._house_tip(j) + f"\n{kw:+.1f} kW this hour")
        self._items["hour_label"].setPlainText(label or f"hour {i} of the period")
