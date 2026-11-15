"""Exercise device-first navigation without hardware, host discovery, or user state."""

import os
import threading
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from mxcc import gui
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
def test_unsupported_device_opens_its_own_details_without_keypad_controls(window, kind):
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
    assert "not available" in window.details.summary.text()
    click(window.details.back_button)
    assert window.views.currentWidget() is (window.console if kind == "dialpad" else window.home)


def test_global_settings_returns_to_the_page_that_opened_it(window):
    click(window.home.settings_button)
    assert window.views.currentWidget() is window.settings_page
    click(window.settings_back)
    assert window.views.currentWidget() is window.home

    click(window.home.cards[CREATIVE_CONSOLE_ID])
    click(window.console.settings_button)
    assert window.views.currentWidget() is window.settings_page
    click(window.settings_back)
    assert window.views.currentWidget() is window.console
    click(window.console.cards["keypad"])
    window.navigation.setCurrentRow(1)
    applications_page = window.stack.currentWidget()
    window.navigation.setCurrentRow(3)
    assert window.views.currentWidget() is window.settings_page
    click(window.settings_back)
    assert window.views.currentWidget() is window.editor
    assert window.selected_device_id == "keypad"
    assert window.navigation.currentRow() == 1
    assert window.stack.currentWidget() is applications_page


@pytest.mark.parametrize("in_settings", [False, True])
def test_unplugging_selected_device_returns_to_console_even_from_settings(window, in_settings):
    open_keypad(window)
    if in_settings:
        window.navigation.setCurrentRow(3)
    window._devices_updated([device("mouse", kind="mouse"), device("dialpad", kind="dialpad")])
    assert window.views.currentWidget() is window.console
    assert window.selected_device_id is None
    assert "keypad" not in window.console.cards
    assert "MX Creative Keypad disconnected" in window.console.notice.text()
    window._devices_updated([device("mouse", kind="mouse")])
    assert window.views.currentWidget() is window.home
    assert CREATIVE_CONSOLE_ID not in window.home.cards


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
