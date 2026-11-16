"""Known product renders remain offline, transparent and model-specific."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from mxcc.device_artwork import _render_key, draw_device_artwork, device_artwork_control_regions
from mxcc.devices import _device


_APP = None


def test_known_renders_and_controls_fit_design_space_without_generic_mouse_substitution():
    global _APP
    _APP = QApplication.instance() or QApplication(["device-artwork-test"])
    for kind, product, count in (("keypad", 0xc354, 0), ("dialpad", 0xbc00, 6),
                                 ("mouse", 0xc07e, 2), ("mouse", 0xb042, 9)):
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


@pytest.mark.parametrize("controls", [False, True])
def test_model_selection_uses_id_then_exact_logitech_name_without_family_substitution(controls):
    def key(product, name="", vendor=0x046d, kind="mouse"):
        return _render_key(kind, vendor, product, name=name, controls=controls)

    assert key(0xc07e) == ("g402-side" if controls else "g402-angle")
    master_key = "mx-master-4-controls" if controls else "mx-master-4"
    assert key(0xb042) == master_key
    # A Bluetooth alias cannot override an established hardware model ID.
    assert key(0xc07e, "MX Master 4") != key(0xb042)
    assert key(0xffff, "  Logitech  MX MASTER 4  ") == master_key
    assert key(0xffff, "Logi MX Master 4 for Mac") == master_key
    assert key(0xffff, "G402 Gaming Mouse") == key(0xc07e)
    for name in ("MX Master 3S", "MX Master 40", "MX Master", "Mouse", "Unknown"):
        assert key(0xffff, name) is None
    assert key(0xffff, "MX Master 4", vendor=0x1234) is None
    assert key(0xffff, "MX Master 4", kind="receiver") is None


@pytest.mark.parametrize("controls", [False, True])
def test_known_mouse_photos_survive_reported_names_that_change_discovered_kind(controls):
    global _APP
    _APP = QApplication.instance() or QApplication(["device-artwork-test"])

    def render(name, product):
        device = _device("test", name, product, "Bluetooth")
        image = QImage(260, 240, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            assert draw_device_artwork(painter, device.kind, device.vendor_id,
                                       device.product_id, name=device.name, controls=controls)
        finally:
            painter.end()
        return device, image

    _, g402 = render("G402 Gaming Mouse", 0xc07e)
    device, abbreviated = render("G402", 0xc07e)
    assert device.kind == "device"
    assert abbreviated == g402

    _, master = render("MX Master 4", 0xb042)
    assert master != g402
    for alias, inferred_kind in (("Work pointer", "device"), ("Work keyboard", "keyboard")):
        device, renamed = render(alias, 0xb042)
        assert device.kind == inferred_kind
        assert renamed == master

    # Even a recognized product number does not override the vendor or turn
    # a receiver card into the peripheral whose editable name it happens to use.
    assert _render_key("device", 0x1234, 0xb042, name="MX Master 4") is None
    assert _render_key("receiver", 0x046d, 0xb042, name="MX Master 4") is None
