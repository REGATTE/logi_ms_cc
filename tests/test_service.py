"""Lifecycle decisions with fake IPC and subprocesses; never touch host services."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from mxcc import __version__, ipc, service


def compatible_status(**values):
    return {"ok": True, "version": __version__, "capabilities": [service.REQUIRED_CAPABILITY], **values}


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
    monkeypatch.setattr(service, "socket_path", lambda: tmp_path / "control.sock")
    monkeypatch.setattr(service, "worker_binary", lambda: Path("/checkout/worker"))
    arguments = ["/checkout/worker", "--config", str(runtime), "--gui-command", "/checkout/run-dev.sh"]
    monkeypatch.setattr(service, "agent_arguments", Mock(return_value=arguments))
    request = Mock(side_effect=[ipc.WorkerUnavailable("not running"), compatible_status()])
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
    request.return_value = compatible_status(connected=True, version="99.0.0")
    assert service.ensure_running() == request.return_value
    run.assert_not_called()
    popen.assert_not_called()


def test_installed_unit_is_started_without_enabling_it(lifecycle):
    unit, _, _, run, popen = lifecycle
    unit.write_text("[Service]\n")
    run.side_effect = [result("255"), result()]
    assert service.ensure_running() == compatible_status()
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
    assert service.ensure_running() == compatible_status()
    command = run.call_args.args[0]
    assert command[:7] == [
        "systemd-run", "--user", "--unit=mx-creative-control-dev", "--collect",
        "--service-type=exec", "--property=Restart=on-failure", "--property=RestartSec=2",
    ]
    assert "--setenv=DISPLAY=:44" in command
    assert "--expand-environment=no" in command
    assert "--property=PartOf=graphical-session.target" in command
    assert "--setenv=XDG_CONFIG_HOME=/tmp/test configuration" in command
    assert all(item.split("=", 2)[1] in service.SESSION_ENVIRONMENT
               for item in command if item.startswith("--setenv="))
    assert not any("must-not-copy" in item or "MXCC_TEST_API_TOKEN" in item for item in command)
    assert command[command.index("--") + 1:] == arguments
    popen.assert_not_called()


def test_existing_transient_unit_is_reused_during_restart(lifecycle):
    _, _, _, run, popen = lifecycle
    run.side_effect = [result("255"), result("loaded"), result()]
    assert service.ensure_running() == compatible_status()
    assert run.call_args.args[0] == ["systemctl", "--user", "start", service.DEV_SERVICE]
    assert all(item.args[0][0] != "systemd-run" for item in run.call_args_list)
    popen.assert_not_called()


def test_collected_transient_unit_is_recreated_after_upgrade(lifecycle):
    _, arguments, _, run, popen = lifecycle
    run.side_effect = [result("255"), result("loaded"),
                       result(stderr="Unit not found", code=5), result("not-found", code=4), result()]
    assert service.ensure_running() == compatible_status()
    command = run.call_args.args[0]
    assert command[0] == "systemd-run"
    assert command[command.index("--") + 1:] == arguments
    popen.assert_not_called()


def test_failed_transient_unit_is_not_recreated_if_it_still_exists(lifecycle):
    _, _, _, run, popen = lifecycle
    run.side_effect = [result("255"), result("loaded"),
                       result(stderr="Service failed", code=1), result("loaded")]
    with pytest.raises(RuntimeError, match="Service failed"):
        service.ensure_running()
    assert not any(call.args[0][0] == "systemd-run" for call in run.call_args_list)
    popen.assert_not_called()


@pytest.mark.parametrize("failure", [OSError("manager unavailable"), subprocess.TimeoutExpired("systemctl", 3)])
def test_transient_recheck_failure_keeps_original_start_error(lifecycle, failure):
    _, _, _, run, popen = lifecycle
    run.side_effect = [result("255"), result("loaded"),
                       result(stderr="Service failed", code=1), failure]
    with pytest.raises(RuntimeError, match="Service failed"):
        service.ensure_running()
    assert not any(call.args[0][0] == "systemd-run" for call in run.call_args_list)
    popen.assert_not_called()


def test_successful_start_of_collected_upgrade_unit_recreates_once_when_not_ready(lifecycle):
    _, arguments, request, run, popen = lifecycle
    request.side_effect = [
        {"ok": True, "capabilities": ["website_profiles_v1"]}, {"ok": True},
        *[ipc.WorkerUnavailable("not ready")] * 30, compatible_status(),
    ]
    run.side_effect = [result(__version__), result("255"), result("loaded"), result(),
                       result("not-found", code=4), result("255"), result("not-found"), result()]
    assert service.ensure_running() == compatible_status()
    commands = [item.args[0] for item in run.call_args_list]
    assert commands.count(["systemctl", "--user", "start", service.DEV_SERVICE]) == 1
    creations = [command for command in commands if command[0] == "systemd-run"]
    assert len(creations) == 1
    assert creations[0][creations[0].index("--") + 1:] == arguments
    assert service.time.sleep.call_count == 30
    popen.assert_not_called()


def test_collected_unit_retry_reuses_concurrently_recreated_unit(lifecycle):
    _, _, request, run, popen = lifecycle
    request.side_effect = [*[ipc.WorkerUnavailable("not ready")] * 31, compatible_status()]
    run.side_effect = [result("255"), result("loaded"), result(), result("not-found", code=4),
                       result("255"), result("loaded"), result()]
    assert service.ensure_running() == compatible_status()
    commands = [item.args[0] for item in run.call_args_list]
    assert commands.count(["systemctl", "--user", "start", service.DEV_SERVICE]) == 2
    assert not any(command[0] == "systemd-run" for command in commands)
    popen.assert_not_called()


@pytest.mark.parametrize("inspection", [result("loaded"), result("masked"), result("not-found", code=1)])
def test_readiness_timeout_does_not_recreate_without_confirmed_absent_unit(lifecycle, inspection):
    _, _, request, run, popen = lifecycle
    request.side_effect = ipc.WorkerUnavailable("not ready")
    run.side_effect = [result("255"), result("loaded"), result(), inspection]
    with pytest.raises(RuntimeError, match="did not become ready"):
        service.ensure_running()
    assert request.call_count == 31
    assert not any(item.args[0][0] == "systemd-run" for item in run.call_args_list)
    popen.assert_not_called()


@pytest.mark.parametrize("failure", [OSError("manager unavailable"), subprocess.TimeoutExpired("systemctl", 3)])
def test_readiness_recheck_errors_are_reported_without_second_launch(lifecycle, failure):
    _, _, request, run, popen = lifecycle
    request.side_effect = ipc.WorkerUnavailable("not ready")
    run.side_effect = [result("255"), result("loaded"), result(), failure]
    with pytest.raises(RuntimeError, match="did not become ready; could not inspect"):
        service.ensure_running()
    assert run.call_count == 4
    popen.assert_not_called()


def test_recreated_transient_worker_start_failure_is_reported(lifecycle):
    _, _, request, run, popen = lifecycle
    request.side_effect = ipc.WorkerUnavailable("not ready")
    run.side_effect = [result("255"), result("loaded"), result(), result("not-found", code=4),
                       result("255"), result("not-found"), result(stderr="Replacement start failed", code=1)]
    with pytest.raises(RuntimeError, match="Replacement start failed"):
        service.ensure_running()
    assert request.call_count == 31
    popen.assert_not_called()


def test_recreated_transient_worker_gets_only_one_additional_readiness_wait(lifecycle):
    _, _, request, run, popen = lifecycle
    request.side_effect = ipc.WorkerUnavailable("not ready")
    run.side_effect = [result("255"), result("loaded"), result(), result("not-found", code=4),
                       result("255"), result("not-found"), result()]
    with pytest.raises(RuntimeError, match="did not become ready"):
        service.ensure_running()
    assert request.call_count == 61
    assert run.call_count == 7
    assert service.time.sleep.call_count == 60
    popen.assert_not_called()


def test_user_manager_disappearing_during_retry_does_not_launch_direct_worker(lifecycle):
    _, _, request, run, popen = lifecycle
    request.side_effect = ipc.WorkerUnavailable("not ready")
    run.side_effect = [result("255"), result("loaded"), result(), result("not-found", code=4),
                       result(stderr="Manager unavailable", code=1)]
    with pytest.raises(RuntimeError, match="did not become ready"):
        service.ensure_running()
    assert request.call_count == 31
    popen.assert_not_called()


@pytest.mark.parametrize("managed", [True, False])
def test_nontransient_readiness_timeout_never_tries_another_launch(lifecycle, managed):
    unit, _, request, run, popen = lifecycle
    request.side_effect = ipc.WorkerUnavailable("not ready")
    if managed:
        unit.write_text("[Service]\n")
        run.side_effect = [result("255"), result()]
    else:
        run.side_effect = [result(stderr="Manager unavailable", code=1)]
    with pytest.raises(RuntimeError, match="did not become ready"):
        service.ensure_running()
    assert request.call_count == 31
    assert run.call_count == (2 if managed else 1)
    assert popen.call_count == (0 if managed else 1)


def test_unavailable_user_manager_falls_back_to_detached_process(lifecycle):
    _, arguments, _, run, popen = lifecycle
    run.return_value = result(stderr="Failed to connect to bus", code=1)
    assert service.ensure_running() == compatible_status()
    assert popen.call_args.args[0] == arguments
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert Path(popen.call_args.kwargs["stdout"].name).name == "worker.log"
    assert run.call_count == 1


def test_missing_systemctl_also_uses_direct_fallback(lifecycle):
    _, _, _, run, popen = lifecycle
    run.side_effect = FileNotFoundError("systemctl")
    assert service.ensure_running() == compatible_status()
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


@pytest.mark.parametrize("old_capability", ["device_actions_v1", "website_profiles_v1", "dialpad_dial_v1"])
def test_old_worker_is_verified_stopped_and_restarted_with_installed_unit(lifecycle, old_capability):
    unit, _, request, run, popen = lifecycle
    unit.write_text('[Service]\n')
    service.socket_path().touch()
    old = {'ok': True, 'pid': 41, 'capabilities': [old_capability]}
    current = compatible_status(pid=42)
    order = []
    responses = iter([old, {'ok': True}, current])

    def answer(command, **kwargs):
        order.append(command)
        if command == 'shutdown':
            service.socket_path().unlink()
        return next(responses)

    process_results = iter([result(__version__), result('255'), result()])
    def execute(command, **kwargs):
        order.append(command[-1])
        return next(process_results)

    request.side_effect = answer
    run.side_effect = execute
    assert service.ensure_running() == current
    assert order == ['status', '--version', 'shutdown', '--value', service.SERVICE, 'status']
    assert request.call_args_list == [call('status'), call('shutdown', timeout=2), call('status', timeout=0.15)]
    assert run.call_args_list[0] == call(['/checkout/worker', '--version'], capture_output=True, text=True, timeout=3)
    assert run.call_args.args[0] == ['systemctl', '--user', 'start', service.SERVICE]
    popen.assert_not_called()


def test_outdated_replacement_binary_leaves_existing_worker_running(lifecycle):
    _, _, request, run, popen = lifecycle
    request.side_effect = None
    request.return_value = {'ok': True, 'version': '0.1.3'}
    run.return_value = result('0.1.3\n')
    with pytest.raises(RuntimeError, match='rebuild its worker before upgrading'):
        service.ensure_running()
    request.assert_called_once_with('status')
    run.assert_called_once_with(['/checkout/worker', '--version'], capture_output=True, text=True, timeout=3)
    popen.assert_not_called()


@pytest.mark.parametrize('failure', [FileNotFoundError('missing worker'), subprocess.TimeoutExpired('worker', 3)])
def test_unverifiable_replacement_does_not_stop_running_worker(lifecycle, failure):
    _, _, request, run, popen = lifecycle
    request.side_effect = None
    request.return_value = {'ok': True}
    run.side_effect = failure
    with pytest.raises(RuntimeError, match='Could not verify the replacement worker'):
        service.ensure_running()
    request.assert_called_once_with('status')
    popen.assert_not_called()


@pytest.mark.parametrize('failure', [{'ok': False, 'error': 'refused shutdown'}, ipc.WorkerUnavailable('shutdown connection failed')])
def test_failed_upgrade_shutdown_never_starts_a_second_worker(lifecycle, failure):
    _, _, request, run, popen = lifecycle
    request.side_effect = [{'ok': True}, failure]
    run.return_value = result(__version__)
    with pytest.raises(RuntimeError, match='shutdown'):
        service.ensure_running()
    assert request.call_args_list == [call('status'), call('shutdown', timeout=2)]
    run.assert_called_once_with(['/checkout/worker', '--version'], capture_output=True, text=True, timeout=3)
    popen.assert_not_called()


def test_upgrade_waits_for_socket_exit_and_reports_a_worker_that_wont_stop(lifecycle):
    _, _, request, run, popen = lifecycle
    service.socket_path().touch()
    request.side_effect = [{'ok': True}, {'ok': True}]
    run.return_value = result(__version__)
    with pytest.raises(RuntimeError, match='old worker did not stop'):
        service.ensure_running()
    assert service.time.sleep.call_count == 30
    assert request.call_count == 2
    assert run.call_count == 1
    popen.assert_not_called()


def test_newly_started_worker_must_advertise_required_capability(lifecycle):
    unit, _, request, run, popen = lifecycle
    unit.write_text('[Service]\n')
    request.side_effect = [ipc.WorkerUnavailable('not running'), {'ok': True, 'version': '0.1.3'}]
    run.side_effect = [result('255'), result()]
    with pytest.raises(RuntimeError, match='started worker is incompatible'):
        service.ensure_running()
    assert request.call_args_list == [call('status'), call('status', timeout=0.15)]
    popen.assert_not_called()
