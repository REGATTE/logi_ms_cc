"""On-demand native Actions Ring; importing this module never starts a worker."""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QCursor, QIcon, QPainter, QPalette, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from .icons import application_icon, display_icon, setup_icon_theme
from .key_button import KeyButton
from .appearance import paint_neumorphic_surface


def ring_position(cursor: QPoint, available: QRect, size: QSize) -> QPoint:
    """Keep the whole ring on the cursor's screen, including negative origins."""
    x = max(available.left(), min(cursor.x() - size.width() // 2,
                                 available.right() - size.width() + 1))
    y = max(available.top(), min(cursor.y() - size.height() // 2,
                                available.bottom() - size.height() + 1))
    return QPoint(x, y)


def foreground_application(foreground: dict, applications):
    identities = {value.casefold() for value in foreground.get("identities", [])}
    return next((app for app in applications if identities.intersection(
        {app.id.removesuffix(".desktop").casefold(), app.wm_class.casefold()} - {""})), None)


class RingButton(KeyButton):
    """Paint original artwork on a rounded bubble, without adding an icon tile."""

    hovered = Signal(int)

    def __init__(self, slot, parent=None, *, editing=False, add=False, hover_zoom=False):
        super().__init__(slot, parent)
        self.editing = editing
        self.hover_zoom = hover_zoom
        self._rest_geometry = QRect()
        self._hover_scale = 1.0
        self._hover_start = 1.0
        self._hover_target = 1.0
        self._hover_animation = None
        if hover_zoom:
            self._hover_animation = QVariantAnimation(self)
            self._hover_animation.setDuration(100)
            self._hover_animation.setStartValue(0.0)
            self._hover_animation.setEndValue(1.0)
            self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._hover_animation.valueChanged.connect(self._advance_hover)
        self.filled = False
        self.application_artwork = False
        self.symbolic_artwork = False
        self.add = add
        self.setCheckable(editing)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        icon_size = 30 if editing else 28
        self.setIconSize(QSize(icon_size, icon_size))
        self.set_reorder_enabled(editing)

    def enterEvent(self, event):
        self._animate_hover(True)
        self.hovered.emit(self.slot)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(False)
        self.hovered.emit(-1)
        self.update()
        super().leaveEvent(event)

    def set_rest_geometry(self, geometry):
        self._rest_geometry = QRect(geometry)
        self._set_hover_scale(self._hover_scale)

    def _set_hover_scale(self, scale):
        self._hover_scale = scale
        if not self._rest_geometry.isEmpty():
            area = QRectF(self._rest_geometry)
            center = area.center()
            area.setWidth(area.width() * scale)
            area.setHeight(area.height() * scale)
            area.moveCenter(center)
            # Keep a full pixel envelope around the exact painted scale.
            self.setGeometry(area.toAlignedRect())
        self.update()

    def _advance_hover(self, progress):
        self._set_hover_scale(self._hover_start
                              + (self._hover_target - self._hover_start) * progress)

    def _animate_hover(self, hovered):
        if self._hover_animation is None:
            return
        self._hover_animation.stop()
        self._hover_start = self._hover_scale
        self._hover_target = 1.3 if hovered else 1.0
        if hovered:
            self.raise_()
        if self._hover_start != self._hover_target:
            self._hover_animation.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._hover_animation is not None:
            self._hover_animation.stop()
            self._hover_start = self._hover_target = 1.0
            self._set_hover_scale(1.0)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.hovered.emit(self.slot)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.hovered.emit(-1)
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect()
        if self.hover_zoom and not self._rest_geometry.isEmpty():
            rect = QRect(QPoint(), self._rest_geometry.size())
            painter.translate((self.width() - rect.width() * self._hover_scale) / 2,
                              (self.height() - rect.height() * self._hover_scale) / 2)
            painter.scale(self._hover_scale, self._hover_scale)
        palette = self.palette()
        active = self.isChecked() or self.hasFocus() or self.underMouse() or self.property("dropTarget")
        fill = palette.color(QPalette.ColorRole.Highlight if active else QPalette.ColorRole.Button)
        border = palette.color(QPalette.ColorRole.Accent if active else QPalette.ColorRole.Mid)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 2 if active else 1))
        corner = min(18, rect.width() * .32)
        if self.editing or (self.add and not self.hover_zoom):
            paint_neumorphic_surface(painter, QRectF(rect).adjusted(-3, -3, 3, 3),
                                     palette, radius=corner, pressed=active, focused=active)
        else:
            painter.drawRoundedRect(QRectF(rect).adjusted(2, 2, -2, -2), corner, corner)
        if self.filled:
            inset = 10 if self.editing else 8
            area = rect.adjusted(inset, inset, -inset, -inset)
            if not self.symbolic_artwork:
                self.icon().paint(painter, area)
            else:
                # Symbolic theme icons can be dark even in a dark Qt palette.
                # Tint only explicit symbolic artwork: opaque/color icons lose
                # their internal details when their alpha is used as a mask.
                artwork = self.icon().pixmap(area.size())
                if not artwork.isNull():
                    ink = QPainter(artwork)
                    ink.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                    ink.fillRect(artwork.rect(), palette.color(QPalette.ColorRole.ButtonText))
                    ink.end()
                    painter.drawPixmap(area, artwork)
        elif self.add:
            painter.setPen(QPen(palette.color(QPalette.ColorRole.ButtonText), 2))
            center = rect.center()
            painter.drawLine(center + QPoint(-6, 0), center + QPoint(6, 0))
            painter.drawLine(center + QPoint(0, -6), center + QPoint(0, 6))


