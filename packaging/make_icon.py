"""Draw the app icon: the transformer symbol (two overlapping windings) on the app's blue, with a red hot spot.

    python packaging/make_icon.py        → src/hotspotlv/data/icon.png (window icon) + packaging/hotspot-lv.ico (exe)

Drawn by code so that it can be rebuilt and reviewed; no image from elsewhere.
"""

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen

ROOT = Path(__file__).resolve().parents[1]
ACCENT, HOT = QColor("#1f4e79"), QColor("#c0392b")


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    p.setPen(Qt.NoPen)
    p.setBrush(ACCENT)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 44 * s, 44 * s)
    pen = QPen(QColor("white"), 18 * s)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(100 * s, 128 * s), 58 * s, 58 * s)       # primary winding
    p.drawEllipse(QPointF(156 * s, 128 * s), 58 * s, 58 * s)       # secondary winding
    p.setPen(Qt.NoPen)
    p.setBrush(HOT)
    p.drawEllipse(QPointF(128 * s, 128 * s), 22 * s, 22 * s)       # the hot spot where they overlap
    p.end()
    return img


def main() -> int:
    app = QGuiApplication(sys.argv)                                    # noqa: F841 (Qt needs it for painting fonts)
    png = ROOT / "src" / "hotspotlv" / "data" / "icon.png"
    ico = ROOT / "packaging" / "hotspot-lv.ico"
    if not draw(256).save(str(png)):
        raise SystemExit(f"could not write {png}")
    if not draw(256).save(str(ico)):                                   # Qt's ICO writer stores the 256-px image
        raise SystemExit(f"could not write {ico}")
    print(png, png.stat().st_size, "bytes;", ico, ico.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
