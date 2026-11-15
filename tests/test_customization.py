"""Keep every device editor usable at the supported minimum window size."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, QRect
from PySide6.QtWidgets import QApplication, QMessageBox

from mxcc import gui, paths
from mxcc.customization import CustomizationWorkspace
from mxcc.device_controls import controls_for_device
from mxcc.devices import Device


_APP = None


@pytest.fixture
def editor_window(monkeypatch, tmp_path):
    global _APP
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS"):
        directory = tmp_path / name.lower()
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(name, str(directory))
    monkeypatch.delenv("MXCC_BUNDLED_PROFILES", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _APP = QApplication.instance() or QApplication(["mxcc-customization-test"])

    def forbidden(*args, **kwargs):
        pytest.fail("Editor layout checks must not contact a worker or discover host devices")

    for owner, name in ((gui, "discover_devices"), (gui, "discover_applications"),
                        (gui.ipc, "request"), (gui.service, "ensure_running"),
                        (QMessageBox, "warning")):
        monkeypatch.setattr(owner, name, forbidden)
    inventory = []
    for kind, product, title in (("keypad", 0xc354, "MX Creative Keypad"),
                                 ("dialpad", 0xbc00, "MX Dialpad"),
                                 ("mouse", 0xc07e, "Gaming Mouse G402"),
                                 ("device", 0xf001, "New Logitech device")):
        inventory.append(Device(
            kind, title, kind, "USB", True, kind == "keypad", 0x046d, product,
            battery_percent=81 if kind == "dialpad" else None,
            controls=controls_for_device(vendor_id=0x046d, product_id=product, kind=kind),
        ))
    window = gui.MainWindow(connect_worker=False, applications=[], devices=inventory)
    window.resize(970, 710)
    window.show()
    _APP.processEvents()
    yield window
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_all_customization_pages_fit_without_clipping_or_writing_settings(editor_window, theme):
    window = editor_window
    window.appearance.set_preference(theme)
    original = paths.settings_path().read_bytes(), paths.runtime_path().read_bytes()
    for destination in ("keypad", "dialpad", "mouse", "device", "ring"):
        if destination == "ring":
            window.show_ring()
        else:
            window.open_device(destination)
        QApplication.processEvents()
        assert (window.width(), window.height()) == (970, 710), destination
        visible = [workspace for workspace in window.views.currentWidget().findChildren(CustomizationWorkspace)
                   if workspace.isVisible()]
        assert len(visible) == 1, destination
        workspace = visible[0]
        stage = QRect(workspace.stage.mapTo(window, QPoint()), workspace.stage.size())
        panel = QRect(workspace.library.mapTo(window, QPoint()), workspace.library.size())
        assert stage.right() < panel.left(), destination
        assert window.rect().contains(stage), destination
        assert window.rect().contains(panel), destination
        assert workspace.library.visibleRegion().boundingRect() == workspace.library.rect(), destination
        assert window.footer.settings_button.isVisible(), destination
        assert window.footer.refresh_button.isVisible(), destination
    assert (paths.settings_path().read_bytes(), paths.runtime_path().read_bytes()) == original
