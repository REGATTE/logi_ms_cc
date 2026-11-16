"""Shared actions remain portable and execute only validated declarative types."""

import copy
import json
import os
from pathlib import Path

import pytest

from mxcc import paths
from mxcc.actions import (launcher_available, launcher_entry, profile_entry_action,
                          validate_action_entry, validate_device_actions)
from mxcc.applications import Application
from mxcc.configuration import compact_launchers, compile_runtime, default_settings, save_settings, validate_settings
from mxcc.profiles import validate_profile
from mxcc.transfer import export_bundle, read_bundle


_APP = None


def application(identifier="editor.desktop"):
    return Application(identifier, "Editor", "/installed/icons/editor.png", "editor %U", "Editor",
                       Path("/installed") / identifier)


def entry(kind="shortcut", **fields):
    return {"label": "My action", "icon": "input-keyboard", "action": {"type": kind, **fields}}


def target(**changes):
    return {"vendor_id": 0x046d, "product_id": 0xbc00, "control_id": "button:3", **changes}


def test_custom_launchers_compact_without_losing_missing_apps_or_shortcut_order():
    app = application()
    shortcut = entry(keys="control+s")
    missing = entry("launch", desktop_id="missing.desktop")
    launchers = [missing, None, shortcut, app.id, "@screenshot", "@action-ring", None, None, None]
    before = copy.deepcopy(launchers)
    packed = compact_launchers(launchers, iter([app]))
    assert packed == [shortcut, app.id, "@screenshot", "@action-ring", missing] + [None] * 4
    assert launchers == before
    assert launcher_available(app.id, iter([app]))
    assert not launcher_available(missing, [app])
    settings = default_settings([app]) | {"launchers": launchers}
    rendered = []
    def render(label, icon, application):
        rendered.append((label, icon, application))
        return "face.jpg"
    runtime = compile_runtime(settings, [app], {}, render)
    keys = runtime["pages"][0]["keys"]
    assert [key["action"]["type"] for key in keys if key] == ["shortcut", "launch", "screenshot", "open_ring"]
    assert keys[0]["action"]["keys"] == "Ctrl+S"
    assert keys[1]["action"]["wm_classes"] == ["editor"]
    assert (app.name, app.icon, app) in rendered
    assert validate_settings(settings)["launchers"][2]["action"]["keys"] == "Ctrl+S"


def test_device_assignments_export_with_portable_artwork_and_compile_local_identity(tmp_path):
    app = application()
    stored = launcher_entry(app.id, [app])
    assert stored["icon"] == "application-x-executable"
    settings = default_settings([app])
    settings["device_actions"] = [target() | stored,
                                  target(control_id="button:1") | entry(keys="Ctrl+S"),
                                  target(control_id="button:2") | entry("screenshot"),
                                  target(control_id="button:4") | entry("launch", desktop_id="missing.desktop")]
    source = tmp_path / "export.json"
    export_bundle(source, settings, {})
    restored, profiles = read_bundle(source)
    assert restored == settings and profiles == {}
    runtime = compile_runtime(restored, [app], profiles, lambda *_: "face.jpg")
    assert len(runtime["device_actions"]) == 3
    assert runtime["device_actions"][0] == target() | {
        "action": {"type": "launch", "desktop_id": app.id, "wm_classes": ["editor"], "profile_id": None}}
    assert runtime["device_actions"][1]["action"] == {"type": "shortcut", "keys": "Ctrl+S"}
    assert runtime["device_actions"][2]["action"] == {"type": "screenshot"}
    assert "missing.desktop" in source.read_text()
    assert app.icon not in source.read_text()


def test_master4_haptic_ring_assignment_survives_export_and_runtime_compilation(tmp_path):
    panel = target(product_id=0xb042, control_id="hidpp:416")
    assignment = panel | entry("open_ring")
    settings = default_settings([])
    settings["device_actions"] = [assignment]
    source = tmp_path / "haptic.json"
    export_bundle(source, settings, {})
    restored, profiles = read_bundle(source)
    assert restored["device_actions"] == [assignment]
    runtime = compile_runtime(restored, [], profiles, lambda *_: "face.jpg")
    assert runtime["device_actions"] == [panel | {"action": {"type": "open_ring"}}]


