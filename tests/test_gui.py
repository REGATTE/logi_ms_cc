"""Exercise real Qt widgets without a display, worker, or user configuration."""

import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
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
    yield _APP, warnings
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
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


def test_real_window_apply_launcher_and_original_icon(isolated_qt, installed_app):
    _, warnings = isolated_qt
    window = gui.MainWindow(connect_worker=False, applications=[installed_app])
    window.show()
    window.select_key(6)
    window.app_combo.setCurrentIndex(window.app_combo.findData(installed_app.id))
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
    window.import_shortcuts(application=installed_app)
    assert not warnings
    assert window.settings["profile_assignments"][installed_app.id] == "editor"
    assert window.app_table.cellWidget(0, 1).currentData() == "editor"
    assert window.runtime["profiles"][0]["page"]["keys"][0]["action"] == {"type": "shortcut", "keys": "Ctrl+S"}
    combo = window.app_table.cellWidget(0, 1)
    combo.setCurrentIndex(combo.findData(None))
    assert window.settings["profile_assignments"][installed_app.id] is None
    assert window.runtime["profiles"] == []


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
    window.preview_profile.setCurrentIndex(window.preview_profile.findData(preview_id))
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


def connected_status():
    return {"ok": True, "connected": True, "keypad_access": True, "input_access": True,
            "focus_supported": True, "tray_available": True, "active_app": "Editor", "paused": False}


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
    window.app_combo.setCurrentIndex(window.app_combo.findData(None))
    window.apply_key()
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
