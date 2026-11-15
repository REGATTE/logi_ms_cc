"""Device discovery stays off the UI thread and ends with the settings process."""

from PySide6.QtCore import QEvent, QObject, QPointF, QRunnable, QSize, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

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


class _ControlRow(QFrame):
    selected = Signal(str)

    def __init__(self, control, parent=None):
        super().__init__(parent)
        self.control_id = control.id
        self.setObjectName("deviceControlRow")
        self.setProperty("selected", False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(control.label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.selected.emit(self.control_id)
        super().mousePressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selected.emit(self.control_id)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.selected.emit(self.control_id)
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            self.selected.emit(self.control_id)
        return super().eventFilter(watched, event)


class DeviceDetails(QWidget):
    """Discovered controls with unassigned options, plus connection facts."""

    back_requested = Signal()
    assignment_changed = Signal(str, bool)
    edit_ring_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceDetails")
        self._bindings = []
        self._device = None
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
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("editorSearch")
        self.search_edit.setPlaceholderText("Search controls")
        self.search_edit.setAccessibleName("Search device controls")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_controls)
        panel.addWidget(self.search_edit)
        self.selected_title = QLabel("Choose a control")
        self.selected_title.setObjectName("section")
        self.selected_title.setWordWrap(True)
        panel.addWidget(self.selected_title)
        self.explanation = QLabel()
        self.explanation.setObjectName("muted")
        self.explanation.setWordWrap(True)
        panel.addWidget(self.explanation)
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setObjectName("editorScroll")
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.controls_content = QWidget()
        self.controls_content.setObjectName("editorCategories")
        self.controls_layout = QVBoxLayout(self.controls_content)
        self.controls_layout.setContentsMargins(0, 4, 4, 0)
        self.controls_layout.setSpacing(14)
        self.controls_layout.addStretch()
        self.controls_scroll.setWidget(self.controls_content)
        panel.addWidget(self.controls_scroll, 1)
        self.controls_empty = QLabel("Ubuntu has not reported any assignable controls for this device.")
        self.controls_empty.setObjectName("muted")
        self.controls_empty.setWordWrap(True)
        self.controls_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(self.controls_empty, 1)
        self.no_results = QLabel("No matching controls")
        self.no_results.setObjectName("muted")
        self.no_results.setWordWrap(True)
        self.no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results.hide()
        panel.addWidget(self.no_results, 1)
        self.blocked_space = QWidget()
        panel.addWidget(self.blocked_space, 1)
        self.blocked_space.hide()
        self.mapping_combos = {}
        self.control_rows = {}
        self.control_labels = {}
        self.control_groups = []
        self.selected_control = None
        self._controls_key = None
        self.edit_ring_button = QPushButton("Edit Action Ring")
        self.edit_ring_button.setObjectName("editorApply")
        self.edit_ring_button.setToolTip("Customize the actions shown by this device's assigned ring buttons")
        self.edit_ring_button.clicked.connect(self.edit_ring_requested)
        panel.addWidget(self.edit_ring_button)
        self.edit_ring_button.hide()
        self.save_hint = QLabel("Changes save automatically")
        self.save_hint.setObjectName("muted")
        panel.addWidget(self.save_hint)
        scroll = QScrollArea()
        scroll.setObjectName("editorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.workspace)
        layout.addWidget(scroll, 1)

    def _toggle_information(self, visible):
        self.facts.setVisible(visible)
        self.info_button.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)

    def _set_controls(self, device, *, blocked):
        controls = tuple(getattr(device, "controls", ())) if not blocked else ()
        key = (device.id, controls, blocked)
        if key == self._controls_key:
            return
        same_device = self._controls_key and self._controls_key[0] == device.id
        selection = self.selected_control if same_device else None
        if not same_device:
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
            self.info_button.setChecked(False)
        self._controls_key = key
        self.mapping_combos = {}
        self.control_rows = {}
        self.control_labels = {control.id: control.label for control in controls}
        self.control_groups = []
        while self.controls_layout.count() > 1:
            item = self.controls_layout.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        groups = {"Buttons": [], "Dials & wheels": [], "Keys": [], "Other controls": []}
        for control in controls:
            group = ("Buttons" if control.kind == "button" else
                     "Dials & wheels" if control.kind in ("wheel", "dial", "scroll", "axis") else
                     "Keys" if control.kind == "key" else "Other controls")
            groups[group].append(control)
        for title, members in groups.items():
            if not members:
                continue
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(8)
            caption = QLabel(title)
            caption.setObjectName("section")
            group_layout.addWidget(caption)
            for control in members:
                row = _ControlRow(control)
                row.selected.connect(self.select_control)
                row_layout = QVBoxLayout(row)
                row_layout.setContentsMargins(10, 9, 10, 9)
                row_layout.setSpacing(5)
                name = QLabel(control.label)
                name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                name.setWordWrap(True)
                name.setMinimumWidth(80)
                name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                row_layout.addWidget(name)
                combo = _AssignmentCombo()
                combo.setObjectName("deviceControlAssignment")
                combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                combo.setPlaceholderText("Not assigned")
                combo.setAccessibleName(f"Action for {control.label}")
                supported = (control.kind == "button" and (
                    (device.vendor_id, device.product_id) in ((0x046d, 0xc07e), (0x046d, 0xbc00))
                    or control.id in {f"key:{code}" for code in range(0x110, 0x117)}))
                if supported:
                    combo.addItem("Not assigned", False)
                    combo.addItem("Open Action Ring", True)
                    combo.currentIndexChanged.connect(
                        lambda index, identifier=control.id, picker=combo:
                        self.assignment_changed.emit(identifier, bool(picker.currentData())))
                    combo.setToolTip("Press once to open the ring; press again to close. X11 required.")
                else:
                    combo.setToolTip("Actions for this control are not available yet.")
                combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                combo.setMinimumContentsLength(12)
                combo.installEventFilter(row)
                row_layout.addWidget(combo)
                group_layout.addWidget(row)
                self.mapping_combos[control.id] = combo
                self.control_rows[control.id] = row
            self.controls_layout.insertWidget(self.controls_layout.count() - 1, group)
            self.control_groups.append((title, group, tuple(control.id for control in members)))
        self.controls_count.setText(f"{len(controls)} {'input' if len(controls) == 1 else 'inputs'}")
        self.controls_count.setVisible(bool(controls))
        self.search_edit.setVisible(bool(controls))
        self.selected_title.setVisible(bool(controls))
        self.save_hint.setVisible(any(combo.count() for combo in self.mapping_combos.values()))
        self.controls_empty.setVisible(not controls and not blocked)
        self.blocked_space.setVisible(blocked)
        self.selected_control = None
        self.select_control(selection if selection in self.control_rows else None)
        self.selection_label.setVisible(bool(controls))
        self.set_bindings(self._bindings)
        self._filter_controls(self.search_edit.text())

    def _filter_controls(self, text):
        query = text.strip().casefold()
        visible_count = 0
        for title, group, identifiers in self.control_groups:
            group_visible = False
            for identifier in identifiers:
                visible = not query or query in f"{title} {self.control_labels[identifier]}".casefold()
                self.control_rows[identifier].setVisible(visible)
                visible_count += visible
                group_visible |= visible
            group.setVisible(group_visible)
        self.controls_scroll.setVisible(bool(visible_count))
        self.no_results.setVisible(bool(self.control_rows) and not visible_count)

    def set_bindings(self, bindings):
        self._bindings = list(bindings)
        if self._device is None:
            return
        assigned = {item["control_id"] for item in bindings
                    if (item["vendor_id"], item["product_id"]) ==
                    (self._device.vendor_id, self._device.product_id)}
        for control_id, combo in self.mapping_combos.items():
            if combo.count():
                combo.blockSignals(True)
                combo.setCurrentIndex(1 if control_id in assigned else 0)
                combo.blockSignals(False)
        self.edit_ring_button.setVisible(any(
            combo.currentData() is True for combo in self.mapping_combos.values()))

    def set_backend_status(self, error):
        if self._device is not None and not self._device.supported:
            self.explanation.setText(error or self._assignment_explanation())
            self.explanation.setVisible(bool(self.explanation.text()))

    def _assignment_explanation(self):
        if not self.mapping_combos:
            return ""
        if any(combo.count() for combo in self.mapping_combos.values()):
            return "Assign a button to open your Action Ring. Wheels and other actions will be available later."
        return "Controls reported by Ubuntu are listed below. Mapping actions are not available yet."

    def select_control(self, identifier):
        if identifier is not None and identifier not in self.control_rows:
            return
        self.selected_control = identifier
        for control_id, row in self.control_rows.items():
            selected = control_id == identifier
            if row.property("selected") != selected:
                row.setProperty("selected", selected)
                row.style().unpolish(row)
                row.style().polish(row)
                row.update()
        self.art.set_selected_control(identifier)
        if identifier is not None:
            if self.control_rows[identifier].isHidden():
                self.search_edit.clear()
            self.selected_title.setText(self.control_labels[identifier])
            self.selection_label.setText(f"Selected: {self.control_labels[identifier]}")
            self.controls_scroll.ensureWidgetVisible(self.control_rows[identifier], 0, 16)
        else:
            self.selected_title.setText("Choose a control")
            self.selection_label.setText("Select a control on the device or in the list."
                                         if self.art.hotspots else "Select a control from the list.")

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
            self.summary.setText("ALL CONTROLS")
            self.explanation.setText(self._assignment_explanation())
        self.explanation.setVisible(bool(self.explanation.text()))
