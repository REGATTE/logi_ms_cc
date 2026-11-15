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
def test_other_device_offers_ring_on_buttons_only_without_keypad_controls(window, kind):
    if kind == "dialpad":
        click(window.home.cards[CREATIVE_CONSOLE_ID])
        click(window.console.cards[kind])
    else:
        click(window.home.cards[kind])
    selected = window.devices[kind]
    assert window.views.currentWidget() is window.details
    assert not window.editor.isVisible()
    assert window.details.title.text() == selected.name
    assert window.details.values["connection"].text() == selected.connection
    assert window.details.values["status"].text() == "Connected"
    assert window.details.values["battery"].text() == ("81%" if kind == "dialpad" else "Not reported")
    assert window.details.summary.text() == "ALL CONTROLS"
    assert len(window.details.mapping_combos) == (6 if kind == "dialpad" else 2)
    for identifier, combo in window.details.mapping_combos.items():
        assert combo.isEnabled()
        if identifier.startswith("button:"):
            assert combo.count() == 2 and combo.currentIndex() == 0
            assert combo.currentData() is False
            assert combo.itemText(1) == "Open Action Ring"
        else:
            assert combo.count() == 0 and combo.currentIndex() == -1
            assert combo.currentData() is None
        assert combo.placeholderText() == "Not assigned"
    click(window.details.back_button)
    assert window.views.currentWidget() is (window.console if kind == "dialpad" else window.home)


def test_ring_assignment_persists_and_is_restored_after_navigation(window):
    import json
    window.open_device("mouse")
    window.details.mapping_combos["button:4"].setCurrentIndex(1)
    expected = [{"vendor_id": 0x046d, "product_id": 0xc07e, "control_id": "button:4"}]
    assert window.settings["action_ring"]["bindings"] == expected
    assert json.loads(paths.runtime_path().read_text())["action_ring"]["bindings"] == expected
    window.show_home()
    window.open_device("mouse")
    assert window.details.mapping_combos["button:4"].currentData() is True
    window.details.mapping_combos["button:4"].setCurrentIndex(0)
    assert window.settings["action_ring"]["bindings"] == []


def test_control_search_and_artwork_selection_share_the_device_workspace(window):
    window.open_device("dialpad")
    details = window.details
    before = paths.runtime_path().read_bytes()
    details.search_edit.setFocus()
    details.search_edit.setText("roller")
    QApplication.processEvents()
    assert [identifier for identifier, row in details.control_rows.items() if row.isVisible()] == ["wheel:roller"]
    click(details.art.hotspots["button:1"])
    assert details.search_edit.text() == ""
    assert details.selected_title.text() == details.control_labels["button:1"]
    assert details.control_rows["button:1"].isVisible()
    details.search_edit.setText("missing control")
    assert details.no_results.isVisible()
    assert not details.controls_scroll.isVisible()
    assert paths.runtime_path().read_bytes() == before
    assert not details.facts.isVisible()
    click(details.info_button)
    assert details.facts.isVisible()
    assert details.values["battery"].text() == "81%"
    window.open_device("mouse")
    assert details.search_edit.text() == ""
    assert not details.facts.isVisible()
    assert not details.edit_ring_button.isVisible()
    details.mapping_combos["button:4"].setCurrentIndex(1)
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


def test_mapping_metadata_refresh_keeps_focus_and_does_not_write_assignments(window):
    settings_before = paths.settings_path().read_bytes()
    runtime_before = paths.runtime_path().read_bytes()
    window.open_device("dialpad")
    combo = next(iter(window.details.mapping_combos.values()))
    combo.setFocus()
    selected = next(iter(window.details.mapping_combos))
    assert window.details.selected_control == selected
    assert window.details.art.selected_control == selected
    window._devices_updated([replace(window.devices["dialpad"], battery_percent=37)])
    assert next(iter(window.details.mapping_combos.values())) is combo
    assert combo.hasFocus()
    assert window.details.values["battery"].text() == "37%"
    assert window.details.selected_control == selected
    assert window.details.art.selected_control == selected
    window.open_device("mouse")
    QApplication.processEvents()
    assert list(window.details.mapping_combos) == ["button:4", "button:5"]
    assert "dial:main" not in window.details.mapping_combos
    assert window.details.selected_control is None
    assert window.details.art.selected_control is None
    assert paths.settings_path().read_bytes() == settings_before
    assert paths.runtime_path().read_bytes() == runtime_before


