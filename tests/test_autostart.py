"""Login setup uses temporary files and fake services, never this PC's session."""

import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mxcc import service


def result(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.fixture
def login(monkeypatch, tmp_path):
    configuration = tmp_path / "config/mx-creative-control"
    configuration.mkdir(parents=True)
    runtime = configuration / "runtime.json"
    runtime.write_text("{}")
    launcher = tmp_path / "app checkout/run-dev.sh"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o700)
    worker = tmp_path / "worker"
    worker.write_text("#!/bin/sh\n")
    worker.chmod(0o700)
    unit = tmp_path / service.SERVICE
    monkeypatch.setattr(service, "USER_UNIT", unit)
    monkeypatch.setattr(service, "config_dir", lambda: configuration)
    monkeypatch.setattr(service, "runtime_path", lambda: runtime)
    monkeypatch.setattr(service, "gui_command", lambda: str(launcher))
    monkeypatch.setattr(service, "worker_binary", lambda: worker)
    run = Mock(return_value=result("disabled", code=1))
    monkeypatch.setattr(service.subprocess, "run", run)
    monkeypatch.setattr(service.subprocess, "Popen", Mock(side_effect=AssertionError("No process during setup")))
    monkeypatch.setattr(service.ipc, "request", Mock(side_effect=AssertionError("No IPC during setup")))
    return SimpleNamespace(path=configuration.parent / "autostart/mx-creative-control.desktop",
                           runtime=runtime, launcher=launcher, worker=worker, unit=unit, run=run)


@pytest.mark.parametrize("installed", [False, True])
def test_enable_is_private_idempotent_and_disable_preserves_running_worker(login, installed, monkeypatch):
    if installed:
        login.unit.touch()
    monkeypatch.setenv("DISPLAY", ":91")
    monkeypatch.setenv("MXCC_SECRET", "do-not-save")
    service.set_autostart(True)
    content = login.path.read_text()
    assert f'Exec="{login.launcher}" startup\n' in content
    assert "X-MXCC-Autostart=true" in content
    assert ":91" not in content and "do-not-save" not in content
    assert stat.S_IMODE(login.path.stat().st_mode) == 0o600
    assert service.autostart_enabled()
    original = login.path.stat()
    service.set_autostart(True)
    assert login.path.stat().st_ino == original.st_ino
    assert login.path.stat().st_mtime_ns == original.st_mtime_ns
    service.set_autostart(False)
    service.set_autostart(False)
    assert not login.path.exists()
    assert not service.autostart_enabled()
    assert all(item.args[0] == ["systemctl", "--user", "is-enabled", service.SERVICE]
               for item in login.run.call_args_list)


@pytest.mark.parametrize("enabled", [True, False])
def test_migrates_enabled_legacy_unit_without_stopping_it(login, enabled):
    login.run.side_effect = [result("enabled\n"), result()]
    service.set_autostart(enabled)
    assert login.path.exists() is enabled
    assert [item.args[0] for item in login.run.call_args_list] == [
        ["systemctl", "--user", "is-enabled", service.SERVICE],
        ["systemctl", "--user", "disable", service.SERVICE],
    ]


@pytest.mark.parametrize("previous", [None, b"[Desktop Entry]\nX-MXCC-Autostart=true\nHidden=true\n"])
@pytest.mark.parametrize("enabled", [True, False])
def test_failed_legacy_migration_restores_previous_file(login, previous, enabled):
    if previous is not None:
        login.path.parent.mkdir()
        login.path.write_bytes(previous)
    login.run.side_effect = [result("enabled"), result(stderr="permission denied", code=1)]
    with pytest.raises(RuntimeError, match="permission denied"):
        service.set_autostart(enabled)
    assert (login.path.read_bytes() if login.path.exists() else None) == previous
    assert not list(login.path.parent.glob(".mxcc-startup-*"))


def test_missing_systemd_still_supports_graphical_autostart(login):
    login.run.side_effect = FileNotFoundError("systemctl")
    service.set_autostart(True)
    assert service.autostart_enabled()
    service.set_autostart(False)
    assert not service.autostart_enabled()


