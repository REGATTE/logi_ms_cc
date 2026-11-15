"""Device overview navigation and honest connection state in real Qt widgets."""

import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mxcc.devices import Device
from mxcc.device_views import CREATIVE_CONSOLE_ID, ConsoleDevices, DevicesHome

_APP = None


@pytest.fixture
def qt_app():
    global _APP
    _APP = QApplication.instance() or QApplication(["mxcc-device-view-test"])
    return _APP


@pytest.fixture
def overview(qt_app):
    home = DevicesHome()
    home.resize(1200, 820)
    home.show()
    _APP.processEvents()
    yield home
    home.close()
    home.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


@pytest.fixture
def console(qt_app):
    page = ConsoleDevices()
    page.resize(970, 710)
    page.show()
    qt_app.processEvents()
    yield page
    page.close()
    page.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


def device(identifier="keypad", *, kind="keypad", connected=True, battery=None):
    return Device(id=identifier, name="MX Creative Keypad" if kind == "keypad" else identifier.title(),
                  kind=kind, connection="USB" if kind != "dialpad" else "Bluetooth",
                  connected=connected, supported=kind == "keypad", vendor_id=0x046d,
                  product_id=0xc354 if kind == "keypad" else 0, battery_percent=battery)


def test_console_members_share_one_home_card_and_other_devices_remain_separate(overview):
    selected = []
    overview.device_selected.connect(selected.append)
    overview.update_devices([device(), device("dialpad", kind="dialpad"), device("mouse", kind="mouse")])
    QApplication.processEvents()
    assert set(overview.cards) == {CREATIVE_CONSOLE_ID, "mouse"}
    QTest.mouseClick(overview.cards[CREATIVE_CONSOLE_ID], Qt.MouseButton.LeftButton)
    overview.cards["mouse"].setFocus()
    QTest.keyClick(overview.cards["mouse"], Qt.Key.Key_Return)
    assert selected == [CREATIVE_CONSOLE_ID, "mouse"]
    assert "MX Creative Console" in overview.cards[CREATIVE_CONSOLE_ID].accessibleName()
    assert overview.cards["mouse"].support_label.text() == "Device details"


def test_connected_unknown_and_battery_are_not_invented(overview):
    overview.update_devices([device(), device("receiver-mouse", kind="mouse", connected=None),
                             device("dialpad", kind="dialpad", battery=0)])
    group = overview.cards[CREATIVE_CONSOLE_ID]
    assert group.connection_label.text() == "Keypad · Connected\nDialpad · Connected"
    assert overview.cards["receiver-mouse"].connection_label.text() == "Connection unknown · USB"
    assert "Dialpad · Connected · Bluetooth · 0% battery" in group.toolTip()


def test_rescan_preserves_existing_card_focus_and_removes_disconnected_devices(overview):
    keypad, dialpad = device(), device("dialpad", kind="dialpad", battery=90)
    overview.update_devices([keypad, dialpad])
    card = overview.cards[CREATIVE_CONSOLE_ID]
    card.setFocus()
    overview.update_devices([keypad, replace(dialpad, battery_percent=89)])
    assert overview.cards[CREATIVE_CONSOLE_ID] is card
    assert card.hasFocus()
    assert "89% battery" in card.toolTip()
    overview.update_devices([keypad])
    assert set(overview.cards) == {CREATIVE_CONSOLE_ID}
    assert "Dialpad" not in card.connection_label.text()
    assert "battery" not in card.connection_label.text()
    assert card.hasFocus()
    overview.update_devices([])
    assert overview.empty_state.isVisible()
    assert not overview.scroll.isVisible()


