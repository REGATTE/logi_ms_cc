"""Clickable regions for verified device drawings, without input-device access."""

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion, QTransform
from PySide6.QtWidgets import QAbstractButton

from .device_views import DeviceArt
from .device_artwork import device_artwork_control_regions


def _rounded(x, y, width, height, radius=4):
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, width, height), radius, radius)
    return path


def _circle(x, y, radius):
    path = QPainterPath()
    path.addEllipse(QPointF(x, y), radius, radius)
    return path


def _regions(vendor_id, product_id):
    """Coordinates match DeviceArt's unscaled 260×240 single-device drawing."""
    artwork_regions = device_artwork_control_regions(vendor_id, product_id)
    if artwork_regions:
        return artwork_regions
    if vendor_id != 0x046d:
        return {}
    if product_id == 0xc07e:
        return {"button:4": _rounded(72, 111, 14, 24),
                "button:5": _rounded(78, 79, 16, 26)}
    if product_id == 0xbc00:
        return {
            "button:1": _circle(76, 45, 9),
            "button:2": _circle(102, 45, 9),
            "button:3": _circle(73, 181, 10),
            "button:4": _circle(187, 181, 10),
            "dial:main": _circle(130, 116, 49),
            "wheel:roller": _rounded(155, 36, 36, 19),
        }
    return {}


class _ControlHotspot(QAbstractButton):
    """A shaped, keyboard-accessible button laid over a physical control."""

    def __init__(self, title, source_path, parent):
        super().__init__(parent)
        self.source_path = source_path
        self.shape = QPainterPath()
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        self.setToolTip(title)

    def place(self, transform):
        path = transform.map(self.source_path)
        bounds = path.boundingRect().adjusted(-4, -4, 4, 4).toAlignedRect()
        self.setGeometry(bounds)
        self.shape = QTransform.fromTranslate(-bounds.x(), -bounds.y()).map(path)
        # A widget mask keeps padded bounding-box corners from intercepting
        # pointer events outside the actual physical control shape.
        self.setMask(QRegion(self.shape.toFillPolygon().toPolygon()))
        self.update()

    def hitButton(self, point):
        return self.shape.contains(QPointF(point))

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave,
                            QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.update()
        return result

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor("#60d6bd")
        active = self.isChecked()
        fill = QColor(accent)
        fill.setAlpha(85 if active else 45 if self.underMouse() else 12)
        border = QColor(accent)
        border.setAlpha(255 if active else 180 if self.underMouse() else 100)
        if active:
            glow = QColor(accent); glow.setAlpha(50)
            painter.setPen(QPen(glow, 7))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.shape)
        style = Qt.PenStyle.DashLine if self.hasFocus() and not active else Qt.PenStyle.SolidLine
        painter.setPen(QPen(border, 2 if active else 1.2, style))
        painter.setBrush(fill)
        painter.drawPath(self.shape)
        painter.end()


class InteractiveDeviceArt(DeviceArt):
    """Select verified controls on an illustration; unknown layouts stay honest."""

    control_selected = Signal(str)

    def __init__(self, kind="device", parent=None):
        super().__init__(kind, parent)
        self.controls_view = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.hotspots = {}
        self.selected_control = None
        self._device_key = None
        self._control_ids = set()

    def set_device(self, device, controls=None):
        controls = tuple(device.controls if controls is None else controls)
        key = (device.id, device.vendor_id, device.product_id, device.kind, controls)
        self.set_kind(device.kind)
        self.set_model(device.vendor_id, device.product_id)
        if key == self._device_key:
            return
        previous_id = self._device_key[0] if self._device_key else None
        self._device_key = key
        self._control_ids = {control.id for control in controls}
        if previous_id != device.id or self.selected_control not in self._control_ids:
            self.selected_control = None
        for hotspot in self.hotspots.values():
            hotspot.hide()
            hotspot.deleteLater()
        self.hotspots = {}
        regions = _regions(device.vendor_id, device.product_id)
        for control in controls:
            if control.id not in regions:
                continue
            hotspot = _ControlHotspot(control.label, regions[control.id], self)
            hotspot.clicked.connect(lambda checked=False, identifier=control.id: self._activate(identifier))
            self.hotspots[control.id] = hotspot
            hotspot.show()
        self._arrange_hotspots()
        self.set_selected_control(self.selected_control)

    def set_selected_control(self, identifier):
        self.selected_control = identifier if identifier in self._control_ids else None
        for control_id, hotspot in self.hotspots.items():
            hotspot.setChecked(control_id == self.selected_control)
            hotspot.update()

    def _activate(self, identifier):
        self.set_selected_control(identifier)
        self.control_selected.emit(identifier)

    def _arrange_hotspots(self):
        scale = min(self.width() / 260, self.height() / 240)
        transform = QTransform()
        transform.translate((self.width() - 260 * scale) / 2, (self.height() - 240 * scale) / 2)
        transform.scale(scale, scale)
        transform.translate(130, 120)
        transform.scale(1.15, 1.15)
        transform.translate(-130, -120)
        for hotspot in self.hotspots.values():
            hotspot.place(transform)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_hotspots()
