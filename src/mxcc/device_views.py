"""Device-first Qt overview with locally drawn, scalable hardware illustrations."""

from __future__ import annotations

from math import ceil, cos, radians, sin

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)


CREATIVE_CONSOLE_ID = "@creative-console"


def is_console_device(device):
    return device.kind in ("keypad", "dialpad")


def _label(text: str, name: str, *, wrap=False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return widget


class IconAction(QAbstractButton):
    """Small palette-aware line icon with a full keyboard/mouse target."""

    def __init__(self, kind, title, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("iconAction")
        self.setFixedSize(40, 40)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        self.setToolTip(title)

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
        if self.isEnabled() and (self.underMouse() or self.hasFocus() or self.isDown()):
            color = self.palette().color(QPalette.ColorRole.Accent)
            fill = QColor(color)
            fill.setAlpha(55 if self.isDown() else 30)
            painter.setBrush(fill)
            painter.setPen(QPen(color, 1.5) if self.hasFocus() else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 7, 7)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        color = self.palette().color(group, QPalette.ColorRole.WindowText)
        painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.translate(8, 8)
        path = QPainterPath()
        if self.kind == "refresh":
            path.moveTo(19, 8)
            path.cubicTo(17.8, 5.5, 15.2, 4, 12, 4)
            path.cubicTo(7.6, 4, 4, 7.6, 4, 12)
            path.cubicTo(4, 16.4, 7.6, 20, 12, 20)
            path.cubicTo(16, 20, 19.3, 17.2, 19.9, 13.5)
            path.moveTo(19, 3.5)
            path.lineTo(19, 8)
            path.lineTo(14.5, 8)
            painter.drawPath(path)
        else:
            for tooth in range(8):
                for offset, radius in ((-18, 7.5), (-10, 10), (10, 10), (18, 7.5)):
                    angle = radians(tooth * 45 + offset - 90)
                    point = QPointF(12 + radius * cos(angle), 12 + radius * sin(angle))
                    if path.elementCount():
                        path.lineTo(point)
                    else:
                        path.moveTo(point)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawEllipse(QPointF(12, 12), 3.2, 3.2)
        painter.end()


class AppFooter(QWidget):
    """One shared action row, independent of the currently displayed page."""

    refresh_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appFooter")
        self.setFixedHeight(64)
        self.refresh_button = IconAction("refresh", "Refresh connected devices")
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.settings_button = IconAction("settings", "Application settings")
        self.settings_button.clicked.connect(self.settings_requested)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(36, 0, 36, 24)
        layout.setSpacing(0)
        actions = QWidget()
        actions.setFixedWidth(88)
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        action_layout.addWidget(self.refresh_button)
        action_layout.addWidget(self.settings_button)
        layout.addWidget(actions)
        layout.addStretch()
        title = _label("MX Creative Control", "appFooterLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addStretch()
        layout.addSpacing(88)

    def set_scanning(self, scanning: bool):
        self.refresh_button.setEnabled(not scanning)
        title = "Refreshing devices…" if scanning else "Refresh connected devices"
        self.refresh_button.setToolTip(title)
        self.refresh_button.setAccessibleName(title)


class ConnectionIndicator(QWidget):
    """One device's connection state; only visible searches need a timer."""

    COLORS = {"connected": "#31b96e", "disconnected": "#e35b63",
              "unknown": "#e9ba3e", "searching": "#e9ba3e"}

    def __init__(self, device=None, parent=None, *, show_battery=False):
        super().__init__(parent)
        self.setObjectName("connectionIndicator")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)
        self.setMinimumWidth(18)
        self.device = device
        self.show_battery = show_battery
        self.state = "unknown"
        self.blink_on = True
        self._scanning = False
        self._force_search = False
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._blink)
        self._refresh()

    @property
    def battery_text(self):
        if self.show_battery and self.device is not None and self.device.battery_percent is not None:
            return f"{self.device.battery_percent}%"
        return ""

    @property
    def description(self):
        state = {"connected": "Connected", "disconnected": "Disconnected",
                 "unknown": "Connection unknown", "searching": "Searching"}[self.state]
        if self.device is None:
            return state
        details = [state, self.device.connection]
        if self.device.battery_percent is not None:
            details.append(f"{self.device.battery_percent}% battery")
        return " · ".join(value for value in details if value)

    def set_device(self, device):
        self.device = device
        self._refresh()

    def set_scanning(self, scanning: bool, force: bool = False):
        self._scanning = scanning
        self._force_search = force
        self._refresh()

    def _refresh(self):
        connected = self.device.connected if self.device is not None else None
        if self._scanning and (self._force_search or connected is not True):
            state = "searching"
        else:
            state = "connected" if connected is True else "disconnected" if connected is False else "unknown"
        if state != self.state:
            self.state = state
            self.blink_on = True
        if state == "searching" and self.isVisible():
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
            self.blink_on = True
        name = self.device.name if self.device is not None else "Device"
        text = f"{name} · {self.description}"
        if self.device is not None and self.device.detail:
            text += f"\n{self.device.detail}"
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setMinimumWidth(18 + (8 + self.fontMetrics().horizontalAdvance(self.battery_text)
                                  if self.battery_text else 0))
        self.update()

    def _blink(self):
        self.blink_on = not self.blink_on
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()

    def hideEvent(self, event):
        self.timer.stop()
        self.blink_on = True
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.COLORS[self.state])
        if self.state == "searching" and not self.blink_on:
            color.setAlpha(45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        battery = self.battery_text
        text_width = self.fontMetrics().horizontalAdvance(battery)
        total_width = 10 + (8 + text_width if battery else 0)
        left = (self.width() - total_width) / 2
        painter.drawEllipse(QPointF(left + 5, self.height() / 2), 5, 5)
        if battery:
            painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            painter.drawText(QRectF(left + 18, 0, text_width, self.height()),
                             Qt.AlignmentFlag.AlignCenter, battery)
        painter.end()


class DeviceArt(QWidget):
    """Hardware silhouettes drawn as vectors, independent of display scale/theme."""

    def __init__(self, kind: str = "device", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.kinds = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(100, 100)

    def sizeHint(self):
        return QSize(260, 240)

    def set_kind(self, kind: str):
        if kind != self.kind or self.kinds is not None:
            self.kind = kind
            self.kinds = None
            self.update()

    def set_kinds(self, kinds):
        kinds = tuple(kinds)
        if kinds != self.kinds:
            self.kinds = kinds
            self.update()

    @staticmethod
    def _body(painter, rectangle, radius=22):
        gradient = QLinearGradient(rectangle.topLeft(), rectangle.bottomRight())
        gradient.setColorAt(0, QColor("#8c8e91"))
        gradient.setColorAt(0.12, QColor("#686b6e"))
        gradient.setColorAt(0.6, QColor("#4a4d50"))
        gradient.setColorAt(1, QColor("#343639"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#919496"), 1.3))
        painter.drawRoundedRect(rectangle, radius, radius)

    @staticmethod
    def _shadow(painter, center, width=155):
        painter.setPen(Qt.PenStyle.NoPen)
        for spread in range(12, 0, -1):
            painter.setBrush(QColor(0, 0, 0, 3 + (12 - spread) // 2))
            painter.drawEllipse(QPointF(center, 211), width / 2 + spread, 5 + spread / 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / 260, self.height() / 240)
        painter.translate((self.width() - 260 * scale) / 2, (self.height() - 240 * scale) / 2)
        painter.scale(scale, scale)
        kinds = self.kinds if self.kinds is not None else (self.kind,)
        for index, kind in enumerate(kinds):
            painter.save()
            factor = min(1.15, 260 / (172 * len(kinds)))
            # Keep a pair's smaller drawings near their dots, on the same
            # visual baseline as a full-size device illustration.
            center_y = 120 if len(kinds) == 1 else 234 - 101 * factor
            painter.translate(260 * (index + 0.5) / len(kinds), center_y)
            painter.scale(factor, factor)
            painter.translate(-130, -120)
            self._draw_device(painter, kind.casefold())
            painter.restore()
        painter.end()

    def _draw_device(self, painter, kind):
        self._shadow(painter, 130, 178 if kind == "keyboard" else 135)
        if kind == "keypad":
            self._keypad(painter)
        elif kind == "dialpad":
            self._dialpad(painter)
        elif kind == "mouse":
            self._mouse(painter)
        elif kind == "keyboard":
            self._keyboard(painter)
        elif kind in ("camera", "webcam"):
            self._camera(painter)
        elif kind == "receiver":
            self._receiver(painter)
        else:
            self._generic(painter)

    def _keypad(self, p):
        self._body(p, QRectF(62, 17, 136, 188), 20)
        for row in range(3):
            for column in range(3):
                rect = QRectF(75 + column * 37, 32 + row * 42, 34, 38)
                gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                gradient.setColorAt(0, QColor("#282b2d"))
                gradient.setColorAt(1, QColor("#121416"))
                p.setBrush(gradient)
                p.setPen(QPen(QColor("#7e8183"), 0.8))
                p.drawRoundedRect(rect, 5, 5)
                p.setPen(QPen(QColor("#464a4d"), 0.7))
                p.drawLine(QPointF(rect.left() + 4, rect.bottom() - 4),
                           QPointF(rect.right() - 4, rect.bottom() - 4))
        for x, direction in ((75, -1), (112, 1)):
            p.setBrush(QColor("#53575a"))
            p.setPen(QPen(QColor("#84888b"), 0.8))
            p.drawRoundedRect(QRectF(x, 164, 34, 25), 5, 5)
            p.setPen(QPen(QColor("#b4b9bc"), 1.2))
            middle = x + 17
            p.drawLine(QPointF(middle - direction * 2, 172), QPointF(middle + direction * 2, 176))
            p.drawLine(QPointF(middle + direction * 2, 176), QPointF(middle - direction * 2, 180))
        p.setPen(QColor("#b8babc"))
        font = QFont(p.font()); font.setPixelSize(10); font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.drawText(QRectF(155, 167, 28, 20), Qt.AlignmentFlag.AlignCenter, "MX")

    def _dialpad(self, p):
        self._body(p, QRectF(53, 20, 154, 183), 20)
        for x in (76, 102):
            p.setBrush(QColor("#51575b")); p.setPen(QPen(QColor("#93999d"), 1))
            p.drawEllipse(QPointF(x, 45), 9, 9)
        p.setBrush(QColor("#292d31")); p.setPen(QPen(QColor("#92999e"), 1))
        p.drawRoundedRect(QRectF(155, 36, 36, 19), 4, 4)
        p.setPen(QPen(QColor("#777f84"), 0.6))
        for x in range(160, 188, 3):
            p.drawLine(QPointF(x, 38), QPointF(x, 53))
        p.setPen(QPen(QColor("#303438"), 4)); p.setBrush(QColor("#41474b"))
        p.drawEllipse(QPointF(130, 119), 53, 53)
        knob = QLinearGradient(86, 74, 170, 162)
        knob.setColorAt(0, QColor("#c5c9cb")); knob.setColorAt(0.45, QColor("#93999d")); knob.setColorAt(1, QColor("#626a70"))
        p.setBrush(knob); p.setPen(QPen(QColor("#c5c9cb"), 1.2))
        p.drawEllipse(QPointF(130, 116), 49, 49)
        for x in (73, 187):
            p.setBrush(QColor("#545b60")); p.setPen(QPen(QColor("#a0a6aa"), 1))
            p.drawEllipse(QPointF(x, 181), 10, 10)
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor("#b0b5b8"), 0.8))
            p.drawEllipse(QPointF(x, 181), 3, 3)

    def _mouse(self, p):
        outline = QPainterPath(QPointF(128, 19))
        outline.cubicTo(170, 15, 185, 48, 186, 101)
        outline.cubicTo(198, 152, 181, 198, 144, 205)
        outline.cubicTo(111, 216, 78, 194, 69, 170)
        outline.lineTo(58, 158); outline.lineTo(68, 124)
        outline.cubicTo(72, 60, 88, 24, 128, 19)
        gradient = QLinearGradient(74, 28, 183, 192)
        gradient.setColorAt(0, QColor("#74797d")); gradient.setColorAt(0.25, QColor("#3e4348")); gradient.setColorAt(1, QColor("#1c2024"))
        p.setBrush(gradient); p.setPen(QPen(QColor("#878e94"), 1.2)); p.drawPath(outline)
        p.setPen(QPen(QColor("#16191d"), 2))
        p.drawLine(QPointF(128, 24), QPointF(130, 104))
        p.drawLine(QPointF(75, 108), QPointF(184, 102))
        p.setBrush(QColor("#15191c")); p.setPen(QPen(QColor("#8a9298"), 1))
        p.drawRoundedRect(QRectF(120, 40, 18, 34), 6, 6)
        p.setPen(QPen(QColor("#737d83"), 0.9))
        for y in range(44, 71, 4):
            p.drawLine(QPointF(123, y), QPointF(135, y))
        p.setBrush(QColor("#24292e")); p.setPen(QPen(QColor("#727b82"), 0.8))
        p.drawRoundedRect(QRectF(78, 79, 16, 26), 4, 4)
        p.drawRoundedRect(QRectF(72, 111, 14, 24), 4, 4)
        p.setPen(QPen(QColor("#36b9cb"), 2.2))
        p.drawLine(QPointF(89, 141), QPointF(101, 177))
        p.drawLine(QPointF(101, 177), QPointF(117, 183))

    def _keyboard(self, p):
        self._body(p, QRectF(18, 66, 224, 114), 10)
        for row in range(5):
            for column in range(13):
                p.setBrush(QColor("#292d31")); p.setPen(QPen(QColor("#7a8085"), 0.6))
                if row == 4 and 3 <= column <= 7:
                    continue
                p.drawRoundedRect(QRectF(27 + column * 16, 77 + row * 18, 13, 14), 2, 2)
        p.drawRoundedRect(QRectF(75, 149, 77, 14), 2, 2)

    def _camera(self, p):
        self._body(p, QRectF(45, 69, 170, 83), 22)
        p.setBrush(QColor("#121a21")); p.setPen(QPen(QColor("#969fa7"), 3))
        p.drawEllipse(QPointF(130, 111), 27, 27)
        p.setBrush(QColor("#28485c")); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(130, 111), 13, 13)
        p.setBrush(QColor("#7999af")); p.drawEllipse(QPointF(125, 106), 4, 4)
        self._body(p, QRectF(100, 155, 60, 38), 5)

    def _receiver(self, p):
        self._body(p, QRectF(99, 104, 62, 82), 9)
        p.setBrush(QColor("#b4b8ba")); p.setPen(QPen(QColor("#e0e2e3"), 1))
        p.drawRoundedRect(QRectF(105, 51, 50, 57), 2, 2)
        p.setBrush(QColor("#444b50")); p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(113, 62, 10, 9)); p.drawRect(QRectF(138, 62, 10, 9))

    def _generic(self, p):
        self._body(p, QRectF(71, 47, 118, 150), 24)
        p.setPen(QPen(QColor("#b7c0c6"), 4)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(109, 92, 42, 42), 9, 9)
        for x in (116, 130, 144):
            p.drawLine(QPointF(x, 82), QPointF(x, 92))
            p.drawLine(QPointF(x, 134), QPointF(x, 144))


class _DeviceCardBase(QAbstractButton):
    """A keyboard-accessible whole-device target, borderless until hover/focus."""

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(292, 348)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 16)
        layout.setSpacing(7)
        self.art = DeviceArt(kind, self)
        layout.addWidget(self.art, 1)
        self.indicators = {}
        self._scanning = False
        self._force_search = False
        self.indicator_row = QWidget(self)
        self.indicator_row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.indicator_layout = QHBoxLayout(self.indicator_row)
        self.indicator_layout.setContentsMargins(0, 0, 0, 0)
        self.indicator_layout.setSpacing(0)
        layout.addWidget(self.indicator_row)
        self.name_label = _label("", "deviceName", wrap=True)
        self.support_label = _label("", "deviceSupport")
        for widget in (self.name_label, self.support_label):
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(widget)

    def _set_indicators(self, devices):
        devices = tuple(devices)
        identifiers = {device.id for device in devices}
        while self.indicator_layout.count():
            self.indicator_layout.takeAt(0)
        for identifier in set(self.indicators) - identifiers:
            indicator = self.indicators.pop(identifier)
            indicator.hide()
            indicator.deleteLater()
        for device in devices:
            indicator = self.indicators.get(device.id)
            if indicator is None:
                indicator = ConnectionIndicator(device, self.indicator_row, show_battery=True)
                self.indicators[device.id] = indicator
            else:
                indicator.set_device(device)
            indicator.set_scanning(self._scanning, self._force_search)
            self.indicator_layout.addWidget(indicator, 1)
            indicator.show()
        self._refresh_connection_metadata()

    def _refresh_connection_metadata(self):
        details = "\n".join(indicator.toolTip() for indicator in self.indicators.values())
        self.setAccessibleName(f"{self.name_label.text()}, {details}")
        self.setToolTip(details)

    def set_scanning(self, scanning: bool, force: bool = False):
        self._scanning = scanning
        self._force_search = force
        for indicator in self.indicators.values():
            indicator.set_scanning(scanning, force)
        self._refresh_connection_metadata()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        if not (self.underMouse() or self.hasFocus() or self.isDown()):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().color(QPalette.ColorRole.Highlight)
        fill = QColor(color); fill.setAlpha(45 if self.isDown() else 22)
        border = QColor(color); border.setAlpha(180 if self.hasFocus() else 65)
        p.setBrush(fill); p.setPen(QPen(border, 2 if self.hasFocus() else 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 18, 18)
        p.end()


class DeviceCard(_DeviceCardBase):
    def __init__(self, device, parent=None):
        super().__init__(device.kind, parent)
        self.update_device(device)

    def update_device(self, device):
        self.device = device
        self.art.set_kind(device.kind)
        self.name_label.setText(device.name)
        self._set_indicators([device])
        self.support_label.setText("Customize" if device.supported else "Controls" if device.controls else "Device details")
        self.setAccessibleDescription("Open device settings" if device.supported else "Open controls and device details")
        self.update()


class ConsoleCard(_DeviceCardBase):
    """One home entry containing only the Creative Console members detected."""

    def __init__(self, devices, parent=None):
        super().__init__("device", parent)
        self.update_devices(devices)

    def update_devices(self, devices):
        self.devices = tuple(devices)
        self.art.set_kinds(device.kind for device in self.devices)
        self.name_label.setText("MX Creative Console")
        self._set_indicators(self.devices)
        self.support_label.setText("Choose a device")
        self.setAccessibleDescription("Choose a connected keypad or dialpad")
        self.update()


class _DeviceGallery(QWidget):
    """A small wrapping layout whose minimum width never locks its old columns."""

    def __init__(self, parent=None, *, max_columns=3):
        super().__init__(parent)
        self.setObjectName("deviceGallery")
        self.items = []
        self.columns = 1
        self.max_columns = max_columns
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_items(self, cards):
        self.items = list(cards)
        for card in self.items:
            card.setParent(self)
            card.show()
        self._arrange()

    def minimumSizeHint(self):
        return QSize(1, self.minimumHeight())

    def sizeHint(self):
        return QSize(1000, self.minimumHeight())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def _arrange(self):
        width, height, gap = 292, 348, 24
        self.columns = min(self.max_columns, max(1, (self.width() + gap) // (width + gap)))
        rows = ceil(len(self.items) / self.columns)
        content_height = max(0, rows * height + (rows - 1) * gap)
        self.setMinimumHeight(content_height + 60 if rows else 0)
        top = max(30, (self.height() - content_height) // 2)
        for index, card in enumerate(self.items):
            row, column = divmod(index, self.columns)
            count = min(self.columns, len(self.items) - row * self.columns)
            left = max(0, (self.width() - count * width - (count - 1) * gap) // 2)
            card.move(left + column * (width + gap), top + row * (height + gap))


class DevicesHome(QWidget):
    device_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceHome")
        self.cards = {}
        self._devices = ()
        self._scanning = False
        self._force_search = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(_label("Your devices", "homeTitle"))
        heading.addStretch()
        layout.addLayout(heading)
        self.subtitle = _label("Select a device to personalize your setup.", "homeSubtitle", wrap=True)
        layout.addWidget(self.subtitle)
        self.notice = _label("", "homeNotice", wrap=True)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("deviceScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.gallery = _DeviceGallery()
        self.scroll.setWidget(self.gallery)
        layout.addWidget(self.scroll, 1)
        self.empty_state = QWidget()
        self.empty_state.setObjectName("homeEmpty")
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.addStretch()
        self.empty_title = _label("No Logitech devices found", "section")
        self.empty_description = _label("Connect a device by USB or Bluetooth, then refresh.", "homeSubtitle", wrap=True)
        for widget in (self.empty_title, self.empty_description):
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(widget)
        empty_layout.addStretch()
        layout.addWidget(self.empty_state, 1)
        self.scroll.hide()

    def update_devices(self, devices):
        devices = tuple(devices)
        if devices == self._devices:
            return
        self._devices = devices
        members = sorted((device for device in devices if is_console_device(device)),
                         key=lambda device: (device.kind != "keypad", device.name, device.id))
        entries = ([(CREATIVE_CONSOLE_ID, members)] if members else [])
        entries.extend((device.id, device) for device in devices if not is_console_device(device))
        identifiers = {identifier for identifier, _ in entries}
        for identifier in set(self.cards) - identifiers:
            card = self.cards.pop(identifier)
            card.hide()
            card.deleteLater()
        ordered = []
        for identifier, value in entries:
            if identifier not in self.cards:
                card = ConsoleCard(value, self.gallery) if identifier == CREATIVE_CONSOLE_ID else DeviceCard(value, self.gallery)
                card.clicked.connect(lambda checked=False, identifier=identifier: self.device_selected.emit(identifier))
                self.cards[identifier] = card
            elif identifier == CREATIVE_CONSOLE_ID:
                self.cards[identifier].update_devices(value)
            else:
                self.cards[identifier].update_device(value)
            self.cards[identifier].set_scanning(self._scanning, self._force_search)
            ordered.append(self.cards[identifier])
        self.gallery.set_items(ordered)
        self.scroll.setVisible(bool(devices))
        self.empty_state.setVisible(not devices)
        count = len(devices)
        self.subtitle.setText(f"{count} Logitech {'device' if count == 1 else 'devices'} · Select one to get started."
                              if count else "Your connected Logitech devices will appear here.")

    def set_scanning(self, scanning: bool, force: bool = False):
        self._scanning = scanning
        self._force_search = force
        for card in self.cards.values():
            card.set_scanning(scanning, force)
        self.empty_title.setText("Looking for your devices…" if scanning else "No Logitech devices found")
        self.empty_description.setText("Checking connected USB and Bluetooth devices." if scanning
                                       else "Connect a device by USB or Bluetooth, then refresh.")

    def set_error(self, message: str):
        self.notice.setText(message)
        self.notice.setVisible(bool(message))


class ConsoleDevices(QWidget):
    """Choose either real Creative Console member before opening its controls."""

    back_requested = Signal()
    device_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("consoleDevices")
        self.cards = {}
        self._devices = ()
        self._scanning = False
        self._force_search = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(24, 20, 28, 14)
        self.back_button = QPushButton("←")
        self.back_button.setObjectName("topbarButton")
        self.back_button.setToolTip("All devices")
        self.back_button.setAccessibleName("All devices")
        self.back_button.clicked.connect(self.back_requested)
        header.addWidget(self.back_button)
        header.addWidget(_label("MX Creative Console", "deviceTitle"))
        header.addStretch()
        layout.addLayout(header)
        content = QVBoxLayout()
        content.setContentsMargins(24, 22, 24, 24)
        content.setSpacing(12)
        content.addWidget(_label("Choose a device", "section"))
        content.addWidget(_label("Select a device below to continue.", "homeSubtitle", wrap=True))
        self.notice = _label("", "homeNotice", wrap=True)
        self.notice.hide()
        content.addWidget(self.notice)
        self.missing_notice = _label("", "homeSubtitle", wrap=True)
        content.addWidget(self.missing_notice)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("deviceScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.gallery = _DeviceGallery(max_columns=2)
        self.scroll.setWidget(self.gallery)
        content.addWidget(self.scroll, 1)
        layout.addLayout(content, 1)
        self._update_missing_notice()

    def _update_missing_notice(self):
        kinds = {device.kind for device in self._devices}
        if not kinds:
            message = "No MX Creative Console devices detected. Connect your keypad or dialpad to continue."
        elif "keypad" not in kinds:
            message = "No keypad detected. Connect it to add it to this console."
        elif "dialpad" not in kinds:
            message = "No dialpad detected. Connect it to add it to this console."
        else:
            message = ""
        self.missing_notice.setText(message)
        self.missing_notice.setVisible(bool(message))

    def update_devices(self, devices):
        devices = tuple(sorted((device for device in devices if is_console_device(device)),
                               key=lambda device: (device.kind != "keypad", device.name, device.id)))
        if devices == self._devices:
            return
        self._devices = devices
        identifiers = {device.id for device in devices}
        for identifier in set(self.cards) - identifiers:
            card = self.cards.pop(identifier)
            card.hide()
            card.deleteLater()
        ordered = []
        for device in devices:
            if device.id not in self.cards:
                card = DeviceCard(device, self.gallery)
                card.clicked.connect(lambda checked=False, identifier=device.id: self.device_selected.emit(identifier))
                self.cards[device.id] = card
            else:
                self.cards[device.id].update_device(device)
            self.cards[device.id].set_scanning(self._scanning, self._force_search)
            ordered.append(self.cards[device.id])
        self.gallery.set_items(ordered)
        self._update_missing_notice()

    def set_scanning(self, scanning: bool, force: bool = False):
        self._scanning = scanning
        self._force_search = force
        for card in self.cards.values():
            card.set_scanning(scanning, force)

    def set_error(self, message: str):
        self.notice.setText(message)
        self.notice.setVisible(bool(message))
