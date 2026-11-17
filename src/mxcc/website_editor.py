"""Website overrides for device controls and the shared Action Ring."""

from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from .action_picker import ActionPicker
from .actions import is_dial_target, supported_device_target, validate_action_entry
from .customization import CustomizationWorkspace
from .device_controls import MODEL_CONTROLS
from .ring_editor import RingEditor
from .website_profiles import normalize_domain, validate_website_profiles


class _WebsiteRing(RingEditor):
    domain = ""

    def _refresh(self):
        super()._refresh()
        self.layout_name.setText(self.domain)
        self.layout_description.setText("Website-specific Action Rings are used in Firefox only")
        self.canvas.title = self.domain


class _WebsiteTabs(QTabWidget):
    def heightForWidth(self, width):
        page = self.currentWidget()
        if page is None:
            return super().heightForWidth(width)
        # The action picker has its own scrolling. Do not force the outer page
        # to use its preferred height and push Apply beneath the fixed footer.
        height = page.layout().minimumHeightForWidth(max(1, width - 4))
        return max(page.minimumSizeHint().height(), height) + self.tabBar().sizeHint().height() + 4


class DialShortcutPicker(QWidget):
    """Edit both directions as one physical-control assignment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Large dial shortcuts")
        title.setObjectName("section")
        layout.addWidget(title)
        hint = QLabel("Send a keyboard shortcut for each dial increment. "
                      "The small roller keeps its normal behavior.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Video frames")
        self.clockwise_edit = QLineEdit()
        self.clockwise_edit.setPlaceholderText("Right")
        self.counterclockwise_edit = QLineEdit()
        self.counterclockwise_edit.setPlaceholderText("Left")
        for title, field in (("Action name", self.label_edit),
                             ("Clockwise shortcut", self.clockwise_edit),
                             ("Counterclockwise shortcut", self.counterclockwise_edit)):
            field.setAccessibleName(title)
            form.addRow(title, field)
        layout.addLayout(form)
        self.video_button = QPushButton("Use video frame shortcuts")
        self.video_button.clicked.connect(self._video_shortcuts)
        layout.addWidget(self.video_button)
        example = QLabel("Video preset: clockwise → Right (next frame), "
                         "counterclockwise → Left (previous frame). "
                         "The website must support these keys; click its video viewer first.")
        example.setWordWrap(True)
        example.setObjectName("muted")
        layout.addWidget(example)
        layout.addStretch()
        self._icon = "input-keyboard"

    def _video_shortcuts(self):
        self.label_edit.setText("Video frames")
        self.clockwise_edit.setText("Right")
        self.counterclockwise_edit.setText("Left")

    def draft(self):
        return {"label": self.label_edit.text(), "icon": self._icon,
                "clockwise": self.clockwise_edit.text(), "counterclockwise": self.counterclockwise_edit.text()}

    def set_draft(self, draft):
        self.label_edit.setText(draft["label"])
        self._icon = draft["icon"]
        self.clockwise_edit.setText(draft["clockwise"])
        self.counterclockwise_edit.setText(draft["counterclockwise"])

    def set_entry(self, entry):
        self.set_draft({"label": entry["label"] if entry else "", "icon": entry["icon"] if entry else "input-keyboard",
                        "clockwise": entry["action"]["clockwise"] if entry else "",
                        "counterclockwise": entry["action"]["counterclockwise"] if entry else ""})

    def value(self):
        return validate_action_entry({"label": self.label_edit.text(), "icon": self._icon,
                                      "action": {"type": "dial_shortcut", "clockwise": self.clockwise_edit.text(),
                                                 "counterclockwise": self.counterclockwise_edit.text()}}, allow_dial=True)


class WebsiteEditor(QWidget):
    changed = Signal(object)
    setup_requested = Signal()

    def __init__(self, settings, applications, parent=None):
        super().__init__(parent)
        self._profiles = []
        self._settings = {}
        self._loading = False
        self._devices = {}
        self._status = {}
        self.current_domain = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.website_combo = QComboBox()
        self.website_combo.setMinimumWidth(200)
        self.website_combo.setAccessibleName("Website profile")
        self.website_combo.currentIndexChanged.connect(self._select_website)
        top.addWidget(self.website_combo, 1)
        self.add_button = QPushButton("Add website")
        self.add_button.clicked.connect(self._add_dialog)
        top.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove website")
        self.remove_button.clicked.connect(self._remove_dialog)
        top.addWidget(self.remove_button)
        self.setup_button = QPushButton("Set up Firefox")
        self.setup_button.clicked.connect(self.setup_requested)
        top.addWidget(self.setup_button)
        layout.addLayout(top)
        self.connection = QLabel("ChatGPT's embedded browser connects automatically. Set up Firefox to use Firefox tabs.")
        self.connection.setWordWrap(True)
        self.connection.setObjectName("status")
        layout.addWidget(self.connection)
        self.empty = QLabel("Add a website, then choose its control shortcuts or Action Ring.\n"
                            "Profiles match the exact hostname, including subdomains.")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.tabs = _WebsiteTabs()
        layout.addWidget(self.tabs, 1)
        self.buttons = CustomizationWorkspace()
        self.tabs.addTab(self.buttons, "Control shortcuts")
        stage = self.buttons.stage_layout
        title = QLabel("Website control actions")
        title.setObjectName("section")
        stage.addWidget(title)
        explanation = QLabel("Choose a device and control, then apply a shortcut. "
                             "These assignments take priority on this website. "
                             "Other controls keep their usual actions.")
        explanation.setWordWrap(True)
        stage.addWidget(explanation)
        self.device_combo = QComboBox()
        self.device_combo.setAccessibleName("Device for website shortcuts")
        self.device_combo.currentIndexChanged.connect(self._populate_controls)
        stage.addWidget(self.device_combo)
        self.control_combo = QComboBox()
        self.control_combo.setAccessibleName("Control for website shortcuts")
        self.control_combo.currentIndexChanged.connect(self._select_control)
        stage.addWidget(self.control_combo)
        self.assignment = QLabel()
        self.assignment.setWordWrap(True)
        stage.addWidget(self.assignment)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        stage.addWidget(self.summary)
        stage.addStretch()
        note = QLabel("The MX Dialpad's large dial supports directional shortcuts. "
                      "Small roller and mouse wheel mappings are not available yet. "
                      "To use a website ring, assign a button to System → Open Action Ring.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        stage.addWidget(note)
        panel = self.buttons.panel
        self.picker = ActionPicker(applications)
        panel.addWidget(self.picker, 1)
        self.dial_picker = DialShortcutPicker()
        panel.addWidget(self.dial_picker, 1)
        self.dial_status = QLabel()
        self.dial_status.setObjectName("status")
        self.dial_status.setWordWrap(True)
        self.dial_status.hide()
        panel.addWidget(self.dial_status)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("status")
        self.error_label.hide()
        panel.addWidget(self.error_label)
        actions = QHBoxLayout()
        self.clear_button = QPushButton("Use normal action")
        self.clear_button.clicked.connect(self.clear_mapping)
        actions.addWidget(self.clear_button)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("editorApply")
        self.apply_button.clicked.connect(self.apply_mapping)
        actions.addWidget(self.apply_button)
        panel.addLayout(actions)
        ring_page = QWidget()
        ring_layout = QVBoxLayout(ring_page)
        ring_layout.setContentsMargins(0, 4, 0, 0)
        self.custom_ring = QCheckBox("Use a separate Action Ring for this website")
        self.custom_ring.toggled.connect(self._toggle_ring)
        ring_layout.addWidget(self.custom_ring)
        ring_hint = QLabel("Website-specific Action Rings work in Firefox. "
                           "For ChatGPT, leave this unchecked to use its normal app or default ring. "
                           "Website dial and button shortcuts work in both browsers.")
        ring_hint.setWordWrap(True)
        ring_hint.setObjectName("muted")
        ring_layout.addWidget(ring_hint)
        self.ring_editor = _WebsiteRing({"layouts": {"default": []}, "bindings": []}, applications)
        self.ring_editor.canvas.setMinimumHeight(280)
        for control in (self.ring_editor.add_layout_button, self.ring_editor.profile_scroll,
                        self.ring_editor.import_button, self.ring_editor.export_button,
                        self.ring_editor.preview_button):
            control.hide()
        self.ring_editor.changed.connect(self._save_ring)
        ring_layout.addWidget(self.ring_editor, 1)
        self.tabs.addTab(ring_page, "Action Ring")
        self.set_devices([])
        self.set_settings(settings)

    def value(self):
        return deepcopy(self._profiles)

    def _profile(self):
        return next((item for item in self._profiles if item["domain"] == self.current_domain), None)

    def set_settings(self, settings):
        self._settings = deepcopy(settings)
        self._profiles = validate_website_profiles(settings.get("website_profiles", []))
        self.website_combo.blockSignals(True)
        self.website_combo.clear()
        for profile in self._profiles:
            self.website_combo.addItem(profile["domain"], profile["domain"])
        index = self.website_combo.findData(self.current_domain)
        self.website_combo.setCurrentIndex(max(0, index))
        self.website_combo.blockSignals(False)
        self._select_website()

    def set_applications(self, applications):
        self.picker.set_applications(applications)
        self.ring_editor.set_applications(applications)

    def set_devices(self, devices):
        previous = self.device_combo.currentData()
        previous_target = self._target()
        draft = self.picker.draft()
        dial_draft = self.dial_picker.draft()
        entries = {
            (0x046d, 0xbc00): ("MX Dialpad", MODEL_CONTROLS[(0x046d, 0xbc00)]),
            (0x046d, 0xb042): ("MX Master 4", MODEL_CONTROLS[(0x046d, 0xb042)]),
            (0x046d, 0xc07e): ("G402", MODEL_CONTROLS[(0x046d, 0xc07e)]),
        }
        for device in devices:
            if any(supported_device_target(device.vendor_id, device.product_id, control.id)
                   for control in device.controls):
                entries[(device.vendor_id, device.product_id)] = (device.name, device.controls)
        if entries == self._devices:
            return
        self._devices = entries
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for key, (name, _) in entries.items():
            self.device_combo.addItem(name, key)
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(previous)))
        self.device_combo.blockSignals(False)
        self._populate_controls()
        if self._target() == previous_target:
            self.picker.set_draft(draft)
            self.dial_picker.set_draft(dial_draft)

    def select_device(self, vendor, product):
        index = self.device_combo.findData((vendor, product))
        if index >= 0:
            self.device_combo.setCurrentIndex(index)

    def _populate_controls(self):
        previous = self.control_combo.currentData()
        self.control_combo.blockSignals(True)
        self.control_combo.clear()
        key = self.device_combo.currentData()
        if key in self._devices:
            for control in self._devices[key][1]:
                if supported_device_target(*key, control.id):
                    self.control_combo.addItem("Large dial" if is_dial_target(*key, control.id) else control.label, control.id)
        self.control_combo.setCurrentIndex(max(0, self.control_combo.findData(previous)))
        self.control_combo.blockSignals(False)
        self._select_control()

    def _target(self):
        key, control = self.device_combo.currentData(), self.control_combo.currentData()
        if key is None or control is None:
            return None
        return {"vendor_id": key[0], "product_id": key[1], "control_id": control}

    def _entry(self, values):
        target = self._target()
        return next((entry for entry in values if target and all(entry[k] == v for k, v in target.items())), None)

    def _dial_selected(self):
        target = self._target()
        return target is not None and is_dial_target(target["vendor_id"], target["product_id"], target["control_id"])

    def _select_control(self):
        profile = self._profile()
        entry = self._entry(profile["device_actions"]) if profile else None
        normal = self._entry(self._settings.get("device_actions", []))
        if normal is None and self._target() in self._settings.get("action_ring", {}).get("bindings", []):
            normal = {"label": "Action Ring", "icon": "view-grid", "action": {"type": "open_ring"}}
        dial = self._dial_selected()
        self.picker.setVisible(not dial)
        self.dial_picker.setVisible(dial)
        if dial:
            self.dial_picker.set_entry(entry or normal)
        else:
            self.picker.set_entry(entry or normal)
        self.clear_button.setEnabled(entry is not None)
        self.apply_button.setEnabled(profile is not None and self._target() is not None)
        self.assignment.setText(f"Website action: {entry['label']}" if entry else
                                f"Normal action: {normal['label']}" if normal else "Using the device's normal behavior")
        count = len(profile["device_actions"]) if profile else 0
        self.summary.setText(f"{count} control assignment{'s' if count != 1 else ''} for this website")
        self.error_label.hide()
        self._refresh_dial_status()

    def _select_website(self, *_):
        self.current_domain = self.website_combo.currentData()
        profile = self._profile()
        self.tabs.setVisible(profile is not None)
        self.empty.setVisible(profile is None)
        self.remove_button.setEnabled(profile is not None)
        self._select_control()
        self._loading = True
        try:
            custom = profile is not None and profile["ring"] is not None
            self.custom_ring.setChecked(custom)
            self.ring_editor.domain = self.current_domain or "Website"
            slots = profile["ring"] if custom else self._settings.get("action_ring", {}).get("layouts", {}).get("default", [])
            self.ring_editor.set_settings({"layouts": {"default": slots}, "bindings": []})
            self.ring_editor.setEnabled(custom)
        finally:
            self._loading = False

    def add_website(self, value):
        domain = normalize_domain(value)
        if not any(item["domain"] == domain for item in self._profiles):
            candidate = self.value() + [{"domain": domain, "device_actions": [], "ring": None}]
            self._profiles = validate_website_profiles(candidate)
            self.current_domain = domain
            self._publish()
        self.open_website(domain)
        return domain

    def open_website(self, domain, *, ring=False, add_action=False):
        index = self.website_combo.findData(domain)
        if index < 0:
            return False
        self.website_combo.setCurrentIndex(index)
        self.tabs.setCurrentIndex(1 if ring else 0)
        if ring and add_action:
            self.custom_ring.setChecked(True)
            self.ring_editor.begin_add()
        return True

    def _add_dialog(self):
        value, accepted = QInputDialog.getText(self, "Add website", "Website address or hostname (for example github.com)")
        if accepted:
            try:
                self.add_website(value)
            except ValueError as error:
                QMessageBox.warning(self, "Invalid website", str(error))

    def remove_website(self):
        self._profiles = [item for item in self._profiles if item["domain"] != self.current_domain]
        self.current_domain = None
        self._publish()

    def _remove_dialog(self):
        if QMessageBox.question(self, "Remove website", f"Remove all overrides for {self.current_domain}?") == QMessageBox.StandardButton.Yes:
            self.remove_website()

    def _publish(self):
        self._settings["website_profiles"] = self.value()
        self.set_settings(self._settings)
        self.changed.emit(self.value())

    def apply_mapping(self):
        profile, target = self._profile(), self._target()
        if profile is None or target is None:
            return
        try:
            entry = self.dial_picker.value() if self._dial_selected() else self.picker.value()
        except ValueError as error:
            self.error_label.setText(str(error)); self.error_label.show()
            return
        profile["device_actions"] = [item for item in profile["device_actions"] if not all(item[k] == v for k, v in target.items())]
        profile["device_actions"].append({**target, **entry})
        self._publish()

    def clear_mapping(self):
        profile, target = self._profile(), self._target()
        if profile is not None and target:
            profile["device_actions"] = [item for item in profile["device_actions"] if not all(item[k] == v for k, v in target.items())]
            self._publish()

    def _toggle_ring(self, checked):
        if self._loading or self._profile() is None:
            return
        self._profile()["ring"] = self.ring_editor.value()["layouts"]["default"] if checked else None
        self._publish()

    def _save_ring(self):
        if not self._loading and self._profile() is not None:
            self._profile()["ring"] = self.ring_editor.value()["layouts"]["default"]
            self._publish()

    def set_status(self, status):
        self._status = deepcopy(status)
        domain = status.get("active_website")
        source = status.get("website_source")
        active_app = status.get("active_app")
        chatgpt_focused = isinstance(active_app, str) and active_app.casefold() == "chatgpt"
        embedded = status.get("embedded_browser", {})
        embedded = embedded if isinstance(embedded, dict) else {}
        errors = embedded.get("errors", [])
        embedded_error = " · ".join(value for value in errors if isinstance(value, str)) if isinstance(errors, list) else ""
        if domain:
            suffix = "Website profile active" if status.get("website_profile") else "Using normal actions"
            browser = "ChatGPT embedded browser" if source == "chatgpt" else "Firefox"
            text = f"{browser} · {domain} · {suffix}"
        elif chatgpt_focused:
            if embedded_error:
                text = f"ChatGPT embedded browser · {embedded_error}"
            elif embedded.get("available") is False:
                text = "ChatGPT embedded browser · Website detection is unavailable"
            else:
                text = ("ChatGPT embedded browser · Click inside a website to use its shortcuts. "
                        "Chat and the address bar use normal actions.")
        elif status.get("browser_error"):
            text = f"Firefox · {status['browser_error']}"
        elif status.get("browser_connected"):
            text = "Firefox connected · Select a website tab to use its shortcuts"
        elif embedded.get("connected"):
            text = "ChatGPT embedded browser ready · Click inside a website. Set up Firefox to use Firefox tabs."
        elif embedded.get("available"):
            text = "ChatGPT embedded browser available · Waiting for website focus. Set up Firefox to use Firefox tabs."
        else:
            text = "ChatGPT's embedded browser connects automatically · Firefox is not connected; use Set up Firefox for Firefox tabs"
        self.connection.setText(text)
        self._refresh_dial_status()

    def _refresh_dial_status(self):
        backend = self._status.get("dialpad_dial", {})
        errors = backend.get("errors", []) if isinstance(backend, dict) else []
        error = " · ".join(value for value in errors if isinstance(value, str)) if isinstance(errors, list) else ""
        self.dial_status.setText(f"Large dial: {error}" if error else "")
        self.dial_status.setVisible(self._dial_selected() and bool(error))
