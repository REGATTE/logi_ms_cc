"""Offline official product renders and their verified control geometry.

Source rectangles select devices from the original product sheets at paint time;
the bundled PNG files remain unchanged Logitech CDN renders. Coordinates below
use DeviceArt's 260×240 design space before its normal widget transform.
"""

from functools import lru_cache
from pathlib import Path
import re

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
    "mx-master-4": ("mx-master-4.png", (800, 691),
                    (195, 110, 430, 480), (49.375, 20, 161.25, 180)),
    "mx-master-4-controls": ("mx-master-4-controls.png", (1000, 864),
                             (148, 194, 681, 430), (20, 38, 220, 138.91)),
}

# Exact model identities, with separate overview and control views where needed.
_MODELS = {
    ("keypad", 0xc354): ("keypad", "keypad"),
    ("dialpad", 0xbc00): ("dialpad", "dialpad"),
    ("mouse", 0xc07e): ("g402-angle", "g402-side"),
    ("mouse", 0xb042): ("mx-master-4", "mx-master-4-controls"),
}
_MODEL_IDS = {product: renders for (_, product), renders in _MODELS.items()}
# Transport-specific IDs can differ. Match complete model names, never a broad
# family such as "MX Master" or "mouse", and never a receiver's identity.
_MODEL_NAMES = {
    "mx creative keypad": ("keypad", 0xc354),
    "mx dialpad": ("dialpad", 0xbc00),
    "g402": ("mouse", 0xc07e),
    "g402 gaming mouse": ("mouse", 0xc07e),
    "gaming mouse g402": ("mouse", 0xc07e),
    "g402 hyperion fury": ("mouse", 0xc07e),
    "g402 hyperion fury gaming mouse": ("mouse", 0xc07e),
    "mx master 4": ("mouse", 0xb042),
    "mx master 4 for mac": ("mouse", 0xb042),
    "mx master 4 for business": ("mouse", 0xb042),
}


def _render_key(kind, vendor_id=None, product_id=None, *, name="", controls=False):
    # The combined Creative Console card draws its two known components.
    if vendor_id is None and product_id is None and kind in ("keypad", "dialpad"):
        return kind
    if vendor_id != 0x046d or kind == "receiver":
        return None
    # Discovery infers kind from the reported name, including a user-editable
    # Bluetooth alias. An established hardware ID takes precedence over it.
    model = _MODEL_IDS.get(product_id)
    if model is None:
        normalized = re.sub(r"^(?:logitech|logi)\s+", "", " ".join(name.casefold().split()))
        identity = _MODEL_NAMES.get(normalized)
        if identity is not None and identity[0] == kind:
            model = _MODELS[identity]
    return model[bool(controls)] if model else None


@lru_cache(maxsize=len(_RENDERS))
def _pixmap(filename):
    return QPixmap(str(_ASSETS / filename))


def draw_device_artwork(painter, kind, vendor_id=None, product_id=None, *, name="", controls=False):
    """Draw an exact known model; return False for the existing vector fallback."""
    key = _render_key(kind, vendor_id, product_id, name=name, controls=controls)
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
    elif (vendor_id, product_id) == (0x046d, 0xb042):
        key = "mx-master-4-controls"
        # Front three-quarter view exposes the real side buttons as well as
        # the two main buttons. Coordinates refer to the unchanged CDN PNG.
        regions = {
            "key:272": _polygon(((385, 335), (526, 352), (553, 376), (490, 446),
                                  (358, 551), (290, 576), (240, 563), (292, 499),
                                  (340, 445), (367, 396))),
            "key:273": _polygon(((369, 329), (333, 357), (294, 380), (266, 420),
                                  (249, 452), (194, 507), (175, 520), (165, 493),
                                  (175, 452), (211, 400), (259, 364), (309, 340))),
            "key:274": _polygon(((256, 450), (273, 459), (293, 421), (321, 392),
                                  (356, 375), (368, 381), (377, 368), (355, 357),
                                  (319, 367), (284, 396))),
            "key:275": _polygon(((632, 368), (663, 333), (673, 328),
                                  (681, 334), (676, 349), (642, 379))),
            "key:276": _polygon(((584, 406), (615, 377), (627, 375), (633, 383),
                                  (605, 411), (590, 425), (582, 423), (580, 416))),
            "key:278": _polygon(((515, 465), (551, 432), (560, 432),
                                  (566, 439), (559, 452), (529, 480), (519, 480))),
            "hidpp:196": _polygon(((420, 298), (456, 272), (475, 274),
                                    (473, 283), (440, 305), (424, 306))),
            "hidpp:416": _polygon(((555, 486), (591, 452), (649, 396), (700, 342),
                                    (718, 341), (766, 369), (796, 415), (809, 458),
                                    (796, 504), (769, 525), (641, 552), (607, 548),
                                    (578, 521))),
            "rel:6": _polygon(((555, 349), (615, 286), (638, 270), (653, 281),
                                (671, 302), (669, 317), (607, 379), (590, 389))),
        }
        # Clicking the main wheel selects its button. Scrolling remains a
        # separate selector entry; it must not cover the same clickable pixels.
        for identifier in ("key:272", "key:273"):
            regions[identifier] = regions[identifier].subtracted(regions["key:274"])
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