class RingCanvas(QWidget):
    """Configured actions only, plus one permanent control for opening the editor."""

    selected = Signal(int)
    moved = Signal(int, int)
    add_requested = Signal()

    def __init__(self, slots, parent=None, *, editing=False, title="Action Ring", icon=None,
                 show_labels=None):
        super().__init__(parent)
        self.editing = editing
        self.title = title
        self.show_labels = editing if show_labels is None else show_labels
        self.center_icon = icon or application_icon()
        self.selected_slot = 0 if editing else -1
        self.hover_slot = -1
        self.slots = []
        self.visible_slots = []
        self.setMinimumSize(220, 220)
        self.buttons = []
        for slot in range(7):
            button = RingButton(slot, self, editing=editing, hover_zoom=not editing)
            button.clicked.connect(lambda checked=False, index=slot: self._select(index))
            button.hovered.connect(self._hover)
            button.moved.connect(self.moved)
            self.buttons.append(button)
        self.add_button = RingButton(7, self, add=True, hover_zoom=not editing)
        self.add_button.clicked.connect(self.add_requested)
        self.add_button.hovered.connect(self._hover)
        self.set_slots(slots)

    def set_slots(self, slots):
        if not isinstance(slots, list) or len(slots) > 7:
            raise ValueError("An Action Ring supports at most seven actions")
        self.slots = slots
        self.visible_slots = [index for index, entry in enumerate(slots) if entry is not None]
        if self.selected_slot not in self.visible_slots:
            self.selected_slot = -1
        for index, button in enumerate(self.buttons):
            entry = slots[index] if index < len(slots) else None
            button.filled = entry is not None
            button.application_artwork = bool(entry and entry["action"]["type"] == "launch")
            button.setEnabled(entry is not None)
            button.setVisible(entry is not None)
            button.set_draggable(entry is not None)
            label = entry["label"] if entry else ""
            button.setToolTip(label)
            ordinal = self.visible_slots.index(index) + 1 if entry else 0
            button.setAccessibleName(f"{ordinal}: {label}" if entry else "")
            artwork = display_icon(entry["icon"], application=button.application_artwork) if entry else application_icon()
            button.symbolic_artwork = False
            if entry and not button.application_artwork:
                name = entry["icon"]
                candidates = [name if name.endswith("-symbolic") else name + "-symbolic"]
                if entry["action"]["type"] == "screenshot" and name == "applets-screenshooter":
                    candidates.append("camera-photo-symbolic")
                for candidate in candidates:
                    if not candidate.startswith("/") and QIcon.hasThemeIcon(candidate):
                        artwork = QIcon.fromTheme(candidate)
                        button.symbolic_artwork = True
                        break
            button.setIcon(artwork)
            button.setChecked(self.editing and index == self.selected_slot)
            button.update()
        label = "Add action" if len(self.visible_slots) < 7 else "Edit actions"
        self.add_button.setToolTip(label)
        self.add_button.setAccessibleName(label)
        self.add_button.setEnabled(True)
        self.add_button.show()
        self._position_buttons()
        self.update()

    def select(self, index):
        self.selected_slot = index if index in self.visible_slots else -1
        for number, button in enumerate(self.buttons):
            button.setChecked(self.editing and number == self.selected_slot)
        self.update()

    def _select(self, index):
        if index not in self.visible_slots:
            return
        if self.editing:
            self.select(index)
        self.selected.emit(index)

    def _hover(self, index):
        self.hover_slot = index
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_buttons()

    def _orbit(self):
        diameter = min(self.width(), self.height())
        return min(100 if self.editing else 72, diameter * .32)

    def _position_buttons(self):
        diameter = min(self.width(), self.height())
        size = (min(50, round(diameter * .18)) if self.editing
                else min(44, round(diameter * .2)))
        radius = self._orbit()
        visible = [self.buttons[index] for index in self.visible_slots] + [self.add_button]
        for index, button in enumerate(visible):
            # The final control is always +, anchored at six o'clock.
            angle = math.pi / 2 + (index + 1) * 2 * math.pi / len(visible)
            x = round(self.width() / 2 + math.cos(angle) * radius - size / 2)
            y = round(self.height() / 2 + math.sin(angle) * radius - size / 2)
            button.set_rest_geometry(QRect(x, y, size, size))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        palette = self.palette()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        border = palette.color(QPalette.ColorRole.Mid)
        border.setAlpha(75)
        painter.setPen(QPen(border, 1))
        radius = self._orbit()
        painter.drawEllipse(QPoint(center), round(radius), round(radius))
        painter.setBrush(palette.color(QPalette.ColorRole.Window))
        painter.setPen(QPen(border, 1))
        center_radius = 21 if self.editing else 18
        icon_radius = 12 if self.editing else 11
        painter.drawEllipse(center, center_radius, center_radius)
        self.center_icon.paint(painter, QRect(center.x() - icon_radius, center.y() - icon_radius,
                                              icon_radius * 2, icon_radius * 2))
        if self.show_labels:
            self._paint_labels(painter)

    def _paint_labels(self, painter):
        center = self.rect().center()
        palette = self.palette()
        visible = [(self.buttons[index], self.slots[index]["label"]) for index in self.visible_slots]
        visible.append((self.add_button, self.add_button.toolTip()))
        for button, label in visible:
            point = button.geometry().center()
            dx, dy = point.x() - center.x(), point.y() - center.y()
            if abs(dx) > self._orbit() * .35:
                if dx > 0:
                    left = button.geometry().right() + 10
                    area = QRect(left, point.y() - 15, max(0, self.width() - left - 12), 30)
                    alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                else:
                    right = button.geometry().left() - 10
                    area = QRect(12, point.y() - 15, max(0, right - 12), 30)
                    alignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            else:
                top = button.geometry().bottom() + 9 if dy > 0 else button.geometry().top() - 39
                area = QRect(point.x() - 80, top, 160, 30)
                alignment = Qt.AlignmentFlag.AlignCenter
            painter.setPen(palette.color(QPalette.ColorRole.Accent if button.isChecked()
                                         else QPalette.ColorRole.WindowText))
            painter.drawText(area, alignment,
                             painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, area.width()))


