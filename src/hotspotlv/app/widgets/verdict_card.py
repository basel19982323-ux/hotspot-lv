"""The verdict card: the first thing a planner reads.

    ┌──────────────────────────────────────────────────────────────┐
    │ ▌ AT RISK                                                     │
    │ ▌ house voltage outside ±10 % (EIFS 2023:3) …                 │
    │ ▌                                                             │
    │ ▌  ON PAPER                    REALLY                         │
    │ ▌  hours > 80 %     207        life used      0.56 d/yr       │
    │ ▌  hours > 100 %     22        max hot-spot   98 °C           │
    │ ▌  peak            136 %       max load       1.36 × rated    │
    │ ▌                              voltage bad h  127             │
    │ ▌ notes: …                                                    │
    └──────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from hotspotlv.app.theme import ACCENT, BORDER, MUTED, VERDICT_COLORS
from hotspotlv.verdict import Verdict

SOURCE_WORDING = {"cable catalogue (feeder.py)": "cable rating (Prysmian AXQJ in ground, 20 °C soil)"}


def _fmt(key: str, v) -> str:
    """Whole hours, °C and % to one decimal, days to three — no false precision."""
    if v is None:
        return "not checked"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        f = float(v)
        if math.isnan(f):
            return "–"
        if "hours" in key:
            return f"{f:,.0f}"
        if "days" in key:
            return f"{f:.3f}" if f < 1 else f"{f:.2f}"
        if "× rated" in key:
            return f"{f:.2f}"
        if "°C" in key or "%" in key:
            return f"{f:,.1f}" if f < 100 else f"{f:,.0f}"
        return f"{f:,.2f}"
    return str(v)


class VerdictCard(QFrame):
    def __init__(self, parent=None, empty_text: str = "Run a case to get a verdict."):
        super().__init__(parent)
        self._empty_text = empty_text
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(f"QFrame {{ border: 1px solid {BORDER}; border-radius: 8px; background: white; }}")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._bar = QFrame()
        self._bar.setFixedWidth(10)
        outer.addWidget(self._bar)
        body = QVBoxLayout()
        body.setContentsMargins(14, 10, 14, 10)
        outer.addLayout(body, 1)

        self._level = QLabel("—")
        self._level.setStyleSheet("font-size: 20pt; font-weight: 700; border: none;")
        self._headline = QLabel(empty_text)
        self._headline.setWordWrap(True)
        self._headline.setStyleSheet("font-size: 11pt; border: none;")
        self._reasons = QLabel("")
        self._reasons.setWordWrap(True)
        self._reasons.setTextFormat(Qt.RichText)
        self._reasons.setStyleSheet(f"color: {MUTED}; border: none;")
        body.addWidget(self._level)
        body.addWidget(self._headline)
        body.addWidget(self._reasons)

        cols = QHBoxLayout()
        self._paper = self._column("ON PAPER (nameplate rule)")
        self._really = self._column("REALLY (thermal model + limits)")
        cols.addWidget(self._paper[0], 1)
        cols.addWidget(self._really[0], 1)
        body.addLayout(cols)

        self._notes = QLabel("")
        self._notes.setWordWrap(True)
        self._notes.setTextFormat(Qt.RichText)
        self._notes.setStyleSheet(f"color: {MUTED}; font-size: 9.5pt; border: none;")
        body.addWidget(self._notes)
        self.set_verdict(None)

    @staticmethod
    def _column(title: str):
        box = QWidget()
        box.setStyleSheet("border: none;")
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 6, 0, 0)
        grid.setHorizontalSpacing(12)
        head = QLabel(title)
        head.setStyleSheet(f"color: {ACCENT}; font-weight: 600; font-size: 9pt; letter-spacing: 1px;")
        grid.addWidget(head, 0, 0, 1, 2)
        return box, grid

    @staticmethod
    def _fill(grid: QGridLayout, items: dict):
        while grid.count() > 1:
            w = grid.takeAt(1).widget()
            if w:
                w.deleteLater()
        for rr in range(grid.rowCount()):
            grid.setRowStretch(rr, 0)
        r = 0
        for r, (k, v) in enumerate(items.items(), start=1):
            key = QLabel(k)
            key.setStyleSheet(f"color: {MUTED};")
            val = QLabel(_fmt(k, v))
            val.setStyleSheet("font-weight: 600;" + (f" color: {MUTED};" if v is None else ""))
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(key, r, 0)
            grid.addWidget(val, r, 1)
        grid.addWidget(QWidget(), r + 1, 0)
        grid.setRowStretch(r + 1, 1)                      # both columns start at the top

    def set_verdict(self, v: Verdict | None, title: str | None = None):
        if v is None:
            self._bar.setStyleSheet(f"background: {BORDER}; border-top-left-radius: 8px; border-bottom-left-radius: 8px;")
            self._level.setText("—")
            self._level.setStyleSheet(f"font-size: 20pt; font-weight: 700; color: {MUTED}; border: none;")
            self._headline.setText(self._empty_text)
            self._reasons.setText("")
            self._notes.setText("")
            self._fill(self._paper[1], {})
            self._fill(self._really[1], {})
            return
        color = VERDICT_COLORS.get(v.level, MUTED)
        self._bar.setStyleSheet(f"background: {color}; border-top-left-radius: 8px; border-bottom-left-radius: 8px;")
        self._level.setText(v.level.upper() + (f"  ·  {title}" if title else ""))
        self._level.setStyleSheet(f"font-size: 20pt; font-weight: 700; color: {color}; border: none;")
        self._headline.setText(v.headline())
        reasons = []
        for r in v.reasons:
            text = str(r)
            for old, new in SOURCE_WORDING.items():
                text = text.replace(old, new)
            reasons.append(f"• {text}")
        self._reasons.setText("<br>".join(reasons) if reasons else "No rule is violated.")
        self._fill(self._paper[1], v.on_paper)
        self._fill(self._really[1], v.really)
        self._notes.setText(("<b>Notes</b><br>• " + "<br>• ".join(v.notes)) if v.notes else "")
