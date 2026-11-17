"""Register the Firefox host for this user without changing browser preferences."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import zipfile

from .browser_bridge import EXTENSION_ID, HOST_NAME
from .paths import config_dir

_MARKER = "# MX Creative Control Firefox native host v1"
_ASSETS = ("manifest.json", "background.js", "popup.html", "popup.js", "popup.css")


def extension_path() -> Path:
    """The source manifest is directly loadable from Firefox's debugging page."""
    source = Path(__file__).resolve().parents[2] / "browser-extension/firefox/manifest.json"
    installed = Path("/usr/share/mx-creative-control/browser-extension/firefox/manifest.json")
    for candidate in (source, installed):
        if candidate.is_file():
            return candidate
    raise RuntimeError("The Firefox extension is missing. Reinstall the app or restore browser-extension/firefox.")


def _manifest_path() -> Path:
    # Ubuntu's Firefox Snap asks the host-side WebExtensions portal/proxy to
    # read this standard Mozilla location; a path inside the Snap is not needed.
    return Path.home() / ".mozilla/native-messaging-hosts" / f"{HOST_NAME}.json"


def _host_path() -> Path:
    return config_dir() / "browser/native-host"


def _host_content() -> str:
    source = str(Path(__file__).resolve().parents[1])
    bootstrap = f"import sys; sys.path.insert(0, {source!r}); from mxcc.browser_bridge import main; raise SystemExit(main())"
    return (f"#!/bin/sh\n{_MARKER}\nexec " + shlex.quote(sys.executable)
            + " -I -u -c " + shlex.quote(bootstrap) + ' "$@"\n')


def _manifest(host: Path) -> dict:
    return {"name": HOST_NAME, "description": "MX Creative Control website shortcuts",
            "path": str(host), "type": "stdio", "allowed_extensions": [EXTENSION_ID]}


def _owned_file(path: Path):
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symbolic link: {path}")
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError(f"This file is not owned by your user: {path}")
    return True


def _check_existing(manifest: Path, host: Path):
    if _owned_file(host) and _MARKER not in host.read_text().splitlines()[:2]:
        raise RuntimeError("An unrelated native host already occupies the setup path.")
    if _owned_file(manifest):
        try:
            previous = json.loads(manifest.read_text())
        except (OSError, ValueError) as error:
            raise RuntimeError("An existing Firefox host manifest cannot be safely replaced.") from error
        if previous != _manifest(host):
            raise RuntimeError("An unrelated Firefox host manifest already uses this name.")


def _private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError(f"Setup needs a real directory owned by your user: {path}")


def _write(path: Path, data: bytes, mode: int):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def installation_status() -> dict:
    manifest, host = _manifest_path(), _host_path()
    try:
        extension = extension_path()
        _check_existing(manifest, host)
        installed = (manifest.is_file() and host.is_file()
                     and os.access(host, os.X_OK) and host.read_text() == _host_content())
        error = None
    except (OSError, RuntimeError, ValueError) as failure:
        installed, extension, error = False, None, str(failure)
    return {"installed": installed, "manifest_path": str(manifest),
            "host_path": str(host), "extension_path": str(extension) if extension else None,
            "error": error}


def setup_firefox() -> dict:
    source = extension_path()
    manifest, host = _manifest_path(), _host_path()
    _check_existing(manifest, host)
    for name in _ASSETS:
        if not (source.parent / name).is_file():
            raise RuntimeError(f"The Firefox extension is incomplete: {name}")
    _private_directory(host.parent)
    _private_directory(manifest.parent)
    package = host.parent / "mx-creative-control-firefox.zip"
    if package.is_symlink():
        raise RuntimeError("Refusing to replace a symbolic-link extension package.")
    _write(host, _host_content().encode("utf-8"), 0o700)
    _write(manifest, (json.dumps(_manifest(host), indent=2) + "\n").encode("utf-8"), 0o600)
    # Only the known extension assets are packaged, never tests or local files.
    from io import BytesIO
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in _ASSETS:
            archive.writestr(name, (source.parent / name).read_bytes())
    _write(package, output.getvalue(), 0o600)
    return {**installation_status(), "package_path": str(package),
            "instructions": "In Firefox, open about:debugging#/runtime/this-firefox, choose Load Temporary Add-on, and select the extension manifest or ZIP. Allow the connection prompt if shown. Temporary installation lasts until Firefox restarts; permanent installation requires a Mozilla-signed extension."}
