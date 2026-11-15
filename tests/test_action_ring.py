"""Transient ring behavior without contacting the worker or injecting input."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QEnterEvent, QIcon, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mxcc.action_ring import RingCanvas, RingOverlay, _connect_session, foreground_application, ring_position
from mxcc.applications import Application

_APP = None


@pytest.fixture
def overlay():
    global _APP
    _APP = QApplication.instance() or QApplication(["mxcc-ring-test"])
    entry = {"label": "Copy", "icon": "edit-copy", "action": {"type": "shortcut", "keys": "Ctrl+C"}}
    widget = RingOverlay({"token": "one", "layout": {"slots": [entry]},
                          "foreground": {"window": 42, "name": "Editor", "identities": ["editor"]}})
    widget.show()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


def test_ring_position_clamps_to_cursor_monitor_including_negative_origin():
    size = QSize(280, 280)
    monitor = QRect(-1920, -200, 1920, 1080)
    assert ring_position(QPoint(-1910, -190), monitor, size) == QPoint(-1920, -200)
    assert ring_position(QPoint(-4, 870), monitor, size) == QPoint(-280, 600)
    assert ring_position(QPoint(-1000, 400), monitor, size) == QPoint(-1140, 260)


def test_select_hides_before_dispatch_and_cannot_execute_twice(overlay):
    selected, cancelled = [], []
    overlay.selected.connect(lambda slot: selected.append((slot, overlay.isVisible())))
    overlay.cancelled.connect(lambda: cancelled.append(True))
    QTest.mouseClick(overlay.canvas.buttons[0], Qt.MouseButton.LeftButton)
    overlay.choose(0)
    overlay.cancel()
    assert selected == [(0, False)]
    assert not cancelled
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)


def test_empty_slots_cannot_execute_and_escape_cancels_once(overlay):
    selected, cancelled = [], []
    overlay.selected.connect(selected.append)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    QTest.keyClick(overlay, Qt.Key.Key_2)
    assert overlay.isVisible() and not selected
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    overlay.close()
    assert not selected
    assert cancelled == [True]


def test_canvas_click_or_popup_dismissal_cancels_without_action(overlay):
    selected, cancelled = [], []
    overlay.selected.connect(selected.append)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    QTest.mouseClick(overlay.canvas, Qt.MouseButton.LeftButton,
                     pos=overlay.canvas.rect().center())
    assert not selected and cancelled == [True]


def test_keyboard_can_select_an_action_without_pointing(overlay):
    selected = []
    overlay.selected.connect(selected.append)
    QTest.keyClick(overlay, Qt.Key.Key_Right)
    assert overlay.canvas.buttons[0].hasFocus()
    QTest.keyClick(overlay.canvas.buttons[0], Qt.Key.Key_Return)
    assert selected == [0]


def test_foreground_app_matching_retains_original_desktop_icon():
    app = Application("org.example.Editor.desktop", "Editor", "/original/icon.svg", "editor",
                      "EditorClass", Path("/apps/editor.desktop"))
    assert foreground_application({"identities": ["editorclass"]}, [app]) is app
    assert foreground_application({"identities": ["ORG.EXAMPLE.EDITOR"]}, [app]) is app
    assert foreground_application({"identities": []}, [app]) is None


def test_non_symbolic_system_artwork_retains_internal_colors(overlay, tmp_path):
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("#e03030"))
    painter = QPainter(image)
    painter.fillRect(16, 0, 16, 32, QColor("#3060e0"))
    painter.end()
    path = tmp_path / "opaque-system-artwork.png"
    assert image.save(str(path))
    entry = {"label": "Custom shortcut", "icon": str(path),
             "action": {"type": "shortcut", "keys": "Ctrl+C"}}
    overlay.canvas.set_slots([entry])
    button = overlay.canvas.buttons[0]
    rendered = button.grab().toImage()
    assert rendered.pixelColor(button.width() // 2 - 8, button.height() // 2) == QColor("#e03030")
    assert rendered.pixelColor(button.width() // 2 + 8, button.height() // 2) == QColor("#3060e0")


@pytest.mark.parametrize("count", [0, 1, 7])
def test_compact_ring_shows_only_actions_and_one_permanent_plus(overlay, count):
    entry = overlay.canvas.slots[0]
    overlay.canvas.set_slots([entry] * count)
    QApplication.processEvents()
    assert overlay.size() == QSize(232, 232)
    assert sum(button.isVisible() for button in overlay.canvas.buttons) == count
    assert overlay.canvas.add_button.isVisible() and overlay.canvas.add_button.isEnabled()
    plus_center = overlay.canvas.add_button.geometry().center()
    assert abs(plus_center.x() - overlay.canvas.rect().center().x()) <= 1
    assert plus_center.y() > overlay.canvas.rect().center().y()
    shown = [button for button in overlay.canvas.buttons if button.isVisible()] + [overlay.canvas.add_button]
    for button in shown:
        assert button.size() == QSize(44, 44)
        assert overlay.canvas.rect().contains(button.geometry())
    assert all(not first.geometry().intersects(second.geometry())
               for index, first in enumerate(shown) for second in shown[index + 1:])
    assert all(not button.isEnabled() for button in overlay.canvas.buttons[count:])
    selected = []
    overlay.selected.connect(selected.append)
    for button in overlay.canvas.buttons[count:]:
        button.click()
    assert selected == []
    with pytest.raises(ValueError):
        overlay.canvas.set_slots([entry] * 8)


def test_number_shortcuts_follow_visible_actions_without_exposing_legacy_holes(overlay):
    entry = overlay.canvas.slots[0]
    overlay.canvas.set_slots([None, entry, None, entry])
    selected = []
    overlay.selected.connect(selected.append)
    assert overlay.canvas.visible_slots == [1, 3]
    assert overlay.canvas.buttons[0].isHidden() and overlay.canvas.buttons[2].isHidden()
    QTest.keyClick(overlay, Qt.Key.Key_2)
    assert selected == [3]


@pytest.mark.parametrize("count", [0, 7])
def test_plus_hides_then_requests_editor_once_even_at_capacity(overlay, count, monkeypatch):
    entry = overlay.canvas.slots[0]
    overlay.canvas.set_slots([entry] * count)
    requests = []
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(True)))

    def request(command, **values):
        requests.append((command, values, overlay.isVisible()))
        return {"ok": True}

    _connect_session(overlay, "one", request)
    if count:
        QTest.mouseClick(overlay.canvas.add_button, Qt.MouseButton.LeftButton)
    else:
        QTest.keyClick(overlay, Qt.Key.Key_Plus)
    assert not overlay.isVisible()
    overlay.configure_ring()
    QApplication.processEvents()
    assert requests == [("ring_configure", {"timeout": 3, "token": "one"}, False)]
    assert quits == [True]


def test_empty_ring_keyboard_navigation_can_reach_plus(overlay):
    overlay.canvas.set_slots([])
    configured = []
    overlay.configure_requested.connect(lambda: configured.append(True))
    QTest.keyClick(overlay, Qt.Key.Key_Right)
    assert overlay.canvas.add_button.hasFocus()
    QTest.keyClick(overlay.canvas.add_button, Qt.Key.Key_Return)
    assert configured == [True]


@pytest.mark.parametrize("cursor", [QPoint(-1000, 400), QPoint(-4, 870)])
def test_bloom_starts_at_cursor_and_settles_into_native_controls(overlay, monkeypatch, cursor):
    monitor = QRect(-1920, -200, 1920, 1080)

    class Screen:
        def availableGeometry(self):
            return monitor

    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: cursor))
    monkeypatch.setattr(QApplication, "screenAt", staticmethod(lambda point: Screen()))
    overlay.open_at_cursor()
    animation = overlay._bloom_animation
    assert animation.state() == QAbstractAnimation.State.Running
    assert overlay.canvas.isHidden()
    assert monitor.contains(overlay.geometry())
    assert overlay._bloom_origin == QPointF(cursor - overlay.pos())
    assert overlay._bloom_rect().topLeft() == overlay._bloom_origin
    assert overlay._bloom_rect().isEmpty()
    assert overlay._bloom_pixmap.toImage().pixelColor(0, 0).alpha() == 0

    animation.setCurrentTime(animation.duration() // 2)
    assert 0 < overlay._bloom_progress < 1
    assert 0 < overlay._bloom_rect().width() < overlay.width()
    assert QRectF(overlay.rect()).contains(overlay._bloom_rect())
    animation.setCurrentTime(animation.duration())
    assert animation.state() == QAbstractAnimation.State.Stopped
    assert overlay.canvas.isVisible()
    assert overlay._bloom_pixmap is None
    assert overlay._bloom_rect() == QRectF(overlay.rect())
    assert overlay.canvas.buttons[0].size() == QSize(44, 44)


@pytest.mark.parametrize("dismiss", ["cancel", "hide"])
def test_dismissing_mid_bloom_stops_animation_without_reviving_popup(overlay, dismiss):
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.open_at_cursor()
    animation = overlay._bloom_animation
    animation.setCurrentTime(animation.duration() // 2)
    getattr(overlay, dismiss)()
    assert animation.state() == QAbstractAnimation.State.Stopped
    assert overlay._bloom_pixmap is None
    animation.setCurrentTime(animation.duration())
    QApplication.processEvents()
    overlay.cancel()
    assert not overlay.isVisible() and not overlay.canvas.isVisible()
    assert cancelled == [True]


@pytest.mark.parametrize("key, expected", [(Qt.Key.Key_1, "action"),
                                          (Qt.Key.Key_Plus, "configure"),
                                          (Qt.Key.Key_Escape, "cancel")])
def test_bloom_does_not_delay_keyboard_actions_or_cancellation(overlay, key, expected):
    events = []
    overlay.selected.connect(lambda slot: events.append(("action", overlay.isVisible())))
    overlay.configure_requested.connect(lambda: events.append(("configure", overlay.isVisible())))
    overlay.cancelled.connect(lambda: events.append(("cancel", overlay.isVisible())))
    overlay.open_at_cursor()
    QTest.keyClick(overlay, key)
    assert events == [(expected, False)]
    assert overlay._bloom_animation.state() == QAbstractAnimation.State.Stopped
    assert overlay._bloom_pixmap is None


@pytest.mark.parametrize("key", [Qt.Key.Key_Right, Qt.Key.Key_Tab, Qt.Key.Key_Backtab])
def test_keyboard_navigation_settles_bloom_before_focusing_controls(overlay, key):
    overlay.open_at_cursor()
    QTest.keyClick(overlay, key)
    assert overlay.canvas.isVisible()
    assert overlay._bloom_animation.state() == QAbstractAnimation.State.Stopped
    assert overlay._bloom_pixmap is None
    controls = [overlay.canvas.buttons[0], overlay.canvas.add_button]
    assert any(button.hasFocus() for button in controls)


@pytest.mark.parametrize("target", ["action", "configure", "cancel"])
def test_pointer_can_use_visible_artwork_during_bloom(overlay, target):
    events = []
    overlay.selected.connect(lambda slot: events.append(("action", overlay.isVisible())))
    overlay.configure_requested.connect(lambda: events.append(("configure", overlay.isVisible())))
    overlay.cancelled.connect(lambda: events.append(("cancel", overlay.isVisible())))
    overlay.open_at_cursor()
    animation = overlay._bloom_animation
    animation.setCurrentTime(animation.duration() // 3)
    if target == "action":
        point = overlay.canvas.buttons[0].geometry().center()
    elif target == "configure":
        point = overlay.canvas.add_button.geometry().center()
    else:
        point = overlay.canvas.rect().center()
    displayed = overlay._bloom_rect()
    position = QPoint(round(displayed.left() + point.x() * displayed.width() / overlay.width()),
                      round(displayed.top() + point.y() * displayed.height() / overlay.height()))
    QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=position)
    assert events == [(target, False)]
    assert animation.state() == QAbstractAnimation.State.Stopped
    assert overlay._bloom_pixmap is None


def _enter_option(button):
    point = QPointF(button.rect().center())
    QApplication.sendEvent(button, QEnterEvent(point, point,
                                               QPointF(button.mapToGlobal(point.toPoint()))))


def test_hover_enlarges_artwork_around_fixed_center_and_returns_without_drift(overlay):
    entry = overlay.canvas.slots[0]
    overlay.canvas.set_slots([entry] * 7)
    button = overlay.canvas.buttons[0]
    resting = button.geometry()
    red = QColor("#ed1731")
    artwork = QPixmap(32, 32)
    artwork.fill(red)
    button.setIcon(QIcon(artwork))
    button.symbolic_artwork = False

    def artwork_width():
        image = button.grab().toImage()
        return sum(image.pixelColor(x, image.height() // 2) == red
                   for x in range(image.width()))

    original_artwork_width = artwork_width()
    for _ in range(2):
        _enter_option(button)
        animation = button._hover_animation
        animation.setCurrentTime(animation.duration() // 2)
        assert 1 < button._hover_scale < 1.3
        animation.setCurrentTime(animation.duration())
        assert button._hover_scale == pytest.approx(1.3)
        assert QRectF(button.geometry()).center() == QRectF(resting).center()
        assert overlay.canvas.rect().contains(button.geometry())
        assert artwork_width() / original_artwork_width == pytest.approx(1.3, abs=.06)
        QApplication.sendEvent(button, QEvent(QEvent.Type.Leave))
        animation.setCurrentTime(animation.duration())
        assert button.geometry() == resting
        assert button._hover_scale == 1


@pytest.mark.parametrize("target", ["action", "configure"])
def test_enlarged_option_area_is_clickable(overlay, target):
    button = overlay.canvas.buttons[0] if target == "action" else overlay.canvas.add_button
    resting = button.geometry()
    _enter_option(button)
    animation = button._hover_animation
    animation.setCurrentTime(animation.duration())
    point = button.mapTo(overlay.canvas, QPoint(3, button.height() // 2))
    assert not resting.contains(point)
    assert overlay.canvas.childAt(point) is button
    events = []
    overlay.selected.connect(lambda slot: events.append("action"))
    overlay.configure_requested.connect(lambda: events.append("configure"))
    QTest.mouseClick(button, Qt.MouseButton.LeftButton,
                     pos=button.mapFrom(overlay.canvas, point))
    assert events == [target]
    assert not overlay.isVisible()


def test_hiding_popup_stops_and_resets_hover_animation(overlay):
    button = overlay.canvas.buttons[0]
    resting = button.geometry()
    _enter_option(button)
    animation = button._hover_animation
    animation.setCurrentTime(animation.duration() // 2)
    assert button._hover_scale > 1
    overlay.hide()
    assert animation.state() == QAbstractAnimation.State.Stopped
    assert button._hover_scale == 1
    assert button.geometry() == resting
    QTest.qWait(animation.duration() + 20)
    assert button.geometry() == resting and not overlay.isVisible()


def test_editor_actions_and_plus_keep_their_size_on_hover(overlay):
    editor = RingCanvas(overlay.canvas.slots, editing=True)
    editor.resize(408, 390)
    editor.show()
    QApplication.processEvents()
    try:
        for button in (editor.buttons[0], editor.add_button):
            resting = button.geometry()
            _enter_option(button)
            assert not button.hover_zoom
            assert button._hover_animation is None
            assert button.geometry() == resting
            QApplication.sendEvent(button, QEvent(QEvent.Type.Leave))
    finally:
        editor.close()
        editor.deleteLater()