@pytest.mark.parametrize("value", [
    entry("command", command="arbitrary command"),
    entry("launch", desktop_id="../unsafe.desktop"),
    entry("shortcut", keys="Ctrl+C", command="arbitrary command"),
    entry("shortcut", keys="Ctrl+C") | {"icon": "/tmp/custom.png"},
])
def test_unsupported_actions_and_executable_fields_are_rejected(value):
    with pytest.raises(ValueError):
        validate_action_entry(value)


@pytest.mark.parametrize("changes", [
    {"vendor_id": True}, {"control_id": "dial:main"},
    {"product_id": 0xc07e, "control_id": "button:1"},
    {"product_id": 0x1234, "control_id": "key:279"},
    {"product_id": 0xb042, "control_id": "hidpp:196"},
    {"product_id": 0xb042, "control_id": "hidpp:0416"},
    {"product_id": 0xc07e, "control_id": "hidpp:416"},
    {"vendor_id": 0x1234, "product_id": 0xb042, "control_id": "hidpp:416"},
])
def test_unmapped_or_invalid_device_targets_are_rejected(changes):
    with pytest.raises(ValueError):
        validate_device_actions([target(**changes) | entry(keys="Ctrl+C")])


def test_duplicate_targets_cannot_execute_both_legacy_ring_and_new_action():
    assignment = target() | entry("open_ring")
    assert validate_device_actions([assignment]) == [assignment]
    for assignments, legacy in (([assignment, assignment], []), ([assignment], [target()])):
        with pytest.raises(ValueError, match="Duplicate"):
            validate_device_actions(assignments, legacy)
    with pytest.raises(ValueError):
        validate_action_entry(entry("open_ring"), allow_ring=False)


def test_mixed_profile_actions_preserve_slots_and_legacy_keyboard_schema():
    app = application()
    profile = {"version": 1, "id": "default", "name": "Mixed", "shortcuts": [
        {"slot": 2, "label": "Save", "keys": "control+s"},
        {"slot": 4, **launcher_entry(app.id, [app])},
        {"slot": 8, **entry("open_ring")},
    ]}
    normalized = validate_profile(profile)
    assert normalized["shortcuts"][0]["keys"] == "Ctrl+S"
    assert "action" not in normalized["shortcuts"][0]
    assert profile_entry_action(normalized["shortcuts"][0])["action"] == {"type": "shortcut", "keys": "Ctrl+S"}
    runtime = compile_runtime(default_settings([app]), [app], {"default": normalized}, lambda *_: "face.jpg")
    keys = runtime["pages"][1]["keys"]
    assert [i + 1 for i, key in enumerate(keys) if key] == [2, 4, 8]
    assert [key["action"]["type"] for key in keys if key] == ["shortcut", "launch", "open_ring"]
    invalid = copy.deepcopy(profile)
    invalid["shortcuts"][1]["keys"] = "Ctrl+C"
    with pytest.raises(ValueError):
        validate_profile(invalid)


def test_legacy_settings_gain_empty_device_actions_without_writes(monkeypatch, tmp_path):
    settings = default_settings([])
    settings.pop("device_actions")
    destination = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: destination)
    normalized = validate_settings(settings)
    assert normalized["device_actions"] == []
    assert not destination.exists()
    save_settings(normalized)
    assert json.loads(destination.read_text())["device_actions"] == []


def test_profile_dialog_preserves_mixed_actions_while_editing_shortcuts(monkeypatch, tmp_path):
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QApplication
    from mxcc.profile_editor import ProfileEditor
    _APP = QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "profiles_dir", lambda: tmp_path)
    profile = validate_profile({"version": 1, "id": "mixed-dialog", "name": "Mixed", "shortcuts": [
        {"slot": 1, "label": "Save", "keys": "Ctrl+S"},
        {"slot": 2, **entry("screenshot")},
    ]})
    dialog = ProfileEditor(None, profile)
    assert not dialog.table.item(1, 1).flags() & Qt.ItemFlag.ItemIsEditable
    dialog.table.item(0, 1).setText("Ctrl+Shift+S")
    dialog.save()
    assert dialog.result_profile["shortcuts"][0]["keys"] == "Ctrl+Shift+S"
    assert dialog.result_profile["shortcuts"][1] == profile["shortcuts"][1]
    dialog.close()
    dialog.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()