class RingOverlay(QWidget):
    """A popup owns its clicks; closing never replays a click into another app."""

    selected = Signal(int)
    cancelled = Signal()
    configure_requested = Signal()

    def __init__(self, context, applications=(), parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.setWindowTitle("Action Ring")
        self.setWindowIcon(application_icon())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.finished = False
        self.context = context
        foreground = context.get("foreground", {})
        application = foreground_application(foreground, applications)
        icon = display_icon(application.icon, application=True) if application else application_icon()
        name = application.name if application else foreground.get("name") or "Action Ring"
        self.canvas = RingCanvas(context["layout"]["slots"], self, title=name, icon=icon)
        self.canvas.selected.connect(self.choose)
        self.canvas.add_requested.connect(self.configure_ring)
        self._bloom_pixmap = None
        self._bloom_progress = 1.0
        self._bloom_origin = QPointF()
        self._bloom_animation = QVariantAnimation(self)
        self._bloom_animation.setDuration(220)
        self._bloom_animation.setStartValue(0.0)
        self._bloom_animation.setEndValue(1.0)
        self._bloom_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._bloom_animation.valueChanged.connect(self._advance_bloom)
        self._bloom_animation.finished.connect(self._finish_bloom)
        self.resize(232, 232)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.canvas.setGeometry(self.rect())

    def open_at_cursor(self):
        if self.finished:
            return
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        size = min(232, available.width(), available.height())
        self.canvas.setMinimumSize(0, 0)
        self.resize(size, size)
        self.move(ring_position(cursor, available, self.size()))
        self.canvas.setGeometry(self.rect())
        self._bloom_origin = QPointF(cursor - self.pos())
        self._bloom_progress = 0.0
        # Render once at native resolution; only this small image is scaled
        # during opening. The editor and native button hitboxes stay unchanged.
        ratio = self.devicePixelRatioF()
        self._bloom_pixmap = QPixmap(round(size * ratio), round(size * ratio))
        self._bloom_pixmap.setDevicePixelRatio(ratio)
        self._bloom_pixmap.fill(Qt.GlobalColor.transparent)
        self.canvas.render(self._bloom_pixmap, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        self.canvas.hide()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._bloom_animation.start()

    def _advance_bloom(self, progress):
        self._bloom_progress = progress
        self.update()

    def _bloom_rect(self):
        progress = self._bloom_progress
        return QRectF(self._bloom_origin.x() * (1 - progress),
                      self._bloom_origin.y() * (1 - progress),
                      self.width() * progress, self.height() * progress)

    def _finish_bloom(self):
        self._bloom_animation.stop()
        self._bloom_pixmap = None
        self._bloom_progress = 1.0
        if not self.finished:
            self.canvas.show()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._bloom_pixmap is not None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self._bloom_rect(), self._bloom_pixmap,
                               QRectF(self._bloom_pixmap.rect()))

    def event(self, event):
        # Tab traversal is handled by QWidget before keyPressEvent.
        if (event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)):
            self._finish_bloom()
        return super().event(event)

    def _hide_finished(self):
        self.finished = True
        # Qt's popup grab ends before the worker restores focus and sends keys.
        self.hide()
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        if QWidget.keyboardGrabber() is self:
            self.releaseKeyboard()

    def choose(self, slot):
        if self.finished or slot not in self.canvas.visible_slots:
            return
        self._hide_finished()
        self.selected.emit(slot)

    def configure_ring(self):
        if not self.finished:
            self._hide_finished()
            self.configure_requested.emit()

    def cancel(self):
        if not self.finished:
            self._hide_finished()
            self.cancelled.emit()

    def mousePressEvent(self, event):
        if (self._bloom_pixmap is not None and self._bloom_progress > 0
                and event.button() == Qt.MouseButton.LeftButton):
            # Match the currently visible artwork, not its final position.
            point = (event.position() - self._bloom_rect().topLeft()) / self._bloom_progress
            buttons = [self.canvas.buttons[index] for index in self.canvas.visible_slots]
            for button in buttons + [self.canvas.add_button]:
                if QRectF(button.geometry()).contains(point):
                    button.click()
                    event.accept()
                    return
        # Blank canvas and the center are deliberately safe cancellation areas.
        self.cancel()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        if Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_7:
            ordinal = event.key() - Qt.Key.Key_1
            if ordinal < len(self.canvas.visible_slots):
                self.choose(self.canvas.visible_slots[ordinal])
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.configure_ring()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._finish_bloom()
            valid = [self.canvas.buttons[index] for index in self.canvas.visible_slots] + [self.canvas.add_button]
            focused = next((button for button in valid if button.hasFocus()), None)
            step = -1 if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
            position = valid.index(focused) if focused is not None else (-1 if step > 0 else 0)
            valid[(position + step) % len(valid)].setFocus()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        super().hideEvent(event)
        # Qt dismisses popups on outside clicks without necessarily closing them.
        was_finished = self.finished
        self.finished = True
        self._finish_bloom()
        if not was_finished:
            self.cancelled.emit()

    def closeEvent(self, event):
        self.cancel()
        event.accept()


