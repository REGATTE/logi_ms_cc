"""Frameless window gestures remain usable without device or worker access."""

import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from mxcc.window_frame import RoundedWindow

_APP = None


@pytest.fixture
def window():
    global _APP
    _APP = QApplication.instance() or QApplication(["window-frame-test"])
    previous_style = _APP.styleSheet()
    _APP.setStyleSheet("QMainWindow#appWindow { background: transparent; }")
    frame = RoundedWindow()
    frame.setMinimumSize(120, 100)
    frame.resize(320, 240)
    content = QWidget()
    content.setObjectName("content")
    content.setStyleSheet("QWidget#content { background: #e5e6e9; }")
    frame.setCentralWidget(content)
    frame.mark_drag_region(content)
    frame.show()
    _APP.processEvents()
    yield frame
    frame.close()
    frame.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()
    _APP.setStyleSheet(previous_style)


def test_rounded_window_has_transparent_corners_and_square_maximized_edges(window):
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    image = window.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(10, 10).alpha() == 255
    assert image.pixelColor(window.width() // 2, 4).alpha() == 255
    assert window.contentsMargins().left() == 8
    changes = []
    window.window_state_changed.connect(lambda: changes.append(True))
    window.toggle_maximized()
    QApplication.processEvents()
    assert window.isMaximized() and window.contentsMargins().left() == 0
    assert window.grab().toImage().pixelColor(0, 0).alpha() > 0
    assert not window._frame_edges(QPoint(0, 0))
    window.toggle_maximized()
    QApplication.processEvents()
    assert not window.isMaximized() and window.contentsMargins().left() == 8
    assert len(changes) == 2


def test_border_delegates_resize_to_window_manager_and_content_keeps_arrow(window, monkeypatch):
    resize = Mock(return_value=True)
    monkeypatch.setattr(window.windowHandle(), "startSystemResize", resize)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    resize.assert_called_once_with(Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    assert window._pointer_start is None
    assert window._frame_edges(QPoint(window.width() - 2, 80)) == Qt.Edge.RightEdge
    assert not window._frame_edges(window.rect().center())
    QTest.mouseMove(window, QPoint(2, 80))
    assert window.cursor().shape() == Qt.CursorShape.SizeHorCursor
    assert window.centralWidget().cursor().shape() == Qt.CursorShape.ArrowCursor


def test_marked_background_moves_but_buttons_keep_their_clicks(window, monkeypatch):
    move = Mock(return_value=True)
    monkeypatch.setattr(window.windowHandle(), "startSystemMove", move)
    surface = window.centralWidget()
    button = QPushButton("Action", surface)
    button.setGeometry(20, 20, 80, 32)
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    button.show()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicks == [True] and not move.called
    QTest.mouseClick(surface, Qt.MouseButton.LeftButton, pos=QPoint(200, 40))
    move.assert_called_once_with()
    QTest.mouseDClick(surface, Qt.MouseButton.LeftButton, pos=QPoint(200, 40))
    assert window.isMaximized()


def test_fallback_drag_and_resize_respect_minimum_size_and_stop_on_hide(window, monkeypatch):
    monkeypatch.setattr(window.windowHandle(), "startSystemMove", lambda: False)
    monkeypatch.setattr(window.windowHandle(), "startSystemResize", lambda edges: False)
    initial = window.geometry()
    origin = QPointF(300, 200)
    window._begin_pointer_gesture(origin)
    assert window._move_pointer(origin + QPointF(25, 15))
    assert window.pos() == initial.topLeft() + QPoint(25, 15)
    window._end_pointer_gesture()
    initial = window.geometry()
    window._begin_pointer_gesture(origin, Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    window._move_pointer(origin + QPointF(1000, 1000))
    assert window.size() == window.minimumSize()
    assert window.geometry().bottomRight() == initial.bottomRight()
    window.hide()
    assert window._pointer_start is None
    assert not window._move_pointer(origin)
