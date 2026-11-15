"""Compact Action Ring editor with an application strip and an action library."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .action_ring import RingCanvas
from .customization import CustomizationWorkspace
from .icons import application_icon, display_icon
from .profiles import ICON_PATTERN


MAX_ACTIONS = 7


class RingEditor(CustomizationWorkspace):
    changed = Signal()
    preview_requested = Signal()

    def __init__(self, settings, applications, parent=None):
        super().__init__(parent)
        self.applications = list(applications)
        self.apps = {app.id: app for app in self.applications}
        self.current_layout = "default"
        self.current_slot = 0
        self._settings = {"layouts": {"default": []}, "bindings": []}
        self._profile_buttons = {}
        stage_layout, panel = self.stage_layout, self.panel
        top = QHBoxLayout()
        self.layout_name = QLabel("All applications")
        self.layout_name.setObjectName("section")
        self.layout_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.layout_name, 1)
        profile_host = QWidget()
        self.profile_strip = QHBoxLayout(profile_host)
        self.profile_strip.setContentsMargins(0, 0, 0, 0)
        self.profile_strip.setSpacing(5)
        self.profile_scroll = QScrollArea()
        self.profile_scroll.setObjectName("editorProfiles")
        self.profile_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.profile_scroll.setWidgetResizable(True)
        self.profile_scroll.setFixedHeight(46)
        self.profile_scroll.setMinimumWidth(42)
        self.profile_scroll.setMaximumWidth(220)
        self.profile_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.profile_scroll.setWidget(profile_host)
        top.addWidget(self.profile_scroll)
        self.add_layout_button = QToolButton()
        self.add_layout_button.setObjectName("ringProfile")
        self.add_layout_button.setText("+")
        self.add_layout_button.setFixedSize(36, 36)
        self.add_layout_button.setToolTip("Add an application-specific ring")
        self.add_layout_button.setAccessibleName("Add application ring")
        self.add_layout_button.clicked.connect(self._add_layout_dialog)
        top.addWidget(self.add_layout_button)
        stage_layout.addLayout(top)
        self.layout_combo = QComboBox(self)
        self.layout_combo.hide()  # Shared selection model for profile chips and integrations.
        self.layout_combo.currentIndexChanged.connect(self._layout_changed)
        self.layout_description = QLabel("Default actions for applications without their own ring")
        self.layout_description.setObjectName("muted")
        self.layout_description.setWordWrap(True)
        stage_layout.addWidget(self.layout_description)
        self.canvas = RingCanvas([], editing=True, title="All applications")
        self.canvas.setMinimumSize(408, 390)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.selected.connect(self.select_slot)
        self.canvas.moved.connect(self.move_slot)
        self.canvas.add_requested.connect(self.begin_add)
        stage_layout.addWidget(self.canvas, 1)
        hint = QLabel("Click to edit · Drag to reorder · + to add an action")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stage_layout.addWidget(hint)
        toolbar = QHBoxLayout()
        self.remove_layout_button = QPushButton("Use default ring")
        self.remove_layout_button.setToolTip("Remove this application's override")
        self.remove_layout_button.clicked.connect(self.remove_layout)
        toolbar.addWidget(self.remove_layout_button)
        toolbar.addStretch()
        self.import_button = QPushButton("Import")
        self.import_button.clicked.connect(self._import_dialog)
        toolbar.addWidget(self.import_button)
        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self._export_dialog)
        toolbar.addWidget(self.export_button)
        self.preview_button = QPushButton("Try ring")
        self.preview_button.setToolTip("Open the ring for the currently active application")
        self.preview_button.clicked.connect(self.preview_requested)
        toolbar.addWidget(self.preview_button)
        stage_layout.addLayout(toolbar)
        heading = QHBoxLayout()
        title = QLabel("ALL ACTIONS")
        title.setObjectName("section")
        heading.addWidget(title)
        heading.addStretch()
        self.capacity_label = QLabel("0 / 7")
        self.capacity_label.setObjectName("muted")
        heading.addWidget(self.capacity_label)
        panel.addLayout(heading)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("ringSearch")
        self.search_edit.setPlaceholderText("Search actions and applications")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_library)
        panel.addWidget(self.search_edit)
        self.slot_title = QLabel("Add an action")
        self.slot_title.setObjectName("section")
        panel.addWidget(self.slot_title)
        self.selection_hint = QLabel("Choose an action below, then add it to the ring.")
        self.selection_hint.setObjectName("muted")
        self.selection_hint.setWordWrap(True)
        panel.addWidget(self.selection_hint)
        self.kind_combo = QComboBox(self)
        for title, kind in (("Not assigned", "none"), ("Open application", "launch"),
                            ("Keyboard shortcut", "shortcut"), ("Screenshot", "screenshot")):
            self.kind_combo.addItem(title, kind)
        self.kind_combo.hide()
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)

        scroll = QScrollArea()
        scroll.setObjectName("ringLibraryScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("ringCategories")
        categories = QVBoxLayout(content)
        categories.setContentsMargins(0, 0, 0, 0)
        categories.setSpacing(10)
        self.category_buttons, self.category_pages, self.category_frames = {}, {}, {}
        for kind, title in (("launch", "Open application"), ("shortcut", "Keyboard shortcut"), ("screenshot", "System")):
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(10)
            control = QToolButton()
            control.setObjectName("ringCategory")
            control.setText(title)
            control.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            control.setArrowType(Qt.ArrowType.RightArrow)
            control.setCheckable(True)
            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            control.clicked.connect(lambda checked=False, value=kind: self._choose_category(value))
            group_layout.addWidget(control)
            page = QWidget()
            group_layout.addWidget(page)
            categories.addWidget(group)
            self.category_buttons[kind], self.category_pages[kind], self.category_frames[kind] = control, page, group
        categories.addStretch()
        scroll.setWidget(content)
        panel.addWidget(scroll, 1)
        self.no_results = QLabel("No matching actions or applications")
        self.no_results.setObjectName("muted")
        self.no_results.setWordWrap(True)
        self.no_results.hide()
        panel.addWidget(self.no_results)

        app_layout = QVBoxLayout(self.category_pages["launch"])
        app_layout.setContentsMargins(0, 0, 0, 0)
        self.app_combo = QComboBox(self)
        self.app_combo.hide()
        self.app_combo.currentIndexChanged.connect(self._sync_app_list)
        self.app_list = QListWidget()
        self.app_list.setObjectName("ringApps")
        self.app_list.setIconSize(QSize(24, 24))
        self.app_list.setMinimumHeight(120)
        self.app_list.setMaximumHeight(190)
        self.app_list.currentRowChanged.connect(self.app_combo.setCurrentIndex)
        app_layout.addWidget(self.app_list)
        self.app_hint = QLabel("Focus an existing window, or launch the app if it is closed.")
        self.app_hint.setObjectName("muted")
        self.app_hint.setWordWrap(True)
        app_layout.addWidget(self.app_hint)
        form = QFormLayout(self.category_pages["shortcut"])
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Command palette")
        form.addRow("Action name", self.label_edit)
        self.keys_edit = QLineEdit()
        self.keys_edit.setPlaceholderText("Ctrl+Shift+P")
        form.addRow("Shortcut", self.keys_edit)
        self.icon_edit = QLineEdit()
        self.icon_edit.setPlaceholderText("input-keyboard")
        form.addRow("Icon name (optional)", self.icon_edit)
        system = QVBoxLayout(self.category_pages["screenshot"])
        system.setContentsMargins(0, 0, 0, 0)
        screenshot = QLabel("Screenshot\nOpens the desktop screenshot tool.")
        screenshot.setWordWrap(True)
        system.addWidget(screenshot)
        self.error_label = QLabel()
        self.error_label.setObjectName("status")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        panel.addWidget(self.error_label)
        actions = QHBoxLayout()
        self.clear_button = QPushButton("Remove")
        self.clear_button.clicked.connect(self.clear_slot)
        actions.addWidget(self.clear_button)
        self.apply_button = QPushButton("Add action")
        self.apply_button.setObjectName("ringApply")
        self.apply_button.clicked.connect(self.apply_slot)
        actions.addWidget(self.apply_button, 1)
        panel.addLayout(actions)
        self._populate_applications()
        self.set_settings(settings)

    def value(self):
        return copy.deepcopy(self._settings)

    def _slots(self):
        return self._settings["layouts"][self.current_layout]

    def _populate_applications(self):
        self.app_combo.blockSignals(True)
        self.app_list.blockSignals(True)
        self.app_combo.clear()
        self.app_list.clear()
        for app in self.applications:
            icon = display_icon(app.icon, application=True)
            self.app_combo.addItem(icon, app.name, app.id)
            item = QListWidgetItem(icon, app.name)
            item.setData(Qt.ItemDataRole.UserRole, app.id)
            item.setToolTip(app.id)
            self.app_list.addItem(item)
        self.app_combo.blockSignals(False)
        self.app_list.blockSignals(False)
        self._sync_app_list()

    def _sync_app_list(self, _index=None):
        self.app_list.blockSignals(True)
        self.app_list.setCurrentRow(self.app_combo.currentIndex())
        self.app_list.blockSignals(False)

    def set_applications(self, applications):
        """Refresh discovery while retaining assignments and unapplied edits."""
        kind = self.kind_combo.currentData()
        fields = (self.label_edit.text(), self.keys_edit.text(), self.icon_edit.text())
        identifier, name = self.app_combo.currentData(), self.app_combo.currentText()
        self.applications = list(applications)
        self.apps = {app.id: app for app in self.applications}
        self._populate_applications()
        self.set_settings(self._settings)
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(kind))
        self.label_edit.setText(fields[0]); self.keys_edit.setText(fields[1]); self.icon_edit.setText(fields[2])
        if identifier is not None:
            self._select_application(identifier, name)
        self._filter_library(self.search_edit.text())

    def set_settings(self, settings):
        from .ring_configuration import validate_ring
        ring = settings.get("action_ring", settings)
        if "layouts" not in ring:
            ring = {"layouts": {"default": []}, "bindings": []}
        self._settings = validate_ring(ring)
        if self.current_layout not in self._settings["layouts"]:
            self.current_layout = "default"
        self.current_slot = min(self.current_slot, len(self._slots()))
        self._populate_profiles()
        self._refresh()

    def _populate_profiles(self):
        while self.profile_strip.count():
            item = self.profile_strip.takeAt(0)
            item.widget().hide(); item.widget().deleteLater()
        self._profile_buttons = {}
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        for identifier in self._settings["layouts"]:
            app = self.apps.get(identifier)
            name = "All applications" if identifier == "default" else app.name if app else identifier
            icon = application_icon() if identifier == "default" or app is None else display_icon(app.icon, application=True)
            self.layout_combo.addItem(icon, name, identifier)
            control = QToolButton()
            control.setObjectName("ringProfile")
            control.setIcon(icon)
            control.setIconSize(QSize(22, 22))
            control.setFixedSize(36, 36)
            control.setCheckable(True)
            control.setChecked(identifier == self.current_layout)
            control.setToolTip(name)
            control.setAccessibleName(name)
            control.clicked.connect(lambda checked=False, value=identifier: self.open_layout(value))
            self.profile_strip.addWidget(control)
            self._profile_buttons[identifier] = control
        self.layout_combo.setCurrentIndex(self.layout_combo.findData(self.current_layout))
        self.layout_combo.blockSignals(False)
        self.profile_scroll.setFixedWidth(min(220, max(42, len(self._profile_buttons) * 41)))
        self.profile_scroll.ensureWidgetVisible(self._profile_buttons[self.current_layout])

    def _layout_changed(self, _index):
        identifier = self.layout_combo.currentData()
        if identifier in self._settings["layouts"]:
            self.current_layout, self.current_slot = identifier, 0
            self._refresh()

    def open_layout(self, identifier, add_action=False):
        if identifier not in self._settings["layouts"]:
            if identifier not in self.apps:
                identifier = "default"
            else:
                self.add_layout(identifier)
        self.current_layout = identifier
        self.layout_combo.setCurrentIndex(self.layout_combo.findData(identifier))
        self.current_slot = 0
        self._refresh()
        if add_action:
            self.begin_add()

    def _refresh(self):
        count = len(self._slots())
        self.capacity_label.setText(f"{count} / {MAX_ACTIONS}")
        self.remove_layout_button.setVisible(self.current_layout != "default")
        app = self.apps.get(self.current_layout)
        name = app.name if app else "All applications" if self.current_layout == "default" else self.current_layout
        self.layout_name.setText(name)
        self.layout_name.setToolTip(name)
        self.layout_description.setText("Default actions for applications without their own ring" if self.current_layout == "default"
                                        else "These actions appear while this application is active")
        for identifier, control in self._profile_buttons.items():
            control.setChecked(identifier == self.current_layout)
        self.canvas.title = name
        self.canvas.center_icon = display_icon(app.icon, application=True) if app else application_icon()
        slots = copy.deepcopy(self._slots())
        for entry in slots:
            if entry["action"]["type"] == "launch" and (app := self.apps.get(entry["action"]["desktop_id"])):
                entry["label"], entry["icon"] = app.name, app.icon
        self.canvas.set_slots(slots)
        self.current_slot = min(self.current_slot, count)
        self.select_slot(self.current_slot)

    def _select_application(self, identifier, name):
        index = self.app_combo.findData(identifier)
        if index < 0:
            label = name if "(not installed)" in name else f"{name} (not installed)"
            self.app_combo.addItem(label, identifier)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, identifier)
            self.app_list.addItem(item)
            index = self.app_combo.count() - 1
        self.app_combo.setCurrentIndex(index)
        self._sync_app_list()

    def select_slot(self, index):
        if type(index) is not int or not 0 <= index <= len(self._slots()):
            return
        self.search_edit.clear()
        self.current_slot = index
        entry = self._slots()[index] if index < len(self._slots()) else None
        self.canvas.select(index if entry else -1)
        app = self.apps.get(entry["action"].get("desktop_id")) if entry else None
        self.slot_title.setText(app.name if app else entry["label"] if entry else "Add an action")
        self.selection_hint.setText("Choose an action to replace this bubble." if entry else "Choose an action below, then add it to the ring.")
        self.apply_button.setText("Apply" if entry else "Add action")
        self.apply_button.setEnabled(entry is not None or len(self._slots()) < MAX_ACTIONS)
        self.clear_button.setEnabled(entry is not None)
        kind = entry["action"]["type"] if entry else "none"
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(kind))
        self._kind_changed(self.kind_combo.currentIndex())
        self.label_edit.setText(entry["label"] if entry else "")
        self.keys_edit.setText(entry["action"].get("keys", "") if entry else "")
        self.icon_edit.setText(entry["icon"] if entry else "")
        if kind == "launch":
            self._select_application(entry["action"]["desktop_id"], entry["label"])
        self.error_label.hide()

    def begin_add(self):
        self.search_edit.clear()
        if len(self._slots()) >= MAX_ACTIONS:
            self.select_slot(min(self.current_slot, MAX_ACTIONS - 1))
            self.selection_hint.setText("All 7 actions are in use. Select a bubble to replace or remove it.")
            self.apply_button.setFocus()
            return
        self.select_slot(len(self._slots()))
        self.search_edit.setFocus()

    def _choose_category(self, kind):
        index = self.kind_combo.findData(kind)
        if self.kind_combo.currentIndex() == index:
            self.kind_combo.setCurrentIndex(0)
        else:
            self.kind_combo.setCurrentIndex(index)

    def _kind_changed(self, _index):
        kind = self.kind_combo.currentData()
        for value, button in self.category_buttons.items():
            selected = value == kind
            button.setChecked(selected)
            button.setArrowType(Qt.ArrowType.DownArrow if selected else Qt.ArrowType.RightArrow)
            self.category_pages[value].setVisible(selected)
        if hasattr(self, "error_label"):
            self.error_label.hide()

    def _filter_library(self, text):
        query = text.strip().casefold()
        matches = 0
        for index in range(self.app_list.count()):
            item = self.app_list.item(index)
            shown = not query or query in item.text().casefold() or query in str(item.data(Qt.ItemDataRole.UserRole)).casefold()
            item.setHidden(not shown)
            matches += shown
        category_matches = {
            "launch": bool(matches) or not query or query in "open application launch app",
            "shortcut": not query or query in "keyboard shortcut custom keys ctrl alt shift super",
            "screenshot": not query or query in "system screenshot screen capture print",
        }
        for kind, frame in self.category_frames.items():
            frame.setVisible(category_matches[kind])
        self.no_results.setVisible(not any(category_matches.values()))
        if query and matches:
            self.kind_combo.setCurrentIndex(self.kind_combo.findData("launch"))

    def _store(self, candidate):
        from .ring_configuration import validate_ring
        try:
            validated = validate_ring(candidate)
        except ValueError as error:
            self.error_label.setText(str(error)); self.error_label.show()
            return False
        self._settings = validated
        self._refresh()
        self.changed.emit()
        return True

    def apply_slot(self):
        kind = self.kind_combo.currentData()
        entry = None
        if kind == "launch":
            app = self.apps.get(self.app_combo.currentData())
            if app is None:
                self.error_label.setText("Choose an installed application."); self.error_label.show()
                return
            entry = {"label": app.name, "icon": app.icon if ICON_PATTERN.fullmatch(app.icon) else "application-x-executable",
                     "action": {"type": "launch", "desktop_id": app.id}}
        elif kind == "shortcut":
            entry = {"label": self.label_edit.text().strip(), "icon": self.icon_edit.text().strip() or "input-keyboard",
                     "action": {"type": "shortcut", "keys": self.keys_edit.text().strip()}}
        elif kind == "screenshot":
            entry = {"label": "Screenshot", "icon": "applets-screenshooter", "action": {"type": "screenshot"}}
        if entry is None:
            self.error_label.setText("Choose an action from the library first."); self.error_label.show()
            return
        candidate = self.value()
        slots = candidate["layouts"][self.current_layout]
        if self.current_slot < len(slots):
            slots[self.current_slot] = entry
        elif len(slots) < MAX_ACTIONS:
            slots.append(entry)
        else:
            self.error_label.setText("A ring can contain at most 7 actions."); self.error_label.show()
            return
        self._store(candidate)

    def clear_slot(self):
        if self.current_slot >= len(self._slots()):
            return
        candidate = self.value()
        del candidate["layouts"][self.current_layout][self.current_slot]
        self._store(candidate)

    def move_slot(self, source, target):
        if (type(source) is not int or type(target) is not int
                or not 0 <= source < len(self._slots()) or not 0 <= target < len(self._slots()) or source == target):
            return
        candidate = self.value()
        slots = candidate["layouts"][self.current_layout]
        slots.insert(target, slots.pop(source))
        self.current_slot = target
        self._store(candidate)

    def add_layout(self, identifier):
        if identifier not in self.apps or identifier in self._settings["layouts"]:
            return
        candidate = self.value()
        candidate["layouts"][identifier] = copy.deepcopy(candidate["layouts"]["default"])
        previous, self.current_layout = self.current_layout, identifier
        if self._store(candidate):
            self._populate_profiles()
        else:
            self.current_layout = previous

    def remove_layout(self):
        if self.current_layout == "default":
            return
        candidate = self.value()
        del candidate["layouts"][self.current_layout]
        self.current_layout, self.current_slot = "default", 0
        if self._store(candidate):
            self._populate_profiles()

    def _add_layout_dialog(self):
        available = [app for app in self.applications if app.id not in self._settings["layouts"]]
        if not available:
            QMessageBox.information(self, "Application rings", "Every installed application already has a ring.")
            return
        names = [f"{app.name} · {app.id}" for app in available]
        name, accepted = QInputDialog.getItem(self, "Add application ring", "Application", names, 0, False)
        if accepted:
            self.add_layout(available[names.index(name)].id)

    def export_to(self, path: Path):
        data = {"version": 1, "layouts": self.value()["layouts"]}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def import_from(self, path: Path):
        from .ring_configuration import validate_ring
        with path.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("Ring configuration exceeds 256 KiB")
        try:
            data = json.loads(raw.decode("utf-8"))
        except RecursionError as error:
            raise ValueError("Ring configuration is nested too deeply") from error
        if (not isinstance(data, dict) or set(data) != {"version", "layouts"}
                or type(data["version"]) is not int or data["version"] != 1):
            raise ValueError("This is not an Action Ring export")
        candidate = validate_ring({"layouts": data["layouts"], "bindings": self.value()["bindings"]})
        self.set_settings(candidate)
        self.changed.emit()

    def _export_dialog(self):
        name, _ = QFileDialog.getSaveFileName(self, "Export Action Ring", "action-ring.json", "JSON files (*.json)")
        if name:
            try:
                self.export_to(Path(name))
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Could not export ring", str(error))

    def _import_dialog(self):
        name, _ = QFileDialog.getOpenFileName(self, "Import Action Ring", "", "JSON files (*.json)")
        if name:
            try:
                self.import_from(Path(name))
            except (OSError, ValueError, UnicodeError) as error:
                QMessageBox.warning(self, "Could not import ring", str(error))
