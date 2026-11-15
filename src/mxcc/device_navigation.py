"""Device discovery stays off the UI thread and ends with the settings process."""

from copy import deepcopy

from PySide6.QtCore import QObject, QPointF, QRunnable, QSize, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from .action_picker import ActionPicker
from .customization import CustomizationWorkspace
from .device_views import ConnectionIndicator
from .device_interaction import InteractiveDeviceArt
from .icons import application_icon


class _ScanSignals(QObject):
    finished = Signal(object, str)


class _Scan(QRunnable):
    def __init__(self, provider):
        super().__init__()
        self.provider = provider
        self.signals = _ScanSignals()

    def run(self):
        try:
            devices = list(self.provider())
        except Exception as error:
            self.signals.finished.emit([], str(error))
        else:
            self.signals.finished.emit(devices, "")


class DeviceScanner(QObject):
    updated = Signal(object)
    failed = Signal(str)
    scanning = Signal(bool)

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.busy = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

    @Slot()
    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.scanning.emit(True)
        scan = _Scan(self.provider)
        scan.signals.finished.connect(self._finished)
        self.pool.start(scan)

    @Slot(object, str)
    def _finished(self, devices, error):
        self.busy = False
        self.scanning.emit(False)
        if error:
            self.failed.emit(error)
        else:
            self.updated.emit(devices)


