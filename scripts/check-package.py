#!/usr/bin/env python3
"""Check a built .deb and smoke-test its Qt runtime without installing it."""
import argparse
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("package", type=Path)
args = parser.parse_args()
package = args.package.resolve()
required = {
    "./usr/bin/mx-creative-control",
    "./usr/lib/mx-creative-control/mxcc-worker",
    "./usr/lib/mx-creative-control/setup-permissions",
    "./usr/lib/mx-creative-control/python/mxcc/__main__.py",
    "./usr/lib/mx-creative-control/python/mxcc/appearance.py",
    "./usr/lib/mx-creative-control/python/mxcc/application_views.py",
    "./usr/lib/mx-creative-control/python/mxcc/key_button.py",
    "./usr/lib/mx-creative-control/python/mxcc/devices.py",
    "./usr/lib/mx-creative-control/python/mxcc/device_views.py",
    "./usr/lib/mx-creative-control/python/mxcc/device_navigation.py",
    "./usr/lib/udev/rules.d/70-mx-creative-control.rules",
    "./usr/lib/systemd/user/mx-creative-control.service",
    "./usr/share/polkit-1/actions/org.mxcreativecontrol.setup-permissions.policy",
    "./usr/share/applications/mx-creative-control.desktop",
    "./usr/share/icons/hicolor/scalable/apps/mx-creative-control.svg",
    "./usr/share/mx-creative-control/shortcuts/config/default.json",
}
process = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(package)], stdout=subprocess.PIPE)
seen = set()
with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
    for member in archive:
        seen.add(member.name)
        if member.uid != 0 or member.gid != 0:
            raise SystemExit(f"Non-root archive owner: {member.name}")
        if ".." in Path(member.name).parts or "__pycache__" in Path(member.name).parts:
            raise SystemExit(f"Unwanted package path: {member.name}")
        if member.mode & 0o6002:
            raise SystemExit(f"Unsafe package mode: {member.name}")
if process.wait() != 0:
    raise SystemExit("Could not read package archive")
if required - seen:
    raise SystemExit(f"Missing package paths: {sorted(required - seen)}")
