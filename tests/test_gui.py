"""Exercise real Qt widgets without a display, worker, or user configuration."""

import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from mxcc import gui, paths
from mxcc.applications import Application
from mxcc.icons import FaceRenderer
from mxcc.profile_editor import ProfileEditor
from mxcc.profiles import load_profiles

_APP = None


@pytest.fixture
def isolated_qt(monkeypatch, tmp_path):
    global _APP
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS"):
        directory = tmp_path / name.lower()
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(name, str(directory))
    monkeypatch.delenv("MXCC_BUNDLED_PROFILES", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _APP = QApplication.instance() or QApplication(["mxcc-test"])
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _, title, message: warnings.append((title, message)))
    monkeypatch.setattr(QMessageBox, "information", lambda *_: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    def forbid_worker(*args, **kwargs):
        raise AssertionError("GUI tests must not contact or start a hardware worker")
    monkeypatch.setattr(gui.ipc, "request", forbid_worker)
    monkeypatch.setattr(gui.service, "ensure_running", forbid_worker)
    monkeypatch.setattr(gui.service, "set_autostart", forbid_worker)
    monkeypatch.setattr(gui, "discover_devices", lambda: [])
    yield _APP, warnings
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


@pytest.fixture
def installed_app(isolated_qt, tmp_path):
    icon = QImage(64, 64, QImage.Format.Format_RGB32)
    icon.fill(QColor("#db2424"))
    path = tmp_path / "original.png"
    assert icon.save(str(path))
    return Application("editor.desktop", "Editor", str(path), "editor %U", "Editor", tmp_path / "editor.desktop")


def custom_profile(profile_id="editor"):
    return {"version": 1, "id": profile_id, "name": "Editor actions", "desktop_ids": ["editor.desktop"], "wm_classes": ["Editor"], "shortcuts": [{"slot": 1, "label": "Save", "keys": "Ctrl+S", "icon": "document-save"}]}


def choose_application(window, identifier):
    window.key_picker.kind_combo.setCurrentIndex(window.key_picker.kind_combo.findData("launch"))
    window.app_combo.setCurrentIndex(window.app_combo.findData(identifier))


def test_autostart_toggle_saves_only_login_preference(isolated_qt, installed_app, monkeypatch):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    before = window.settings.copy()
    runtime = paths.runtime_path().read_bytes(), paths.runtime_path().stat().st_mtime_ns
    setup = Mock()
    monkeypatch.setattr(gui.service, "set_autostart", setup)
    monkeypatch.setattr(window, "persist", Mock(side_effect=AssertionError("Login preferences must not reload keys")))
    for enabled in (True, False):
        window.start_at_login.setChecked(enabled)
        assert window.settings == {**before, "start_at_login": enabled}
        assert json.loads(paths.settings_path().read_text())["start_at_login"] is enabled
        assert window.start_at_login.isEnabled()
        assert window.autostart_status.text()
        assert (paths.runtime_path().read_bytes(), paths.runtime_path().stat().st_mtime_ns) == runtime
    assert setup.call_args_list == [call(True), call(False)]


def test_autostart_setup_failure_keeps_saved_preference_and_reverts_toggle(isolated_qt, installed_app, monkeypatch):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    before = paths.settings_path().read_bytes()
    setup = Mock(side_effect=RuntimeError("Test login setup failure"))
    monkeypatch.setattr(gui.service, "set_autostart", setup)
    window.start_at_login.setChecked(True)
    assert not window.start_at_login.isChecked() and window.start_at_login.isEnabled()
    assert window.settings["start_at_login"] is False
    assert paths.settings_path().read_bytes() == before
    assert "Test login setup failure" in window.autostart_status.text()
    setup.assert_called_once_with(True)


@pytest.mark.parametrize("rollback_error", [None, RuntimeError("rollback unavailable")])
def test_autostart_preference_failure_rolls_back_setup_and_reports_failure(isolated_qt, installed_app, monkeypatch, rollback_error):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    before = paths.settings_path().read_bytes()
    setup = Mock(side_effect=[None, rollback_error])
    monkeypatch.setattr(gui.service, "set_autostart", setup)
    monkeypatch.setattr(gui, "save_settings", Mock(side_effect=OSError("preference denied")))
    window.start_at_login.setChecked(True)
    assert setup.call_args_list == [call(True), call(False)]
    assert not window.start_at_login.isChecked() and window.start_at_login.isEnabled()
    assert window.settings["start_at_login"] is False
    assert paths.settings_path().read_bytes() == before
    assert "preference denied" in window.autostart_status.text()
    if rollback_error is not None:
        assert "rollback unavailable" in window.autostart_status.text()


def test_autostart_sync_repairs_enabled_setup_without_enabling_disabled_preference(isolated_qt, installed_app, monkeypatch):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    setup = Mock()
    monkeypatch.setattr(gui.service, "set_autostart", setup)
    window.connect_worker = True
    window._sync_autostart()
    setup.assert_not_called()
    window.settings["start_at_login"] = True
    window._sync_autostart()
    setup.assert_called_once_with(True)
    setup.side_effect = RuntimeError("Repair needed")
    window._sync_autostart()
    assert "Repair needed" in window.autostart_status.text()
    assert window.settings["start_at_login"] is True


def test_frameless_window_top_controls_preserve_background_worker(isolated_qt, installed_app):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    window.show()
    QApplication.processEvents()
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    controls = window.window_controls
    assert controls.parentWidget() is window.home
    assert controls.mapTo(window, QPoint()).y() < 64
    assert controls.mapTo(window, QPoint()).x() > window.width() / 2
    controls.maximize_button.click()
    QApplication.processEvents()
    assert window.isMaximized()
    assert controls.maximize_button.kind == "restore"
    assert controls.isVisible() and controls.mapTo(window, QPoint()).y() < 64
    controls.maximize_button.click()
    QApplication.processEvents()
    assert not window.isMaximized()
    assert controls.maximize_button.kind == "maximize"
    controls.minimize_button.click()
    QApplication.processEvents()
    assert window.isMinimized()
    window.showNormal()
    window.timer.start()
    window.device_timer.start()
    controls.close_button.click()
    assert not window.isVisible()
    assert not window.timer.isActive() and not window.device_timer.isActive()


def test_profile_strip_tracks_assignments_order_and_selects_hardware_profile(isolated_qt, installed_app, monkeypatch):
    writer = replace(installed_app, id="writer.desktop", name="Writer", wm_class="Writer")
    window = gui.MainWindow(connect_worker=False, applications=[installed_app, writer])
    window.profiles["editor"] = custom_profile()
    window.profiles["writer"] = {**custom_profile("writer"), "desktop_ids": [writer.id], "wm_classes": ["Writer"]}
    window.settings["launchers"] = [installed_app.id, writer.id] + [None] * 7
    window.settings["profile_assignments"] = {installed_app.id: "editor", writer.id: "writer"}
    window.persist()
    window._populate_apps()
    assert [profile for profile, _ in window.profile_chips] == [None, "editor", "writer"]
    window.move_launcher(0, 1)
    assert [profile for profile, _ in window.profile_chips] == [None, "writer", "editor"]
    window.assign_profile(installed_app.id, None)
    assert [profile for profile, _ in window.profile_chips] == [None, "writer"]

    request = Mock(return_value={"ok": True})
    monkeypatch.setattr(gui.ipc, "request", request)
    window.connect_worker = True
    window.profile_chips[1][1].click()
    assert window.page == 1 and window.preview_profile_id == "writer"
    assert request.call_args_list == [call("profile", id="writer"), call("page", index=1)]
    assert window.profile_chips[1][1].isChecked()
    window.profile_chips[0][1].click()
    assert window.page == 0 and window.profile_chips[0][1].isChecked()


def test_real_window_apply_launcher_and_original_icon(isolated_qt, installed_app):
    _, warnings = isolated_qt
    window = gui.MainWindow(connect_worker=False, applications=[installed_app])
    window.show()
    window.select_key(6)
    choose_application(window, installed_app.id)
    QTest.mouseClick(window.apply_button, Qt.MouseButton.LeftButton)
    assert not warnings
    assert window.key_buttons[1].toolTip() == "Editor"
    assert window.selected_slot == 1
    settings = json.loads(paths.settings_path().read_text())
    assert settings["launchers"] == ["@screenshot", "editor.desktop"] + [None] * 7
    key = json.loads(paths.runtime_path().read_text())["pages"][0]["keys"][1]
    assert key["action"] == {"type": "launch", "desktop_id": "editor.desktop",
                             "wm_classes": ["editor"], "profile_id": None}
    image_path = paths.cache_dir() / key["image"]
    assert image_path.read_bytes().startswith(b"\xff\xd8")
    image = QImage(str(image_path))
    assert (image.width(), image.height()) == (118, 118)
    color = image.pixelColor(59, 47)
    assert color.red() > 180 and color.green() < 70 and color.blue() < 70
    assert not window.timer.isActive()


def test_action_library_search_and_add_preserve_layout_until_apply(isolated_qt, installed_app):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    before = paths.settings_path().read_bytes(), paths.runtime_path().read_bytes()
    window.begin_add_launcher()
    assert window.selected_slot == 1  # Screenshot already occupies the first key.
    window.launcher_search.setText("EDITOR.DESKTOP")
    visible = [window.launcher_actions.item(row) for row in range(window.launcher_actions.count())
               if not window.launcher_actions.item(row).isHidden()]
    assert [item.text() for item in visible] == [installed_app.name]
    window.launcher_actions.setCurrentItem(visible[0])
    assert window.selected_app() == installed_app
    assert (paths.settings_path().read_bytes(), paths.runtime_path().read_bytes()) == before
    window.apply_button.click()
    assert window.settings["launchers"][:2] == ["@screenshot", installed_app.id]
    assert window.launcher_actions.currentItem().data(Qt.ItemDataRole.UserRole) == installed_app.id
    window.launcher_search.setText("No matching action")
    assert not window.launcher_no_results.isHidden()
    window.change_page(1)
    assert window.key_picker.kind_combo.currentData() == "shortcut"
    assert window.key_picker.label_edit.text()
    assert window.key_picker.keys_edit.text()
    assert set(window.key_picker.category_buttons) == {"launch", "shortcut", "system"}


def test_custom_key_actions_survive_reordering_and_reopening(isolated_qt, installed_app):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    window.select_key(1)
    shortcut = {"label": "Save", "icon": "document-save", "action": {"type": "shortcut", "keys": "Ctrl+S"}}
    window.key_picker.set_entry(shortcut)
    window.apply_button.click()
    assert window.settings["launchers"][1] == shortcut
    assert window.runtime["pages"][0]["keys"][1]["action"] == shortcut["action"]
    window.move_launcher(1, 0)
    assert window.settings["launchers"][0] == shortcut
    reopened = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    reopened.select_key(0)
    assert reopened.key_picker.value() == shortcut
    reopened.clear_key_button.click()
    assert reopened.settings["launchers"] == ["@screenshot"] + [None] * 8


def test_profile_keys_accept_application_system_and_shortcut_actions(isolated_qt, installed_app):
    _, warnings = isolated_qt
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    window.change_page(1)
    original = window.profiles["default"]["shortcuts"][:]
    window.select_key(0)
    choose_application(window, installed_app.id)
    window.apply_button.click()
    saved = load_profiles()["default"]
    assert saved["shortcuts"][0]["action"] == {"type": "launch", "desktop_id": installed_app.id}
    assert saved["shortcuts"][1:] == original[1:]
    assert window.runtime["pages"][1]["keys"][0]["action"]["desktop_id"] == installed_app.id
    window.key_picker.set_entry({"label": "Screenshot", "icon": "applets-screenshooter", "action": {"type": "screenshot"}})
    window.apply_button.click()
    assert window.runtime["pages"][1]["keys"][0]["action"] == {"type": "screenshot"}
    window.clear_key_button.click()
    assert window.runtime["pages"][1]["keys"][0] is None
    assert not warnings


def test_profile_action_save_failure_restores_previous_profile(isolated_qt, installed_app, monkeypatch):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    window.change_page(1)
    original = window.profiles["default"]
    before = paths.settings_path().read_bytes(), paths.runtime_path().read_bytes()
    monkeypatch.setattr(window, "persist", Mock(side_effect=OSError("Disk unavailable")))
    window.key_picker.set_entry({"label": "Screenshot", "icon": "applets-screenshooter", "action": {"type": "screenshot"}})
    window.apply_button.click()
    assert window.profiles["default"] == original
    assert not (paths.profiles_dir() / "default.json").exists()
    assert (paths.settings_path().read_bytes(), paths.runtime_path().read_bytes()) == before


def test_device_action_replaces_legacy_ring_binding_and_can_be_removed(isolated_qt, installed_app):
    from mxcc.device_controls import controls_for_device
    from mxcc.devices import Device
    target = {"vendor_id": 0x046d, "product_id": 0xbc00, "control_id": "button:3"}
    device = Device("dialpad", "MX Dialpad", "dialpad", "Bluetooth", True, False, 0x046d, 0xbc00,
                    controls=controls_for_device(vendor_id=0x046d, product_id=0xbc00, kind="dialpad"))
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[device])
    window.settings["action_ring"]["bindings"] = [target]
    window.persist()
    window._refresh_device_actions()
    window.open_device(device.id)
    shortcut = {"label": "Save", "icon": "document-save", "action": {"type": "shortcut", "keys": "Ctrl+S"}}
    window.assign_device_action("button:3", shortcut)
    assert window.settings["action_ring"]["bindings"] == []
    assert window.settings["device_actions"] == [{**target, **shortcut}]
    assert window.runtime["device_actions"] == [{**target, "action": shortcut["action"]}]
    window.assign_device_action("button:3", None)
    assert window.settings["device_actions"] == window.runtime["device_actions"] == []


