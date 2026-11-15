"""Illustration control selection uses UI hotspots, never physical input."""

import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mxcc.device_controls import DeviceControl, controls_for_device
from mxcc.device_interaction import InteractiveDeviceArt
from mxcc.devices import Device

_APP = None


@pytest.fixture
def art():
    global _APP
    _APP = QApplication.instance() or QApplication(["mxcc-device-interaction-test"])
    widget = InteractiveDeviceArt()
    widget.resize(260, 240)
    widget.show()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


def device(kind="mouse"):
    product = 0xc07e if kind == "mouse" else 0xbc00
    return Device(kind, kind, kind, "USB", True, False, 0x046d, product,
                  controls=controls_for_device(vendor_id=0x046d, product_id=product, kind=kind))


def test_g402_only_exposes_requested_side_buttons_and_selects_with_mouse_and_keyboard(art):
    selected = []
    art.control_selected.connect(selected.append)
    art.set_device(device())
    assert list(art.hotspots) == ["button:4", "button:5"]
    QTest.mouseClick(art.hotspots["button:4"], Qt.MouseButton.LeftButton)
    assert art.selected_control == "button:4"
    assert art.hotspots["button:4"].isChecked()
    art.hotspots["button:5"].setFocus()
    QTest.keyClick(art.hotspots["button:5"], Qt.Key.Key_Return)
    assert art.selected_control == "button:5"
    assert not art.hotspots["button:4"].isChecked()
    QTest.keyClick(art.hotspots["button:5"], Qt.Key.Key_Space)
    assert art.hotspots["button:5"].isChecked()
    assert selected == ["button:4", "button:5", "button:5"]


def test_hotspots_follow_artwork_scale_and_letterboxing(art):
    art.set_device(device())
    first = art.hotspots["button:4"]
    center = first.mapTo(art, first.shape.boundingRect().center().toPoint())
    # Back button on the official side photo, after the standard 1.15x artwork fit.
    assert abs(center.x() - 176) <= 1 and abs(center.y() - 103) <= 1
    art.resize(520, 600)
    QApplication.processEvents()
    center = first.mapTo(art, first.shape.boundingRect().center().toPoint())
    assert abs(center.x() - 351) <= 1 and abs(center.y() - 266) <= 1
    assert first.hitButton(first.shape.boundingRect().center().toPoint())
    assert not first.hitButton(QPoint(0, 0))


def test_dial_and_roller_each_have_one_selector_and_refresh_preserves_focus(art):
    dialpad = device("dialpad")
    art.set_device(dialpad)
    assert set(art.hotspots) == {control.id for control in dialpad.controls}
    assert len(art.hotspots) == 6
    for identifier in ("dial:main", "wheel:roller"):
        QTest.mouseClick(art.hotspots[identifier], Qt.MouseButton.LeftButton)
        assert art.selected_control == identifier
    roller = art.hotspots["wheel:roller"]
    roller.setFocus()
    art.set_device(replace(dialpad, battery_percent=42))
    assert art.hotspots["wheel:roller"] is roller
    assert roller.hasFocus() and roller.isChecked()
    art.set_device(device())
    assert art.selected_control is None
    assert set(art.hotspots) == {"button:4", "button:5"}


def test_whole_rotary_controls_select_the_same_mapping_from_either_half(art):
    art.resize(520, 480)
    art.set_device(device("dialpad"))
    QApplication.processEvents()
    for identifier, source_x, source_y in (
        ("wheel:roller", 174, 52), ("wheel:roller", 174, 60),
        ("dial:main", 105, 129), ("dial:main", 155, 129),
    ):
        point = QPoint(round(260 + (source_x - 130) * 2.3),
                       round(240 + (source_y - 120) * 2.3))
        target = art.childAt(point)
        assert target is art.hotspots[identifier]
        QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target.mapFrom(art, point))
        assert art.selected_control == identifier


def test_unknown_layout_retains_list_selection_without_invented_hotspots_or_signals(art):
    selected = []
    art.control_selected.connect(selected.append)
    unknown = replace(device(), id="future", product_id=0xf001,
                      controls=(DeviceControl("key:272", "Left button", "button", "linux"),))
    art.set_device(unknown)
    assert not art.hotspots
    art.set_selected_control("key:272")
    assert art.selected_control == "key:272"
    assert selected == []
    art.set_device(unknown, controls=())
    assert art.selected_control is None
