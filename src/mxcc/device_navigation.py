"""Device discovery stays off the UI thread and ends with the settings process."""

from PySide6.QtCore import QEvent, QObject, QPointF, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .device_views import ConnectionIndicator
from .device_interaction import InteractiveDeviceArt


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceDetails")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 28)
        layout.setSpacing(24)
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
        body = QHBoxLayout()
        body.setSpacing(32)
        left = QWidget()
        left.setMinimumWidth(260)
        left.setMaximumWidth(340)
        artwork = QVBoxLayout(left)
        artwork.setContentsMargins(0, 0, 0, 0)
        artwork.setSpacing(16)
        self.art = InteractiveDeviceArt("keypad")
        self.art.setMinimumSize(240, 220)
        self.art.control_selected.connect(self.select_control)
        artwork.addWidget(self.art, 1)
        self.selection_label = QLabel()
        self.selection_label.setObjectName("deviceSelection")
        self.selection_label.setWordWrap(True)
        self.selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        artwork.addWidget(self.selection_label)
        self.connection_dot = ConnectionIndicator(parent=self, show_battery=True)
        artwork.addWidget(self.connection_dot)
        facts_frame = QFrame()
        facts_frame.setObjectName("deviceFacts")
        facts = QFormLayout(facts_frame)
        facts.setContentsMargins(18, 16, 18, 16)
        facts.setVerticalSpacing(12)
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
        artwork.addWidget(facts_frame)
        self.capability_note = QLabel()
        self.capability_note.setObjectName("muted")
        self.capability_note.setWordWrap(True)
        artwork.addWidget(self.capability_note)
        body.addWidget(left, 1)
        self.mapping_panel = QFrame()
        self.mapping_panel.setObjectName("deviceMappings")
        panel = QVBoxLayout(self.mapping_panel)
        panel.setContentsMargins(22, 22, 22, 22)
        panel.setSpacing(12)
        heading = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("section")
        self.summary.setWordWrap(True)
        heading.addWidget(self.summary, 1)
        self.controls_count = QLabel()
        self.controls_count.setObjectName("applicationCount")
        heading.addWidget(self.controls_count, 0, Qt.AlignmentFlag.AlignTop)
        panel.addLayout(heading)
        self.explanation = QLabel()
        self.explanation.setObjectName("muted")
        self.explanation.setWordWrap(True)
        panel.addWidget(self.explanation)
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setObjectName("deviceControlsScroll")
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.controls_content = QWidget()
        self.controls_content.setObjectName("deviceControlsContent")
        self.controls_layout = QVBoxLayout(self.controls_content)
        self.controls_layout.setContentsMargins(0, 4, 6, 0)
        self.controls_layout.setSpacing(18)
        self.controls_layout.addStretch()
        self.controls_scroll.setWidget(self.controls_content)
        panel.addWidget(self.controls_scroll, 1)
        self.controls_empty = QLabel("Ubuntu has not reported any assignable controls for this device.")
        self.controls_empty.setObjectName("muted")
        self.controls_empty.setWordWrap(True)
        self.controls_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(self.controls_empty, 1)
        self.blocked_space = QWidget()
        panel.addWidget(self.blocked_space, 1)
        self.blocked_space.hide()
        self.mapping_combos = {}
        self.control_rows = {}
        self.control_labels = {}
        self.selected_control = None
        self._controls_key = None
        body.addWidget(self.mapping_panel, 2)
        layout.addLayout(body, 1)

    def _set_controls(self, device, *, blocked):
        controls = tuple(getattr(device, "controls", ())) if not blocked else ()
        key = (device.id, controls, blocked)
        if key == self._controls_key:
            return
        selection = self.selected_control if self._controls_key and self._controls_key[0] == device.id else None
        self._controls_key = key
        self.mapping_combos = {}
        self.control_rows = {}
        self.control_labels = {control.id: control.label for control in controls}
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
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(12, 6, 12, 6)
                row_layout.setSpacing(12)
                name = QLabel(control.label)
                name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                name.setWordWrap(True)
                name.setMinimumWidth(80)
                name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                row_layout.addWidget(name, 1)
                combo = _AssignmentCombo()
                combo.setObjectName("deviceControlAssignment")
                combo.setFixedWidth(190)
                combo.setPlaceholderText("Not assigned")
                combo.setAccessibleName(f"Action for {control.label}")
                combo.setToolTip("Action choices are not available yet.")
                combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                combo.setMinimumContentsLength(12)
                combo.installEventFilter(row)
                row_layout.addWidget(combo)
                group_layout.addWidget(row)
                self.mapping_combos[control.id] = combo
                self.control_rows[control.id] = row
            self.controls_layout.insertWidget(self.controls_layout.count() - 1, group)
        self.controls_count.setText(f"{len(controls)} {'input' if len(controls) == 1 else 'inputs'}")
        self.controls_count.setVisible(bool(controls))
        self.controls_scroll.setVisible(bool(controls))
        self.controls_empty.setVisible(not controls and not blocked)
        self.blocked_space.setVisible(blocked)
        self.selected_control = None
        self.select_control(selection if selection in self.control_rows else None)
        self.selection_label.setVisible(bool(controls))

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
            self.selection_label.setText(f"Selected: {self.control_labels[identifier]}")
            self.controls_scroll.ensureWidgetVisible(self.control_rows[identifier], 0, 16)
        else:
            self.selection_label.setText("Select a control on the device or in the list."
                                         if self.art.hotspots else "Select a control from the list.")

    def show_device(self, device, *, multiple_keypads=False):
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
            self.summary.setText("Control assignments")
            self.explanation.setText("Action choices are not available yet.")