def test_assigned_applications_follow_keypad_order_and_preserve_shortcuts(isolated_qt, installed_app):
    writer = replace(installed_app, id="writer.desktop", name="Writer")
    unused = replace(installed_app, id="unused.desktop", name="Unused")
    window = gui.MainWindow(connect_worker=False, applications=[installed_app, writer, unused])
    window.profiles["editor"] = custom_profile()
    window.settings["launchers"] = [writer.id, "@screenshot", installed_app.id, writer.id, "missing.desktop"] + [None] * 4
    window.settings["profile_assignments"][installed_app.id] = "editor"
    window.persist()
    window._populate_apps()
    assert list(window.application_cards) == [writer.id, "@screenshot", installed_app.id]
    assert window.application_cards[writer.id].name_label.text() == "Writer"
    screenshot = window.application_cards["@screenshot"]
    assert screenshot.profile_combo is screenshot.import_button is screenshot.edit_button is None
    assert window.app_combo.findData(unused.id) >= 0
    window.search.setText("Editor")
    assert [card.isHidden() for card in window.application_cards.values()] == [True, True, False]
    window.search.clear()
    window.move_launcher(2, 0)
    assert list(window.application_cards) == [installed_app.id, writer.id, "@screenshot"]
    window.select_key(0)
    window.apply_key()
    assert window.settings["profile_assignments"][installed_app.id] == "editor"
    window.clear_key_button.click()
    assert list(window.application_cards) == [writer.id, "@screenshot"]
    assert set(window.key_picker.category_buttons) == {"launch", "shortcut", "system"}