with tempfile.TemporaryDirectory(prefix="mxcc-package-check-") as directory:
    root = Path(directory)
    subprocess.run(["dpkg-deb", "--extract", str(package), directory], check=True)
    subprocess.run(["dpkg-deb", "--control", str(package), str(root / "control")], check=True)
    policy = ET.parse(root / "usr/share/polkit-1/actions/org.mxcreativecontrol.setup-permissions.policy")
    if policy.find('.//annotate[@key="org.freedesktop.policykit.exec.path"]').text != "/usr/lib/mx-creative-control/setup-permissions":
        raise SystemExit("Policy does not match the packaged helper")
    private = root / "usr/lib/mx-creative-control/python"
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    environment = os.environ.copy()
    environment.update({"QT_QPA_PLATFORM": "offscreen", "XDG_CACHE_HOME": str(root / "cache"),
                        "XDG_CONFIG_HOME": str(root / "config"), "XDG_RUNTIME_DIR": str(runtime),
                        "MXCC_BUNDLED_PROFILES": str(root / "usr/share/mx-creative-control/shortcuts/config")})
    # Import only the actual bundled runtime, not the development virtualenv.
    smoke = """
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, qVersion
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QImage, QPalette
from PySide6.QtNetwork import QLocalServer
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget
import mxcc
from mxcc.gui import MainWindow
from mxcc.configuration import default_settings
from mxcc.applications import Application
from mxcc.devices import Device
from mxcc.device_views import CREATIVE_CONSOLE_ID
from mxcc.key_button import LAUNCHER_MIME
from mxcc.paths import profiles_dir, runtime_path, settings_path
app = QApplication([])
widget = QWidget()
server = QLocalServer()
assert qVersion() == '6.10.2', qVersion()
assert QSvgRenderer(b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>').isValid()
assert QSvgRenderer(sys.argv[3]).isValid()
app.setWindowIcon(QIcon(sys.argv[3]))
assert not app.windowIcon().pixmap(64, 64).isNull()
image = QImage(72, 72, QImage.Format.Format_RGB32)
image.fill(0)
assert image.save(sys.argv[2], 'JPEG')
# Existing version-1 settings must acquire the system theme without losing keys.
applications = [Application(f'{name}.desktop', name.title(), sys.argv[3], name,
                            name, Path(f'/unused/{name}.desktop'))
                for name in ('alpha', 'beta', 'gamma')]
inventory = [Device('test-keypad', 'MX Creative Keypad', 'keypad', 'USB', True, True, 0x046d, 0xc354),
             Device('test-dialpad', 'MX Dialpad', 'dialpad', 'Bluetooth', True, False, 0x046d, 0xbc00, battery_percent=81)]
profiles_dir().mkdir(parents=True, exist_ok=True)
(profiles_dir() / 'alpha.json').write_text(json.dumps({
    'version': 1, 'id': 'alpha', 'name': 'Alpha shortcuts',
    'desktop_ids': ['alpha.desktop'], 'wm_classes': ['alpha'],
    'shortcuts': [{'slot': 1, 'label': 'Copy', 'keys': 'Ctrl+C', 'icon': 'edit-copy'}],
}))
legacy = default_settings(applications)
legacy['launchers'] = [None, 'alpha.desktop', 'missing.desktop', None,
                       'beta.desktop', 'gamma.desktop', None, '@screenshot', None]
legacy.pop('theme', None)
settings_path().parent.mkdir(parents=True, exist_ok=True)
settings_path().write_text(json.dumps(legacy))
window = MainWindow(connect_worker=False, applications=applications, devices=inventory)
window.show()
app.processEvents()
assert window.views.currentWidget() is window.home
assert set(window.home.cards) == {CREATIVE_CONSOLE_ID}
assert window.home.cards[CREATIVE_CONSOLE_ID].indicators['test-dialpad'].battery_text == '81%'
assert window.device_scanner is None and not window.device_timer.isActive()
window.footer.settings_button.click()
assert window.views.currentWidget() is window.settings_page
window.settings_back.click()
assert window.views.currentWidget() is window.home
window.home.cards[CREATIVE_CONSOLE_ID].click()
assert window.views.currentWidget() is window.console
assert set(window.console.cards) == {device.id for device in inventory}
assert window.console.cards['test-dialpad'].indicators['test-dialpad'].battery_text == '81%'
window.footer.settings_button.click()
assert window.views.currentWidget() is window.settings_page
window.settings_back.click()
assert window.views.currentWidget() is window.console
window.console.cards['test-dialpad'].click()
assert window.views.currentWidget() is window.details
assert window.details.title.text() == 'MX Dialpad'
assert 'not available' in window.details.summary.text().lower()
window.details.back_button.click()
assert window.views.currentWidget() is window.console
window.console.cards['test-keypad'].click()
assert window.views.currentWidget() is window.editor
assert window.navigation.currentItem().text() == 'Keys'
window.navigation.setCurrentRow(1)
app.processEvents()
assert list(window.application_cards) == ['alpha.desktop', 'beta.desktop', 'gamma.desktop', '@screenshot']
assert window.application_cards['alpha.desktop'].profile_summary.text() == '1 shortcut'
assert window.application_cards['beta.desktop'].edit_button.text() == 'Set up'
assert window.application_cards['@screenshot'].profile_combo is None
window.search.setText('beta')
assert not window.application_cards['beta.desktop'].isHidden()
assert window.application_cards['alpha.desktop'].isHidden()
window.search.clear()
window.navigation.setCurrentRow(0)
window.back_button.click()
assert window.views.currentWidget() is window.console
window.console.back_button.click()
assert window.views.currentWidget() is window.home
window.home.cards[CREATIVE_CONSOLE_ID].click()
window.console.cards['test-keypad'].click()
assert window.views.currentWidget() is window.editor
app.processEvents()
assert not window.windowIcon().pixmap(64, 64).isNull()
assert json.loads(settings_path().read_text())['theme'] == 'system'
assert window.settings['launchers'] == ['alpha.desktop', 'beta.desktop', 'gamma.desktop',
                                        '@screenshot', 'missing.desktop', None, None, None, None]
compiled = json.loads(runtime_path().read_text())
assert len(compiled['pages']) == 2
assert all(len(page['keys']) == 9 for page in compiled['pages'])
assert not window.timer.isActive()
launch_actions = {key['action']['desktop_id']: key['action']
                  for key in compiled['pages'][0]['keys']
                  if key and key['action']['type'] == 'launch'}
for application in applications:
    classes = launch_actions[application.id]['wm_classes']
    assert isinstance(classes, list) and classes
    assert all(isinstance(value, str) and value for value in classes)
    assert application.wm_class.casefold() in {value.casefold() for value in classes}
assert launch_actions['alpha.desktop']['profile_id'] == 'alpha'
assert any(profile['id'] == 'alpha' for profile in compiled['profiles'])
assert launch_actions['beta.desktop'].get('profile_id') is None
assert launch_actions['gamma.desktop'].get('profile_id') is None
def displayed_launchers():
    keys = json.loads(runtime_path().read_text())['pages'][0]['keys']
    return [key['action'].get('desktop_id', '@screenshot') if key else None for key in keys]
assert displayed_launchers() == ['alpha.desktop', 'beta.desktop', 'gamma.desktop',
                                 '@screenshot', None, None, None, None, None]
original_runtime = runtime_path().read_bytes()
for mode in ('light', 'dark', 'system'):
    index = window.theme_combo.findData(mode)
    assert index >= 0, mode
    window.theme_combo.setCurrentIndex(index)
    app.processEvents()
    assert window.appearance.preference == mode
    assert json.loads(settings_path().read_text())['theme'] == mode
    if mode != 'system':
        assert window.appearance.effective_theme == mode
        assert (app.palette().color(QPalette.ColorRole.Window).lightness() < 128) == (mode == 'dark')
# Exercise the real Qt notification path without changing desktop preferences.
for scheme, expected in ((Qt.ColorScheme.Dark, 'dark'), (Qt.ColorScheme.Light, 'light')):
    app.styleHints().colorSchemeChanged.emit(scheme)
    app.processEvents()
    assert window.appearance.effective_theme == expected
assert runtime_path().read_bytes() == original_runtime

def drop_launcher(source_slot, target_slot):
    source, target = window.key_buttons[source_slot], window.key_buttons[target_slot]
    mime = QMimeData()
    mime.setData(LAUNCHER_MIME, str(source_slot).encode('ascii'))
    class Enter(QDragEnterEvent):
        def source(self):
            return source
    class Drop(QDropEvent):
        def source(self):
            return source
    enter = Enter(QPoint(20, 20), Qt.DropAction.MoveAction, mime,
                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted()
    drop = Drop(QPointF(20, 20), Qt.DropAction.MoveAction, mime,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, drop)
    assert drop.isAccepted()
    app.processEvents()

# Actual packaged native-drop handlers must save and compile insertion order.
shortcuts_before = json.loads(runtime_path().read_text())['pages'][1]
drop_launcher(0, 2)
assert displayed_launchers() == ['beta.desktop', 'gamma.desktop', 'alpha.desktop',
                                 '@screenshot', None, None, None, None, None]
drop_launcher(1, 8)
expected = ['beta.desktop', 'alpha.desktop', '@screenshot', 'gamma.desktop']
assert displayed_launchers() == expected + [None] * 5
saved = json.loads(settings_path().read_text())
assert saved['launchers'] == expected + ['missing.desktop'] + [None] * 4
assert json.loads(runtime_path().read_text())['pages'][1] == shortcuts_before
window.close()
app.processEvents()
window = MainWindow(connect_worker=False, applications=applications, devices=inventory)
window.show()
app.processEvents()
assert window.views.currentWidget() is window.home
window.home.cards[CREATIVE_CONSOLE_ID].click()
assert window.views.currentWidget() is window.console
window.console.cards['test-keypad'].click()
assert window.views.currentWidget() is window.editor
assert window.settings['launchers'] == saved['launchers']
assert displayed_launchers() == expected + [None] * 5
window.close()
app.processEvents()
print('Bundled device navigation, battery indicators, application cards, settings, activation metadata, packed Apps/native reorder, themes and runtime smoke: OK')
"""
    icon = root / "usr/share/icons/hicolor/scalable/apps/mx-creative-control.svg"
    subprocess.run(["/usr/bin/python3", "-I", "-c", smoke, str(private), str(root / "face.jpg"), str(icon)],
                   env=environment, check=True)
    subprocess.run([str(root / "usr/lib/mx-creative-control/mxcc-worker"), "--check-config",
                    str(root / "config/mx-creative-control/runtime.json")],
                   env=environment, check=True)
    helper = root / "usr/lib/mx-creative-control/setup-permissions"
    rejected = subprocess.run([str(helper), "unexpected"], capture_output=True, text=True)
    if rejected.returncode != 1 or "accepts no arguments" not in rejected.stderr:
        raise SystemExit("Permission helper did not reject unexpected input")
print(f"Package structure, permissions and private runtime: OK ({package.name})")
