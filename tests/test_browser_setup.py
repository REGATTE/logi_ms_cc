"""Firefox setup tests write only temporary directories, never the real profile."""

import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
import zipfile

import pytest

from mxcc import browser_setup as setup


@pytest.fixture
def locations(monkeypatch, tmp_path):
    manifest = tmp_path / "mozilla/native-messaging-hosts" / f"{setup.HOST_NAME}.json"
    monkeypatch.setattr(setup, "_manifest_path", lambda: manifest)
    monkeypatch.setattr(setup, "config_dir", lambda: tmp_path / "config")
    return manifest, setup._host_path()


def test_registration_is_idempotent_private_and_packages_only_extension_assets(locations):
    manifest, host = locations
    assert setup.installation_status()["installed"] is False
    assert not manifest.parent.exists() and not host.parent.exists()
    first = setup.setup_firefox()
    assert first["installed"] and setup.setup_firefox()["installed"]
    data = json.loads(manifest.read_text())
    assert data == {"name": setup.HOST_NAME, "description": "MX Creative Control website shortcuts",
                    "path": str(host), "type": "stdio", "allowed_extensions": [setup.EXTENSION_ID]}
    assert stat.S_IMODE(host.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600
    assert stat.S_IMODE(host.parent.stat().st_mode) == 0o700
    with zipfile.ZipFile(first["package_path"]) as archive:
        assert set(archive.namelist()) == set(setup._ASSETS)
        extension = json.loads(archive.read("manifest.json"))
    assert set(extension["permissions"]) == {"tabs", "nativeMessaging"}
    assert "content_scripts" not in extension
    assert extension["browser_specific_settings"]["gecko"]["id"] == setup.EXTENSION_ID


def test_generated_launcher_preserves_spaces_and_ignores_python_environment(locations, monkeypatch, tmp_path):
    _, host = locations
    fake = tmp_path / "python with spaces"
    received = tmp_path / "arguments.json"
    fake.write_text("#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\n"
                    f"Path({str(received)!r}).write_text(json.dumps(sys.argv[1:]))\n")
    fake.chmod(0o700)
    monkeypatch.setattr(setup.sys, "executable", str(fake))
    setup.setup_firefox()
    subprocess.run([str(host), "/a path/manifest.json", setup.EXTENSION_ID], check=True)
    arguments = json.loads(received.read_text())
    assert arguments[:3] == ["-I", "-u", "-c"]
    assert arguments[-2:] == ["/a path/manifest.json", setup.EXTENSION_ID]


@pytest.mark.parametrize("kind", ["host", "manifest", "malformed_manifest"])
def test_unrelated_existing_files_are_preserved(locations, kind):
    manifest, host = locations
    path = host if kind == "host" else manifest
    path.parent.mkdir(parents=True)
    content = "unrelated" if kind != "manifest" else json.dumps({"name": "other"})
    path.write_text(content)
    with pytest.raises(RuntimeError):
        setup.setup_firefox()
    assert path.read_text() == content
    assert setup.installation_status()["installed"] is False
    assert setup.installation_status()["error"]


@pytest.mark.parametrize("target", ["host", "manifest"])
def test_symlink_target_is_not_followed(locations, tmp_path, target):
    manifest, host = locations
    path = host if target == "host" else manifest
    original = tmp_path / "protected"
    original.write_text("keep")
    path.parent.mkdir(parents=True)
    path.symlink_to(original)
    with pytest.raises(RuntimeError):
        setup.setup_firefox()
    assert original.read_text() == "keep" and path.is_symlink()


def test_host_directory_symlink_is_rejected(locations, tmp_path):
    _, host = locations
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    host.parent.parent.mkdir(parents=True)
    host.parent.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(RuntimeError):
        setup.setup_firefox()
    assert not list(elsewhere.iterdir())


def test_incomplete_extension_does_not_register_host(locations, monkeypatch, tmp_path):
    manifest, host = locations
    source = tmp_path / "extension/manifest.json"
    source.parent.mkdir()
    source.write_text("{}")
    monkeypatch.setattr(setup, "extension_path", lambda: source)
    with pytest.raises(RuntimeError, match="incomplete"):
        setup.setup_firefox()
    assert not host.exists() and not manifest.exists()


def test_real_registered_host_round_trips_native_frames_to_isolated_worker_socket(locations):
    manifest, host = locations
    setup.setup_firefox()
    # A short private runtime path avoids the Unix socket pathname length limit.
    with tempfile.TemporaryDirectory(prefix="mxcc-bridge-") as temporary:
        directory = Path(temporary) / "mx-creative-control"
        directory.mkdir(mode=0o700)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(directory / "control.sock"))
            server.listen(2)
            server.settimeout(3)

            def answer():
                messages = []
                for _ in range(2):
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(3)
                        message = bytearray()
                        while b"\n" not in message:
                            message.extend(connection.recv(4096))
                        messages.append(json.loads(message))
                        connection.sendall(b'{"ok":true}\n')
                return messages

            payload = json.dumps({"domain": "example.com", "focused": True, "window_id": 7}).encode()
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(answer)
                result = subprocess.run([str(host), str(manifest), setup.EXTENSION_ID],
                    input=struct.pack("=I", len(payload)) + payload,
                    capture_output=True, timeout=4,
                    env={**os.environ, "XDG_RUNTIME_DIR": temporary})
                messages = pending.result(timeout=4)
            assert result.returncode == 0 and result.stderr == b""
            assert json.loads(result.stdout[4:]) == {"ok": True}
            assert messages[0]["command"] == "browser_context"
            assert messages[0]["domain"] == "example.com" and messages[0]["focused"] is True
            assert messages[0]["window_id"] == 7
            assert messages[1]["command"] == "browser_disconnect"
            assert set(messages[1]) == {"command", "browser", "session"}
            assert messages[0]["session"] == messages[1]["session"]