def test_application_cards_route_actions_and_distinguish_search_from_empty_layout(isolated_qt, installed_app, monkeypatch):
    imported, edited = [], []
    monkeypatch.setattr(gui.MainWindow, "import_shortcuts", lambda self, checked=False, *, application=None: imported.append(application))
    monkeypatch.setattr(gui.MainWindow, "edit_shortcuts", lambda self, checked=False, *, application=None: edited.append(application))
    writer = replace(installed_app, id="writer.desktop", name="Writer")
    window = gui.MainWindow(connect_worker=False, applications=[installed_app, writer])
    window.settings["launchers"] = [installed_app.id, writer.id] + [None] * 7
    window.persist()
    window._populate_apps()
    window.application_cards[writer.id].import_button.click()
    window.application_cards[installed_app.id].edit_button.click()
    assert imported == [writer]
    assert edited == [installed_app]
    assert window.applications_empty.isHidden()

    window.search.setText("WRITER.DESKTOP")
    assert window.application_cards[installed_app.id].isHidden()
    assert not window.application_cards[writer.id].isHidden()
    window.search.setText("No matching installed application")
    assert all(card.isHidden() for card in window.application_cards.values())
    assert not window.applications_empty.isHidden()
    search_message = window.applications_empty_title.text()
    assert search_message
    window.search.clear()
    assert window.applications_empty.isHidden()
    assert all(not card.isHidden() for card in window.application_cards.values())

    window.settings["launchers"] = [None] * 9
    window.persist()
    window._populate_apps()
    assert not window.application_cards
    assert not window.applications_empty.isHidden()
    assert window.applications_empty_title.text()
    assert window.applications_empty_title.text() != search_message