def test_home_actions_and_scan_failure_keep_existing_devices_usable(overview):
    refreshes, settings = [], []
    overview.refresh_requested.connect(lambda: refreshes.append(True))
    overview.settings_requested.connect(lambda: settings.append(True))
    QTest.mouseClick(overview.refresh_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(overview.settings_button, Qt.MouseButton.LeftButton)
    assert refreshes == settings == [True]
    overview.update_devices([device()])
    overview.set_scanning(True)
    assert not overview.refresh_button.isEnabled()
    assert overview.cards[CREATIVE_CONSOLE_ID].isEnabled()
    overview.set_error("Bluetooth status is unavailable.")
    overview.set_scanning(False)
    assert overview.notice.isVisible()
    assert overview.refresh_button.isEnabled()
    overview.set_error("")
    assert not overview.notice.isVisible()


def test_gallery_wraps_on_narrow_windows_without_horizontal_scroll(overview):
    overview.update_devices([device(), device("dialpad", kind="dialpad"), device("mouse", kind="mouse"),
                             device("keyboard", kind="keyboard")])
    QApplication.processEvents()
    assert overview.gallery.columns == 3
    assert len({card.y() for card in overview.cards.values()}) == 1
    overview.resize(970, 710)
    QApplication.processEvents()
    assert overview.gallery.columns == 2
    assert len({card.y() for card in overview.cards.values()}) == 2
    assert overview.scroll.horizontalScrollBar().maximum() == 0


def test_console_shows_real_members_side_by_side_without_automatically_selecting_one(console):
    selected = []
    console.device_selected.connect(selected.append)
    console.update_devices([device("mouse", kind="mouse"), device(), device("dialpad", kind="dialpad", battery=73)])
    QApplication.processEvents()
    assert not selected
    assert set(console.cards) == {"keypad", "dialpad"}
    assert console.navigation.count() == 2
    assert console.navigation.currentRow() == -1
    assert console.gallery.columns == 2
    assert console.cards["keypad"].y() == console.cards["dialpad"].y()
    assert console.cards["keypad"].x() < console.cards["dialpad"].x()
    assert console.scroll.horizontalScrollBar().maximum() == 0
    assert not console.missing_notice.isVisible()
    assert console.cards["dialpad"].connection_label.text() == "Connected · Bluetooth · 73% battery"
    assert console.cards["dialpad"].support_label.text() == "Device details"

    QTest.mouseClick(console.cards["keypad"], Qt.MouseButton.LeftButton)
    dialpad_item = console.navigation.item(1)
    QTest.mouseClick(console.navigation.viewport(), Qt.MouseButton.LeftButton,
                     pos=console.navigation.visualItemRect(dialpad_item).center())
    console.navigation.setCurrentRow(0)
    QTest.keyClick(console.navigation, Qt.Key.Key_Return)
    assert selected == ["keypad", "dialpad", "keypad"]


def test_console_updates_keep_focused_cards_and_never_invent_a_missing_counterpart(console):
    selected = []
    console.device_selected.connect(selected.append)
    keypad, dialpad = device(), device("dialpad", kind="dialpad", battery=90)
    console.update_devices([keypad, dialpad])
    card = console.cards["keypad"]
    card.setFocus()
    console.update_devices([keypad, replace(dialpad, battery_percent=89)])
    assert console.cards["keypad"] is card
    assert card.hasFocus()
    assert "89% battery" in console.cards["dialpad"].connection_label.text()
    console.update_devices([keypad])
    assert set(console.cards) == {"keypad"}
    assert console.navigation.count() == 1
    assert "No dialpad detected" in console.missing_notice.text()
    assert not selected
    console.update_devices([dialpad])
    assert set(console.cards) == {"dialpad"}
    assert "No keypad detected" in console.missing_notice.text()
    console.update_devices([])
    assert not console.cards
    assert console.navigation.count() == 0
    assert "No MX Creative Console devices detected" in console.missing_notice.text()


def test_console_actions_and_multiple_keypads_preserve_real_ids(console):
    selected, back, settings = [], [], []
    console.device_selected.connect(selected.append)
    console.back_requested.connect(lambda: back.append(True))
    console.settings_requested.connect(lambda: settings.append(True))
    console.update_devices([device(), device("second-keypad"), device("dialpad", kind="dialpad", connected=None)])
    QTest.mouseClick(console.cards["second-keypad"], Qt.MouseButton.LeftButton)
    assert selected == ["second-keypad"]
    assert "Connection unknown" in console.cards["dialpad"].connection_label.text()
    QTest.mouseClick(console.back_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(console.settings_button, Qt.MouseButton.LeftButton)
    assert back == settings == [True]
    console.set_error("Keypad disconnected.")
    assert console.notice.isVisible()
    assert console.cards["dialpad"].isEnabled()
    console.set_error("")
    assert not console.notice.isVisible()
