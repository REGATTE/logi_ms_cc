"""On-demand native Qt settings window; the Rust process owns the keypad."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QProcess, QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QStackedWidget,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__, ipc, service
from .applications import discover_applications
from .application_views import ApplicationCard
from .appearance import AppearanceController
from .configuration import compact_launchers, compile_runtime, load_settings, profile_for_app, save_settings
from .customization import CustomizationWorkspace
from .device_navigation import DeviceDetails, DeviceScanner
from .device_views import AppFooter, CREATIVE_CONSOLE_ID, ConsoleDevices, DevicesHome, is_console_device
from .devices import discover_devices
from .icons import FaceRenderer, display_icon, setup_icon_theme
from .key_button import KeyButton
from .paths import cache_dir, profiles_dir, runtime_path
from .profile_editor import ProfileEditor
from .profiles import ProfileExistsError, atomic_json, import_profile, load_profiles
from .ring_editor import RingEditor
from .transfer import export_bundle, read_bundle, write_imported_profiles


def label(text: str, kind: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(kind)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, callback, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    if primary:
        widget.setObjectName("primary")
    return widget


class _StatusNotice(QLabel):
    """Reserve space for feedback only while there is a message to show."""

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class MainWindow(QMainWindow):
    def __init__(self, *, connect_worker: bool = True, applications=None, devices=None,
                 ring_layout=None, add_ring_action=False):
        super().__init__()
        setup_icon_theme()
        self.connect_worker = connect_worker
        self.apps = discover_applications() if applications is None else list(applications)
        self.profiles = load_profiles()
        self.settings = load_settings(self.apps)
        self.appearance = AppearanceController(QApplication.instance(), parent=self)
        self.appearance.set_preference(self.settings["theme"])
        self.runtime = {}
        self.selected_slot = 0
        self.page = 0
        self.preview_profile_id = "default"
        self.status = {}
        self.worker_error = None
        self.worker_sync_error = None
        self.permission_process = None
        self.selected_device_id = None
        self.devices = {}
        self._explicit_device_scan = False
        self._settings_return = "home"
        self.setWindowTitle("MX Creative Control")
        self.resize(1200, 820)
        self.setMinimumSize(970, 710)
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.views = QStackedWidget()
        shell.addWidget(self.views, 1)
        self.footer = AppFooter()
        shell.addWidget(self.footer)
        self.footer.refresh_requested.connect(lambda: self.refresh_devices(explicit=True))
        self.footer.settings_requested.connect(self.show_settings)
        self.home = DevicesHome()
        self.views.addWidget(self.home)
        self.home.device_selected.connect(self.open_device)
        self.home.ring_requested.connect(self.show_ring)
        self.console = ConsoleDevices()
        self.views.addWidget(self.console)
        self.console.back_requested.connect(self.show_home)
        self.console.device_selected.connect(self.open_device)
        self.editor = QWidget()
        self.editor.setObjectName("editorPage")
        self.views.addWidget(self.editor)
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(12, 16, 12, 0)
        editor_layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.back_button = button("←", self._back_to_devices)
        self.back_button.setObjectName("topbarButton")
        self.back_button.setToolTip("MX Creative Console")
        self.back_button.setAccessibleName("MX Creative Console")
        header.addWidget(self.back_button)
        self.device_title = label("MX Creative Keypad", "deviceTitle")
        header.addWidget(self.device_title)
        self.navigation = QListWidget()
        self.navigation.setObjectName("editorTabs")
        self.navigation.setFlow(QListWidget.Flow.LeftToRight)
        self.navigation.setWrapping(False)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setFixedSize(216, 40)
        self.navigation.addItems(["Keys", "Applications"])
        for row, width in enumerate((72, 140)):
            self.navigation.item(row).setSizeHint(QSize(width, 36))
        header.addWidget(self.navigation)
        header.addStretch()
        self.connection = label("Checking keypad…", "status")
        header.addWidget(self.connection)
        editor_layout.addLayout(header)
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        editor_layout.addLayout(content, 1)
        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        self._build_customize()
        self._build_applications()
        self._build_settings()
        self.notice = _StatusNotice()
        self.notice.setObjectName("status")
        self.notice.setWordWrap(True)
        self.notice.setText("")
        content.addWidget(self.notice)
        self.navigation.currentRowChanged.connect(self._navigate_editor)
        self.navigation.setCurrentRow(0)
        self.details = DeviceDetails()
        self.details.back_requested.connect(self._back_to_devices)
        self.details.assignment_changed.connect(self.assign_ring_button)
        self.details.edit_ring_requested.connect(self.show_ring)
        self.views.addWidget(self.details)
        self.ring_page = QWidget()
        ring_page_layout = QVBoxLayout(self.ring_page)
        ring_page_layout.setContentsMargins(12, 16, 12, 0)
        ring_header = QHBoxLayout()
        self.ring_back = button("←", self.show_home)
        self.ring_back.setObjectName("topbarButton")
        self.ring_back.setAccessibleName("All devices")
        self.ring_back.setToolTip("All devices")
        ring_header.addWidget(self.ring_back)
        ring_header.addWidget(label("Action Ring", "deviceTitle"))
        ring_header.addStretch()
        ring_page_layout.addLayout(ring_header)
        self.ring_editor = RingEditor(self.settings, self.apps, self.ring_page)
        self.ring_editor.changed.connect(self.save_ring)
        self.ring_editor.preview_requested.connect(self.preview_ring)
        ring_scroll = QScrollArea()
        ring_scroll.setObjectName("editorScroll")
        ring_scroll.setWidgetResizable(True)
        ring_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ring_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ring_scroll.setWidget(self.ring_editor)
        ring_page_layout.addWidget(ring_scroll, 1)
        self.views.addWidget(self.ring_page)
        self.details.set_bindings(self.settings["action_ring"]["bindings"])
        self._populate_apps()
        self.persist(notify=False)
        self.refresh_keys()
        self.timer = QTimer(self)
        self.timer.setInterval(1200)
        self.timer.timeout.connect(self.refresh_status)
        if connect_worker:
            QTimer.singleShot(0, lambda: self.start_worker(preserve_context=ring_layout is not None))
            self.timer.start()
        else:
            self.connection.setText("Preview · worker not connected")
        self.device_scanner = None
        self.device_timer = QTimer(self)
        self.device_timer.setInterval(5000)
        self.device_timer.timeout.connect(self.refresh_devices)
        if devices is not None:
            self._devices_updated(devices)
        elif connect_worker:
            self.device_scanner = DeviceScanner(discover_devices, self)
            self.device_scanner.updated.connect(self._devices_updated)
            self.device_scanner.failed.connect(lambda error: self.home.set_error(f"Could not refresh devices: {error}"))
            self.device_scanner.scanning.connect(self._set_device_scanning)
            QTimer.singleShot(0, self.refresh_devices)
            self.device_timer.start()
        if ring_layout is not None:
            self.show_ring(ring_layout, add_action=add_ring_action)
        else:
            self.show_home()

    def refresh_devices(self, *, explicit=False):
        if self.device_scanner is not None:
            if not self.device_scanner.busy:
                self._explicit_device_scan = explicit
            self.device_scanner.refresh()

    def _set_device_scanning(self, scanning):
        self.footer.set_scanning(scanning)
        self.home.set_scanning(scanning, force=self._explicit_device_scan)
        self.console.set_scanning(scanning, force=self._explicit_device_scan)
        self.details.connection_dot.set_scanning(scanning, force=self._explicit_device_scan)
        if not scanning:
            self._explicit_device_scan = False

    def _devices_updated(self, devices):
        previous = self.devices.get(self.selected_device_id)
        detected = {device.id: device for device in devices}
        # Keep devices seen during this window's session so an unplugged device
        # can show its disconnected indicator and reconnect in the same card.
        self.devices = {identifier: detected.get(identifier, replace(device, connected=False, battery_percent=None))
                        for identifier, device in self.devices.items()}
        self.devices.update(detected)
        self.home.update_devices(self.devices.values())
        self.console.update_devices(self.devices.values())
        self.home.set_error("")
        self.console.set_error("")
        if previous is not None and previous.connected is not False and self.selected_device_id not in detected:
            if is_console_device(previous) and any(is_console_device(item) for item in self.devices.values()):
                self.show_console()
                self.console.set_error(f"{previous.name} disconnected. Reconnect it to continue.")
            else:
                self.show_home()
                self.home.set_error(f"{previous.name} disconnected. Reconnect it to continue.")
        elif self.selected_device_id in self.devices:
            device = self.devices[self.selected_device_id]
            if self.views.currentWidget() is self.editor and not self._can_customize(device):
                self.open_device(device.id)
            elif self.views.currentWidget() is self.details:
                if self._can_customize(device):
                    self.open_device(device.id)
                else:
                    self.details.show_device(device, multiple_keypads=self._multiple_keypads(device))

    def _multiple_keypads(self, device):
        return device.supported and sum(item.supported and item.connected is True for item in self.devices.values()) > 1

    def _can_customize(self, device):
        return device.supported and device.connected is True and not self._multiple_keypads(device)

    def open_device(self, device_id):
        if device_id == CREATIVE_CONSOLE_ID:
            self.show_console()
            return
        device = self.devices.get(device_id)
        if device is None:
            return
        self.selected_device_id = device_id
        if self._can_customize(device):
            self.device_title.setText(device.name)
            self.views.setCurrentWidget(self.editor)
            self.navigation.setCurrentRow(0)
        else:
            self.details.show_device(device, multiple_keypads=self._multiple_keypads(device))
            parent_title = "MX Creative Console" if is_console_device(device) else "All devices"
            self.details.back_button.setToolTip(parent_title)
            self.details.back_button.setAccessibleName(parent_title)
            self.views.setCurrentWidget(self.details)

    def show_home(self):
        self.selected_device_id = None
        self.views.setCurrentWidget(self.home)
        self.home.setFocus()

    def show_ring(self, layout_id=None, *, add_action=False):
        self.selected_device_id = None
        self.ring_editor.set_settings(self.settings)
        self.views.setCurrentWidget(self.ring_page)
        if layout_id is not None:
            self.ring_editor.open_layout(layout_id, add_action=add_action)

    def save_ring(self):
        try:
            self.persist(settings={**self.settings, "action_ring": self.ring_editor.value()})
            self.details.set_bindings(self.settings["action_ring"]["bindings"])
        except (OSError, ValueError, RuntimeError) as exc:
            self.ring_editor.set_settings(self.settings)
            QMessageBox.warning(self, "Could not save Action Ring", str(exc))

    def assign_ring_button(self, control_id, assigned):
        device = self.devices.get(self.selected_device_id)
        if device is None:
            return
        binding = {"vendor_id": device.vendor_id, "product_id": device.product_id, "control_id": control_id}
        ring = self.settings["action_ring"]
        bindings = [item for item in ring["bindings"] if item != binding]
        if assigned:
            bindings.append(binding)
        try:
            self.persist(settings={**self.settings, "action_ring": {**ring, "bindings": bindings}})
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Could not save button assignment", str(exc))
        self.details.set_bindings(self.settings["action_ring"]["bindings"])

    def preview_ring(self):
        if not self.connect_worker:
            return
        try:
            response = ipc.request("ring_toggle", timeout=2)
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Could not open Action Ring"))
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Action Ring", str(exc))

    def show_console(self):
        if not any(is_console_device(device) for device in self.devices.values()):
            self.show_home()
            return
        self.selected_device_id = None
        self.views.setCurrentWidget(self.console)
        self.console.setFocus()

    def _back_to_devices(self):
        device = self.devices.get(self.selected_device_id)
        if device is not None and is_console_device(device):
            self.show_console()
        else:
            self.show_home()

    def _navigate_editor(self, index):
        if index >= 0:
            self.stack.setCurrentIndex(index)

    def show_settings(self, return_to=None):
        current = self.views.currentWidget()
        if current is self.settings_page:
            return
        if return_to is None:
            return_to = "ring" if current is self.ring_page else "editor" if current is self.editor else "console" if current is self.console else "details" if current is self.details else "home"
        self._settings_return = return_to
        self.views.setCurrentWidget(self.settings_page)

    def _leave_settings(self):
        if self._settings_return == "ring":
            self.show_ring()
            return
        if self._settings_return == "console":
            self.show_console()
            return
        if self._settings_return == "details" and self.selected_device_id in self.devices:
            self.open_device(self.selected_device_id)
            return
        if self._settings_return == "editor" and self.selected_device_id in self.devices:
            device = self.devices[self.selected_device_id]
            if self._can_customize(device):
                self.views.setCurrentWidget(self.editor)
                return
        self.show_home()

    def _build_customize(self):
        self.keypad_workspace = widget = CustomizationWorkspace()
        layout = widget.stage_layout
        row = QHBoxLayout()
        self.keypad_scope = label("All applications", "section")
        self.keypad_scope.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.keypad_scope, 1)
        profile_host = QWidget()
        self.profile_strip = QHBoxLayout(profile_host)
        self.profile_strip.setContentsMargins(0, 0, 0, 0)
        self.profile_strip.setSpacing(5)
        self.profile_chips = []
        self.profile_scroll = QScrollArea()
        self.profile_scroll.setObjectName("editorProfiles")
        self.profile_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.profile_scroll.setWidgetResizable(True)
        self.profile_scroll.setFixedHeight(46)
        self.profile_scroll.setMinimumWidth(42)
        self.profile_scroll.setMaximumWidth(220)
        self.profile_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.profile_scroll.setWidget(profile_host)
        row.addWidget(self.profile_scroll)
        add_application = QToolButton()
        add_application.setObjectName("editorProfile")
        add_application.setText("+")
        add_application.setFixedSize(36, 36)
        add_application.setToolTip("Add an application to the keypad")
        add_application.setAccessibleName("Add keypad application")
        add_application.clicked.connect(self.begin_add_launcher)
        row.addWidget(add_application)
        layout.addLayout(row)
        self.keypad_description = label("Launch applications and switch to their shortcuts.", "muted", True)
        layout.addWidget(self.keypad_description)
        layout.addStretch()
        self.device = QFrame()
        self.device.setObjectName("device")
        self.device.setFixedSize(330, 330)
        grid = QGridLayout(self.device)
        grid.setContentsMargins(18, 18, 18, 18)
        grid.setSpacing(8)
        self.key_buttons = []
        for index in range(9):
            key = KeyButton(index, self.device)
            key.setObjectName("key")
            key.setFixedSize(88, 88)
            key.setIconSize(QSize(80, 80))
            key.setCheckable(True)
            key.setAccessibleName(f"Key {index + 1}")
            key.clicked.connect(lambda checked=False, slot=index: self.select_key(slot))
            key.moved.connect(self.move_launcher)
            grid.addWidget(key, index // 3, index % 3)
            self.key_buttons.append(key)
        layout.addWidget(self.device, 0, Qt.AlignmentFlag.AlignHCenter)
        pages = QHBoxLayout()
        pages.addStretch()
        self.page_buttons = []
        for index, text in enumerate(("Apps", "Shortcuts")):
            control = button(text, lambda checked=False, p=index: self.change_page(p))
            control.setObjectName("editorPageButton")
            control.setCheckable(True)
            self.page_buttons.append(control)
            pages.addWidget(control)
        pages.addStretch()
        layout.addLayout(pages)
        self.reorder_hint = label("Drag icons to reorder. Changes save automatically.", "muted", True)
        self.reorder_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.reorder_hint)
        layout.addStretch()
        self.inspector = widget.library
        side = widget.panel
        side.addWidget(label("ALL ACTIONS", "section"))
        self.launcher_search = QLineEdit()
        self.launcher_search.setObjectName("editorSearch")
        self.launcher_search.setPlaceholderText("Search applications and actions")
        self.launcher_search.setClearButtonEnabled(True)
        self.launcher_search.textChanged.connect(self._filter_launcher_actions)
        side.addWidget(self.launcher_search)
        self.key_action_title = label("Application", "section")
        side.addWidget(self.key_action_title)
        self.key_description = label("Choose an app or a system action for this key.", "muted", True)
        side.addWidget(self.key_description)
        self.app_combo = QComboBox(widget)
        self.app_combo.hide()  # Selection model shared with import and profile actions.
        self.app_combo.currentIndexChanged.connect(self._sync_launcher_action)
        self.launcher_actions = QListWidget()
        self.launcher_actions.setObjectName("editorApps")
        self.launcher_actions.setIconSize(QSize(24, 24))
        self.launcher_actions.setMinimumHeight(80)
        self.launcher_actions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.launcher_actions.currentRowChanged.connect(self.app_combo.setCurrentIndex)
        side.addWidget(self.launcher_actions, 1)
        self.launcher_no_results = label("No matching applications or actions", "muted", True)
        self.launcher_no_results.hide()
        side.addWidget(self.launcher_no_results)
        self.shortcut_preview = QWidget()
        shortcut_preview_layout = QVBoxLayout(self.shortcut_preview)
        shortcut_preview_layout.setContentsMargins(0, 6, 0, 0)
        self.shortcut_name = label("", "section", True)
        self.shortcut_keys = label("", "muted", True)
        shortcut_preview_layout.addWidget(self.shortcut_name)
        shortcut_preview_layout.addWidget(self.shortcut_keys)
        shortcut_preview_layout.addStretch()
        side.addWidget(self.shortcut_preview, 1)
        self.active_label = label("Active application: —", "muted", True)
        side.addWidget(self.active_label)
        self.apply_button = button("Apply to key", self.apply_key)
        self.apply_button.setObjectName("editorApply")
        side.addWidget(self.apply_button)
        self.import_button = button("Import shortcuts…", self.import_shortcuts)
        self.edit_button = button("Edit shortcuts…", self.edit_shortcuts)
        shortcuts = QHBoxLayout()
        shortcuts.setSpacing(6)
        self.import_button.setText("Import shortcuts")
        self.edit_button.setText("Edit shortcuts")
        shortcuts.addWidget(self.import_button)
        shortcuts.addWidget(self.edit_button)
        side.addLayout(shortcuts)
        self.use_shortcuts_button = button("Use shortcuts on keypad", self.use_preview_profile)
        side.addWidget(self.use_shortcuts_button)
        side.addWidget(button("Follow active app", self.follow_active_app))
        scroll = QScrollArea()
        scroll.setObjectName("editorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        self.stack.addWidget(scroll)

    def _build_applications(self):
        widget = QWidget()
        widget.setObjectName("applicationsPage")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 18, 0, 0)
        layout.setSpacing(16)
        row = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(6)
        heading.addWidget(label("Applications", "title"))
        heading.addWidget(label("Manage shortcuts for the apps on your keypad.", "muted", True))
        row.addLayout(heading, 1)
        self.applications_count = label("", "applicationCount")
        row.addWidget(self.applications_count, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)
        self.search = QLineEdit()
        self.search.setObjectName("applicationSearch")
        self.search.setPlaceholderText("Search assigned applications…")
        self.search.setAccessibleName("Search assigned applications")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_apps)
        toolbar.addWidget(self.search, 1)
        rescan = button("Rescan apps", self.rescan)
        rescan.setObjectName("applicationAction")
        rescan.setToolTip("Refresh installed applications and shortcut files")
        toolbar.addWidget(rescan)
        layout.addLayout(toolbar)
        self.applications_scroll = QScrollArea()
        self.applications_scroll.setObjectName("applicationsScroll")
        self.applications_scroll.setWidgetResizable(True)
        self.applications_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.applications_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cards = QWidget()
        cards.setObjectName("applicationCards")
        self.application_cards_layout = QVBoxLayout(cards)
        self.application_cards_layout.setContentsMargins(0, 0, 4, 0)
        self.application_cards_layout.setSpacing(10)
        self.application_cards_layout.addStretch()
        self.application_cards = {}
        self.applications_scroll.setWidget(cards)
        layout.addWidget(self.applications_scroll, 1)
        self.applications_empty = QFrame()
        self.applications_empty.setObjectName("applicationsEmpty")
        empty = QVBoxLayout(self.applications_empty)
        empty.setContentsMargins(24, 32, 24, 32)
        empty.addStretch()
        self.applications_empty_title = label("", "section")
        self.applications_empty_description = label("", "muted", True)
        for item in (self.applications_empty_title, self.applications_empty_description):
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.addWidget(item)
        empty.addStretch()
        layout.addWidget(self.applications_empty, 1)
        self.applications_empty.hide()
        layout.addWidget(label("Add or reorder applications on the Keys page.", "muted", True))
        self.stack.addWidget(widget)

    def _build_permissions(self):
        widget = QWidget()
        widget.setObjectName("permissionsSection")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Permissions & connection", "section"))
        layout.addWidget(label("The app runs as your normal user. Ubuntu may ask an administrator to authorize device access once.", "muted", True))
        self.permission_labels = {}
        form = QFormLayout()
        form.setVerticalSpacing(22)
        for key, title in (("keypad", "Keypad access"), ("dialpad", "Dialpad button access"), ("input", "Shortcut input"),
                           ("focus", "App detection"), ("tray", "Panel icon")):
            value = label("Not checked", wrap=True)
            self.permission_labels[key] = value
            form.addRow(title, value)
        layout.addSpacing(20)
        layout.addLayout(form)
        layout.addSpacing(15)
        self.repair_button = button("Set up device permissions…", self.setup_permissions, True)
        layout.addWidget(self.repair_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(button("Reconnect / check again", self.start_worker), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(label("On Wayland, app keys launch applications, but this version cannot focus their existing windows or switch pages automatically. X11 supports both; manual pages work on either session.", "muted", True))
        self.permissions_notice = label("", "status", True)
        self.permissions_notice.hide()
        layout.addWidget(self.permissions_notice)
        return widget

    def _build_settings(self):
        self.settings_page = QWidget()
        self.settings_page.setObjectName("settingsPage")
        outer = QVBoxLayout(self.settings_page)
        outer.setContentsMargins(32, 24, 32, 28)
        header = QHBoxLayout()
        self.settings_back = button("←", self._leave_settings)
        self.settings_back.setObjectName("topbarButton")
        self.settings_back.setAccessibleName("Back from settings")
        header.addWidget(self.settings_back)
        header.addWidget(label("Settings", "deviceTitle"))
        header.addStretch()
        outer.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(22, 24, 22, 0)
        layout.addWidget(label("Appearance", "section"))
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("Appearance")
        for title, value in (("System setting", "system"), ("Light", "light"), ("Dark", "dark")):
            self.theme_combo.addItem(title, value)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.settings["theme"]))
        self.theme_combo.currentIndexChanged.connect(self.change_theme)
        appearance_form = QFormLayout()
        appearance_form.addRow("Theme", self.theme_combo)
        layout.addLayout(appearance_form)
        layout.addWidget(label("System setting follows your desktop’s light or dark appearance.", "muted", True))
        layout.addSpacing(16)
        layout.addWidget(label("Background & configuration", "section"))
        self.start_at_login = QCheckBox("Start the keypad worker when I sign in")
        self.start_at_login.setChecked(self.settings["start_at_login"])
        self.start_at_login.toggled.connect(self.change_autostart)
        layout.addWidget(self.start_at_login)
        self.auto_switch = QCheckBox("Automatically show shortcuts after pressing an app key (X11)")
        self.auto_switch.setChecked(self.settings["auto_switch"])
        self.auto_switch.toggled.connect(lambda value: self.setting_changed("auto_switch", value))
        layout.addWidget(self.auto_switch)
        self.show_labels = QCheckBox("Show labels below icons on the keypad")
        self.show_labels.setChecked(self.settings["show_labels"])
        self.show_labels.toggled.connect(lambda value: self.setting_changed("show_labels", value))
        layout.addWidget(self.show_labels)
        layout.addWidget(label("Closing this window frees the Qt interface. The small background worker keeps the keypad and panel icon active.", "muted", True))
        layout.addSpacing(26)
        layout.addWidget(label("Take your setup to another Ubuntu PC", "section"))
        row = QHBoxLayout()
        row.addWidget(button("Export configuration…", self.export_configuration))
        row.addWidget(button("Import configuration…", self.import_configuration))
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label("Exports include layouts and shortcut profiles. Install the apps on the other PC; their original icons are resolved there.", "muted", True))
        layout.addSpacing(26)
        self.permissions_section = self._build_permissions()
        layout.addWidget(self.permissions_section)
        layout.addSpacing(20)
        layout.addWidget(label(f"Shortcut profiles\n{profiles_dir()}", "muted", True))
        layout.addWidget(label(f"MX Creative Control {__version__} · Independent Linux application", "muted"))
        layout.addStretch()
        scroll.setWidget(widget)
        self.views.addWidget(self.settings_page)

    def _populate_apps(self):
        if self.preview_profile_id not in self.profiles:
            self.preview_profile_id = "default"
        self.app_combo.blockSignals(True)
        self.app_combo.clear()
        self.app_combo.addItem("Empty key", None)
        self.app_combo.addItem(display_icon("applets-screenshooter"), "Screenshot", "@screenshot")
        self.app_combo.addItem(display_icon("view-grid"), "Open Action Ring", "@action-ring")
        for app in self.apps:
            self.app_combo.addItem(display_icon(app.icon, application=True), app.name, app.id)
        self.app_combo.blockSignals(False)
        self.launcher_actions.blockSignals(True)
        self.launcher_actions.clear()
        for index in range(self.app_combo.count()):
            item = QListWidgetItem(self.app_combo.itemIcon(index), self.app_combo.itemText(index))
            item.setData(Qt.ItemDataRole.UserRole, self.app_combo.itemData(index))
            self.launcher_actions.addItem(item)
        self.launcher_actions.blockSignals(False)
        self._filter_launcher_actions(self.launcher_search.text())
        self._populate_application_cards()
        self.select_key(self.selected_slot)
        self._populate_profile_strip()

    def _populate_application_cards(self):
        indexed = {app.id: app for app in self.apps}
        identifiers = list(dict.fromkeys(identifier for identifier in self.settings["launchers"]
                                        if identifier == "@screenshot" or identifier in indexed))
        for card in self.application_cards.values():
            self.application_cards_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.application_cards = {}
        for identifier in identifiers:
            app = indexed.get(identifier)
            system_action = identifier == "@screenshot"
            slots = [index + 1 for index, value in enumerate(self.settings["launchers"]) if value == identifier]
            card = ApplicationCard(identifier, "Screenshot" if system_action else app.name,
                                   display_icon("applets-screenshooter") if system_action else display_icon(app.icon, application=True),
                                   slots, system_action=system_action)
            if app is not None:
                self.fill_profile_combo(card.profile_combo, app.id)
                card.set_profile_summary(profile_for_app(app, self.profiles, self.settings["profile_assignments"]))
                card.profile_changed.connect(lambda profile_id, a=app: self.assign_profile(a.id, profile_id))
                card.import_requested.connect(lambda a=app: self.import_shortcuts(application=a))
                card.edit_requested.connect(lambda a=app: self.edit_shortcuts(application=a))
            self.application_cards[identifier] = card
            self.application_cards_layout.insertWidget(self.application_cards_layout.count() - 1, card)
        self.applications_count.setText(f"{len(identifiers)} assigned")
        self.filter_apps(self.search.text())

    def _populate_profile_strip(self):
        while self.profile_strip.count():
            item = self.profile_strip.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.profile_chips = []

        def chip(title, icon, profile_id):
            control = QToolButton()
            control.setObjectName("editorProfile")
            control.setFixedSize(38, 38)
            control.setIconSize(QSize(24, 24))
            control.setCheckable(True)
            control.setToolTip(title)
            control.setAccessibleName(title)
            if icon is not None:
                control.setIcon(icon)
            else:
                control.setText("▦")
            control.clicked.connect(lambda checked=False, p=profile_id: self._select_profile_chip(p))
            self.profile_strip.addWidget(control)
            self.profile_chips.append((profile_id, control))

        chip("All applications · launcher", None, None)
        indexed = {app.id: app for app in self.apps}
        added = set()
        for app_id in self.settings["launchers"]:
            app = indexed.get(app_id)
            profile = profile_for_app(app, self.profiles, self.settings["profile_assignments"]) if app else None
            if profile and profile["id"] not in added:
                chip(f"{app.name} · shortcuts", display_icon(app.icon, application=True), profile["id"])
                added.add(profile["id"])
        self.profile_scroll.setFixedWidth(min(220, max(46, len(self.profile_chips) * 43)))
        self._refresh_profile_strip()

    def _select_profile_chip(self, profile_id):
        if profile_id is None:
            self.change_page(0)
        else:
            self.preview_profile_id = profile_id
            self.use_preview_profile()

    def _refresh_profile_strip(self):
        for profile_id, control in self.profile_chips:
            control.setChecked(profile_id is None if self.page == 0 else profile_id == self.preview_profile_id)
            if control.isChecked():
                self.profile_scroll.ensureWidgetVisible(control)
        profile = self.profiles.get(self.preview_profile_id, {})
        self.keypad_scope.setText("All applications" if self.page == 0 else profile.get("name", "Shortcuts"))
        self.keypad_description.setText("Launch applications and switch to their shortcuts." if self.page == 0
                                       else "Application shortcuts · select a key to inspect its action.")

    def begin_add_launcher(self):
        self.change_page(0)
        empty = next((index for index, key in enumerate(self.runtime["pages"][0]["keys"]) if key is None), None)
        if empty is not None:
            self.select_key(empty)
        else:
            self.notice.setText("All nine keys are assigned. Choose a key to replace its action.")
        self.launcher_search.clear()
        self.launcher_search.setFocus()

    def _filter_launcher_actions(self, text):
        query = text.strip().casefold()
        visible = 0
        for index in range(self.launcher_actions.count()):
            item = self.launcher_actions.item(index)
            matches = query in f"{item.text()} {item.data(Qt.ItemDataRole.UserRole) or ''}".casefold()
            item.setHidden(not matches)
            visible += matches
        self.launcher_no_results.setVisible(not visible and self.page == 0)

    def _sync_launcher_action(self, index):
        self.launcher_actions.blockSignals(True)
        self.launcher_actions.setCurrentRow(index)
        if self.launcher_actions.currentItem() is not None:
            self.launcher_actions.scrollToItem(self.launcher_actions.currentItem())
        self.launcher_actions.blockSignals(False)
        self._refresh_action_buttons()

    def fill_profile_combo(self, combo, app_id):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Automatic match", "__auto__")
        combo.addItem("No app profile", None)
        for profile in self.profiles.values():
            combo.addItem(profile["name"], profile["id"])
        current = self.settings["profile_assignments"].get(app_id, "__auto__")
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.blockSignals(False)

    def _refresh_action_buttons(self):
        self.import_button.setEnabled(self.page == 0 and self.selected_app() is not None)
        self.edit_button.setEnabled(self.page == 1 or self.selected_app() is not None)
        self.use_shortcuts_button.setEnabled(self.page == 1)
        self.use_shortcuts_button.setVisible(self.page == 1)

    def selected_app(self):
        app_id = self.app_combo.currentData()
        return next((app for app in self.apps if app.id == app_id), None)

    def select_key(self, slot):
        self.selected_slot = slot
        for index, key in enumerate(self.key_buttons):
            key.setChecked(index == slot)
        self.inspector.setAccessibleName(f"Key {slot + 1} settings")
        app_id = self.settings["launchers"][slot]
        self.app_combo.setCurrentIndex(max(0, self.app_combo.findData(app_id)))
        self._sync_launcher_action(self.app_combo.currentIndex())
        self._refresh_action_buttons()
        self.key_action_title.setText("Application" if self.page == 0 else "Shortcut")
        self.key_description.setText("Choose an app or a system action for this key." if self.page == 0 else "Edit this app’s shortcuts to change its keys.")
        self.app_combo.setEnabled(self.page == 0)
        self.launcher_actions.setVisible(self.page == 0)
        self.launcher_search.setVisible(self.page == 0)
        self.shortcut_preview.setVisible(self.page == 1)
        if self.page == 1:
            profile = self.profiles.get(self.preview_profile_id, self.profiles.get("default", {}))
            shortcut = next((item for item in profile.get("shortcuts", []) if item["slot"] == slot + 1), {})
            self.shortcut_name.setText(shortcut.get("label", "Not assigned"))
            self.shortcut_keys.setText(shortcut.get("keys", "Use Edit shortcuts to add an action here."))
        self._filter_launcher_actions(self.launcher_search.text())
        self.apply_button.setEnabled(self.page == 0)
        self.apply_button.setVisible(self.page == 0)

    def change_page(self, page):
        self.page = page
        self.refresh_keys()
        self.select_key(self.selected_slot)
        if self.connect_worker:
            try:
                ipc.request("page", index=page)
            except ipc.WorkerUnavailable:
                pass

    def use_preview_profile(self):
        profile_id = self.preview_profile_id
        available = profile_id == "default" or any(p["id"] == profile_id for p in self.runtime["profiles"])
        if not available:
            QMessageBox.information(self, "Assign this profile first", "Assign this profile to an installed application in Applications before using it on the keypad.")
            return
        if self.connect_worker:
            try:
                response = ipc.request("profile", id=profile_id)
                if not response["ok"]:
                    raise RuntimeError(response.get("error", "Could not select profile."))
            except (ipc.WorkerUnavailable, RuntimeError) as exc:
                self.notice.setText(str(exc))
                return
        self.change_page(1)
        self.notice.setText("Manual shortcut profile selected. Use Follow active app to resume automatic matching.")

    def follow_active_app(self):
        if self.connect_worker:
            try:
                ipc.request("profile", id=None)
            except ipc.WorkerUnavailable as exc:
                self.notice.setText(str(exc))
                return
        self.settings["auto_switch"] = True
        self.auto_switch.blockSignals(True)
        self.auto_switch.setChecked(True)
        self.auto_switch.blockSignals(False)
        self.safe_save()
        self.notice.setText("Automatic app shortcuts are enabled on X11. App keys switch pages while their app is active.")

    def refresh_keys(self, *_):
        if not self.runtime:
            return
        if self.page == 0:
            keys = self.runtime["pages"][0]["keys"]
        else:
            profile_id = self.preview_profile_id
            profile = self.profiles.get(profile_id, self.profiles.get("default"))
            keys = [None] * 9
            if profile:
                renderer = FaceRenderer(self.settings["show_labels"])
                for shortcut in profile["shortcuts"]:
                    keys[shortcut["slot"] - 1] = {"label": shortcut["label"], "image": renderer(shortcut["label"], shortcut["icon"])}
        for key, entry in zip(self.key_buttons, keys):
            key.setIcon(QIcon(str(cache_dir() / entry["image"])) if entry else QIcon())
            key.setText("" if entry else "+")
            key.setToolTip(entry["label"] if entry else "Empty key")
            key.set_reorder_enabled(self.page == 0)
            key.set_draggable(self.page == 0 and entry is not None)
        self.reorder_hint.setVisible(self.page == 0)
        for index, control in enumerate(self.page_buttons):
            control.setChecked(index == self.page)
        self._refresh_profile_strip()

    def persist(self, *, notify=True, settings=None):
        candidate = self.settings if settings is None else settings
        candidate = {**candidate, "launchers": compact_launchers(candidate["launchers"], self.apps)}
        compiled = compile_runtime(candidate, self.apps, self.profiles,
                                   FaceRenderer(candidate["show_labels"]))
        save_settings(candidate)
        try:
            atomic_json(runtime_path(), compiled)
        except OSError:
            # A failed runtime write must not leave a drag saved only on disk.
            save_settings(self.settings)
            raise
        self.settings = candidate
        self.runtime = compiled
        if notify and self.connect_worker:
            try:
                response = ipc.request("reload")
                if not response["ok"]:
                    raise RuntimeError(response.get("error", "Worker rejected the layout."))
                self.worker_sync_error = None
                self.worker_error = None
                self.notice.setText("Layout saved.")
            except ipc.WorkerUnavailable:
                self.notice.setText("Saved. Start the worker to apply this layout.")
            except RuntimeError as exc:
                self.worker_sync_error = str(exc)
                self.notice.setText(f"Layout saved, but not applied: {exc}")
        self.refresh_keys()

    def apply_key(self):
        launchers = list(self.settings["launchers"])
        launchers[self.selected_slot] = self.app_combo.currentData()
        candidate = {**self.settings, "launchers": launchers,
                     "profile_assignments": dict(self.settings["profile_assignments"])}
        available = {app.id for app in self.apps} | {"@screenshot", "@action-ring"}
        selected = sum(app_id in available for app_id in launchers[:self.selected_slot])
        try:
            self.persist(settings=candidate)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Could not save configuration", str(exc))
            return
        self.select_key(selected)
        self._populate_apps()

    def move_launcher(self, source, destination):
        """Insert the dragged app at a key; dropping past the last app appends."""
        if self.page != 0 or any(type(slot) is not int or not 0 <= slot < 9
                                 for slot in (source, destination)):
            return
        count = sum(key is not None for key in self.runtime["pages"][0]["keys"])
        if source >= count:
            return
        destination = min(destination, count - 1)
        if source == destination:
            return
        launchers = list(self.settings["launchers"])
        launchers.insert(destination, launchers.pop(source))
        try:
            self.persist(settings={**self.settings, "launchers": launchers})
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Could not save order", str(exc))
            return
        self.select_key(destination)
        self._populate_application_cards()
        self._populate_profile_strip()

    def _set_assignment(self, app_id, profile_id, *, settings=None):
        settings = self.settings if settings is None else settings
        if profile_id == "__auto__":
            settings["profile_assignments"].pop(app_id, None)
        else:
            settings["profile_assignments"][app_id] = profile_id

    def assign_profile(self, app_id, profile_id):
        self._set_assignment(app_id, profile_id)
        self.safe_save()
        self._refresh_action_buttons()
        self._populate_profile_strip()
        app = next((app for app in self.apps if app.id == app_id), None)
        card = self.application_cards.get(app_id)
        if app is not None and card is not None:
            card.set_profile_summary(profile_for_app(app, self.profiles, self.settings["profile_assignments"]))

    def safe_save(self):
        try:
            self.persist()
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Could not save configuration", str(exc))

    def rescan(self):
        self.apps = discover_applications()
        self.profiles = load_profiles()
        self.safe_save()
        self._populate_apps()
        self.ring_editor.set_applications(self.apps)
        self.notice.setText(f"Found {len(self.apps)} installed applications.")

    def filter_apps(self, text):
        query = text.strip().casefold()
        visible = 0
        for card in self.application_cards.values():
            matches = query in card.search_text
            card.setVisible(matches)
            visible += matches
        self.applications_scroll.setVisible(bool(visible))
        self.applications_empty.setVisible(not visible)
        if self.application_cards:
            self.applications_empty_title.setText("No matching applications")
            self.applications_empty_description.setText("Try another name or clear your search.")
        else:
            self.applications_empty_title.setText("No applications assigned yet")
            self.applications_empty_description.setText("Choose an application on the Keys page to add it here.")

    def import_shortcuts(self, checked=False, *, application=None):
        application = application or self.selected_app()
        if application is None:
            QMessageBox.information(self, "Choose an application", "Choose an application first, or use the Applications page.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Import shortcut profile", "", "Shortcut profiles (*.json)", "", QFileDialog.Option.DontUseNativeDialog)
        if not filename:
            return
        try:
            try:
                imported = import_profile(Path(filename))
            except ProfileExistsError:
                if QMessageBox.question(self, "Replace shortcut profile?", "A profile with this ID already exists. Replace its local copy?") != QMessageBox.StandardButton.Yes:
                    return
                imported = import_profile(Path(filename), overwrite=True)
            self.profiles = load_profiles()
            self.settings["profile_assignments"][application.id] = imported["id"]
            self.safe_save()
            self._populate_apps()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Profile could not be imported", str(exc))

    def edit_shortcuts(self, checked=False, *, application=None):
        preview_edit = self.page == 1 and application is None
        if preview_edit:
            profile = self.profiles.get(self.preview_profile_id)
            application = next((a for a in self.apps if profile and a.id in profile["desktop_ids"]), None)
        else:
            application = application or self.selected_app()
            if application is None:
                QMessageBox.information(self, "Choose an application", "Choose an application first, or use the Applications page.")
                return
            profile = profile_for_app(application, self.profiles, self.settings["profile_assignments"])
        if application is None and profile is None:
            return
        editor = ProfileEditor(application, profile, self)
        if editor.exec() and editor.result_profile:
            self.profiles = load_profiles()
            if not preview_edit:
                self.settings["profile_assignments"][application.id] = editor.result_profile["id"]
            self.safe_save()
            self._populate_apps()

    def setting_changed(self, key, value):
        self.settings[key] = value
        self.safe_save()

    def change_theme(self, *_):
        """Appearance is a UI preference; never upload keypad images for it."""
        theme = self.theme_combo.currentData()
        candidate = {**self.settings, "theme": theme}
        try:
            save_settings(candidate)
        except (OSError, ValueError) as exc:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.settings["theme"]))
            self.theme_combo.blockSignals(False)
            QMessageBox.warning(self, "Could not save appearance", str(exc))
            return
        self.settings = candidate
        self.appearance.set_preference(theme)
        self.notice.setText("Appearance saved.")

    def change_autostart(self, enabled):
        try:
            service.set_autostart(enabled)
            self.setting_changed("start_at_login", enabled)
        except (OSError, RuntimeError) as exc:
            self.start_at_login.blockSignals(True)
            self.start_at_login.setChecked(not enabled)
            self.start_at_login.blockSignals(False)
            QMessageBox.information(self, "Automatic startup", str(exc))

    def export_configuration(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Export configuration", "mx-creative-control.json", "Configuration (*.json)", "", QFileDialog.Option.DontUseNativeDialog)
        if filename:
            try:
                export_bundle(Path(filename), self.settings, self.profiles)
                self.notice.setText("Configuration exported.")
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def import_configuration(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Import configuration", "", "Configuration (*.json)", "", QFileDialog.Option.DontUseNativeDialog)
        if not filename:
            return
        try:
            settings, profiles = read_bundle(Path(filename))
            if QMessageBox.question(self, "Replace configuration?", "Replace your launcher layout and the profiles included in this export? Available apps will fill the keys in order; unavailable apps stay saved for other computers.") != QMessageBox.StandardButton.Yes:
                return
            # Startup is a machine preference, not silently changed by an imported file.
            settings["start_at_login"] = self.settings["start_at_login"]
            compile_runtime(settings, self.apps, profiles, FaceRenderer(settings["show_labels"]))
            write_imported_profiles(profiles)
            self.settings = settings
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(self.theme_combo.findData(settings["theme"]))
            self.theme_combo.blockSignals(False)
            self.appearance.set_preference(settings["theme"])
            self.profiles = load_profiles()
            for checkbox, name in ((self.auto_switch, "auto_switch"), (self.show_labels, "show_labels")):
                checkbox.blockSignals(True)
                checkbox.setChecked(settings[name])
                checkbox.blockSignals(False)
            self.persist()
            self._populate_apps()
            self.ring_editor.set_settings(self.settings)
            self.details.set_bindings(self.settings["action_ring"]["bindings"])
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Import failed", str(exc))

    def start_worker(self, *, preserve_context=False):
        if not self.connect_worker:
            return
        self.connection.setText("Connecting keypad worker…")
        try:
            response = service.ensure_running()
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "The worker could not start."))
            if preserve_context:
                self.worker_sync_error = None
                self.refresh_status()
                self.page = self.status.get("page", 0)
                self.refresh_keys()
                return
            for command, values in (("reload", {}), ("page", {"index": 0})):
                response = ipc.request(command, **values)
                if not response.get("ok"):
                    raise RuntimeError(response.get("error", f"The worker rejected {command}."))
            self.page = 0
            self.worker_sync_error = None
            self.refresh_keys()
            self.select_key(self.selected_slot)
            self.refresh_status()
        except (OSError, RuntimeError) as exc:
            self.worker_sync_error = str(exc)
            self._worker_failed(exc)

    def activate_settings(self, *, ring_layout=None, add_ring_action=False):
        """Reopening an existing window also repairs a lost worker connection."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if ring_layout is not None:
            self.show_ring(ring_layout, add_action=add_ring_action)
        else:
            self.show_home()
        self.refresh_devices()
        self.start_worker(preserve_context=ring_layout is not None)

    def closeEvent(self, event):
        self.device_timer.stop()
        self.timer.stop()
        super().closeEvent(event)

    def _worker_failed(self, error):
        self.status = {}
        self.connection.setText("Worker not running" if isinstance(error, ipc.WorkerUnavailable) else "Keypad setup failed")
        self.active_label.setText("Active application: —")
        for widget in self.permission_labels.values():
            widget.setText("Not checked · reconnect to verify")
        self.worker_error = str(error)
        self.notice.setText(self.worker_error)

    def refresh_status(self):
        try:
            self.status = ipc.request("status")
            if not self.status.get("ok"):
                raise RuntimeError(self.status.get("error", "The worker could not report its status."))
        except (OSError, RuntimeError) as exc:
            self._worker_failed(exc)
            return
        status = self.status
        device = self.devices.get(self.selected_device_id)
        if device is not None:
            backend = status.get("dialpad_buttons" if device.product_id == 0xbc00 else "device_buttons", {})
            self.details.set_backend_status("\n".join(backend.get("errors", [])))
        self.connection.setText("Paused" if status.get("paused") else "Keypad connected" if status.get("connected") else "Keypad disconnected")
        self.active_label.setText("Active application: " + (status.get("active_app") or "—"))
        self.permission_labels["keypad"].setText("Ready" if status.get("keypad_access") else "Connect the keypad or set up device access")
        dialpad_status = status.get("dialpad_buttons", {})
        self.permission_labels["dialpad"].setText(
            "Permission required · use Set up device permissions below" if dialpad_status.get("permission_required") else
            "Ready" if dialpad_status.get("active") else
            "\n".join(dialpad_status.get("errors", [])) or "Assign a dialpad button to check access")
        self.permission_labels["input"].setText("Ready" if status.get("input_access") else "Permission required for /dev/uinput")
        self.permission_labels["focus"].setText("Ready · X11" if status.get("focus_supported") else "Manual selection · automatic detection unavailable")
        self.permission_labels["tray"].setText("Ready" if status.get("tray_available") else "No panel host found. Open settings from the application launcher; on GNOME, enable AppIndicator support.")
        if self.worker_sync_error:
            self.connection.setText("Layout not applied")
            self.notice.setText(self.worker_sync_error)
            return
        error = status.get("last_error") or status.get("error")
        if error:
            self.worker_error = str(error)
            self.notice.setText(self.worker_error)
        elif self.worker_error:
            if self.notice.text() == self.worker_error:
                self.notice.setText("")
            self.worker_error = None

    def setup_permissions(self):
        helper = Path("/usr/lib/mx-creative-control/setup-permissions")
        if not helper.is_file():
            QMessageBox.information(self, "Install the Ubuntu package", "Device permission setup is included in the .deb package. Install it first, then reopen this dialog.")
            return
        details = ("Ubuntu will ask for administrator authentication.\n\n"
                   "This installs access rules for the MX Creative Keypad and Bluetooth MX Dialpad, and enables /dev/uinput so the app can send keyboard shortcuts. "
                   "The input permission also lets other programs in your logged-in session create virtual input devices.\n\n"
                   "The settings window and background worker continue running as your normal user.")
        if QMessageBox.question(self, "Allow keypad and shortcut access?", details,
                                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        self.repair_button.setEnabled(False)
        self.permission_process = QProcess(self)
        self.permission_process.finished.connect(self._permissions_finished)
        self.permission_process.errorOccurred.connect(self._permissions_error)
        self.permission_process.start("/usr/bin/pkexec", [str(helper)])

    def _permissions_error(self, error):
        self.repair_button.setEnabled(True)
        message = "Ubuntu’s authorization service could not start. Check that polkit is installed."
        self.notice.setText(message)
        self.permissions_notice.setText(message)
        self.permissions_notice.show()

    def _permissions_finished(self, code, status):
        self.repair_button.setEnabled(True)
        if code == 0:
            message = "Permissions updated. Reconnect the keypad if it is still unavailable."
            self.notice.setText(message)
            self.permissions_notice.setText(message)
            self.permissions_notice.show()
            self.start_worker()
        elif code in (126, 127):
            message = "Permission setup was cancelled or could not be authorized."
            self.notice.setText(message)
            self.permissions_notice.setText(message)
            self.permissions_notice.show()
        else:
            details = bytes(self.permission_process.readAllStandardError()).decode(errors="replace")
            QMessageBox.warning(self, "Permission setup failed", details.strip() or "Ubuntu could not update the device permissions.")
