"""Transient ring behavior without contacting the worker or injecting input."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mxcc.action_ring import RingOverlay, _connect_session, foreground_application, ring_position
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
    assert overlay.size() == QSize(280, 280)
    assert sum(button.isVisible() for button in overlay.canvas.buttons) == count
    assert overlay.canvas.add_button.isVisible() and overlay.canvas.add_button.isEnabled()
    shown = [button for button in overlay.canvas.buttons if button.isVisible()] + [overlay.canvas.add_button]
    for button in shown:
        assert button.size() == QSize(50, 50)
        assert overlay.canvas.rect().contains(button.geometry())
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
