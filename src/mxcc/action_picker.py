"""Shared declarative action picker for keys, device buttons and the Action Ring."""

from copy import deepcopy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .icons import display_icon
from .profiles import ICON_PATTERN


class ActionPicker(QWidget):
    """Edit a draft; the enclosing page decides when and where it is saved."""

    changed = Signal()

    def __init__(self, applications, parent=None, *, allow_ring=True):
        super().__init__(parent)
        self.allow_ring = allow_ring
        self.applications = list(applications)
        self.apps = {app.id: app for app in self.applications}
        self._loading = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("editorSearch")
        self.search_edit.setPlaceholderText("Search actions and applications")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_library)
        layout.addWidget(self.search_edit)
        self.kind_combo = QComboBox(self)
        for title, kind in (("Not assigned", "none"), ("Open application", "launch"),
                            ("Keyboard shortcut", "shortcut"), ("Screenshot", "screenshot")):
            self.kind_combo.addItem(title, kind)
        if allow_ring:
            self.kind_combo.addItem("Open Action Ring", "open_ring")
        self.kind_combo.hide()
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("editorScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(120)
        content = QWidget()
        content.setObjectName("ringCategories")
        categories = QVBoxLayout(content)
        categories.setContentsMargins(0, 0, 0, 0)
        categories.setSpacing(10)
        self.category_buttons, self.category_pages, self.category_frames = {}, {}, {}
        for kind, title in (("launch", "Open application"), ("shortcut", "Keyboard shortcut"), ("system", "System")):
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(10)
            button = QToolButton()
            button.setObjectName("editorCategory")
            button.setText(title)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setArrowType(Qt.ArrowType.RightArrow)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, value=kind: self._choose_category(value))
            group_layout.addWidget(button)
            page = QWidget()
            group_layout.addWidget(page)
            categories.addWidget(group)
            self.category_buttons[kind], self.category_pages[kind], self.category_frames[kind] = button, page, group
        categories.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        self.no_results = QLabel("No matching actions or applications")
        self.no_results.setObjectName("muted")
        self.no_results.setWordWrap(True)
        self.no_results.hide()
        layout.addWidget(self.no_results)

        apps_layout = QVBoxLayout(self.category_pages["launch"])
        apps_layout.setContentsMargins(0, 0, 0, 0)
        self.app_combo = QComboBox(self)
        self.app_combo.hide()
        self.app_combo.currentIndexChanged.connect(self._sync_app_list)
        self.app_list = QListWidget()
        self.app_list.setObjectName("editorApps")
        self.app_list.setIconSize(QSize(24, 24))
        self.app_list.setMinimumHeight(110)
        self.app_list.setMaximumHeight(190)
        self.app_list.currentRowChanged.connect(self.app_combo.setCurrentIndex)
        apps_layout.addWidget(self.app_list)
        self.app_hint = QLabel("Focus an existing window, or launch the app if it is closed.")
        self.app_hint.setObjectName("muted")
        self.app_hint.setWordWrap(True)
        apps_layout.addWidget(self.app_hint)
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
        for field in (self.label_edit, self.keys_edit, self.icon_edit):
            field.textChanged.connect(self._changed)
        system = QVBoxLayout(self.category_pages["system"])
        system.setContentsMargins(0, 0, 0, 0)
        self.system_combo = QComboBox()
        self.system_combo.addItem(display_icon("applets-screenshooter"), "Screenshot", "screenshot")
        if allow_ring:
            self.system_combo.addItem(display_icon("view-grid"), "Open Action Ring", "open_ring")
        self.system_combo.currentIndexChanged.connect(self._system_changed)
        system.addWidget(self.system_combo)
        self.system_hint = QLabel()
        self.system_hint.setObjectName("muted")
        self.system_hint.setWordWrap(True)
        system.addWidget(self.system_hint)
        self._populate_applications()
        self.set_entry(None)
        self._loading = False

    def _changed(self, *_):
        if not self._loading:
            self.changed.emit()

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

    def _sync_app_list(self, *_):
        self.app_list.blockSignals(True)
        self.app_list.setCurrentRow(self.app_combo.currentIndex())
        self.app_list.blockSignals(False)
        self._changed()

    def select_application(self, identifier, name):
        index = self.app_combo.findData(identifier)
        if index < 0:
            name = name if "(not installed)" in name else f"{name} (not installed)"
            self.app_combo.addItem(name, identifier)
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, identifier)
            self.app_list.addItem(item)
            index = self.app_combo.count() - 1
        self.app_combo.setCurrentIndex(index)
        self._sync_app_list()

    def set_applications(self, applications):
        draft = self.draft()
        loading = self._loading
        self._loading = True
        self.applications = list(applications)
        self.apps = {app.id: app for app in self.applications}
        self._populate_applications()
        self.set_draft(draft)
        self._loading = loading

    def draft(self):
        """Preserve even incomplete fields across discovery and device refreshes."""
        return {"kind": self.kind_combo.currentData(), "desktop_id": self.app_combo.currentData(),
                "app_name": self.app_combo.currentText(), "label": self.label_edit.text(),
                "keys": self.keys_edit.text(), "icon": self.icon_edit.text(), "search": self.search_edit.text()}

    def set_draft(self, draft):
        loading = self._loading
        self._loading = True
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(draft["kind"])))
        self.label_edit.setText(draft["label"])
        self.keys_edit.setText(draft["keys"])
        self.icon_edit.setText(draft["icon"])
        if draft["desktop_id"] is not None:
            self.select_application(draft["desktop_id"], draft["app_name"])
        self.search_edit.setText(draft["search"])
        self._filter_library(self.search_edit.text())
        self._loading = loading

    def set_entry(self, entry):
        loading = self._loading
        self._loading = True
        self.search_edit.clear()
        kind = entry["action"]["type"] if entry else "none"
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(kind)))
        self._kind_changed()
        self.label_edit.setText(entry["label"] if entry else "")
        self.keys_edit.setText(entry["action"].get("keys", "") if entry else "")
        self.icon_edit.setText(entry["icon"] if entry else "")
        if kind == "launch":
            self.select_application(entry["action"]["desktop_id"], entry["label"])
        self._loading = loading

    def clear(self):
        self.set_entry(None)

    def _choose_category(self, category):
        current = self.kind_combo.currentData()
        selected = current in ("screenshot", "open_ring") if category == "system" else current == category
        target = "none" if selected else self.system_combo.currentData() if category == "system" else category
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(target))

    def _kind_changed(self, *_):
        kind = self.kind_combo.currentData()
        category = "system" if kind in ("screenshot", "open_ring") else kind
        for value, button in self.category_buttons.items():
            selected = value == category
            button.setChecked(selected)
            button.setArrowType(Qt.ArrowType.DownArrow if selected else Qt.ArrowType.RightArrow)
            self.category_pages[value].setVisible(selected)
        if category == "system":
            self.system_combo.blockSignals(True)
            self.system_combo.setCurrentIndex(self.system_combo.findData(kind))
            self.system_combo.blockSignals(False)
        self.system_hint.setText("Opens the desktop screenshot tool." if kind != "open_ring"
                                 else "Press once to open the ring, then again to close it.")
        self._changed()

    def _system_changed(self, *_):
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(self.system_combo.currentData()))

    def _filter_library(self, text):
        query = text.strip().casefold()
        matches = 0
        for index in range(self.app_list.count()):
            item = self.app_list.item(index)
            shown = not query or query in item.text().casefold() or query in str(item.data(Qt.ItemDataRole.UserRole)).casefold()
            item.setHidden(not shown)
            matches += shown
        system_terms = "system screenshot screen capture print" + (" action ring" if self.allow_ring else "")
        found = {"launch": bool(matches) or not query or query in "open application launch app",
                 "shortcut": not query or query in "keyboard shortcut custom keys ctrl alt shift super",
                 "system": not query or query in system_terms}
        for kind, frame in self.category_frames.items():
            frame.setVisible(found[kind])
        self.no_results.setVisible(not any(found.values()))
        if query and matches and not self._loading:
            self.kind_combo.setCurrentIndex(self.kind_combo.findData("launch"))

    def value(self):
        from .actions import validate_action_entry
        kind = self.kind_combo.currentData()
        if kind == "launch":
            app = self.apps.get(self.app_combo.currentData())
            if app is None:
                raise ValueError("Choose an installed application.")
            entry = {"label": app.name, "icon": app.icon if ICON_PATTERN.fullmatch(app.icon) else "application-x-executable",
                     "action": {"type": "launch", "desktop_id": app.id}}
        elif kind == "shortcut":
            entry = {"label": self.label_edit.text().strip(), "icon": self.icon_edit.text().strip() or "input-keyboard",
                     "action": {"type": "shortcut", "keys": self.keys_edit.text().strip()}}
        elif kind in ("screenshot", "open_ring"):
            entry = {"label": "Screenshot" if kind == "screenshot" else "Action Ring",
                     "icon": "applets-screenshooter" if kind == "screenshot" else "view-grid",
                     "action": {"type": kind}}
        else:
            raise ValueError("Choose an action from the library first.")
        return deepcopy(validate_action_entry(entry, allow_ring=self.allow_ring))
