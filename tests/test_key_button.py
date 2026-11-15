"""Exercise native key drag gestures without a worker or user configuration."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QIcon, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from mxcc import key_button
from mxcc.key_button import KeyButton, LAUNCHER_MIME

_APP = None


@pytest.fixture
def keypad():
    global _APP
    _APP = QApplication.instance() or QApplication(["mxcc-key-drag-test"])
    container = QWidget()
    source, target = KeyButton(0, container), KeyButton(1, container)
    source.setGeometry(0, 0, 100, 100)
    target.setGeometry(110, 0, 100, 100)
    for key in (source, target):
        key.setCheckable(True)
        key.set_reorder_enabled(True)
    source.set_draggable(True)
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.red)
    source.setIcon(QIcon(pixmap))
    container.show()
    _APP.processEvents()
    yield source, target
    container.close()
    container.deleteLater()
    _APP.processEvents()


class DragEnter(QDragEnterEvent):
    def __init__(self, source, mime, actions=Qt.DropAction.MoveAction):
        super().__init__(QPoint(20, 20), actions, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.origin = source

    def source(self):
        return self.origin


class Drop(QDropEvent):
    def __init__(self, source, mime):
        super().__init__(QPointF(20, 20), Qt.DropAction.MoveAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.origin = source

    def source(self):
        return self.origin


def launcher_mime(value=b"0"):
    mime = QMimeData()
    mime.setData(LAUNCHER_MIME, value)
    return mime


def move_mouse(key, distance):
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(10 + distance, 10),
                       QPointF(key.mapToGlobal(QPoint(10 + distance, 10))),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(key, event)


def test_click_selects_normally_and_drag_starts_only_after_threshold(keypad, monkeypatch):
    source, target = keypad
    clicks, drags = [], []
    source.clicked.connect(lambda checked: clicks.append(checked))

    class CapturedDrag(key_button.QDrag):
        def exec(self, actions):
            drags.append((self.source(), actions, bytes(self.mimeData().data(LAUNCHER_MIME)),
                          self.pixmap().isNull()))
            target._set_drop_target(True)
            return Qt.DropAction.IgnoreAction

    monkeypatch.setattr(key_button, "QDrag", CapturedDrag)
    QTest.mouseClick(source, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert clicks == [True]
    assert not drags
    QTest.mousePress(source, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    move_mouse(source, QApplication.startDragDistance() - 1)
    assert not drags
    move_mouse(source, QApplication.startDragDistance())
    assert drags == [(source, Qt.DropAction.MoveAction, b"0", False)]
    assert not source.isDown()
    assert not target.property("dropTarget")
    QTest.mouseRelease(source, Qt.MouseButton.LeftButton, pos=QPoint(30, 10))
    assert clicks == [True]


@pytest.mark.parametrize("disabled_part", ["empty", "shortcuts"])
def test_empty_keys_and_shortcut_page_cannot_start_a_drag(keypad, monkeypatch, disabled_part):
    source, _ = keypad
    if disabled_part == "empty":
        source.set_draggable(False)
    else:
        source.set_reorder_enabled(False)
    def forbidden(*_):
        raise AssertionError("This key cannot be dragged")
    monkeypatch.setattr(key_button, "QDrag", forbidden)
    QTest.mousePress(source, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    move_mouse(source, QApplication.startDragDistance() + 2)
    QTest.mouseRelease(source, Qt.MouseButton.LeftButton, pos=QPoint(30, 10))


def test_native_enter_drop_emit_move_and_clear_highlight(keypad):
    source, target = keypad
    moves = []
    target.moved.connect(lambda start, end: moves.append((start, end)))
    mime = launcher_mime()
    enter = DragEnter(source, mime)
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted()
    assert enter.dropAction() == Qt.DropAction.MoveAction
    assert target.property("dropTarget")
    event = Drop(source, mime)
    QApplication.sendEvent(target, event)
    assert event.isAccepted()
    assert moves == [(0, 1)]
    assert not target.property("dropTarget")


@pytest.mark.parametrize("invalid", ["foreign", "self", "missing", "malformed", "wrong_slot", "empty_source", "shortcut_source", "shortcut_target", "copy_only"])
def test_drop_rejects_foreign_malformed_empty_and_shortcut_sources(keypad, invalid):
    source, target = keypad
    mime = launcher_mime()
    actions = Qt.DropAction.MoveAction
    foreign = QWidget()
    if invalid == "foreign":
        source = KeyButton(0, foreign)
        source.set_reorder_enabled(True)
        source.set_draggable(True)
    elif invalid == "self":
        source = target
        source.set_draggable(True)
        mime = launcher_mime(b"1")
    elif invalid == "missing":
        mime = QMimeData()
    elif invalid == "malformed":
        mime = launcher_mime(b"\xff\x00")
    elif invalid == "wrong_slot":
        mime = launcher_mime(b"8")
    elif invalid == "empty_source":
        source.set_draggable(False)
    elif invalid == "shortcut_source":
        source.set_reorder_enabled(False)
    elif invalid == "shortcut_target":
        target.set_reorder_enabled(False)
    elif invalid == "copy_only":
        actions = Qt.DropAction.CopyAction
    moves = []
    target.moved.connect(lambda *args: moves.append(args))
    enter = DragEnter(source, mime, actions)
    target.dragEnterEvent(enter)
    assert not enter.isAccepted()
    assert not target.property("dropTarget")
    # A forged drop delivered without an accepted enter is also rejected.
    event = Drop(source, mime)
    if invalid != "copy_only":
        target.dropEvent(event)
        assert not event.isAccepted()
        assert not moves
    foreign.deleteLater()


def test_leaving_or_disabling_drop_target_removes_highlight(keypad):
    source, target = keypad
    mime = launcher_mime()
    target.dragEnterEvent(DragEnter(source, mime))
    assert target.property("dropTarget")
    target.dragLeaveEvent(QDragLeaveEvent())
    assert not target.property("dropTarget")
    target.dragEnterEvent(DragEnter(source, mime))
    assert target.property("dropTarget")
    target.set_reorder_enabled(False)
    assert not target.property("dropTarget")
    assert not target.acceptDrops()