def test_atomic_write_failure_keeps_existing_file_and_legacy_setting(login, monkeypatch):
    login.path.parent.mkdir()
    previous = b"[Desktop Entry]\nX-MXCC-Autostart=true\nHidden=true\n"
    login.path.write_bytes(previous)
    monkeypatch.setattr(service.os, "replace", Mock(side_effect=PermissionError("read-only destination")))
    with pytest.raises(PermissionError, match="read-only destination"):
        service.set_autostart(True)
    assert login.path.read_bytes() == previous
    assert not list(login.path.parent.glob(".mxcc-startup-*"))
    login.run.assert_not_called()


@pytest.mark.parametrize("failure", ["runtime", "worker", "launcher"])
def test_missing_prerequisite_leaves_login_unchanged(login, failure):
    getattr(login, failure).unlink()
    with pytest.raises(RuntimeError):
        service.set_autostart(True)
    assert not login.path.exists()
    login.run.assert_not_called()


@pytest.mark.parametrize("name", ["line\nbreak", "carriage\rreturn", "equal=sign", "tab\tstop"])
def test_invalid_launcher_path_is_rejected_before_writing(login, monkeypatch, name):
    monkeypatch.setattr(service, "gui_command", lambda: str(login.launcher.parent / name))
    with pytest.raises(RuntimeError, match="launcher path"):
        service.set_autostart(True)
    assert not login.path.exists()
    login.run.assert_not_called()


def test_exec_quotes_field_codes_and_shell_characters_without_shell_execution(login, monkeypatch):
    launcher = login.launcher.with_name('space %f dollar$ quote" backtick` back\\slash')
    login.launcher.rename(launcher)
    monkeypatch.setattr(service, "gui_command", lambda: str(launcher))
    service.set_autostart(True)
    content = login.path.read_text()
    assert 'Exec=/usr/bin/env -- "' in content
    assert 'space %%f dollar\\\\$ quote\\\\" backtick\\\\` back\\\\\\\\slash" startup\n' in content
    assert service.autostart_enabled()


@pytest.mark.parametrize("symlink", [False, True])
def test_never_overwrites_unrelated_entry_or_symlink(login, symlink):
    login.path.parent.mkdir()
    original = b"[Desktop Entry]\nType=Application\nExec=/another/app\n"
    if symlink:
        target = login.path.parent / "another.desktop"
        target.write_bytes(original)
        login.path.symlink_to(target)
    else:
        login.path.write_bytes(original)
    with pytest.raises(RuntimeError):
        service.set_autostart(True)
    assert login.path.read_bytes() == original
    login.run.assert_not_called()


@pytest.mark.parametrize("override", ["Hidden=true", "X-GNOME-Autostart-enabled=false", "TryExec=/missing/app"])
def test_reports_disabled_or_stale_entry(login, override):
    service.set_autostart(True)
    key = override.split("=", 1)[0]
    lines = [line for line in login.path.read_text().splitlines() if not line.startswith(key + "=")]
    login.path.write_text("\n".join([*lines, override, ""]))
    assert not service.autostart_enabled()


def test_reports_legacy_enabled_state_without_desktop_entry(login):
    login.run.return_value = result("enabled\n")
    assert service.autostart_enabled()


@pytest.mark.parametrize("installed", [False, True])
def test_login_bootstrap_imports_only_current_session_and_reuses_worker(login, installed, monkeypatch):
    for name in service.SESSION_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISPLAY", ":42")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/current/session/config")
    monkeypatch.setenv("MXCC_SECRET", "never-import")
    if installed:
        login.unit.touch()
    login.run.return_value = result("255")
    ready = {"ok": True, "capabilities": [service.REQUIRED_CAPABILITY]}
    service.ipc.request.side_effect = None
    service.ipc.request.return_value = ready
    assert service.start_at_login() == ready
    service.ipc.request.assert_called_once_with("status")
    if installed:
        assert [item.args[0] for item in login.run.call_args_list] == [
            ["systemctl", "--user", "show", "--property=Version", "--value"],
            ["systemctl", "--user", "import-environment", "DISPLAY", "XDG_CONFIG_HOME"],
        ]
    else:
        login.run.assert_not_called()
    assert not login.path.exists()


def test_failed_session_import_does_not_launch_worker_with_stale_environment(login, monkeypatch):
    login.unit.touch()
    monkeypatch.setenv("DISPLAY", ":42")
    login.run.side_effect = [result("255"), result(stderr="import failed", code=1)]
    with pytest.raises(RuntimeError, match="import failed"):
        service.start_at_login()
    service.ipc.request.assert_not_called()
