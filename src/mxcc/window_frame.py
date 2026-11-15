"""Rounded, frameless settings shell with native window-manager gestures."""

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QMainWindow


class RoundedWindow(QMainWindow):
    """Keep window controls in the app without losing move and resize support."""

    window_state_changed = Signal()
    FRAME_MARGIN = 8
    CORNER_RADIUS = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appWindow")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._pointer_start = None
        self._pointer_geometry = QRect()
        self._resize_edges = Qt.Edge(0)
        self._update_frame()

    def mark_drag_region(self, widget):
        """Enable dragging on this widget's empty surface, never its controls."""
        widget.installEventFilter(self)

    def setCentralWidget(self, widget):
        # An edge resize cursor must not be inherited by the app's controls.
        widget.setCursor(Qt.CursorShape.ArrowCursor)
        super().setCentralWidget(widget)

    def toggle_maximized(self):
        self.showNormal() if self._square_frame() else self.showMaximized()

    def _square_frame(self):
        return self.isMaximized() or self.isFullScreen()

    def _update_frame(self):
        margin = 0 if self._square_frame() else self.FRAME_MARGIN
        self.setContentsMargins(margin, margin, margin, margin)
        self.unsetCursor()
        self.update()

    def _frame_edges(self, point):
        if self._square_frame():
            return Qt.Edge(0)
        edges = Qt.Edge(0)
        if point.x() < self.FRAME_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= self.width() - self.FRAME_MARGIN:
            edges |= Qt.Edge.RightEdge
        if point.y() < self.FRAME_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= self.height() - self.FRAME_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _begin_pointer_gesture(self, global_position, edges=Qt.Edge(0)):
        self._end_pointer_gesture()
        handle = self.windowHandle()
        started = False
        if handle is not None:
            started = handle.startSystemResize(edges) if edges else handle.startSystemMove()
        if not started and not self._square_frame():
            # Some window managers do not implement the native gesture.
            self._pointer_start = global_position.toPoint()
            self._pointer_geometry = self.geometry()
            self._resize_edges = edges

    def _move_pointer(self, global_position):
        if self._pointer_start is None:
            return False
        delta = global_position.toPoint() - self._pointer_start
        initial = self._pointer_geometry
        if not self._resize_edges:
            self.move(initial.topLeft() + delta)
            return True
        result = QRect(initial)
        if self._resize_edges & Qt.Edge.LeftEdge:
            width = max(self.minimumWidth(),
                        min(self.maximumWidth(), initial.width() - delta.x()))
            result.setLeft(initial.right() - width + 1)
        elif self._resize_edges & Qt.Edge.RightEdge:
            result.setWidth(max(self.minimumWidth(),
                                min(self.maximumWidth(), initial.width() + delta.x())))
        if self._resize_edges & Qt.Edge.TopEdge:
            height = max(self.minimumHeight(),
                         min(self.maximumHeight(), initial.height() - delta.y()))
            result.setTop(initial.bottom() - height + 1)
        elif self._resize_edges & Qt.Edge.BottomEdge:
            result.setHeight(max(self.minimumHeight(),
                                 min(self.maximumHeight(), initial.height() + delta.y())))
        self.setGeometry(result)
        return True

    def _end_pointer_gesture(self):
        self._pointer_start = None
        self._resize_edges = Qt.Edge(0)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if (event.button() != Qt.MouseButton.LeftButton
                    or watched.childAt(event.position().toPoint()) is not None):
                return False
            if kind == QEvent.Type.MouseButtonDblClick:
                self._end_pointer_gesture()
                self.toggle_maximized()
            else:
                self._begin_pointer_gesture(event.globalPosition())
            event.accept()
            return True
        if kind == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
            return self._move_pointer(event.globalPosition())
        if kind == QEvent.Type.MouseButtonRelease:
            self._end_pointer_gesture()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        edges = self._frame_edges(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and edges:
            self._begin_pointer_gesture(event.globalPosition(), edges)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._move_pointer(event.globalPosition()):
            event.accept()
            return
        edges = self._frame_edges(event.position().toPoint())
        cursors = {
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
            Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
            Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
        }
        self.setCursor(cursors.get(edges, Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._end_pointer_gesture()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._end_pointer_gesture()
            self._update_frame()
            self.window_state_changed.emit()

    def hideEvent(self, event):
        self._end_pointer_gesture()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # The transparent stylesheet selector changes the widget's Window
        # brush; use the opaque application palette for the rounded surface.
        painter.setBrush(QApplication.palette().color(QPalette.ColorRole.Window))
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Mid), 1))
        radius = 0 if self._square_frame() else self.CORNER_RADIUS
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), radius, radius)
