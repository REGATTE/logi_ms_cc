"""Ring edits retain portable bindings and validate before emitting changes."""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from mxcc.applications import Application
from mxcc.ring_editor import RingEditor

_APP = None


@pytest.fixture
def editor(tmp_path):
    global _APP
    _APP = QApplication.instance() or QApplication(["mxcc-ring-editor-test"])
    path = tmp_path / "original.png"
    icon = QImage(32, 32, QImage.Format.Format_RGB32)
    icon.fill(QColor("#ff0000"))
    assert icon.save(str(path))
    app = Application("editor.desktop", "Editor", str(path), "editor", "Editor", Path("/editor.desktop"))
    settings = {"layouts": {"default": []},
                "bindings": [{"vendor_id": 0x046d, "product_id": 0xc07e, "control_id": "button:4"}]}
    widget = RingEditor(settings, [app])
    widget.resize(1100, 700)
    widget.show()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


def test_app_assignment_uses_original_local_artwork_but_exports_portable_icon(editor):
    changed = []
    editor.changed.connect(lambda: changed.append(True))
    editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("launch"))
    editor.apply_slot()
    entry = editor.value()["layouts"]["default"][0]
    assert entry["action"] == {"type": "launch", "desktop_id": "editor.desktop"}
    assert not entry["icon"].startswith("/")
    assert editor.canvas.slots[0]["icon"] == editor.applications[0].icon
    assert editor.canvas.buttons[0].icon().pixmap(32, 32).toImage().pixelColor(16, 16) == QColor("#ff0000")
    assert changed == [True]


def test_invalid_shortcut_does_not_mutate_settings_or_emit(editor):
    changed = []
    editor.changed.connect(lambda: changed.append(True))
    before = editor.value()
    editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("shortcut"))
    editor.label_edit.setText("Command palette")
    editor.keys_edit.setText("not-a-real-key")
    editor.apply_slot()
    assert editor.value() == before
    assert not changed and not editor.error_label.isHidden()
    editor.keys_edit.setText("Ctrl+Shift+P")
    editor.apply_slot()
    assert editor.value()["layouts"]["default"][0]["action"]["keys"] == "Ctrl+Shift+P"
    assert changed == [True]


def test_per_app_layout_is_independent_and_drag_moves_action(editor):
    editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("screenshot"))
    editor.apply_slot()
    editor.begin_add()
    editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("launch"))
    editor.apply_slot()
    editor.add_layout("editor.desktop")
    assert editor.current_layout == "editor.desktop"
    assert editor.layout_combo.currentData() == "editor.desktop"
    editor.move_slot(0, 1)
    value = editor.value()["layouts"]
    assert value["editor.desktop"][1]["action"] == {"type": "screenshot"}
    assert value["editor.desktop"][0]["action"]["type"] == "launch"
    assert value["default"][0]["action"] == {"type": "screenshot"}
    assert editor.current_slot == 1 and editor.canvas.buttons[1].isChecked()
    editor.clear_slot()
    assert len(editor.value()["layouts"]["editor.desktop"]) == 1
    editor.remove_layout()
    assert editor.current_layout == "default" and editor.layout_combo.currentData() == "default"
    assert list(editor.value()["layouts"]) == ["default"]


def test_ring_json_round_trip_preserves_bindings_and_rejects_bad_import(editor, tmp_path):
    editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("screenshot"))
    editor.apply_slot()
    path = tmp_path / "ring.json"
    editor.export_to(path)
    exported = json.loads(path.read_text())
    assert set(exported) == {"version", "layouts"}
    editor.clear_slot()
    editor.import_from(path)
    assert editor.value()["layouts"]["default"][0]["action"]["type"] == "screenshot"
    assert editor.value()["bindings"][0]["control_id"] == "button:4"
    before = editor.value()
    exported["layouts"]["default"][0]["action"] = {"type": "command", "command": "arbitrary"}
    path.write_text(json.dumps(exported))
    with pytest.raises(ValueError):
        editor.import_from(path)
    assert editor.value() == before