def test_renderer_caches_original_artwork_and_label_preference(isolated_qt, installed_app):
    with_labels = FaceRenderer(True)
    first = with_labels("Editor", "unavailable-icon", installed_app)
    assert with_labels("Editor", "unavailable-icon", installed_app) == first
    second = FaceRenderer(False)("Editor", "unavailable-icon", installed_app)
    assert second != first
    assert QImage(str(paths.cache_dir() / second)).size().width() == 118


def test_icons_only_never_renders_names_when_artwork_is_missing(isolated_qt):
    renderer = FaceRenderer(False)
    missing = "mxcc-intentionally-missing-test-icon"
    assert renderer("First app", missing) == renderer("Different app", missing)
    assert FaceRenderer(True)("First app", missing) != renderer("First app", missing)


def test_shortcut_import_assigns_profile_and_can_disable_it(isolated_qt, installed_app, monkeypatch, tmp_path):
    _, warnings = isolated_qt
    source = tmp_path / "editor.json"
    source.write_text(json.dumps(custom_profile()))
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *_: (str(source), ""))
    window = gui.MainWindow(connect_worker=False, applications=[installed_app])
    choose_application(window, installed_app.id)
    window.apply_key()
    window.import_shortcuts(application=installed_app)
    assert not warnings
    assert window.settings["profile_assignments"][installed_app.id] == "editor"
    assert window.application_cards[installed_app.id].profile_combo.currentData() == "editor"
    assert window.runtime["profiles"][0]["page"]["keys"][0]["action"] == {"type": "shortcut", "keys": "Ctrl+S"}
    combo = window.application_cards[installed_app.id].profile_combo
    combo.setCurrentIndex(combo.findData(None))
    assert window.settings["profile_assignments"][installed_app.id] is None
    assert window.runtime["profiles"] == []
    combo.setCurrentIndex(combo.findData("__auto__"))
    assert installed_app.id not in window.settings["profile_assignments"]
    assert window.runtime["profiles"][0]["page"]["keys"][0]["action"] == {"type": "shortcut", "keys": "Ctrl+S"}


