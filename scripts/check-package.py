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
    "./usr/lib/udev/rules.d/70-mx-creative-control.rules",
    "./usr/lib/systemd/user/mx-creative-control.service",
    "./usr/share/polkit-1/actions/org.mxcreativecontrol.setup-permissions.policy",
    "./usr/share/applications/mx-creative-control.desktop",
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
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import qVersion
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QLocalServer
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget
import mxcc
from mxcc.gui import MainWindow
from mxcc.paths import runtime_path
app = QApplication([])
widget = QWidget()
server = QLocalServer()
assert qVersion() == '6.10.2', qVersion()
assert QSvgRenderer(b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>').isValid()
image = QImage(72, 72, QImage.Format.Format_RGB32)
image.fill(0)
assert image.save(sys.argv[2], 'JPEG')
window = MainWindow(connect_worker=False, applications=[])
window.show()
app.processEvents()
compiled = json.loads(runtime_path().read_text())
assert len(compiled['pages']) == 2
assert all(len(page['keys']) == 9 for page in compiled['pages'])
assert not window.timer.isActive()
window.close()
app.processEvents()
print('Bundled settings window, compiled runtime, Qt SVG and JPEG smoke: OK')
"""
    subprocess.run(["/usr/bin/python3", "-I", "-c", smoke, str(private), str(root / "face.jpg")],
                   env=environment, check=True)
    subprocess.run([str(root / "usr/lib/mx-creative-control/mxcc-worker"), "--check-config",
                    str(root / "config/mx-creative-control/runtime.json")],
                   env=environment, check=True)
    helper = root / "usr/lib/mx-creative-control/setup-permissions"
    rejected = subprocess.run([str(helper), "unexpected"], capture_output=True, text=True)
    if rejected.returncode != 1 or "accepts no arguments" not in rejected.stderr:
        raise SystemExit("Permission helper did not reject unexpected input")
print(f"Package structure, permissions and private runtime: OK ({package.name})")
