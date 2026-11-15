"""The shared picker edits drafts without saving or executing them."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from mxcc.action_picker import ActionPicker
from mxcc.applications import Application
from mxcc.customization import CustomizationWorkspace


_APP = None


@pytest.fixture
def picker():
    global _APP
    _APP = QApplication.instance() or QApplication(["mxcc-picker-test"])
    app = Application("editor.desktop", "Editor", "/original.svg", "editor", "Editor", Path("/editor.desktop"))
    workspace = CustomizationWorkspace()
    widget = ActionPicker([app])
    workspace.panel.addWidget(widget)
    workspace.resize(930, 620)
    workspace.show()
    _APP.processEvents()
    yield widget
    workspace.close()
    workspace.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


def test_categories_produce_portable_app_shortcut_and_system_actions(picker):
    assert set(picker.category_buttons) == {"launch", "shortcut", "system"}
    picker.category_buttons["launch"].click()
    assert picker.value() == {"label": "Editor", "icon": "application-x-executable",
                              "action": {"type": "launch", "desktop_id": "editor.desktop"}}
    picker.category_buttons["shortcut"].click()
    picker.label_edit.setText("Save")
    picker.keys_edit.setText("control+s")
    assert picker.value()["action"] == {"type": "shortcut", "keys": "Ctrl+S"}
    picker.category_buttons["system"].click()
    assert picker.value()["action"] == {"type": "screenshot"}
    picker.system_combo.setCurrentIndex(picker.system_combo.findData("open_ring"))
    assert picker.value()["action"] == {"type": "open_ring"}


def test_selection_requires_a_valid_complete_action(picker):
    with pytest.raises(ValueError, match="Choose an action"):
        picker.value()
    picker.category_buttons["shortcut"].click()
    picker.label_edit.setText("Invalid")
    picker.keys_edit.setText("arbitrary shell command")
    with pytest.raises(ValueError):
        picker.value()
    missing = {"label": "Unavailable", "icon": "application-x-executable",
               "action": {"type": "launch", "desktop_id": "missing.desktop"}}
    picker.set_entry(missing)
    assert picker.app_combo.currentData() == "missing.desktop"
    assert "not installed" in picker.app_combo.currentText()
    with pytest.raises(ValueError, match="installed application"):
        picker.value()


def test_discovery_preserves_incomplete_draft_and_does_not_emit_a_change(picker):
    picker.search_edit.setText("Editor")
    picker.category_buttons["shortcut"].click()
    picker.label_edit.setText("Unfinished")
    picker.keys_edit.setText("Ctrl+")
    changes = []
    picker.changed.connect(lambda: changes.append(True))
    picker.set_applications(picker.applications)
    assert picker.kind_combo.currentData() == "shortcut"
    assert picker.label_edit.text() == "Unfinished"
    assert picker.keys_edit.text() == "Ctrl+"
    assert picker.search_edit.text() == "Editor"
    assert not changes


def test_ring_picker_omits_recursive_ring_action(picker):
    ring = ActionPicker(picker.applications, allow_ring=False)
    assert ring.system_combo.count() == 1
    assert ring.kind_combo.findData("open_ring") == -1
    ring.category_buttons["system"].click()
    assert ring.value()["action"] == {"type": "screenshot"}
    ring.deleteLater()
