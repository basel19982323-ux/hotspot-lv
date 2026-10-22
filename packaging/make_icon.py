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


SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size: int) -> QImage:
    """One size, drawn for that size: below 48 px the lines are relatively thicker so the windings stay rings."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    small = size < 48
    p.setPen(Qt.NoPen)
    p.setBrush(ACCENT)
    margin = 0 if small else 8
    p.drawRoundedRect(QRectF(margin * s, margin * s, (256 - 2 * margin) * s, (256 - 2 * margin) * s), 44 * s, 44 * s)
    pen = QPen(QColor("white"), max(1.5, (24 if small else 18) * s))
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    left, right, r, hot = (86, 170, 50, 26) if small else (100, 156, 58, 22)   # small: rings further apart
    p.drawEllipse(QPointF(left * s, 128 * s), r * s, r * s)        # primary winding
    p.drawEllipse(QPointF(right * s, 128 * s), r * s, r * s)       # secondary winding
    p.setPen(Qt.NoPen)
    p.setBrush(HOT)
    p.drawEllipse(QPointF(128 * s, 128 * s), hot * s, hot * s)     # the hot spot where they overlap
    p.end()
    return img


def main() -> int:
    app = QGuiApplication(sys.argv)                                    # noqa: F841 (Qt needs it for painting fonts)
    png = ROOT / "src" / "hotspotlv" / "data" / "icon.png"
    ico = ROOT / "packaging" / "hotspot-lv.ico"
    if not draw(256).save(str(png)):
        raise SystemExit(f"could not write {png}")
    from PIL import Image                                              # a dev tool only: the app does not bundle PIL
    tmp = ROOT / "build" / "icon"
    tmp.mkdir(parents=True, exist_ok=True)
    images = []
    for size in SIZES:
        f = tmp / f"icon_{size}.png"
        draw(size).save(str(f))
        images.append(Image.open(f).convert("RGBA"))
    images[-1].save(ico, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(png, png.stat().st_size, "bytes;", ico, ico.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