@pytest.mark.parametrize(("kind", "row_id"), [("mouse", "button:5"), ("dialpad", "button:1")])
def test_illustration_and_control_rows_select_each_other(window, kind, row_id):
    window.open_device(kind)
    QApplication.processEvents()
    details = window.details

    click(details.art.hotspots["button:4"])
    assert details.selected_control == details.art.selected_control == "button:4"
    assert [identifier for identifier, row in details.control_rows.items() if row.property("selected")] == ["button:4"]
    assert details.art.hotspots["button:4"].isChecked()

    click(details.control_rows[row_id])
    assert details.selected_control == details.art.selected_control == row_id
    assert [identifier for identifier, row in details.control_rows.items() if row.property("selected")] == [row_id]
    assert details.art.hotspots[row_id].isChecked()
    assert not details.art.hotspots["button:4"].isChecked()

    details.mapping_combos["button:4"].setFocus()
    QApplication.processEvents()
    assert details.selected_control == details.art.selected_control == "button:4"
    assert details.mapping_combos["button:4"].currentData() is False


def test_illustration_selection_reveals_offscreen_control_and_clears_removed_selection(window):
    window.resize(970, 710)
    window.open_device("dialpad")
    QApplication.processEvents()
    details = window.details
    details.controls_scroll.setMaximumHeight(180)
    QApplication.processEvents()
    bar = details.controls_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    click(details.art.hotspots["wheel:roller"])
    assert details.selected_control == details.art.selected_control == "wheel:roller"
    assert bar.value() > 0
    viewport = details.controls_scroll.viewport()
    row = details.control_rows["wheel:roller"]
    assert viewport.rect().contains(QRect(row.mapTo(viewport, QPoint()), row.size()))

    dialpad = window.devices["dialpad"]
    window._devices_updated([replace(dialpad, controls=tuple(control for control in dialpad.controls if control.id != "wheel:roller"))])
    QApplication.processEvents()
    assert details.selected_control is None
    assert details.art.selected_control is None
    assert "wheel:roller" not in details.art.hotspots
    assert not any(row.property("selected") for row in details.control_rows.values())


def test_new_device_controls_appear_and_empty_reports_do_not_invent_buttons(window):
    controls = (DeviceControl("key:272", "Left click", "button", "linux"),
                DeviceControl("rel:8", "Scroll wheel", "wheel", "linux"))
    future = Device("future", "New Logitech device", "device", "USB", True, False,
                    0x046d, 0xf001, controls=controls, controls_note="Controls reported by Ubuntu.")
    window._devices_updated([*window.devices.values(), future])
    click(window.home.cards[future.id])
    assert window.views.currentWidget() is window.details
    assert list(window.details.mapping_combos) == [control.id for control in controls]
    assert not window.details.controls_empty.isVisible()
    assert not window.details.art.hotspots
    click(window.details.control_rows["key:272"])
    assert window.details.selected_control == window.details.art.selected_control == "key:272"
    assert "Left click" in window.details.selection_label.text()
    window._devices_updated([replace(future, controls=())])
    assert not window.details.mapping_combos
    assert window.details.controls_empty.isVisible()
    assert not window.details.controls_scroll.isVisible()
    assert not window.details.explanation.isVisible()
    assert window.details.selected_control is None
    assert window.details.art.selected_control is None


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
    pages = [window.home, window.console, window.details, window.settings_page]
    for page in pages:
        window.views.setCurrentWidget(page)
        QApplication.processEvents()
        assert window.footer.refresh_button.isVisible()
        assert window.footer.settings_button.isVisible()
        click(window.footer.refresh_button)
        assert window._explicit_device_scan is True
    for index in range(window.stack.count()):
        window.open_device("keypad")
        window.navigation.setCurrentRow(index)
        QApplication.processEvents()
        assert window.footer.refresh_button.isVisible()
        assert window.footer.settings_button.isVisible()
        click(window.footer.refresh_button)
    assert scanner.refresh.call_count == len(pages) + window.stack.count()
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
    assert not window.details.mapping_combos
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
