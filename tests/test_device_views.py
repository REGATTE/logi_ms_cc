"""Device overview navigation and honest connection state in real Qt widgets."""

import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mxcc.devices import Device
from mxcc.device_views import AppFooter, CREATIVE_CONSOLE_ID, ConsoleDevices, DevicesHome

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
def footer(qt_app):
    actions = AppFooter()
    actions.resize(970, 64)
    actions.show()
    qt_app.processEvents()
    yield actions
    actions.close()
    actions.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


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
                             device("dialpad", kind="dialpad", battery=0),
                             device("keyboard", kind="keyboard", connected=False)])
    group = overview.cards[CREATIVE_CONSOLE_ID]
    assert group.indicators["keypad"].state == "connected"
    assert group.indicators["keypad"].battery_text == ""
    assert group.indicators["dialpad"].state == "connected"
    assert group.indicators["dialpad"].battery_text == "0%"
    assert overview.cards["receiver-mouse"].indicators["receiver-mouse"].state == "unknown"
    assert overview.cards["receiver-mouse"].indicators["receiver-mouse"].battery_text == ""
    assert overview.cards["keyboard"].indicators["keyboard"].state == "disconnected"
    assert "Connection unknown · USB" in overview.cards["receiver-mouse"].toolTip()
    assert "Dialpad · Connected · Bluetooth · 0% battery" in group.toolTip()


def test_mouse_model_changes_update_both_home_and_detail_artwork(overview):
    from mxcc.device_interaction import InteractiveDeviceArt

    g402 = replace(device("mouse", kind="mouse"), name="G402 Gaming Mouse", product_id=0xc07e)
    master = replace(g402, name="MX Master 4", product_id=0xb042, connection="Bluetooth")
    overview.update_devices([g402])
    card = overview.cards[g402.id]
    detail = InteractiveDeviceArt()
    detail.resize(260, 240)
    detail.set_device(g402)
    g402_card = card.art.grab().toImage()
    g402_detail = detail.grab().toImage()

    overview.update_devices([master])
    detail.set_device(master)
    assert overview.cards[master.id] is card
    assert card.art.model_name == detail.model_name == "MX Master 4"
    master_card = card.art.grab().toImage()
    master_detail = detail.grab().toImage()
    assert master_card != g402_card
    assert master_detail != g402_detail
    assert not detail.hotspots

    # Receiver transport IDs may be unknown to the catalog. Name changes must
    # also update the artwork when kind and IDs stay the same.
    alias = replace(master, product_id=0xffff)
    overview.update_devices([alias])
    detail.set_device(alias)
    assert card.art.grab().toImage() == master_card
    assert detail.grab().toImage() == master_detail
    unknown = replace(alias, name="MX Master 3S")
    overview.update_devices([unknown])
    detail.set_device(unknown)
    assert card.art.grab().toImage() != master_card
    assert detail.grab().toImage() != master_detail
    detail.deleteLater()


def test_downloaded_model_image_updates_cards_and_details_without_leaking_between_models(overview):
    from mxcc.device_interaction import InteractiveDeviceArt

    class Images(QObject):
        ready = Signal(str)

        def __init__(self):
            super().__init__()
            self.requests = []
            self.images = {}

        def request(self, vendor, product, name, *, connected=True):
            self.requests.append((vendor, product, name))
            return f"{product}:{name}"

        def image(self, key):
            return self.images.get(key, QImage())

    images = Images()
    overview.image_provider = images
    mouse = replace(device("mouse", kind="mouse"), name="Office pointer",
                    product_id=0xb034, model_name="MX Master 3S")
    overview.update_devices([mouse])
    card = overview.cards[mouse.id]
    detail = InteractiveDeviceArt()
    detail.set_image_provider(images)
    detail.resize(260, 240)
    detail.set_device(mouse)
    placeholder = card.art.grab().toImage()
    assert images.requests and all(name == "MX Master 3S" for _, _, name in images.requests)
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ff1223"))
    key = card.art.image_key
    images.images[key] = image
    images.ready.emit(key)
    assert card.art.grab().toImage() != placeholder
    assert detail.grab().toImage().pixelColor(130, 120) == QColor("#ff1223")
    assert not detail.hotspots

    other = replace(mouse, product_id=0xf001, model_name="A different model")
    overview.update_devices([other])
    detail.set_device(other)
    assert card.art.remote_image.isNull() and detail.remote_image.isNull()
    images.ready.emit(key)  # An old in-flight response cannot paint a new device.
    assert card.art.remote_image.isNull() and detail.remote_image.isNull()

    # A bundled hardware ID does not download, even with a user nickname.
    images.requests.clear()
    bundled = replace(other, product_id=0xb042, model_name="MX Master 4")
    overview.update_devices([bundled])
    detail.set_device(bundled)
    assert not images.requests
    detail.deleteLater()


def test_rescan_preserves_existing_card_focus_and_removes_disconnected_devices(overview):
    keypad, dialpad = device(), device("dialpad", kind="dialpad", battery=90)
    overview.update_devices([keypad, dialpad])
    card = overview.cards[CREATIVE_CONSOLE_ID]
    card.setFocus()
    overview.update_devices([keypad, replace(dialpad, battery_percent=89)])
    assert overview.cards[CREATIVE_CONSOLE_ID] is card
    assert card.hasFocus()
    assert "89% battery" in card.toolTip()
    assert card.indicators["dialpad"].battery_text == "89%"
    overview.update_devices([keypad, replace(dialpad, battery_percent=None)])
    assert card.indicators["dialpad"].battery_text == ""
    overview.update_devices([keypad])
    assert set(overview.cards) == {CREATIVE_CONSOLE_ID}
    assert set(card.indicators) == {"keypad"}
    assert "battery" not in card.toolTip()
    assert card.hasFocus()
    overview.update_devices([])
    assert not overview.empty_state.isVisible()
    assert overview.scroll.isVisible()
    assert overview.ring_card.isVisible()


