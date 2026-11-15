"""Offline official product renders and their verified control geometry.

Source rectangles select devices from the original product sheets at paint time;
the bundled PNG files remain unchanged Logitech CDN renders. Coordinates below
use DeviceArt's 260×240 design space before its normal widget transform.
"""

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QTransform


_ASSETS = Path(__file__).with_name("assets") / "devices"
# filename, original sheet size, source rectangle, destination rectangle
_RENDERS = {
    "keypad": ("mx-creative-console.png", (2500, 2160),
               (1380, 585, 855, 1010), (52.5, 20, 155, 183)),
    "dialpad": ("mx-creative-console.png", (2500, 2160),
                (270, 585, 1020, 1010), (43, 29, 174, 174)),
    "g402-angle": ("g402-angle.png", (1384, 1038),
                   (120, 80, 1150, 880), (25.5, 30, 209, 160)),
    "g402-side": ("g402-side.png", (1384, 1038),
                  (45, 315, 1322, 410), (14, 84, 232, 72)),
}


def _render_key(kind, vendor_id=None, product_id=None, *, controls=False):
    if kind == "keypad" and vendor_id in (None, 0x046d) and product_id in (None, 0xc354):
        return "keypad"
    if kind == "dialpad" and vendor_id in (None, 0x046d) and product_id in (None, 0xbc00):
        return "dialpad"
    if kind == "mouse" and (vendor_id, product_id) == (0x046d, 0xc07e):
        return "g402-side" if controls else "g402-angle"
    return None


@lru_cache(maxsize=3)
def _pixmap(filename):
    return QPixmap(str(_ASSETS / filename))


def draw_device_artwork(painter, kind, vendor_id=None, product_id=None, *, controls=False):
    """Draw an exact known model; return False for the existing vector fallback."""
    key = _render_key(kind, vendor_id, product_id, controls=controls)
    if key is None:
        return False
    filename, sheet, source, target = _RENDERS[key]
    pixmap = _pixmap(filename)
    if pixmap.isNull():
        return False
    x, y, width, height = source
    source_rect = QRectF(x * pixmap.width() / sheet[0], y * pixmap.height() / sheet[1],
                        width * pixmap.width() / sheet[0], height * pixmap.height() / sheet[1])
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(QRectF(*target), pixmap, source_rect)
    painter.restore()
    return True


def _circle(x, y, radius):
    path = QPainterPath()
    path.addEllipse(QPointF(x, y), radius, radius)
    return path


def _polygon(points):
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    return path


def device_artwork_control_regions(vendor_id, product_id):
    """Control shapes on the interactive product view, or no photo regions."""
    if (vendor_id, product_id) == (0x046d, 0xbc00):
        key = "dialpad"
        roller = QPainterPath()
        roller.addRoundedRect(QRectF(914, 676, 244, 135), 20, 20)
        regions = {
            "button:1": _circle(420, 742, 78),
            "button:2": _circle(602, 742, 78),
            "button:3": _circle(420, 1424, 78),
            "button:4": _circle(1125, 1424, 78),
            "dial:main": _circle(773, 1166, 272),
            "wheel:roller": roller,
        }
    elif (vendor_id, product_id) == (0x046d, 0xc07e):
        key = "g402-side"
        regions = {
            "button:4": _polygon(((855, 413), (1009, 411), (966, 439), (868, 463))),
            "button:5": _polygon(((675, 450), (844, 423), (856, 466), (696, 511))),
        }
    else:
        return {}
    filename, _, source, target = _RENDERS[key]
    if _pixmap(filename).isNull():
        return {}
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    transform = QTransform()
    transform.translate(tx, ty)
    transform.scale(tw / sw, th / sh)
    transform.translate(-sx, -sy)
    return {name: transform.map(path) for name, path in regions.items()}
