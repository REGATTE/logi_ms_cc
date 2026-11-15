"""Known product renders remain offline, transparent and model-specific."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from mxcc.device_artwork import draw_device_artwork, device_artwork_control_regions


_APP = None


def test_known_renders_and_controls_fit_design_space_without_generic_mouse_substitution():
    global _APP
    _APP = QApplication.instance() or QApplication(["device-artwork-test"])
    for kind, product, count in (("keypad", 0xc354, 0), ("dialpad", 0xbc00, 6),
                                 ("mouse", 0xc07e, 2)):
        image = QImage(260, 240, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        assert draw_device_artwork(painter, kind, 0x046d, product, controls=True)
        painter.end()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(130, 120).alpha() > 0
        regions = device_artwork_control_regions(0x046d, product)
        assert len(regions) == count
        assert all(QRectF(0, 0, 260, 240).contains(path.boundingRect()) for path in regions.values())

    image = QImage(260, 240, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    assert not draw_device_artwork(painter, "mouse", 0x046d, 0xffff)
    assert not draw_device_artwork(painter, "mouse")
    painter.end()
    assert not device_artwork_control_regions(0x046d, 0xffff)
