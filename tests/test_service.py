"""Lifecycle decisions with fake IPC and subprocesses; never touch host services."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from mxcc import ipc, service


def result(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.fixture
def lifecycle(monkeypatch, tmp_path):
    configuration = tmp_path / "config"
    configuration.mkdir()
    runtime = configuration / "runtime.json"
    runtime.write_text("{}")
    unit = tmp_path / service.SERVICE
    monkeypatch.setattr(service, "USER_UNIT", unit)
    monkeypatch.setattr(service, "runtime_path", lambda: runtime)
    monkeypatch.setattr(service, "config_dir", lambda: configuration)
    arguments = ["/checkout/worker", "--config", str(runtime), "--gui-command", "/checkout/run-dev.sh"]
    monkeypatch.setattr(service, "agent_arguments", Mock(return_value=arguments))
    request = Mock(side_effect=[ipc.WorkerUnavailable("not running"), {"ok": True}])
    monkeypatch.setattr(service.ipc, "request", request)
    run = Mock()
    popen = Mock(return_value=Mock(poll=Mock(return_value=None)))
    monkeypatch.setattr(service.subprocess, "run", run)
    monkeypatch.setattr(service.subprocess, "Popen", popen)
    monkeypatch.setattr(service.time, "sleep", Mock())
    return unit, arguments, request, run, popen


def test_source_gui_uses_checkout_runner_before_installed_command(monkeypatch, tmp_path):
    source = tmp_path / "project/src/mxcc/service.py"
    runner = tmp_path / "project/scripts/run-dev.sh"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/sh\n")
    runner.chmod(0o755)
    monkeypatch.setattr(service, "__file__", str(source))
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/mx-creative-control")
    assert service.gui_command() == str(runner)


def test_packaged_gui_keeps_installed_command(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "__file__", str(tmp_path / "private/python/mxcc/service.py"))
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/mx-creative-control")
    assert service.gui_command() == "/usr/bin/mx-creative-control"


def test_existing_worker_needs_no_process_or_service(lifecycle):
    _, _, request, run, popen = lifecycle
    request.side_effect = None
    request.return_value = {"ok": True, "connected": True}
    assert service.ensure_running() == request.return_value
    run.assert_not_called()
    popen.assert_not_called()


def test_installed_unit_is_started_without_enabling_it(lifecycle):
    unit, _, _, run, popen = lifecycle
    unit.write_text("[Service]\n")
    run.side_effect = [result("255"), result()]
    assert service.ensure_running() == {"ok": True}
    assert [item.args[0] for item in run.call_args_list] == [
        ["systemctl", "--user", "show", "--property=Version", "--value"],
        ["systemctl", "--user", "start", service.SERVICE],
    ]
    popen.assert_not_called()


def test_source_transient_unit_has_restart_policy_and_only_session_environment(lifecycle, monkeypatch):
    _, arguments, _, run, popen = lifecycle
    monkeypatch.setenv("DISPLAY", ":44")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test configuration")
    monkeypatch.setenv("MXCC_TEST_API_TOKEN", "must-not-copy")
    run.side_effect = [result("255"), result("not-found"), result()]
    assert service.ensure_running() == {"ok": True}
    command = run.call_args.args[0]
    assert command[:7] == [
        "systemd-run", "--user", "--unit=mx-creative-control-dev", "--collect",
        "--service-type=exec", "--property=Restart=on-failure", "--property=RestartSec=2",
    ]
    assert "--setenv=DISPLAY=:44" in command
    assert "--expand-environment=no" in command
    assert "--setenv=XDG_CONFIG_HOME=/tmp/test configuration" in command
    assert all(item.split("=", 2)[1] in service.SESSION_ENVIRONMENT
               for item in command if item.startswith("--setenv="))
    assert not any("must-not-copy" in item or "MXCC_TEST_API_TOKEN" in item for item in command)
    assert command[command.index("--") + 1:] == arguments
    popen.assert_not_called()


def test_existing_transient_unit_is_reused_during_restart(lifecycle):
    _, _, _, run, popen = lifecycle
    run.side_effect = [result("255"), result("loaded"), result()]
    assert service.ensure_running() == {"ok": True}
    assert run.call_args.args[0] == ["systemctl", "--user", "start", service.DEV_SERVICE]
    assert all(item.args[0][0] != "systemd-run" for item in run.call_args_list)
    popen.assert_not_called()


def test_unavailable_user_manager_falls_back_to_detached_process(lifecycle):
    _, arguments, _, run, popen = lifecycle
    run.return_value = result(stderr="Failed to connect to bus", code=1)
    assert service.ensure_running() == {"ok": True}
    assert popen.call_args.args[0] == arguments
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert Path(popen.call_args.kwargs["stdout"].name).name == "worker.log"
    assert run.call_count == 1


def test_missing_systemctl_also_uses_direct_fallback(lifecycle):
    _, _, _, run, popen = lifecycle
    run.side_effect = FileNotFoundError("systemctl")
    assert service.ensure_running() == {"ok": True}
    popen.assert_called_once()


def test_real_unit_failure_is_reported_without_a_second_worker(lifecycle):
    unit, _, _, run, popen = lifecycle
    unit.write_text("[Service]\n")
    run.side_effect = [result("255"), result(stderr="Unit start failed", code=1)]
    with pytest.raises(RuntimeError, match="Unit start failed"):
        service.ensure_running()
    popen.assert_not_called()


def test_missing_configuration_does_not_start_any_worker(lifecycle):
    _, arguments, _, run, popen = lifecycle
    Path(arguments[2]).unlink()
    with pytest.raises(RuntimeError, match="Open settings once"):
        service.ensure_running()
    run.assert_not_called()
    popen.assert_not_called()