def test_existing_profile_import_requires_confirmed_replacement(isolated_qt, installed_app, monkeypatch, tmp_path):
    _, warnings = isolated_qt
    source = tmp_path / "firefox.json"
    source.write_text(json.dumps(custom_profile("firefox")))
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *_: (str(source), ""))
    questions = []
    def confirm(*args):
        questions.append(args[1])
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", confirm)
    window = gui.MainWindow(connect_worker=False, applications=[installed_app])
    window.import_shortcuts(application=installed_app)
    assert questions == ["Replace shortcut profile?"]
    assert not warnings
    assert load_profiles()["firefox"]["name"] == "Editor actions"


def test_profile_editor_saves_normalized_shortcuts_and_rejects_partial_row(isolated_qt, installed_app):
    _, warnings = isolated_qt
    editor = ProfileEditor(installed_app, None)
    editor.name.setText("My editor")
    editor.table.item(0, 0).setText("Save")
    editor.table.item(0, 1).setText("control+s")
    editor.save()
    assert editor.result() == QDialog.DialogCode.Accepted
    assert editor.result_profile["shortcuts"][0]["keys"] == "Ctrl+S"
    assert (paths.profiles_dir() / (editor.result_profile["id"] + ".json")).exists()
    broken = ProfileEditor(installed_app, editor.result_profile)
    broken.table.item(0, 1).setText("")
    broken.save()
    assert broken.result_profile is None
    assert warnings[-1][0] == "Profile could not be saved"
    assert load_profiles()[editor.result_profile["id"]]["shortcuts"][0]["keys"] == "Ctrl+S"


@pytest.mark.parametrize(("preview_id", "expected_app_id"), [("vscode", "code.desktop"), ("default", None)])
def test_editing_preview_uses_its_profile_without_reassigning_launcher(isolated_qt, installed_app, monkeypatch, preview_id, expected_app_id):
    _, warnings = isolated_qt
    chatgpt = replace(installed_app, id="chatgpt.desktop", name="ChatGPT", wm_class="ChatGPT")
    code = replace(installed_app, id="code.desktop", name="VS Code", wm_class="code")
    window = gui.MainWindow(connect_worker=False, applications=[chatgpt, code])
    assert window.selected_app().id == "chatgpt.desktop"
    window.change_page(1)
    window.preview_profile_id = preview_id
    captured = []
    def edit_and_save(editor):
        captured.append((editor.application.id if editor.application else None, editor.profile["id"]))
        editor.name.setText("Edited preview")
        editor.save()
        return editor.result()
    monkeypatch.setattr(ProfileEditor, "exec", edit_and_save)
    window.edit_shortcuts()
    assert captured == [(expected_app_id, preview_id)]
    assert not warnings
    assert window.settings["profile_assignments"] == {}
    assert load_profiles()[preview_id]["name"] == "Edited preview"


