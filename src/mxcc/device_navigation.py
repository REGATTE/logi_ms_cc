"""Device discovery stays off the UI thread and ends with the settings process."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .device_views import ConnectionIndicator, DeviceArt


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


class DeviceDetails(QWidget):
    """Connection facts for devices whose controls are not implemented."""

    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceDetails")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 28)
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
        body.setSpacing(64)
        self.art = DeviceArt("keypad")
        self.art.setMinimumSize(300, 300)
        artwork = QVBoxLayout()
        artwork.addWidget(self.art, 1)
        self.connection_dot = ConnectionIndicator(parent=self)
        artwork.addWidget(self.connection_dot)
        body.addLayout(artwork, 1)
        panel = QVBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("section")
        self.summary.setWordWrap(True)
        panel.addWidget(self.summary)
        self.explanation = QLabel()
        self.explanation.setObjectName("muted")
        self.explanation.setWordWrap(True)
        panel.addWidget(self.explanation)
        panel.addSpacing(24)
        facts = QFormLayout()
        facts.setVerticalSpacing(16)
        self.values = {}
        for key, title in (("connection", "Connection"), ("status", "Status"),
                           ("battery", "Battery"), ("model", "Device ID")):
            value = QLabel()
            self.values[key] = value
            facts.addRow(title, value)
        panel.addLayout(facts)
        panel.addStretch()
        body.addLayout(panel, 1)
        layout.addStretch()
        layout.addLayout(body, 3)
        layout.addStretch()

    def show_device(self, device, *, multiple_keypads=False):
        self.title.setText(device.name)
        self.art.set_kind(device.kind)
        self.connection_dot.set_device(device)
        self.values["connection"].setText(device.connection)
        self.values["status"].setText("Connected" if device.connected is True else
                                      "Disconnected" if device.connected is False else "Connection unknown")
        self.values["battery"].setText(f"{device.battery_percent}%" if device.battery_percent is not None else "Not reported")
        self.values["model"].setText(f"{device.vendor_id:04x}:{device.product_id:04x}")
        if multiple_keypads:
            self.summary.setText("Connect one keypad to customize")
            self.explanation.setText("More than one MX Creative Keypad is connected. This version controls one keypad at a time. Disconnect the other keypad, then select this one from Devices.")
        elif device.supported and device.connected is not True:
            self.summary.setText("Connect your keypad to customize it")
            self.explanation.setText("A connection to this keypad is not confirmed. Reconnect it, then select it from Devices.")
        else:
            self.summary.setText("Customization is not available for this device yet")
            status = "This device is currently disconnected." if device.connected is False else "This device is detected by Ubuntu."
            self.explanation.setText(f"{status} MX Creative Control currently customizes the MX Creative Keypad; your device’s existing behavior is unchanged.")