class _AssignmentCombo(QComboBox):
    """Rounded empty selector with a palette-aware dropdown indicator."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Text), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x, y = self.width() - 14, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5)]))
        painter.end()


class DeviceDetails(QWidget):
    """Discovered controls with unassigned options, plus connection facts."""

    back_requested = Signal()
    assignment_changed = Signal(str, bool)  # Compatibility for older integrations.
    mapping_changed = Signal(str, object)
    edit_ring_requested = Signal()

    def __init__(self, parent=None, *, applications=()):
        super().__init__(parent)
        self.setObjectName("deviceDetails")
        self._bindings = []
        self._actions = []
        self._device = None
        self._backend_error = ""
        self.controls = {}
        self.selected_control = None
        self._controls_key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 0)
        layout.setSpacing(12)
        header = QHBoxLayout()
        self.back_button = QPushButton("←")
        self.back_button.setObjectName("topbarButton")
        self.back_button.setAccessibleName("All devices")
        self.back_button.setToolTip("All devices")
        self.back_button.clicked.connect(self.back_requested)
        header.addWidget(self.back_button)
        self.title = QLabel()
        self.title.setObjectName("deviceTitle")
        header.addWidget(self.title)
        header.addStretch()
        layout.addLayout(header)
        self.workspace = CustomizationWorkspace()
        artwork = self.workspace.stage_layout
        scope = QHBoxLayout()
        scope_title = QLabel("All applications")
        scope_title.setObjectName("section")
        scope.addWidget(scope_title, 1)
        self.scope_button = QToolButton()
        self.scope_button.setObjectName("editorProfile")
        self.scope_button.setIcon(application_icon())
        self.scope_button.setIconSize(QSize(22, 22))
        self.scope_button.setFixedSize(36, 36)
        self.scope_button.setCheckable(True)
        self.scope_button.setChecked(True)
        self.scope_button.clicked.connect(lambda: self.scope_button.setChecked(True))
        self.scope_button.setToolTip("These button assignments apply in every application")
        self.scope_button.setAccessibleName("All applications")
        scope.addWidget(self.scope_button)
        artwork.addLayout(scope)
        scope_hint = QLabel("Button assignments apply across your desktop.")
        scope_hint.setObjectName("muted")
        scope_hint.setWordWrap(True)
        artwork.addWidget(scope_hint)
        self.art = InteractiveDeviceArt("keypad")
        self.art.setMinimumSize(300, 260)
        self.art.control_selected.connect(self.select_control)
        artwork.addWidget(self.art, 1)
        self.selection_label = QLabel()
        self.selection_label.setObjectName("deviceSelection")
        self.selection_label.setWordWrap(True)
        self.selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        artwork.addWidget(self.selection_label)
        self.connection_dot = ConnectionIndicator(parent=self, show_battery=True)
        artwork.addWidget(self.connection_dot)
        self.capability_note = QLabel()
        self.capability_note.setObjectName("muted")
        self.capability_note.setWordWrap(True)
        artwork.addWidget(self.capability_note)
        information = QHBoxLayout()
        self.info_button = QToolButton()
        self.info_button.setObjectName("editorProfile")
        self.info_button.setText("Device information")
        self.info_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.info_button.setArrowType(Qt.ArrowType.RightArrow)
        self.info_button.setCheckable(True)
        self.info_button.toggled.connect(self._toggle_information)
        information.addWidget(self.info_button)
        information.addStretch()
        artwork.addLayout(information)
        self.facts = QWidget()
        self.facts.hide()
        facts = QFormLayout(self.facts)
        facts.setContentsMargins(8, 0, 8, 0)
        facts.setVerticalSpacing(8)
        facts.setHorizontalSpacing(16)
        self.values = {}
        for key, title in (("connection", "Connection"), ("status", "Status"),
                           ("battery", "Battery"), ("model", "Device ID")):
            caption = QLabel(title)
            caption.setObjectName("muted")
            value = QLabel()
            value.setWordWrap(True)
            self.values[key] = value
            facts.addRow(caption, value)
        artwork.addWidget(self.facts)
        self.mapping_panel = self.workspace.library
        panel = self.workspace.panel
        heading = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("section")
        self.summary.setWordWrap(True)
        heading.addWidget(self.summary, 1)
        self.controls_count = QLabel()
        self.controls_count.setObjectName("muted")
        heading.addWidget(self.controls_count, 0, Qt.AlignmentFlag.AlignTop)
        panel.addLayout(heading)
        self.control_selector = _AssignmentCombo()
        self.control_selector.setObjectName("deviceControlAssignment")
        self.control_selector.setAccessibleName("Device control to configure")
        self.control_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.control_selector.setMinimumContentsLength(12)
        self.control_selector.currentIndexChanged.connect(
            lambda _: self.select_control(self.control_selector.currentData()))
        panel.addWidget(self.control_selector)
        self.selected_title = QLabel("Choose a control")
        self.selected_title.setObjectName("section")
        self.selected_title.setWordWrap(True)
        panel.addWidget(self.selected_title)
        self.explanation = QLabel()
        self.explanation.setObjectName("muted")
        self.explanation.setWordWrap(True)
        panel.addWidget(self.explanation)
        self.picker = ActionPicker(applications)
        self.picker.changed.connect(lambda: self.error_label.hide())
        panel.addWidget(self.picker, 1)
        self.controls_empty = QLabel("Ubuntu has not reported any assignable controls for this device.")
        self.controls_empty.setObjectName("muted")
        self.controls_empty.setWordWrap(True)
        self.controls_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(self.controls_empty, 1)
        self.blocked_space = QWidget()
        panel.addWidget(self.blocked_space, 1)
        self.blocked_space.hide()
        self.error_label = QLabel()
        self.error_label.setObjectName("status")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        panel.addWidget(self.error_label)
        actions = QHBoxLayout()
        self.clear_button = QPushButton("Remove")
        self.clear_button.clicked.connect(self.clear_mapping)
        actions.addWidget(self.clear_button)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("editorApply")
        self.apply_button.clicked.connect(self.apply_mapping)
        actions.addWidget(self.apply_button, 1)
        panel.addLayout(actions)
        self.edit_ring_button = QPushButton("Edit Action Ring")
        self.edit_ring_button.setToolTip("Customize the actions shown by this assigned ring button")
        self.edit_ring_button.clicked.connect(self.edit_ring_requested)
        panel.addWidget(self.edit_ring_button)
        self.edit_ring_button.hide()
        scroll = QScrollArea()
        scroll.setObjectName("editorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.workspace)
        layout.addWidget(scroll, 1)

    def _toggle_information(self, visible):
        self.facts.setVisible(visible)
        self.info_button.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)

    def set_applications(self, applications):
        self.picker.set_applications(applications)

    def _set_controls(self, device, *, blocked):
        controls = tuple(getattr(device, "controls", ())) if not blocked else ()
        key = (device.id, controls, blocked)
        if key == self._controls_key:
            return
        same_device = self._controls_key and self._controls_key[0] == device.id
        selection = self.selected_control if same_device else None
        if not same_device:
            self.info_button.setChecked(False)
            self._backend_error = ""
        self._controls_key = key
        self.controls = {control.id: control for control in controls}
        self.control_labels = {control.id: control.label for control in controls}
        self.control_selector.blockSignals(True)
        self.control_selector.clear()
        self.control_selector.addItem("Choose a control", None)
        for control in controls:
            self.control_selector.addItem(control.label, control.id)
        self.control_selector.blockSignals(False)
        self.controls_count.setText(f"{len(controls)} {'input' if len(controls) == 1 else 'inputs'}")
        self.controls_count.setVisible(bool(controls))
        self.control_selector.setVisible(bool(controls))
        self.selected_title.setVisible(bool(controls))
        self.picker.setVisible(bool(controls))
        self.apply_button.setVisible(bool(controls))
        self.clear_button.setVisible(bool(controls))
        self.controls_empty.setVisible(not controls and not blocked)
        self.blocked_space.setVisible(blocked)
        self.selection_label.setVisible(bool(controls))
        if not same_device or selection not in self.controls:
            self.selected_control = None
            self.picker.set_entry(None)
        self.select_control(selection if selection in self.controls else None)

    def _entry_for(self, identifier):
        if self._device is None or identifier is None:
            return None
        target = (self._device.vendor_id, self._device.product_id, identifier)
        for entry in self._actions:
            if (entry["vendor_id"], entry["product_id"], entry["control_id"]) == target:
                return {name: deepcopy(entry[name]) for name in ("label", "icon", "action")}
        if any((item["vendor_id"], item["product_id"], item["control_id"]) == target for item in self._bindings):
            return {"label": "Action Ring", "icon": "view-grid", "action": {"type": "open_ring"}}
        return None

    def set_actions(self, device_actions, ring_bindings=()):
        previous = self._entry_for(self.selected_control)
        self._actions = deepcopy(list(device_actions))
        self._bindings = deepcopy(list(ring_bindings))
        current = self._entry_for(self.selected_control)
        if current != previous:
            self.picker.set_entry(current)
            self.error_label.hide()
        self._refresh_controls()

    def set_bindings(self, bindings):
        self.set_actions(self._actions, bindings)

    def _supported(self, identifier):
        control = self.controls.get(identifier)
        if control is None or control.kind != "button" or self._device.vendor_id != 0x046d:
            return False
        product = self._device.product_id
        if product == 0xc07e:
            return identifier in ("button:4", "button:5")
        if product == 0xbc00:
            return identifier in ("button:1", "button:2", "button:3", "button:4")
        return identifier in {f"key:{code}" for code in range(0x110, 0x117)}

    def set_backend_status(self, error):
        self._backend_error = error or ""
        if self.controls:
            self._refresh_controls()

    def _refresh_controls(self):
        identifier = self.selected_control
        supported = self._supported(identifier)
        entry = self._entry_for(identifier)
        self.picker.setEnabled(supported)
        self.apply_button.setEnabled(supported)
        self.clear_button.setEnabled(supported and entry is not None)
        self.edit_ring_button.setVisible(bool(entry and entry["action"]["type"] == "open_ring"))
        if not self.controls:
            return
        if identifier is None:
            message = "Select a control on the device or from the list above."
        elif not supported:
            message = ("Dial and roller movement mappings are not available yet."
                       if self.controls[identifier].kind in ("dial", "wheel", "scroll", "axis")
                       else "Mapping actions for this control are not available yet.")
        else:
            message = self._backend_error or "Choose an action below, then Apply. Button mappings require X11."
        self.explanation.setText(message)
        self.explanation.setVisible(bool(message))

    def select_control(self, identifier):
        if identifier is not None and identifier not in self.controls:
            return
        changed = identifier != self.selected_control
        self.selected_control = identifier
        self.control_selector.blockSignals(True)
        self.control_selector.setCurrentIndex(max(0, self.control_selector.findData(identifier)))
        self.control_selector.blockSignals(False)
        self.art.set_selected_control(identifier)
        if changed:
            self.picker.set_entry(self._entry_for(identifier))
            self.error_label.hide()
        if identifier is not None:
            self.selected_title.setText(self.control_labels[identifier])
            self.selection_label.setText(f"Selected: {self.control_labels[identifier]}")
        else:
            self.selected_title.setText("Choose a control")
            self.selection_label.setText("Select a control on the device or in the list."
                                         if self.art.hotspots else "Select a control from the list.")
        self._refresh_controls()

    def apply_mapping(self):
        if not self._supported(self.selected_control):
            return
        try:
            entry = self.picker.value()
        except ValueError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        self.mapping_changed.emit(self.selected_control, entry)

    def clear_mapping(self):
        if self._supported(self.selected_control) and self._entry_for(self.selected_control) is not None:
            self.mapping_changed.emit(self.selected_control, None)

    def show_device(self, device, *, multiple_keypads=False):
        self._device = device
        self.title.setText(device.name)
        self.connection_dot.set_device(device)
        self.values["connection"].setText(device.connection)
        self.values["status"].setText("Connected" if device.connected is True else
                                      "Disconnected" if device.connected is False else "Connection unknown")
        self.values["battery"].setText(f"{device.battery_percent}%" if device.battery_percent is not None else "Not reported")
        self.values["model"].setText(f"{device.vendor_id:04x}:{device.product_id:04x}")
        blocked = multiple_keypads or device.supported
        controls = () if blocked else device.controls
        if self._controls_key != (device.id, controls, blocked):
            focused = QApplication.focusWidget()
            if focused and (self.mapping_panel.isAncestorOf(focused) or self.art.isAncestorOf(focused)):
                # Keep Qt from selecting a different row when a focused control
                # disappears during a device/capability refresh.
                self.back_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.art.set_device(device, controls=controls)
        self._set_controls(device, blocked=blocked)
        self.capability_note.setText(getattr(device, "controls_note", "") if not blocked else "")
        self.capability_note.setVisible(bool(self.capability_note.text()))
        if multiple_keypads:
            self.summary.setText("Connect one keypad to customize")
            self.explanation.setText("More than one MX Creative Keypad is connected. This version controls one keypad at a time. Disconnect the other keypad, then select this one from Devices.")
        elif device.supported and device.connected is not True:
            self.summary.setText("Connect your keypad to customize it")
            self.explanation.setText("A connection to this keypad is not confirmed. Reconnect it, then select it from Devices.")
        else:
            self.summary.setText("ALL ACTIONS")
            self.explanation.setText("")
            self._refresh_controls()
        self.explanation.setVisible(bool(self.explanation.text()))