def test_window_export_import_restores_layout_but_keeps_local_startup_preference(isolated_qt, installed_app, monkeypatch, tmp_path):
    _, warnings = isolated_qt
    source = tmp_path / "portable.json"
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *_: (str(source), ""))
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *_: (str(source), ""))
    window = gui.MainWindow(connect_worker=False, applications=[installed_app])
    window.export_configuration()
    bundle = json.loads(source.read_text())
    bundle["settings"]["launchers"][8] = installed_app.id
    bundle["settings"].update(auto_switch=False, show_labels=False, start_at_login=True)
    source.write_text(json.dumps(bundle))
    window.import_configuration()
    assert not warnings
    assert window.settings["launchers"] == ["@screenshot", installed_app.id] + [None] * 7
    assert window.runtime["pages"][0]["keys"][1]["label"] == "Editor"
    assert not window.auto_switch.isChecked()
    assert not window.show_labels.isChecked()
    assert not window.start_at_login.isChecked()
    assert not window.settings["start_at_login"]


def test_permission_dialog_requires_consent_and_launches_only_fixed_helper(isolated_qt, installed_app, monkeypatch):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app])
    window.show_settings()
    assert [window.navigation.item(row).text() for row in range(window.navigation.count())] == ["Keys", "Applications"]
    assert window.settings_page.isAncestorOf(window.repair_button)
    helper = Path("/usr/lib/mx-creative-control/setup-permissions")
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: True if path == helper else real_is_file(path))
    process = Mock()
    process_factory = Mock(return_value=process)
    monkeypatch.setattr(gui, "QProcess", process_factory)
    question = Mock(side_effect=[QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Ok])
    monkeypatch.setattr(QMessageBox, "question", question)

    window.setup_permissions()
    process_factory.assert_not_called()
    assert window.repair_button.isEnabled()
    assert "/dev/uinput" in question.call_args.args[2]
    assert "administrator authentication" in question.call_args.args[2]

    window.setup_permissions()
    process_factory.assert_called_once_with(window)
    process.start.assert_called_once_with("/usr/bin/pkexec", [str(helper)])
    assert not window.repair_button.isEnabled()

    # A cancelled polkit prompt must leave setup available for another attempt.
    finished_callback = process.finished.connect.call_args.args[0]
    finished_callback(126, 0)
    assert window.repair_button.isEnabled()
    assert "cancelled" in window.notice.text()
    assert "cancelled" in window.permissions_notice.text()


def connected_status():
    return {"ok": True, "connected": True, "keypad_access": True, "input_access": True,
            "focus_supported": True, "tray_available": True, "active_app": "Editor", "paused": False}


def test_master4_haptic_uses_its_own_backend_status(isolated_qt, installed_app, monkeypatch):
    from mxcc.device_controls import controls_for_device
    from mxcc.devices import Device
    device = Device("master4", "MX Master 4", "mouse", "Bluetooth", True, False, 0x046d, 0xb042,
                    controls=controls_for_device(vendor_id=0x046d, product_id=0xb042, kind="mouse"))
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[device])
    window.open_device(device.id)
    status = connected_status() | {
        "device_buttons": {"errors": ["Standard button capture unavailable"]},
        "mx_master4_buttons": {"errors": ["Haptic panel access required"], "permission_required": True},
    }
    monkeypatch.setattr(gui.ipc, "request", Mock(return_value=status))
    window.details.select_control("hidpp:416")
    window.refresh_status()
    assert window.details._backend_error == "Haptic panel access required"
    assert "Permission required" in window.permission_labels["master4"].text()
    assert window.details.picker.isEnabled() and window.details.apply_button.isEnabled()
    window.details.select_control("key:278")
    window.refresh_status()
    assert window.details._backend_error == "Standard button capture unavailable"
    status["mx_master4_buttons"] = {"errors": [], "active": [
        {"vendor_id": 0x046d, "product_id": 0xb042, "control_id": "hidpp:416"}]}
    window.details.select_control("hidpp:416")
    window.refresh_status()
    assert window.details._backend_error == ""
    assert window.permission_labels["master4"].text() == "Ready"


def test_window_startup_reloads_running_worker_and_selects_apps(isolated_qt, installed_app, monkeypatch):
    application, warnings = isolated_qt
    ensure = Mock(return_value=connected_status())
    request = Mock(side_effect=[{"ok": True}, {"ok": True}, connected_status()])
    monkeypatch.setattr(gui.service, "ensure_running", ensure)
    monkeypatch.setattr(gui.ipc, "request", request)
    window = gui.MainWindow(connect_worker=True, applications=[installed_app])
    application.processEvents()
    window.timer.stop()
    ensure.assert_called_once_with()
    assert request.call_args_list == [call("reload"), call("page", index=0), call("status")]
    assert window.page == 0
    assert window.page_buttons[0].isChecked()
    assert window.connection.text() == "Keypad connected"
    assert not warnings