def test_missing_app_remains_editable_without_silently_removing_assignment(editor):
    value = editor.value()
    entry = {"label": "Unavailable", "icon": "application-x-executable",
             "action": {"type": "launch", "desktop_id": "unavailable.desktop"}}
    value["layouts"]["default"].append(entry)
    editor.set_settings({"action_ring": value})
    assert editor.app_combo.currentData() == "unavailable.desktop"
    assert "not installed" in editor.app_combo.currentText()
    editor.apply_slot()
    assert editor.value()["layouts"]["default"][0] == entry
    assert not editor.error_label.isHidden()


def test_discovery_refresh_preserves_unapplied_edits_and_adds_new_apps(editor):
    before = editor.value()
    editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("shortcut"))
    editor.label_edit.setText("Unsaved shortcut")
    editor.keys_edit.setText("Ctrl+Shift+P")
    new = Application("new.desktop", "New", "new", "new", "New", Path("/new.desktop"))
    editor.set_applications([new])
    assert editor.value() == before
    assert editor.kind_combo.currentData() == "shortcut"
    assert editor.label_edit.text() == "Unsaved shortcut"
    assert editor.keys_edit.text() == "Ctrl+Shift+P"
    assert editor.app_combo.findData("new.desktop") >= 0


def test_add_is_a_draft_and_seven_actions_is_a_hard_limit(editor):
    assert editor.value()["layouts"]["default"] == []
    assert all(button.isHidden() for button in editor.canvas.buttons)
    assert not editor.canvas.add_button.isHidden()
    editor.canvas.add_button.click()
    assert editor.value()["layouts"]["default"] == []
    for _ in range(7):
        editor.begin_add()
        editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("screenshot"))
        editor.apply_slot()
    assert len(editor.value()["layouts"]["default"]) == 7
    assert editor.capacity_label.text() == "7 / 7"
    editor.canvas.add_button.click()
    assert "All 7 actions" in editor.selection_hint.text()
    assert editor.current_slot < 7
    editor.apply_slot()
    assert len(editor.value()["layouts"]["default"]) == 7
    editor.clear_slot()
    assert len(editor.value()["layouts"]["default"]) == 6
    editor.begin_add()
    assert editor.current_slot == 6
    assert editor.apply_button.text() == "Add action"


def test_deep_link_selects_original_app_and_opens_its_add_action_editor(editor):
    editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("screenshot"))
    editor.apply_slot()
    editor.open_layout("editor.desktop", add_action=True)
    assert editor.current_layout == "editor.desktop"
    assert editor.current_slot == 1
    assert editor.canvas.selected_slot == -1
    assert editor.apply_button.text() == "Add action"
    editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("launch"))
    editor.apply_slot()
    assert len(editor.value()["layouts"]["editor.desktop"]) == 2
    assert len(editor.value()["layouts"]["default"]) == 1
    editor.open_layout("missing.desktop", add_action=True)
    assert editor.current_layout == "default"


def test_library_search_filters_real_actions_and_import_cannot_exceed_seven(editor, tmp_path):
    editor.search_edit.setText("Editor")
    assert not editor.app_list.item(0).isHidden()
    assert editor.kind_combo.currentData() == "launch"
    editor.search_edit.setText("does-not-exist")
    assert editor.no_results.isVisible()
    assert all(frame.isHidden() for frame in editor.category_frames.values())
    editor.search_edit.clear()
    assert not editor.no_results.isVisible()
    original = editor.value()
    path = tmp_path / "too-many.json"
    entry = {"label": "Capture", "icon": "camera-photo", "action": {"type": "screenshot"}}
    path.write_text(json.dumps({"version": 1, "layouts": {"default": [entry] * 8}}))
    with pytest.raises(ValueError, match="seven"):
        editor.import_from(path)
    assert editor.value() == original
