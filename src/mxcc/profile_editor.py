"""A deliberately small editor for the same JSON profiles users can import."""

import copy
import json
from pathlib import Path
import re
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .profiles import ProfileError, import_profile, validate_profile


class ProfileEditor(QDialog):
    def __init__(self, application, profile: dict | None, parent=None):
        super().__init__(parent)
        self.application = application
        self.result_profile = None
        self.profile = copy.deepcopy(profile) if profile else {
            "version": 1,
            "id": re.sub(r"[^a-z0-9_-]", "-", application.id.lower()).strip("-")[:50] + "-custom",
            "name": application.name,
            "desktop_ids": [application.id],
            "wm_classes": [application.wm_class] if application.wm_class else [],
            "shortcuts": [],
        }
        self.setWindowTitle(f"Shortcuts · {self.profile['name']}")
        self.resize(640, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Edit shortcuts, or import this same format as a JSON profile."))
        self.name = QLineEdit(self.profile["name"])
        self.name.setPlaceholderText("Profile name")
        layout.addWidget(self.name)
        self.classes = QLineEdit(", ".join(self.profile.get("wm_classes", [])))
        self.classes.setPlaceholderText("Window classes for automatic switching, separated by commas")
        self.classes.setToolTip("Use the Active application value shown in the main window.")
        layout.addWidget(self.classes)
        self.table = QTableWidget(9, 3)
        self.table.setHorizontalHeaderLabels(["Label", "Shortcut", "Icon name (optional)"])
        self.table.setVerticalHeaderLabels([str(i) for i in range(1, 10)])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row in range(9):
            for column in range(3):
                self.table.setItem(row, column, QTableWidgetItem(""))
        for shortcut in self.profile.get("shortcuts", []):
            row = shortcut["slot"] - 1
            for column, field in enumerate(("label", "keys", "icon")):
                self.table.item(row, column).setText(shortcut.get(field, ""))
            if "action" in shortcut:
                action = shortcut["action"]
                summary = {"launch": "Open application", "screenshot": "Screenshot", "open_ring": "Action Ring", "shortcut": "Keyboard shortcut"}[action["type"]]
                self.table.item(row, 1).setText(summary)
                for column in range(3):
                    item = self.table.item(row, column)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip("Edit this action by selecting its key on the Keys page.")
        layout.addWidget(self.table)
        hint = QLabel("Examples: Ctrl+Shift+P, Alt+Left, F12, Print. Leave a shortcut row empty to clear it. Application and system actions are edited on the Keys page.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        profile = copy.deepcopy(self.profile)
        profile["name"] = self.name.text().strip()
        profile["wm_classes"] = [s.strip() for s in self.classes.text().split(",") if s.strip()]
        profile["shortcuts"] = []
        action_rows = {entry["slot"]: entry for entry in self.profile["shortcuts"] if "action" in entry}
        for row in range(9):
            if row + 1 in action_rows:
                profile["shortcuts"].append(copy.deepcopy(action_rows[row + 1]))
                continue
            label, keys, icon = [self.table.item(row, c).text().strip() for c in range(3)]
            if not any((label, keys, icon)):
                continue
            shortcut = {"slot": row + 1, "label": label, "keys": keys}
            if icon:
                shortcut["icon"] = icon
            profile["shortcuts"].append(shortcut)
        try:
            validate_profile(profile)
            with tempfile.TemporaryDirectory(prefix="mxcc-profile-") as temporary:
                source = Path(temporary) / "profile.json"
                source.write_text(json.dumps(profile, indent=2) + "\n")
                self.result_profile = import_profile(source, overwrite=True)
        except (ProfileError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Profile could not be saved", str(exc))
            return
        self.accept()
