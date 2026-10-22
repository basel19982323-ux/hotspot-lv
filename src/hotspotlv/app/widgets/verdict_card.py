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

import html
import math

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QTextBrowser, QVBoxLayout, QWidget

from hotspotlv.app.theme import ACCENT, BORDER, MUTED, VERDICT_COLORS
from hotspotlv.verdict import Verdict

def fmt_card_value(key: str, v) -> str:
    """Whole hours, °C and % to one decimal, days to three — no false precision.
    One formatter for the screen card and the PDF card, so the two can never disagree."""
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
            return f"{f:.3f}" if f < 1 else (f"{f:.2f}" if f < 100 else f"{f:,.0f}")
        if "× rated" in key:
            return f"{f:.2f}"
        if "°C" in key or "%" in key:
            return f"{f:,.1f}"
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

        # the two columns sit side by side when there is room, else one under the other —
        # the readouts are never squeezed (regression of 21 Oct)
        self._cols = QGridLayout()
        self._paper = self._column("ON PAPER (nameplate rule)")
        self._really = self._column("REALLY (thermal model + limits)")
        self._stacked = False
        self._place(stacked=False)
        body.addLayout(self._cols)

        # notes can be long: a small scrollable box keeps the card's height bounded
        self._notes = QTextBrowser()
        self._notes.setFrameShape(QFrame.NoFrame)
        self._notes.setMaximumHeight(84)
        self._notes.setStyleSheet(f"color: {MUTED}; font-size: 9.5pt; border: none; background: transparent;")
        self._notes.setVisible(False)
        body.addWidget(self._notes)
        self.set_verdict(None)

    def _place(self, stacked: bool):
        for w in (self._paper[0], self._really[0]):
            self._cols.removeWidget(w)
        if stacked:                                  # the column that decides comes first (on a laptop the
            self._cols.addWidget(self._really[0], 0, 0); self._cols.addWidget(self._paper[0], 1, 0)   # second is below the fold)
            self._cols.setColumnStretch(1, 0)
        else:
            self._cols.addWidget(self._paper[0], 0, 0); self._cols.addWidget(self._really[0], 0, 1)
            self._cols.setColumnStretch(0, 1); self._cols.setColumnStretch(1, 1)
        self._stacked = stacked

    def _two_column_width(self) -> int:
        return (self._paper[0].minimumSizeHint().width() + self._really[0].minimumSizeHint().width()
                + self._cols.horizontalSpacing() + 10 + 28 + 8)         # bar + body margins + frame + layout spacing

    def minimumSizeHint(self) -> QSize:                      # one column wide is enough: the columns stack
        s = super().minimumSizeHint()
        one = max(self._paper[0].minimumSizeHint().width(), self._really[0].minimumSizeHint().width()) + 10 + 28 + 8
        return QSize(min(s.width(), one), s.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        want = self.width() < self._two_column_width()
        if want != self._stacked:
            self._place(stacked=want)

    def scrolled(self) -> QScrollArea:
        """The card inside a scroll area: a short window scrolls it instead of squeezing it."""
        area = QScrollArea(); area.setWidget(self); area.setWidgetResizable(True); area.setFrameShape(QFrame.NoFrame)
        area.setMinimumHeight(120)
        return area

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
            val = QLabel(fmt_card_value(k, v))
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
            self._notes.setVisible(False)
            self._fill(self._paper[1], {})
            self._fill(self._really[1], {})
            return
        color = VERDICT_COLORS.get(v.level, MUTED)
        self._bar.setStyleSheet(f"background: {color}; border-top-left-radius: 8px; border-bottom-left-radius: 8px;")
        self._level.setText(v.level.upper() + (f"  ·  {title}" if title else ""))
        self._level.setStyleSheet(f"font-size: 20pt; font-weight: 700; color: {color}; border: none;")
        self._headline.setText(v.headline())
        reasons = [f"• {html.escape(str(r))}" for r in v.reasons]     # rich text: a "<" in a note must not vanish
        self._reasons.setText("<br>".join(reasons) if reasons else "No rule is violated.")
        self._fill(self._paper[1], v.on_paper)
        self._fill(self._really[1], v.really)
        self._notes.setVisible(bool(v.notes))
        self._notes.setHtml(("<b>Notes</b><br>• " + "<br>• ".join(html.escape(n) for n in v.notes)) if v.notes else "")
