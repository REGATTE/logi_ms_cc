"""Native drag-and-drop between launcher keys in one keypad preview."""

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QToolButton


LAUNCHER_MIME = "application/x-mxcc-launcher-slot"


class KeyButton(QToolButton):
    moved = Signal(int, int)

    def __init__(self, slot: int, parent=None):
        super().__init__(parent)
        if type(slot) is not int or not 0 <= slot < 9:
            raise ValueError("Key slot must be between 0 and 8")
        self.slot = slot
        self._reorder_enabled = False
        self._draggable = False
        self._press_position = None
        self.setProperty("dropTarget", False)

    def set_reorder_enabled(self, enabled: bool) -> None:
        self._reorder_enabled = bool(enabled)
        self.setAcceptDrops(self._reorder_enabled)
        if not enabled:
            self._press_position = None
            self._set_drop_target(False)
        self._update_cursor()

    def set_draggable(self, enabled: bool) -> None:
        self._draggable = bool(enabled)
        if not enabled:
            self._press_position = None
        self._update_cursor()

    def _update_cursor(self) -> None:
        if self._reorder_enabled and self._draggable:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def _set_drop_target(self, active: bool) -> None:
        if self.property("dropTarget") != active:
            self.setProperty("dropTarget", active)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def mousePressEvent(self, event):
        self._press_position = (
            event.position().toPoint()
            if event.button() == Qt.MouseButton.LeftButton
            and self._reorder_enabled and self._draggable else None
        )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.position().toPoint() - self._press_position).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._press_position = None
                self.setDown(False)
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData(LAUNCHER_MIME, str(self.slot).encode("ascii"))
                drag.setMimeData(mime)
                pixmap = self.icon().pixmap(self.iconSize())
                if not pixmap.isNull():
                    drag.setPixmap(pixmap)
                    ratio = pixmap.devicePixelRatio()
                    drag.setHotSpot(QPoint(int(pixmap.width() / ratio / 2),
                                          int(pixmap.height() / ratio / 2)))
                try:
                    drag.exec(Qt.DropAction.MoveAction)
                finally:
                    # Cancellation can end the nested drag loop without another
                    # mouse release reaching the source button.
                    self.setDown(False)
                    parent = self.parentWidget()
                    if parent is not None:
                        for child in parent.children():
                            if isinstance(child, KeyButton):
                                child._set_drop_target(False)
                    drag.deleteLater()
                event.accept()
                return
        elif not event.buttons() & Qt.MouseButton.LeftButton:
            self._press_position = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_position = None
        super().mouseReleaseEvent(event)

    def _source(self, event):
        source = event.source()
        if (not self._reorder_enabled or not self.isEnabled()
                or not isinstance(source, KeyButton) or source is self
                or not source._reorder_enabled or not source._draggable
                or not source.isEnabled() or self.parentWidget() is None
                or source.parentWidget() is not self.parentWidget()
                or not event.possibleActions() & Qt.DropAction.MoveAction):
            return None
        mime = event.mimeData()
        if not mime.hasFormat(LAUNCHER_MIME):
            return None
        if bytes(mime.data(LAUNCHER_MIME)) != str(source.slot).encode("ascii"):
            return None
        return source

    def _accept_drag(self, event):
        valid = self._source(event) is not None
        self._set_drop_target(valid)
        if valid:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragEnterEvent(self, event):
        self._accept_drag(event)

    def dragMoveEvent(self, event):
        self._accept_drag(event)

    def dragLeaveEvent(self, event):
        self._set_drop_target(False)
        event.accept()

    def dropEvent(self, event):
        self._set_drop_target(False)
        source = self._source(event)
        if source is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.moved.emit(source.slot, self.slot)