def test_reopening_existing_window_recovers_lost_worker(isolated_qt, installed_app, monkeypatch):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app])
    window.change_page(1)
    window.connect_worker = True
    request = Mock(side_effect=gui.ipc.WorkerUnavailable("worker socket disappeared"))
    monkeypatch.setattr(gui.ipc, "request", request)
    window.refresh_status()
    assert window.connection.text() == "Worker not running"
    assert "worker socket disappeared" in window.notice.text()
    ensure = Mock(return_value=connected_status())
    monkeypatch.setattr(gui.service, "ensure_running", ensure)
    request.reset_mock(side_effect=True)
    request.side_effect = [{"ok": True}, {"ok": True}, connected_status()]

    window.activate_settings()

    ensure.assert_called_once_with()
    assert request.call_args_list == [call("reload"), call("page", index=0), call("status")]
    assert window.page == 0
    assert window.connection.text() == "Keypad connected"
    assert window.permission_labels["keypad"].text() == "Ready"
    assert window.notice.text() == ""


def test_ring_plus_opens_captured_layout_without_resetting_worker_page(isolated_qt, installed_app, monkeypatch):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[])
    window.ring_editor.add_layout(installed_app.id)
    before = paths.runtime_path().read_bytes()
    window.connect_worker = True
    status = connected_status() | {"page": 1}
    request = Mock(return_value=status)
    monkeypatch.setattr(gui.ipc, "request", request)
    monkeypatch.setattr(gui.service, "ensure_running", Mock(return_value=status))

    window.activate_settings(ring_layout=installed_app.id, add_ring_action=True)

    assert window.views.currentWidget() is window.ring_page
    assert window.ring_editor.current_layout == installed_app.id
    assert window.page == 1
    assert request.call_args_list == [call("status")]
    assert paths.runtime_path().read_bytes() == before


def test_ring_editor_initial_navigation_skips_home(isolated_qt, installed_app):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app], devices=[],
                            ring_layout="default", add_ring_action=True)
    assert window.views.currentWidget() is window.ring_page
    assert window.ring_editor.current_layout == "default"


@pytest.mark.parametrize("failure_stage", ["ensure", "reload"])
def test_worker_startup_failure_surfaces_error_without_claiming_connected(isolated_qt, installed_app, monkeypatch, failure_stage):
    window = gui.MainWindow(connect_worker=False, applications=[installed_app])
    window.change_page(1)
    window.connect_worker = True
    error = "Another keypad controller owns the device" if failure_stage == "ensure" else "Runtime configuration could not be loaded"
    ensure = Mock(return_value=connected_status())
    request = Mock(return_value={"ok": False, "error": error})
    if failure_stage == "ensure":
        ensure.side_effect = RuntimeError(error)
    monkeypatch.setattr(gui.service, "ensure_running", ensure)
    monkeypatch.setattr(gui.ipc, "request", request)

    window.start_worker()

    assert window.connection.text() == "Keypad setup failed"
    assert window.notice.text() == error
    assert window.status == {}
    assert window.page == 1
    assert "Not checked" in window.permission_labels["keypad"].text()
    if failure_stage == "ensure":
        request.assert_not_called()
    else:
        request.assert_called_once_with("reload")
    # Merely seeing a live process later does not mean it accepted this layout.
    request.return_value = connected_status()
    window.refresh_status()
    assert window.connection.text() == "Layout not applied"
    assert window.notice.text() == error

    ensure.side_effect = None
    window.start_worker()
    assert window.connection.text() == "Keypad connected"
    assert window.notice.text() == ""


def launcher_window(installed_app):
    apps = [replace(installed_app, id=f'{name}.desktop', name=name.title())
            for name in ('chatgpt', 'code', 'firefox')]
    return gui.MainWindow(connect_worker=False, applications=apps), apps