def test_shared_footer_actions_and_busy_refresh(footer):
    refreshes, settings = [], []
    footer.refresh_requested.connect(lambda: refreshes.append(True))
    footer.settings_requested.connect(lambda: settings.append(True))
    QTest.mouseClick(footer.refresh_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(footer.settings_button, Qt.MouseButton.LeftButton)
    assert refreshes == settings == [True]
    footer.set_scanning(True)
    assert not footer.refresh_button.isEnabled()
    QTest.mouseClick(footer.refresh_button, Qt.MouseButton.LeftButton)
    assert refreshes == [True]
    assert "Refreshing" in footer.refresh_button.accessibleName()
    footer.settings_button.setFocus()
    QTest.keyClick(footer.settings_button, Qt.Key.Key_Return)
    assert settings == [True, True]
    footer.set_scanning(False)
    assert footer.refresh_button.isEnabled()
    assert not footer.refresh_button.text()


def test_home_scan_failure_keeps_existing_devices_usable(overview):
    overview.update_devices([device()])
    overview.set_scanning(True)
    assert overview.cards[CREATIVE_CONSOLE_ID].isEnabled()
    overview.set_error("Bluetooth status is unavailable.")
    overview.set_scanning(False)
    assert overview.notice.isVisible()
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
    assert console.gallery.columns == 2
    assert console.cards["keypad"].y() == console.cards["dialpad"].y()
    assert console.cards["keypad"].x() < console.cards["dialpad"].x()
    assert console.scroll.horizontalScrollBar().maximum() == 0
    assert not console.missing_notice.isVisible()
    assert console.cards["dialpad"].indicators["dialpad"].state == "connected"
    assert console.cards["dialpad"].indicators["dialpad"].battery_text == "73%"
    assert console.cards["keypad"].indicators["keypad"].battery_text == ""
    assert "Connected · Bluetooth · 73% battery" in console.cards["dialpad"].toolTip()
    assert console.cards["dialpad"].support_label.text() == "Device details"

    QTest.mouseClick(console.cards["keypad"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(console.cards["dialpad"], Qt.MouseButton.LeftButton)
    console.cards["keypad"].setFocus()
    QTest.keyClick(console.cards["keypad"], Qt.Key.Key_Return)
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
    assert "89% battery" in console.cards["dialpad"].toolTip()
    assert console.cards["dialpad"].indicators["dialpad"].battery_text == "89%"
    console.update_devices([keypad, replace(dialpad, battery_percent=None)])
    assert console.cards["dialpad"].indicators["dialpad"].battery_text == ""
    console.update_devices([keypad])
    assert set(console.cards) == {"keypad"}
    assert "No dialpad detected" in console.missing_notice.text()
    assert not selected
    console.update_devices([dialpad])
    assert set(console.cards) == {"dialpad"}
    assert "No keypad detected" in console.missing_notice.text()
    console.update_devices([])
    assert not console.cards
    assert "No MX Creative Console devices detected" in console.missing_notice.text()


def test_console_actions_and_multiple_keypads_preserve_real_ids(console):
    selected, back = [], []
    console.device_selected.connect(selected.append)
    console.back_requested.connect(lambda: back.append(True))
    console.update_devices([device(), device("second-keypad"), device("dialpad", kind="dialpad", connected=None)])
    QTest.mouseClick(console.cards["second-keypad"], Qt.MouseButton.LeftButton)
    assert selected == ["second-keypad"]
    assert console.cards["dialpad"].indicators["dialpad"].state == "unknown"
    QTest.mouseClick(console.back_button, Qt.MouseButton.LeftButton)
    assert back == [True]
    console.set_error("Keypad disconnected.")
    assert console.notice.isVisible()
    assert console.cards["dialpad"].isEnabled()
    console.set_error("")
    assert not console.notice.isVisible()


def test_search_indicators_blink_only_while_searching_and_visible(overview):
    overview.update_devices([device(), device("dialpad", kind="dialpad", connected=False)])
    QApplication.processEvents()
    group = overview.cards[CREATIVE_CONSOLE_ID]
    connected = group.indicators["keypad"]
    missing = group.indicators["dialpad"]
    overview.set_scanning(True)
    assert connected.state == "connected" and not connected.timer.isActive()
    assert missing.state == "searching" and missing.timer.isActive()
    before = missing.blink_on
    QTest.qWait(550)
    assert missing.blink_on != before
    overview.hide()
    assert not missing.timer.isActive()
    overview.show()
    assert missing.timer.isActive()
    overview.set_scanning(False)
    assert missing.state == "disconnected" and not missing.timer.isActive()
    overview.set_scanning(True, force=True)
    assert connected.state == "searching" and connected.timer.isActive()
    overview.set_scanning(False)
    assert connected.state == "connected" and not connected.timer.isActive()


def test_console_search_updates_new_cards_and_keeps_individual_dots(console):
    console.set_scanning(True)
    console.update_devices([device(), device("dialpad", kind="dialpad", connected=None)])
    QApplication.processEvents()
    dialpad = console.cards["dialpad"].indicators["dialpad"]
    assert dialpad.state == "searching" and dialpad.timer.isActive()
    assert console.cards["keypad"].indicators["keypad"].state == "connected"
    console.set_scanning(False)
    assert dialpad.state == "unknown" and not dialpad.timer.isActive()
