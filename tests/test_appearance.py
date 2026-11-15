"""Theme preferences affect Qt widgets without touching the keypad runtime."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox

from mxcc import gui, paths
from mxcc.appearance import AppearanceController
from mxcc.configuration import ConfigurationError, load_settings
from mxcc.profile_editor import ProfileEditor
from mxcc.transfer import export_bundle

_APP = None


class SystemHints(QObject):
    colorSchemeChanged = Signal(object)

    def __init__(self, scheme):
        super().__init__()
        self.scheme = scheme

    def colorScheme(self):
        return self.scheme

    def change(self, scheme):
        self.scheme = scheme
        self.colorSchemeChanged.emit(scheme)


@pytest.fixture
def isolated_theme(monkeypatch, tmp_path):
    global _APP
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_DATA_DIRS"):
        directory = tmp_path / key
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(key, str(directory))
    monkeypatch.delenv("MXCC_BUNDLED_PROFILES", raising=False)
    _APP = QApplication.instance() or QApplication(["mxcc-appearance-test"])
    old_palette, old_style = _APP.palette(), _APP.styleSheet()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _, title, detail: warnings.append((title, detail)))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    def no_worker(*args, **kwargs):
        raise AssertionError("Theme tests must not contact or start the keypad worker")
    monkeypatch.setattr(gui.ipc, "request", no_worker)
    monkeypatch.setattr(gui.service, "ensure_running", no_worker)
    yield _APP, warnings
    for widget in _APP.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()
    _APP.setStyleSheet(old_style)
    _APP.setPalette(old_palette)


def is_dark(widget):
    palette = widget.palette()
    return (palette.color(QPalette.ColorRole.Window).lightness()
            < palette.color(QPalette.ColorRole.WindowText).lightness())


def test_system_changes_are_live_and_manual_modes_override_them(isolated_theme):
    app, _ = isolated_theme
    hints = SystemHints(Qt.ColorScheme.Dark)
    controller = AppearanceController(app, hints=hints)
    controller.set_preference("system")
    assert is_dark(app)
    hints.change(Qt.ColorScheme.Light)
    assert not is_dark(app)

    controller.set_preference("light")
    hints.change(Qt.ColorScheme.Dark)
    assert not is_dark(app)
    controller.set_preference("dark")
    hints.change(Qt.ColorScheme.Light)
    assert is_dark(app)
    controller.set_preference("system")
    assert not is_dark(app)


def test_appearance_selection_is_saved_without_rewriting_runtime_or_images(isolated_theme):
    app, warnings = isolated_theme
    window = gui.MainWindow(connect_worker=False, applications=[])
    assert window.theme_combo.currentData() == "system"
    runtime = paths.runtime_path().read_bytes()
    images = {path.name: path.stat().st_mtime_ns for path in paths.cache_dir().iterdir()}

    window.theme_combo.setCurrentIndex(window.theme_combo.findData("dark"))
    app.processEvents()
    assert load_settings()["theme"] == "dark"
    assert window.appearance.effective_theme == "dark"
    assert is_dark(window)
    assert paths.runtime_path().read_bytes() == runtime
    assert {path.name: path.stat().st_mtime_ns for path in paths.cache_dir().iterdir()} == images
    assert not warnings

    reopened = gui.MainWindow(connect_worker=False, applications=[])
    assert reopened.theme_combo.currentData() == "dark"
    assert is_dark(reopened)


def test_import_applies_theme_to_window_dialogs_and_popups(isolated_theme, monkeypatch, tmp_path):
    app, warnings = isolated_theme
    window = gui.MainWindow(connect_worker=False, applications=[])
    window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))
    export = tmp_path / "configuration.json"
    export_bundle(export, {**window.settings, "theme": "dark"}, window.profiles)
    choices = []
    def select_export(*arguments):
        choices.append(arguments)
        return str(export), ""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", select_export)
    window.import_configuration()
    app.processEvents()

    assert window.theme_combo.currentData() == "dark"
    assert window.appearance.effective_theme == "dark"
    assert is_dark(window)
    assert choices[0][-1] == QFileDialog.Option.DontUseNativeDialog
    assert is_dark(window.theme_combo.view())
    editor = ProfileEditor(None, window.profiles["default"], window)
    assert is_dark(editor)
    assert is_dark(editor.table)
    assert is_dark(QMenu(window))
    assert not warnings


def test_failed_theme_save_keeps_existing_preference(isolated_theme, monkeypatch):
    _, warnings = isolated_theme
    window = gui.MainWindow(connect_worker=False, applications=[])
    original = window.settings["theme"]
    effective = window.appearance.effective_theme
    def fail(*args):
        raise ConfigurationError("Disk unavailable")
    monkeypatch.setattr(gui, "save_settings", fail)
    window.theme_combo.setCurrentIndex(window.theme_combo.findData("dark"))
    assert window.settings["theme"] == original
    assert window.theme_combo.currentData() == original
    assert window.appearance.effective_theme == effective
    assert json.loads(paths.settings_path().read_text())["theme"] == original
    assert warnings == [("Could not save appearance", "Disk unavailable")]