def test_drag_reorders_preview_runtime_and_saved_settings(isolated_qt, installed_app, monkeypatch):
    _, warnings = isolated_qt
    window, apps = launcher_window(installed_app)
    before = json.loads(paths.runtime_path().read_text())
    images = {entry['label']: entry['image'] for entry in before['pages'][0]['keys'] if entry}
    request = Mock(return_value={'ok': True})
    monkeypatch.setattr(gui.ipc, 'request', request)
    window.connect_worker = True
    window.key_buttons[0].moved.emit(0, 2)
    expected = ['code.desktop', 'firefox.desktop', 'chatgpt.desktop', '@screenshot'] + [None] * 5
    assert window.settings['launchers'] == expected
    assert json.loads(paths.settings_path().read_text())['launchers'] == expected
    assert [key.toolTip() for key in window.key_buttons[:4]] == ['Code', 'Firefox', 'Chatgpt', 'Screenshot']
    assert window.selected_slot == 2
    assert window.app_combo.currentData() == 'chatgpt.desktop'
    after = json.loads(paths.runtime_path().read_text())
    assert after['pages'][0]['keys'][2]['action']['desktop_id'] == 'chatgpt.desktop'
    assert {entry['label']: entry['image'] for entry in after['pages'][0]['keys'] if entry} == images
    assert after['pages'][1] == before['pages'][1]
    assert after['profiles'] == before['profiles']
    request.assert_called_once_with('reload')
    reopened = gui.MainWindow(connect_worker=False, applications=apps)
    assert reopened.settings['launchers'] == expected
    assert not warnings


def test_drag_backward_and_to_empty_key_keep_apps_together(isolated_qt, installed_app):
    window, _ = launcher_window(installed_app)
    window.move_launcher(2, 0)
    assert window.settings['launchers'][:4] == ['firefox.desktop', 'chatgpt.desktop', 'code.desktop', '@screenshot']
    window.move_launcher(0, 8)
    assert window.settings['launchers'] == ['chatgpt.desktop', 'code.desktop', '@screenshot', 'firefox.desktop'] + [None] * 5
    assert window.selected_slot == 3
    assert all(key is None for key in window.runtime['pages'][0]['keys'][4:])


def test_clearing_app_and_rescanning_close_gaps(isolated_qt, installed_app, monkeypatch):
    window, apps = launcher_window(installed_app)
    window.select_key(1)
    window.clear_key_button.click()
    assert window.settings['launchers'] == ['chatgpt.desktop', 'firefox.desktop', '@screenshot'] + [None] * 6
    monkeypatch.setattr(gui, 'discover_applications', lambda: apps[1:])
    window.rescan()
    assert window.settings['launchers'] == ['firefox.desktop', '@screenshot', 'chatgpt.desktop'] + [None] * 6
    keys = window.runtime['pages'][0]['keys']
    assert [entry['label'] for entry in keys if entry] == ['Firefox', 'Screenshot']
    assert all(entry is None for entry in keys[2:])
    monkeypatch.setattr(gui, 'discover_applications', lambda: apps)
    window.rescan()
    assert [entry['label'] for entry in window.runtime['pages'][0]['keys'] if entry] == ['Firefox', 'Screenshot', 'Chatgpt']


def test_empty_invalid_and_shortcut_drags_do_not_save(isolated_qt, installed_app, monkeypatch):
    window, _ = launcher_window(installed_app)
    save = Mock(wraps=gui.save_settings)
    monkeypatch.setattr(gui, 'save_settings', save)
    for source, destination in ((8, 0), (0, 0), (-1, 0), (0, 9), (True, 0), ('0', 1)):
        window.move_launcher(source, destination)
    window.change_page(1)
    window.move_launcher(0, 2)
    assert all(not key.acceptDrops() for key in window.key_buttons)
    save.assert_not_called()


@pytest.mark.parametrize('failed_write', ['save_settings', 'atomic_json'])
def test_failed_reorder_keeps_previous_order(isolated_qt, installed_app, monkeypatch, failed_write):
    _, warnings = isolated_qt
    window, _ = launcher_window(installed_app)
    before = paths.settings_path().read_bytes(), paths.runtime_path().read_bytes()
    original = window.settings['launchers'][:]
    monkeypatch.setattr(gui, failed_write, Mock(side_effect=OSError('Disk unavailable')))
    window.move_launcher(0, 2)
    assert window.settings['launchers'] == original
    assert window.selected_slot == 0
    assert before == (paths.settings_path().read_bytes(), paths.runtime_path().read_bytes())
    assert warnings == [('Could not save order', 'Disk unavailable')]


def test_worker_rejection_preserves_saved_order_and_reports_sync_error(isolated_qt, installed_app, monkeypatch):
    window, _ = launcher_window(installed_app)
    window.connect_worker = True
    monkeypatch.setattr(gui.ipc, 'request', Mock(return_value={'ok': False, 'error': 'Layout rejected'}))
    window.move_launcher(0, 2)
    assert window.selected_slot == 2
    assert json.loads(paths.settings_path().read_text())['launchers'] == window.settings['launchers']
    assert window.worker_sync_error == 'Layout rejected'
    assert 'saved, but not applied' in window.notice.text()
