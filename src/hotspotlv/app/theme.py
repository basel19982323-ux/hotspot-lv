"""Colours and stylesheet. One place, so the whole app reads as one system."""

VERDICT_COLORS = {"OK": "#2e8b57", "Watch": "#d19a00", "At risk": "#c0392b", "error": "#7f8c8d"}
ACCENT = "#1f4e79"          # deep blue: headers, buttons
INK = "#1e1e1e"
MUTED = "#6b6b6b"
PANEL = "#f6f7f9"
BORDER = "#d9dde3"
LOAD_COLOR = "#1f4e79"      # loading % lines
HOTSPOT_COLOR = "#c0392b"   # hot-spot °C
OIL_COLOR = "#e67e22"       # top-oil °C
AMBIENT_COLOR = "#16a085"   # outdoor °C (teal, distinct from the loading blue)
EXPORT_COLOR = "#2980b9"    # a house exporting (PV)
RULE_80 = "#999999"
RULE_100 = "#555555"

STYLESHEET = f"""
QWidget {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; color: {INK}; }}
QMainWindow, QDialog {{ background: white; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; top: -1px; background: white; }}
QTabBar::tab {{ padding: 8px 16px; border: 1px solid {BORDER}; border-bottom: none; background: {PANEL};
                margin-right: 2px; }}
QTabBar::tab:selected {{ background: white; font-weight: 600; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px;
             background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {ACCENT}; font-weight: 600; }}
QPushButton {{ background: {ACCENT}; color: white; border: none; border-radius: 4px; padding: 7px 14px;
               font-weight: 600; }}
QPushButton:hover {{ background: #27609a; }}
QPushButton:disabled {{ background: #a9b4c0; }}
QPushButton[secondary="true"] {{ background: white; color: {ACCENT}; border: 1px solid {ACCENT}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ padding: 4px 6px; border: 1px solid {BORDER}; border-radius: 4px;
                                                  background: white; }}
QProgressBar {{ border: 1px solid {BORDER}; border-radius: 4px; text-align: center; height: 16px; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
QTableView {{ background: white; gridline-color: {BORDER}; selection-background-color: #dbe7f3; selection-color: {INK};
              alternate-background-color: {PANEL}; }}
QAbstractScrollArea {{ background: white; }}
QScrollArea {{ background: white; border: none; }}
QScrollArea > QWidget > QWidget {{ background: white; }}
QGraphicsView {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; }}
QHeaderView::section {{ background: {PANEL}; padding: 4px 6px; border: none; border-bottom: 1px solid {BORDER};
                        font-weight: 600; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[title="true"] {{ font-size: 13pt; font-weight: 600; color: {ACCENT}; }}
"""
