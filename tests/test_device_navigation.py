"""Exercise device-first navigation without hardware, host discovery, or user state."""

import os
import threading
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from mxcc import gui, paths
from mxcc.device_controls import DeviceControl, controls_for_device
from mxcc.device_navigation import DeviceScanner
from mxcc.device_views import CREATIVE_CONSOLE_ID
from mxcc.devices import Device

_APP = None


def device(identifier="keypad", *, kind="keypad", connected=True):
    names = {"keypad": "MX Creative Keypad", "mouse": "Gaming Mouse G402", "dialpad": "MX Dialpad"}
    products = {"keypad": 0xc354, "mouse": 0xc07e, "dialpad": 0xbc00}
    return Device(
        id=identifier, name=names[kind], kind=kind,
        connection="Bluetooth" if kind == "dialpad" else "USB",
        connected=connected, supported=kind == "keypad", vendor_id=0x046d,
        product_id=products[kind], battery_percent=81 if kind == "dialpad" else None,
        controls=controls_for_device(vendor_id=0x046d, product_id=products[kind], kind=kind),
    )


@pytest.fixture
def isolated_qt(monkeypatch, tmp_path):
    global _APP
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS"):
        directory = tmp_path / name.lower()
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(name, str(directory))
    monkeypatch.delenv("MXCC_BUNDLED_PROFILES", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _APP = QApplication.instance() or QApplication(["mxcc-navigation-test"])
    external_calls = []

    def forbidden(*args, **kwargs):
        external_calls.append((args, kwargs))
        raise AssertionError("Navigation tests must not discover host devices/apps or contact the worker")

    for owner, name in ((gui, "discover_devices"), (gui, "discover_applications"),
                        (gui.ipc, "request"), (gui.service, "ensure_running"),
                        (gui.service, "set_autostart")):
        monkeypatch.setattr(owner, name, forbidden)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    yield _APP
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()
    assert not external_calls
    assert not warnings


@pytest.fixture
def window(isolated_qt):
    inventory = [device(), device("mouse", kind="mouse"), device("dialpad", kind="dialpad")]
    window = gui.MainWindow(connect_worker=False, applications=[], devices=inventory)
    window.show()
    isolated_qt.processEvents()
    return window


def click(widget):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    QApplication.processEvents()


def open_keypad(window):
    click(window.home.cards[CREATIVE_CONSOLE_ID])
    click(window.console.cards["keypad"])


def test_starts_on_device_overview_and_only_selecting_keypad_opens_editor(window):
    assert window.views.currentWidget() is window.home
    assert set(window.home.cards) == {CREATIVE_CONSOLE_ID, "mouse"}
    assert not window.editor.isVisible()
    assert window.device_scanner is None
    assert not window.device_timer.isActive()
    assert not window.timer.isActive()

    click(window.home.cards[CREATIVE_CONSOLE_ID])
    assert window.views.currentWidget() is window.console
    assert set(window.console.cards) == {"keypad", "dialpad"}
    assert not window.editor.isVisible()
    click(window.console.cards["keypad"])
    assert window.views.currentWidget() is window.editor
    assert window.selected_device_id == "keypad"
    assert window.device_title.text() == "MX Creative Keypad"
    assert window.navigation.currentRow() == 0
    click(window.back_button)
    assert window.views.currentWidget() is window.console
    assert window.selected_device_id is None
    click(window.console.back_button)
    assert window.views.currentWidget() is window.home
    assert window.selected_device_id is None


@pytest.mark.parametrize("kind", ["mouse", "dialpad"])
def test_other_device_offers_shared_actions_on_buttons_only(window, kind):
    window.open_device(kind)
    selected, details = window.devices[kind], window.details
    assert window.views.currentWidget() is details
    assert not window.editor.isVisible()
    assert details.title.text() == selected.name
    assert details.values["connection"].text() == selected.connection
    assert details.values["status"].text() == "Connected"
    assert details.values["battery"].text() == ("81%" if kind == "dialpad" else "Not reported")
    assert details.summary.text() == "ALL ACTIONS"
    assert len(details.controls) == (6 if kind == "dialpad" else 2)
    for identifier in details.controls:
        details.control_selector.setCurrentIndex(details.control_selector.findData(identifier))
        assert set(details.picker.category_buttons) == {"launch", "shortcut", "system"}
        assert details.apply_button.isEnabled() == identifier.startswith("button:")
        assert not details.clear_button.isEnabled()
    click(details.back_button)
    assert window.views.currentWidget() is (window.console if kind == "dialpad" else window.home)


def test_ring_assignment_persists_and_is_restored_after_navigation(window):
    import json
    window.open_device("mouse")
    details = window.details
    details.select_control("button:4")
    details.picker.kind_combo.setCurrentIndex(details.picker.kind_combo.findData("open_ring"))
    details.apply_button.click()
    expected = [{"vendor_id": 0x046d, "product_id": 0xc07e, "control_id": "button:4",
                 "label": "Action Ring", "icon": "view-grid", "action": {"type": "open_ring"}}]
    assert window.settings["device_actions"] == expected
    runtime = json.loads(paths.runtime_path().read_text())
    assert runtime["device_actions"][0]["action"] == {"type": "open_ring"}
    window.show_home()
    window.open_device("mouse")
    details.select_control("button:4")
    assert details.picker.value()["action"] == {"type": "open_ring"}
    details.clear_button.click()
    assert window.settings["device_actions"] == []


def test_action_search_and_artwork_selection_share_the_device_workspace(window):
    window.open_device("dialpad")
    details = window.details
    before = paths.runtime_path().read_bytes()
    click(details.art.hotspots["button:1"])
    assert details.control_selector.currentData() == "button:1"
    assert details.selected_title.text() == details.control_labels["button:1"]
    details.picker.search_edit.setText("missing action")
    assert details.picker.no_results.isVisible()
    assert paths.runtime_path().read_bytes() == before
    assert not details.facts.isVisible()
    click(details.info_button)
    assert details.facts.isVisible()
    assert details.values["battery"].text() == "81%"
    window.open_device("mouse")
    assert details.picker.search_edit.text() == ""
    assert not details.facts.isVisible()
    assert not details.edit_ring_button.isVisible()
    details.select_control("button:4")
    details.picker.kind_combo.setCurrentIndex(details.picker.kind_combo.findData("open_ring"))
    details.apply_button.click()
    assert details.edit_ring_button.isVisible()
    requested = []
    details.edit_ring_requested.connect(lambda: requested.append(True))
    click(details.edit_ring_button)
    assert requested == [True]


def test_home_ring_editor_and_footer_return_without_touching_devices(window):
    click(window.home.ring_card)
    assert window.views.currentWidget() is window.ring_page
    before = paths.runtime_path().read_bytes()
    window.footer.settings_button.click()
    assert window.views.currentWidget() is window.settings_page
    window.settings_back.click()
    assert window.views.currentWidget() is window.ring_page
    window.ring_back.click()
    assert window.views.currentWidget() is window.home
    assert paths.runtime_path().read_bytes() == before


def test_mapping_metadata_refresh_keeps_draft_focus_and_does_not_write_assignments(window):
    settings_before = paths.settings_path().read_bytes()
    runtime_before = paths.runtime_path().read_bytes()
    window.open_device("dialpad")
    details = window.details
    details.select_control("button:1")
    details.picker.kind_combo.setCurrentIndex(details.picker.kind_combo.findData("shortcut"))
    details.picker.label_edit.setText("Unsaved shortcut")
    details.picker.keys_edit.setText("Ctrl+Shift+P")
    details.picker.keys_edit.setFocus()
    window._devices_updated([replace(window.devices["dialpad"], battery_percent=37)])
    assert details.picker.keys_edit.hasFocus()
    assert details.picker.label_edit.text() == "Unsaved shortcut"
    assert details.picker.keys_edit.text() == "Ctrl+Shift+P"
    assert details.values["battery"].text() == "37%"
    assert details.selected_control == details.art.selected_control == "button:1"
    window.open_device("mouse")
    QApplication.processEvents()
    assert list(details.controls) == ["button:4", "button:5"]
    assert details.selected_control is None
    assert details.art.selected_control is None
    assert paths.settings_path().read_bytes() == settings_before
    assert paths.runtime_path().read_bytes() == runtime_before


@pytest.mark.parametrize(("kind", "row_id"), [("mouse", "button:5"), ("dialpad", "button:1")])
def test_illustration_and_control_selector_select_each_other(window, kind, row_id):
    window.open_device(kind)
    QApplication.processEvents()
    details = window.details
    click(details.art.hotspots["button:4"])
    assert details.selected_control == details.art.selected_control == "button:4"
    assert details.control_selector.currentData() == "button:4"
    assert details.art.hotspots["button:4"].isChecked()
    details.control_selector.setCurrentIndex(details.control_selector.findData(row_id))
    assert details.selected_control == details.art.selected_control == row_id
    assert details.art.hotspots[row_id].isChecked()
    assert not details.art.hotspots["button:4"].isChecked()


def test_rotary_selection_disables_actions_and_removed_selection_is_cleared(window):
    window.resize(970, 710)
    window.open_device("dialpad")
    QApplication.processEvents()
    details = window.details
    click(details.art.hotspots["wheel:roller"])
    assert details.selected_control == details.art.selected_control == "wheel:roller"
    assert details.control_selector.currentData() == "wheel:roller"
    assert not details.picker.isEnabled()
    assert not details.apply_button.isEnabled()
    assert "movement mappings" in details.explanation.text()
    dialpad = window.devices["dialpad"]
    window._devices_updated([replace(dialpad, controls=tuple(control for control in dialpad.controls if control.id != "wheel:roller"))])
    QApplication.processEvents()
    assert details.selected_control is None
    assert details.art.selected_control is None
    assert "wheel:roller" not in details.art.hotspots
    assert details.control_selector.currentData() is None


def test_new_device_controls_appear_and_empty_reports_do_not_invent_buttons(window):
    controls = (DeviceControl("key:272", "Left click", "button", "linux"),
                DeviceControl("rel:8", "Scroll wheel", "wheel", "linux"))
    future = Device("future", "New Logitech device", "device", "USB", True, False,
                    0x046d, 0xf001, controls=controls, controls_note="Controls reported by Ubuntu.")
    window._devices_updated([*window.devices.values(), future])
    click(window.home.cards[future.id])
    details = window.details
    assert window.views.currentWidget() is details
    assert list(details.controls) == [control.id for control in controls]
    assert not details.controls_empty.isVisible()
    assert not details.art.hotspots
    details.control_selector.setCurrentIndex(details.control_selector.findData("key:272"))
    assert details.selected_control == details.art.selected_control == "key:272"
    assert details.apply_button.isEnabled()
    assert "Left click" in details.selection_label.text()
    window._devices_updated([replace(future, controls=())])
    assert not details.controls
    assert details.controls_empty.isVisible()
    assert not details.picker.isVisible()
    assert not details.explanation.isVisible()
    assert details.selected_control is None
    assert details.art.selected_control is None


def test_global_settings_returns_to_the_page_that_opened_it(window):
    click(window.footer.settings_button)
    assert window.views.currentWidget() is window.settings_page
    click(window.settings_back)
    assert window.views.currentWidget() is window.home

    click(window.home.cards[CREATIVE_CONSOLE_ID])
    click(window.footer.settings_button)
    assert window.views.currentWidget() is window.settings_page
    click(window.settings_back)
    assert window.views.currentWidget() is window.console
    click(window.console.cards["keypad"])
    window.navigation.setCurrentRow(1)
    applications_page = window.stack.currentWidget()
    click(window.footer.settings_button)
    assert window.views.currentWidget() is window.settings_page
    click(window.settings_back)
    assert window.views.currentWidget() is window.editor
    assert window.selected_device_id == "keypad"
    assert window.navigation.currentRow() == 1
    assert window.stack.currentWidget() is applications_page
    window.open_device("dialpad")
    click(window.footer.settings_button)
    assert window.views.currentWidget() is window.settings_page
    click(window.footer.settings_button)
    click(window.settings_back)
    assert window.views.currentWidget() is window.details
    assert window.selected_device_id == "dialpad"


def test_shared_footer_refresh_is_available_on_every_page(window):
    scanner = Mock(busy=False)
    window.device_scanner = scanner
    pages = [window.home, window.console, window.details, window.ring_page, window.settings_page]
    for page in pages:
        window.views.setCurrentWidget(page)
        QApplication.processEvents()
        assert window.footer.refresh_button.isVisible()
        assert window.footer.settings_button.isVisible()
        controls = window.window_controls
        assert controls.isVisible()
        assert controls.parentWidget() is window.views.currentWidget()
        assert controls.mapTo(window, QPoint()).y() < 64
        assert controls.mapTo(window, QPoint()).x() > window.width() / 2
        click(window.footer.refresh_button)
        assert window._explicit_device_scan is True
    for index in range(window.stack.count()):
        window.open_device("keypad")
        window.navigation.setCurrentRow(index)
        QApplication.processEvents()
        assert window.footer.refresh_button.isVisible()
        assert window.footer.settings_button.isVisible()
        controls = window.window_controls
        assert controls.parentWidget() is window.editor
        control_rect = QRect(controls.mapTo(window, QPoint()), controls.size())
        assert window.rect().contains(control_rect)
        assert control_rect.top() < 64
        for other in (window.navigation, window.connection):
            assert not control_rect.intersects(QRect(other.mapTo(window, QPoint()), other.size()))
        click(window.footer.refresh_button)
    assert scanner.refresh.call_count == len(pages) + window.stack.count()
    assert all(control.isVisible() for control in (window.window_controls.minimize_button,
               window.window_controls.maximize_button, window.window_controls.close_button))
    assert not window.footer.isAncestorOf(window.window_controls)
    window._set_device_scanning(True)
    assert not window.footer.refresh_button.isEnabled()
    window._set_device_scanning(False)
    assert window.footer.refresh_button.isEnabled()


@pytest.mark.parametrize("in_settings", [False, True])
def test_unplugging_selected_device_returns_to_console_even_from_settings(window, in_settings):
    open_keypad(window)
    if in_settings:
        click(window.footer.settings_button)
    window._devices_updated([device("mouse", kind="mouse"), device("dialpad", kind="dialpad")])
    assert window.views.currentWidget() is window.console
    assert window.selected_device_id is None
    keypad_card = window.console.cards["keypad"]
    assert keypad_card.indicators["keypad"].state == "disconnected"
    assert "MX Creative Keypad disconnected" in window.console.notice.text()
    window._devices_updated([device("mouse", kind="mouse")])
    assert window.views.currentWidget() is window.console
    assert CREATIVE_CONSOLE_ID in window.home.cards
    assert window.console.cards["dialpad"].indicators["dialpad"].state == "disconnected"
    click(keypad_card)
    assert window.views.currentWidget() is window.details
    assert window.details.values["status"].text() == "Disconnected"
    assert not window.details.controls
    window._devices_updated([device()])
    assert window.console.cards["keypad"] is keypad_card
    assert keypad_card.indicators["keypad"].state == "connected"
    assert window.views.currentWidget() is window.editor


@pytest.mark.parametrize("connected", [False, None])
def test_unconfirmed_keypad_connection_cannot_enter_or_stay_in_editor(window, connected):
    open_keypad(window)
    keypad = replace(window.devices["keypad"], connected=connected)
    window._devices_updated([keypad])
    assert window.views.currentWidget() is window.details
    assert not window.editor.isVisible()
    expected = "Disconnected" if connected is False else "Connection unknown"
    assert window.details.values["status"].text() == expected
    window.show_home()
    open_keypad(window)
    assert window.views.currentWidget() is window.details


def test_second_keypad_blocks_ambiguous_configuration_until_only_one_remains(window):
    open_keypad(window)
    first, second = device(), device("second-keypad")
    window._devices_updated([first, second])
    assert window.views.currentWidget() is window.details
    assert "Connect one keypad" in window.details.summary.text()
    window._devices_updated([first])
    assert window.views.currentWidget() is window.editor
    window._devices_updated([first, second])
    assert window.views.currentWidget() is window.details
    window.show_console()
    click(window.console.cards["second-keypad"])
    assert window.views.currentWidget() is window.details
    assert not window.editor.isVisible()
    window._devices_updated([first])
    assert window.views.currentWidget() is window.console
    click(window.console.cards["keypad"])
    assert window.views.currentWidget() is window.editor


def test_scanner_coalesces_busy_refreshes_and_recovers_after_an_error(isolated_qt):
    entered = threading.Event()
    release = threading.Event()
    calls, inventories, failures, scanning = [], [], [], []

    def provider():
        calls.append(threading.get_ident())
        if len(calls) == 1:
            entered.set()
            if not release.wait(2):
                raise AssertionError("Test did not release the device scan")
            raise RuntimeError("Device scan unavailable")
        return [device()]

    def wait_until(predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        assert predicate()

    scanner = DeviceScanner(provider)
    scanner.updated.connect(inventories.append)
    scanner.failed.connect(failures.append)
    scanner.scanning.connect(scanning.append)
    try:
        scanner.refresh()
        scanner.refresh()
        wait_until(entered.is_set)
        scanner.refresh()
        assert len(calls) == 1
        assert calls[0] != threading.get_ident()
        assert scanning == [True]
        release.set()
        wait_until(lambda: bool(failures))
        assert failures == ["Device scan unavailable"]
        assert not inventories
        assert not scanner.busy
        scanner.refresh()
        wait_until(lambda: bool(inventories))
        assert inventories == [[device()]]
        assert len(calls) == 2
        assert scanning == [True, False, True, False]
        assert not scanner.busy
    finally:
        release.set()
        assert scanner.pool.waitForDone(2000)
        scanner.deleteLater()
        isolated_qt.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        isolated_qt.processEvents()
