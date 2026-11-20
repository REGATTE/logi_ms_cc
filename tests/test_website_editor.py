"""Website UI saves exact-site overrides without touching a browser or devices."""

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from mxcc import gui, paths
from mxcc.applications import Application
from mxcc.configuration import default_settings, save_settings
from mxcc.device_controls import MODEL_CONTROLS, DeviceControl
from mxcc.devices import Device
from mxcc.website_editor import WebsiteEditor


_APP = None


def shortcut(label="Site search", keys="Ctrl+K"):
    return {"label": label, "icon": "system-search", "action": {"type": "shortcut", "keys": keys}}


def target(control="button:1"):
    return {"vendor_id": 0x046d, "product_id": 0xbc00, "control_id": control}


def dialpad(name="MX Dialpad"):
    return Device("dialpad", name, "dialpad", "Bluetooth", True, False, 0x046d, 0xbc00,
                  controls=MODEL_CONTROLS[(0x046d, 0xbc00)])


@pytest.fixture
def isolated_qt(monkeypatch, tmp_path):
    global _APP
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS"):
        directory = tmp_path / name.lower()
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(name, str(directory))
    monkeypatch.delenv("MXCC_BUNDLED_PROFILES", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _APP = QApplication.instance() or QApplication(["mxcc-website-editor-test"])
    def forbidden(*args, **kwargs):
        raise AssertionError("Website UI tests must not access host inventory, browser or worker")
    for owner, name in ((gui, "discover_devices"), (gui, "discover_applications"),
                        (gui.ipc, "request"), (gui.service, "ensure_running"),
                        (gui.service, "set_autostart")):
        monkeypatch.setattr(owner, name, forbidden)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    yield _APP, warnings
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


@pytest.fixture
def editor(isolated_qt):
    settings = default_settings([])
    settings["action_ring"]["layouts"]["default"] = []
    settings["device_actions"] = [target() | shortcut("Normal Copy", "Ctrl+C")]
    widget = WebsiteEditor(settings, [])
    widget.resize(1100, 800)
    widget.show()
    isolated_qt[0].processEvents()
    return widget


@pytest.fixture
def window(isolated_qt):
    folder = Application("mxcc-data-2tb.desktop", "Files — Data_2tb", "folder", "unused", "", Path("/unused/folder.desktop"))
    settings = default_settings([folder])
    settings["launchers"] = [folder.id, "@action-ring"] + [None] * 7
    settings["action_ring"]["layouts"]["default"] = []
    settings["device_actions"] = [target() | shortcut("Normal Copy", "Ctrl+C"),
                                  {"vendor_id": 0x046d, "product_id": 0xb042, "control_id": "hidpp:416",
                                   "label": "Action Ring", "icon": "view-grid", "action": {"type": "open_ring"}}]
    save_settings(settings)
    widget = gui.MainWindow(connect_worker=False, applications=[folder], devices=[dialpad()])
    widget.resize(1200, 850)
    widget.show()
    widget.show_websites()
    isolated_qt[0].processEvents()
    return widget


def set_shortcut(picker, label="Site search", keys="Ctrl+K"):
    picker.kind_combo.setCurrentIndex(picker.kind_combo.findData("shortcut"))
    picker.label_edit.setText(label)
    picker.keys_edit.setText(keys)
    picker.icon_edit.setText("system-search")


def test_add_website_normalizes_and_selects_existing_exact_profile(editor):
    changed = []
    editor.changed.connect(changed.append)
    assert editor.add_website("https://EXAMPLE.com/search?q=1") == "example.com"
    assert editor.current_domain == "example.com"
    assert editor.value() == [{"domain": "example.com", "device_actions": [], "ring": None}]
    assert editor.add_website("example.com") == "example.com"
    assert len(changed) == 1
    editor.add_website("sub.example.com")
    assert [item["domain"] for item in editor.value()] == ["example.com", "sub.example.com"]
    before = editor.value()
    with pytest.raises(ValueError):
        editor.add_website("*.example.com")
    assert editor.value() == before


def test_site_mapping_overrides_and_reverts_without_changing_normal_action(editor):
    editor.add_website("example.com")
    normal = deepcopy(editor._settings["device_actions"])
    assert editor.picker.value() == shortcut("Normal Copy", "Ctrl+C")
    assert not editor.clear_button.isEnabled()
    set_shortcut(editor.picker)
    editor.apply_button.click()
    assert editor.value()[0]["device_actions"] == [target() | shortcut()]
    assert editor._settings["device_actions"] == normal
    assert editor.clear_button.isEnabled()
    editor.add_website("other.example")
    assert editor.picker.value() == shortcut("Normal Copy", "Ctrl+C")
    assert editor.value()[1]["device_actions"] == []
    editor.open_website("example.com")
    assert editor.picker.value() == shortcut()
    editor.clear_button.click()
    assert editor.value()[0]["device_actions"] == []
    assert editor.picker.value() == shortcut("Normal Copy", "Ctrl+C")
    assert editor._settings["device_actions"] == normal


def test_invalid_shortcut_keeps_saved_mapping_and_reports_error(editor):
    editor.add_website("example.com")
    before = editor.value()
    set_shortcut(editor.picker, keys="not-a-key")
    editor.apply_button.click()
    assert editor.value() == before
    assert not editor.error_label.isHidden()
    assert editor.error_label.text()


def test_large_dial_saves_both_directions_as_one_override_and_can_revert(editor):
    editor.add_website("http://10.64.192.14:8800/#/queue")
    assert editor.control_combo.findData("wheel:roller") == -1
    editor.control_combo.setCurrentIndex(editor.control_combo.findData("dial:main"))
    assert editor.control_combo.currentText() == "Large dial"
    assert editor.picker.isHidden()
    assert not editor.dial_picker.isHidden()
    editor.dial_picker.video_button.click()
    editor.apply_button.click()
    expected = target("dial:main") | {"label": "Video frames", "icon": "input-keyboard",
                                     "action": {"type": "dial_shortcut", "clockwise": "Right", "counterclockwise": "Left"}}
    assert editor.value()[0]["device_actions"] == [expected]
    before = editor.value()
    editor.dial_picker.counterclockwise_edit.setText("Ctrl+")
    editor.apply_button.click()
    assert editor.value() == before
    assert not editor.error_label.isHidden()
    editor.control_combo.setCurrentIndex(editor.control_combo.findData("button:1"))
    assert not editor.picker.isHidden()
    assert editor.dial_picker.isHidden()
    editor.control_combo.setCurrentIndex(editor.control_combo.findData("dial:main"))
    assert editor.dial_picker.counterclockwise_edit.text() == "Left"
    editor.clear_button.click()
    assert editor.value()[0]["device_actions"] == []
    assert not editor.clear_button.isEnabled()


def test_dial_draft_survives_device_refresh_and_backend_error_is_target_specific(editor):
    editor.add_website("example.com")
    editor.control_combo.setCurrentIndex(editor.control_combo.findData("dial:main"))
    editor.dial_picker.video_button.click()
    editor.dial_picker.clockwise_edit.setText("Ctrl+")
    draft = editor.dial_picker.draft()
    editor.set_devices([dialpad("My dialpad")])
    assert editor.control_combo.currentData() == "dial:main"
    assert editor.dial_picker.draft() == draft
    editor.set_status({"dialpad_dial": {"errors": ["Could not divert the dial"]}})
    assert "Could not divert" in editor.dial_status.text()
    assert not editor.dial_status.isHidden()
    editor.control_combo.setCurrentIndex(editor.control_combo.findData("button:1"))
    assert editor.dial_status.isHidden()
    editor.control_combo.setCurrentIndex(editor.control_combo.findData("dial:main"))
    editor.set_status({"dialpad_dial": {"errors": []}})
    assert editor.dial_status.isHidden()


def test_gui_persists_dial_directions_to_website_runtime_only(window):
    editor = window.website_editor
    original = deepcopy(window.settings)
    editor.add_website("10.64.192.14")
    editor.control_combo.setCurrentIndex(editor.control_combo.findData("dial:main"))
    editor.dial_picker.video_button.click()
    editor.apply_button.click()
    saved = json.loads(paths.settings_path().read_text())
    runtime = json.loads(paths.runtime_path().read_text())
    assert saved["device_actions"] == original["device_actions"]
    assert saved["launchers"] == original["launchers"]
    assert runtime["website_profiles"][0]["device_actions"] == [target("dial:main") | {
        "action": {"type": "dial_shortcut", "clockwise": "Right", "counterclockwise": "Left"}}]


def test_site_ring_survives_profile_and_tab_changes_and_reverts_to_inherited(editor):
    editor.add_website("example.com")
    editor.open_website("example.com", ring=True, add_action=True)
    assert editor.custom_ring.isChecked()
    assert editor.ring_editor.isEnabled()
    set_shortcut(editor.ring_editor.picker, "Next result", "Ctrl+G")
    editor.ring_editor.apply_button.click()
    expected = [shortcut("Next result", "Ctrl+G")]
    assert editor.value()[0]["ring"] == expected
    editor.tabs.setCurrentIndex(0)
    editor.tabs.setCurrentIndex(1)
    assert editor.ring_editor.value()["layouts"]["default"] == expected
    editor.add_website("other.example")
    assert editor.value()[1]["ring"] is None
    editor.open_website("example.com", ring=True)
    assert editor.ring_editor.value()["layouts"]["default"] == expected
    editor.custom_ring.setChecked(False)
    assert editor.value()[0]["ring"] is None
    assert not editor.ring_editor.isEnabled()
    assert editor._settings["action_ring"]["layouts"]["default"] == []


def test_remove_website_does_not_remove_another_sites_overrides(editor):
    editor.add_website("example.com")
    set_shortcut(editor.picker)
    editor.apply_button.click()
    retained = deepcopy(editor.value()[0])
    editor.add_website("other.example")
    editor.remove_website()
    assert editor.value() == [retained]
    assert editor.current_domain == "example.com"
    editor.remove_website()
    assert editor.value() == []
    assert editor.current_domain is None
    assert editor.tabs.isHidden()
    assert not editor.remove_button.isEnabled()


def test_device_refresh_preserves_unapplied_shortcut_for_unchanged_target(editor):
    editor.add_website("example.com")
    editor.set_devices([dialpad()])
    set_shortcut(editor.picker, "Unfinished command", "Ctrl+")
    draft = editor.picker.draft()
    # A new device and a renamed dialpad are inventory changes, not edits.
    extra = Device("extra", "Another mouse", "mouse", "USB", True, False, 0x046d, 0x9999,
                   controls=(DeviceControl("key:275", "Back", "button", "sysfs"),))
    editor.set_devices([dialpad("My dialpad"), extra])
    assert editor.device_combo.currentData() == (0x046d, 0xbc00)
    assert editor.control_combo.currentData() == "button:1"
    assert editor.picker.draft() == draft
    assert editor.value()[0]["device_actions"] == []


def test_gui_saves_site_actions_and_ring_to_runtime_preserving_existing_controls(window):
    editor = window.website_editor
    original = deepcopy(window.settings)
    editor.add_website("example.com")
    set_shortcut(editor.picker)
    editor.apply_button.click()
    editor.open_website("example.com", ring=True, add_action=True)
    set_shortcut(editor.ring_editor.picker, "Next result", "Ctrl+G")
    editor.ring_editor.apply_button.click()
    saved = json.loads(paths.settings_path().read_text())
    runtime = json.loads(paths.runtime_path().read_text())
    assert saved == window.settings
    assert saved["website_profiles"] == editor.value()
    assert saved["device_actions"] == original["device_actions"]
    assert saved["launchers"] == original["launchers"]
    site = runtime["website_profiles"][0]
    assert site["device_actions"] == [target() | {"action": shortcut()["action"]}]
    assert site["ring"]["id"] == "website:example.com"
    assert site["ring"]["slots"] == [shortcut("Next result", "Ctrl+G")]
    editor.open_website("example.com", ring=False)
    window.show_ring("website:example.com", add_action=True)
    assert window.views.currentWidget() is window.website_page
    assert editor.current_domain == "example.com"
    assert editor.tabs.currentIndex() == 1
    assert editor.ring_editor.current_slot == len(site["ring"]["slots"])
    assert editor.ring_editor.apply_button.text() == "Add action"


def test_runtime_write_failure_rolls_back_saved_and_visible_website_edits(window, isolated_qt, monkeypatch):
    editor = window.website_editor
    editor.add_website("example.com")
    before = deepcopy(window.settings)
    settings_bytes = paths.settings_path().read_bytes()
    runtime_bytes = paths.runtime_path().read_bytes()
    def fail(*_args, **_kwargs):
        raise OSError("Simulated runtime write failure")
    monkeypatch.setattr(gui, "atomic_json", fail)
    set_shortcut(editor.picker)
    editor.apply_button.click()
    assert window.settings == before
    assert editor.value() == before["website_profiles"]
    assert paths.settings_path().read_bytes() == settings_bytes
    assert paths.runtime_path().read_bytes() == runtime_bytes
    assert isolated_qt[1] and "runtime write failure" in isolated_qt[1][-1][-1]


def test_website_connection_status_distinguishes_active_and_default_profiles(editor):
    editor.set_status({"browser_connected": True, "active_website": "example.com", "website_profile": "example.com"})
    assert "example.com" in editor.connection.text() and "profile active" in editor.connection.text()
    editor.set_status({"browser_connected": True, "active_website": "other.example", "website_profile": None})
    assert "Using normal actions" in editor.connection.text()
    editor.set_status({"browser_connected": False})
    assert "connect automatically" in editor.connection.text()
    assert "Set up Firefox" not in editor.connection.text()


@pytest.mark.parametrize("source, label", [("firefox", "Firefox"),
    ("firefox_accessibility", "Firefox automatic"), ("chatgpt", "ChatGPT embedded browser"),
    ("browser_accessibility", "Browser automatic")])
def test_website_status_names_actual_source_even_if_other_provider_has_error(editor, source, label):
    editor.set_status({"active_website": "10.64.192.14", "website_source": source,
                       "website_profile": "10.64.192.14", "browser_error": "Firefox disconnected",
                       "embedded_browser": {"errors": ["Accessibility unavailable"]}})
    assert editor.connection.text() == f"{label} · 10.64.192.14 · Website profile active"
    editor.set_status({"active_website": "example.com", "website_source": source, "website_profile": None})
    assert editor.connection.text() == f"{label} · example.com · Using normal actions"


def test_accessibility_errors_are_only_shown_for_supported_foreground_browsers(editor):
    status = {"active_app": "Chatgpt", "browser_error": "Firefox disconnected",
              "embedded_browser": {"available": False, "connected": False,
                                   "errors": ["Accessibility unavailable", "Restart the browser"]}}
    editor.set_status(status)
    assert editor.connection.text() == "ChatGPT embedded browser · Accessibility unavailable · Restart the browser"
    assert "Firefox" not in editor.connection.text()
    editor.set_status({**status, "active_app": "Firefox"})
    assert editor.connection.text() == "Firefox automatic · Accessibility unavailable · Restart the browser"
    editor.set_status({**status, "active_app": "chatgpt-helper"})
    assert "Accessibility unavailable" not in editor.connection.text()


def test_chat_or_address_bar_focus_uses_normal_actions_without_firefox_setup_error(editor):
    editor.set_status({"active_app": "CHATGPT", "website_source": None, "active_website": None,
                       "browser_connected": False,
                       "embedded_browser": {"available": True, "connected": True, "errors": []}})
    assert "Click inside a website" in editor.connection.text()
    assert "Chat and the address bar use normal actions" in editor.connection.text()
    assert "Firefox" not in editor.connection.text()
    editor.set_status({"active_app": "Settings", "embedded_browser": {"available": True, "connected": True, "errors": []}})
    assert "Automatic website shortcuts ready" in editor.connection.text()
    assert "your browser" in editor.connection.text()
    editor.set_status({"active_app": "Settings", "embedded_browser": {"available": True, "connected": False, "errors": []}})
    assert "Waiting for website focus" in editor.connection.text()
    assert "ready" not in editor.connection.text()


def test_embedded_unavailable_status_does_not_appear_as_firefox_failure(editor):
    editor.set_status({"active_app": "Chatgpt", "embedded_browser": {"available": False, "connected": False, "errors": []}})
    assert editor.connection.text() == "ChatGPT embedded browser · Website detection is unavailable"


@pytest.mark.parametrize("application", ["Firefox", "firefox_firefox", "org.mozilla.firefox", "firefox-esr"])
def test_firefox_without_addon_reports_automatic_focus_guidance(editor, application):
    editor.set_status({"active_app": application, "active_website": None,
                       "browser_connected": False,
                       "embedded_browser": {"available": True, "connected": True, "errors": []}})
    assert "Firefox automatic" in editor.connection.text()
    assert "Click inside a configured website" in editor.connection.text()
    assert "address bar and text fields use normal actions" in editor.connection.text()
    assert "Set up Firefox" not in editor.connection.text()


def test_connected_firefox_addon_takes_precedence_over_accessibility_status(editor):
    status = {"active_app": "firefox_firefox", "browser_connected": True,
              "active_website": None,
              "embedded_browser": {"available": False, "errors": ["Accessibility unavailable"]}}
    editor.set_status(status)
    assert "Firefox add-on connected" in editor.connection.text()
    assert "Accessibility" not in editor.connection.text()
    editor.set_status({**status, "browser_error": "More than one Firefox profile is connected"})
    assert editor.connection.text() == "Firefox add-on · More than one Firefox profile is connected"


@pytest.mark.parametrize("application", ["Google-chrome", "Brave-browser", "org.example.NewBrowser"])
def test_other_browsers_use_worker_detection_instead_of_firefox_connection(editor, application):
    status = {"active_app": application, "browser_connected": True,
              "browser_error": "More than one Firefox profile is connected",
              "embedded_browser": {"browser": "browser", "available": True,
                                   "connected": True, "errors": []}}
    editor.set_status(status)
    assert editor.connection.text().startswith("Browser automatic · Click inside a configured website")
    assert "Firefox" not in editor.connection.text()
    status["embedded_browser"]["errors"] = ["Accessibility unavailable"]
    editor.set_status(status)
    assert editor.connection.text() == "Browser automatic · Accessibility unavailable"
    status["embedded_browser"].update(errors=[], available=False)
    editor.set_status(status)
    assert editor.connection.text() == "Browser automatic · Website detection is unavailable"


def test_firefox_automatic_unavailable_reports_provider_without_addon_instruction(editor):
    editor.set_status({"active_app": "Firefox", "browser_connected": False,
                       "embedded_browser": {"available": False, "connected": False, "errors": []}})
    assert editor.connection.text() == "Firefox automatic · Website detection is unavailable"
