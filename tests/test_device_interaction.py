"""Illustration control selection uses UI hotspots, never physical input."""

import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
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


def master4():
    controls = controls_for_device(vendor_id=0x046d, product_id=0xb042, kind="mouse")
    return Device("master4", "MX Master 4", "mouse", "Bluetooth", True, False,
                  0x046d, 0xb042, controls=controls + (
                      DeviceControl("key:277", "Additional reported button 6", "button", "linux"),),
                  model_name="MX Master 4")


MASTER4_HOTSPOTS = {"key:272", "key:273", "key:274", "key:275", "key:276", "key:278",
                    "hidpp:196", "hidpp:416", "rel:6"}


def interior_point(hotspot):
    """Find a real painted/masked point without assuming a rectangular button."""
    bounds = hotspot.shape.boundingRect().toAlignedRect()
    center = bounds.center()
    points = [center]
    points.extend(QPoint(x, y) for y in range(bounds.top() + 2, bounds.bottom(), 2)
                  for x in range(bounds.left() + 2, bounds.right(), 2))
    return next(point for point in points
                if hotspot.shape.contains(QPointF(point)) and hotspot.mask().contains(point))


def test_master4_photo_controls_are_selectable_with_mouse_and_keyboard(art):
    selected = []
    art.control_selected.connect(selected.append)
    model = master4()
    art.set_device(model)
    assert set(art.hotspots) == MASTER4_HOTSPOTS
    labels = {control.id: control.label for control in model.controls}
    for identifier, hotspot in art.hotspots.items():
        point = interior_point(hotspot)
        assert art.childAt(hotspot.mapTo(art, point)) is hotspot
        QTest.mouseClick(hotspot, Qt.MouseButton.LeftButton, pos=point)
        assert art.selected_control == selected[-1] == identifier
        assert hotspot.isChecked()
        assert all(other.isChecked() == (key == identifier) for key, other in art.hotspots.items())
        assert hotspot.accessibleName() == labels[identifier]
        hotspot.setFocus()
        QTest.keyClick(hotspot, Qt.Key.Key_Return)
        QTest.keyClick(hotspot, Qt.Key.Key_Space)
        assert selected[-3:] == [identifier] * 3
    assert "rel:8" not in art.hotspots  # The wheel surface selects its click action.
    assert "key:277" not in art.hotspots  # No invented physical position.


@pytest.mark.parametrize("size", [(260, 240), (520, 600), (300, 700), (780, 240)])
def test_master4_region_hit_testing_tracks_resize_and_letterboxing(art, size):
    art.set_device(master4())
    art.set_selected_control("key:278")
    art.resize(*size)
    QApplication.processEvents()
    for identifier, hotspot in art.hotspots.items():
        local = interior_point(hotspot)
        point = hotspot.mapTo(art, local)
        assert art.rect().contains(point)
        assert art.childAt(point) is hotspot
        assert hotspot.hitButton(hotspot.mapFrom(art, point))
        assert not hotspot.hitButton(QPoint(0, 0))
        QTest.mouseClick(hotspot, Qt.MouseButton.LeftButton, pos=hotspot.mapFrom(art, point))
        assert art.selected_control == identifier


def test_master4_refresh_preserves_selection_and_other_ids_do_not_borrow_regions(art):
    model = master4()
    art.set_device(model)
    art.set_selected_control("key:278")
    gesture = art.hotspots["key:278"]
    gesture.setFocus()
    art.set_device(replace(model, battery_percent=35))
    assert art.hotspots["key:278"] is gesture
    assert gesture.hasFocus() and gesture.isChecked()
    assert art.selected_control == "key:278"
    for other in (replace(model, product_id=0xffff), replace(model, vendor_id=0x1234)):
        art.set_device(other)
        assert not art.hotspots
    art.set_device(model)
    art.set_device(model, controls=tuple(control for control in model.controls if control.id != "key:278"))
    assert "key:278" not in art.hotspots
    assert art.selected_control is None