def _connect_session(overlay, token, request):
    """Route a single finished popup to its worker-owned session."""
    app = QApplication.instance()

    def finish(command, slot=None):
        def send():
            try:
                # The worker uses a different X11 connection. Confirm that Qt's
                # queued hide/ungrab has reached X before it can inject keys.
                # One sync at session end avoids a cross-connection ordering
                # race; the finished flag already prevents reentrant actions.
                if app.platformName() == "xcb":
                    app.sync()
                values = {"token": token}
                if slot is not None:
                    values["slot"] = slot
                response = request(command, timeout=3, **values)
                if not response.get("ok") and command != "ring_cancel":
                    raise RuntimeError(response.get("error", "The ring request could not complete"))
            except (OSError, RuntimeError) as error:
                if command != "ring_cancel":
                    QMessageBox.warning(None, "Action Ring", str(error))
                else:
                    print(str(error), file=sys.stderr)
            app.quit()
        QTimer.singleShot(0, send)

    overlay.selected.connect(lambda slot: finish("ring_execute", slot))
    overlay.cancelled.connect(lambda: finish("ring_cancel"))
    overlay.configure_requested.connect(lambda: finish("ring_configure"))


def run_ring() -> int:
    """Run exactly one worker-owned ring session, then release the Qt process."""
    from . import ipc
    from .appearance import AppearanceController
    from .applications import discover_applications
    from .configuration import load_settings

    app = QApplication(sys.argv[:1])
    app.setApplicationName("mx-creative-control-ring")
    app.setApplicationDisplayName("Action Ring")
    app.setDesktopFileName("mx-creative-control")
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    setup_icon_theme()
    try:
        context = ipc.request("ring_context", timeout=2)
        if not context.get("ok"):
            raise RuntimeError(context.get("error", "No active ring session"))
        applications = discover_applications()
        appearance = AppearanceController(app)
        appearance.set_preference(load_settings(applications)["theme"])
        overlay = RingOverlay(context, applications)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"Could not open Action Ring: {error}", file=sys.stderr)
        return 1

    _connect_session(overlay, context["token"], ipc.request)
    overlay.open_at_cursor()
    return app.exec()
