"""User-service lifecycle. Never elevate the UI or the hardware worker."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from . import __version__, ipc
from .paths import cache_dir, config_dir, runtime_path, socket_path

SERVICE = "mx-creative-control.service"
DEV_SERVICE = "mx-creative-control-dev.service"
USER_UNIT = Path("/usr/lib/systemd/user") / SERVICE
REQUIRED_CAPABILITY = "device_actions_v1"
# Only session/display locations needed by the worker and its settings launcher
# cross into the transient service. Never copy the caller's entire environment.
SESSION_ENVIRONMENT = (
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_DATA_DIRS",
    "XDG_RUNTIME_DIR", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "XDG_SESSION_DESKTOP",
)


def worker_binary() -> Path:
    override = os.environ.get("MXCC_WORKER")
    candidates = [Path(override)] if override else []
    root = Path(__file__).resolve().parents[2]
    candidates += [Path("/usr/lib/mx-creative-control/mxcc-worker"),
                   root / "worker/target/release/mxcc-worker",
                   root / "worker/target/debug/mxcc-worker"]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise RuntimeError("The keypad worker is missing. Install the package or build worker/ first.")


def gui_command() -> str:
    source = Path(__file__).resolve().parents[1]
    if source.name == "src":
        runner = source.parent / "scripts/run-dev.sh"
        if runner.is_file() and os.access(runner, os.X_OK):
            return str(runner)
    installed = shutil.which("mx-creative-control")
    if installed:
        return installed
    # The local wrapper is created once, not kept as a background Python process.
    path = config_dir() / "open-settings"
    path.parent.mkdir(parents=True, exist_ok=True)
    import shlex
    content = ("#!/bin/sh\n" + "PYTHONPATH=" + shlex.quote(str(source)) + " exec "
               + shlex.quote(sys.executable) + ' -m mxcc "$@"\n')
    path.write_text(content)
    path.chmod(0o700)
    return str(path)


def agent_arguments() -> list[str]:
    return [str(worker_binary()), "--config", str(runtime_path()), "--cache", str(cache_dir()),
            "--socket", str(socket_path()), "--gui-command", gui_command()]


def _user_manager_available() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", "--property=Version", "--value"],
            capture_output=True, text=True, timeout=3)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_managed(command: list[str], unit: str) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Could not start {unit}: {error}") from error
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Could not start {unit}.")


def _start_managed_worker() -> str | None:
    """Start under the user manager, or return None when no manager is available."""
    if not _user_manager_available():
        return None
    if USER_UNIT.is_file():
        _run_managed(["systemctl", "--user", "start", SERVICE], SERVICE)
        return SERVICE

    # A transient worker may already be restarting while its socket is absent.
    # Starting that unit again is idempotent and avoids creating a second worker.
    try:
        existing = subprocess.run(
            ["systemctl", "--user", "show", DEV_SERVICE, "--property=LoadState", "--value"],
            capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Could not inspect {DEV_SERVICE}: {error}") from error
    if existing.stdout.strip() == "loaded":
        _run_managed(["systemctl", "--user", "start", DEV_SERVICE], DEV_SERVICE)
    else:
        command = [
            "systemd-run", "--user", "--unit=mx-creative-control-dev", "--collect",
            "--service-type=exec", "--property=Restart=on-failure", "--property=RestartSec=2",
            "--expand-environment=no",
        ]
        command.extend(f"--setenv={name}={os.environ[name]}"
                       for name in SESSION_ENVIRONMENT if name in os.environ)
        command.extend(["--", *agent_arguments()])
        _run_managed(command, DEV_SERVICE)
    return DEV_SERVICE


def _start_direct_worker():
    """Compatibility fallback for desktops without a systemd user manager."""
    log_path = config_dir() / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and log_path.stat().st_size > 1_000_000:
        log_path.replace(log_path.with_suffix(".previous.log"))
    with log_path.open("ab") as log:
        process = subprocess.Popen(agent_arguments(), stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=log, close_fds=True, start_new_session=True)
    return process, f"See {log_path}."


def _compatible(status: dict) -> bool:
    capabilities = status.get("capabilities")
    return status.get("ok") is True and isinstance(capabilities, list) and REQUIRED_CAPABILITY in capabilities


def _upgrade_worker() -> None:
    """Verify the replacement before asking the old worker to exit normally."""
    try:
        result = subprocess.run([str(worker_binary()), "--version"],
                                capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Could not verify the replacement worker: {error}") from error
    if result.returncode or result.stdout.strip() != __version__:
        raise RuntimeError(f"Install MX Creative Control {__version__} or rebuild its worker before upgrading the running service.")
    try:
        response = ipc.request("shutdown", timeout=2)
    except ipc.WorkerUnavailable as error:
        raise RuntimeError(f"Could not stop the old worker for upgrade: {error}") from error
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "The old worker refused to stop for upgrade."))
    for _ in range(30):
        if not socket_path().exists():
            return
        time.sleep(0.1)
    raise RuntimeError("The old worker did not stop. Quit it from the panel icon, then reopen settings.")


def ensure_running() -> dict:
    running = None
    try:
        running = ipc.request("status")
    except ipc.WorkerUnavailable:
        pass
    if running is not None:
        if _compatible(running):
            return running
        if not running.get("ok"):
            raise RuntimeError(running.get("error", "Could not read worker status."))
    if not runtime_path().exists():
        raise RuntimeError("Open settings once to create your keypad layout.")
    if running is not None:
        _upgrade_worker()
    unit = _start_managed_worker()
    if unit:
        process = None
        diagnostic = f"See journalctl --user -u {unit} -n 60."
    else:
        process, diagnostic = _start_direct_worker()
    for _ in range(30):
        try:
            status = ipc.request("status", timeout=0.15)
        except ipc.WorkerUnavailable:
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"The worker exited. {diagnostic}")
            time.sleep(0.1)
            continue
        if not _compatible(status):
            raise RuntimeError(f"The started worker is incompatible. Install MX Creative Control {__version__} or rebuild its worker. {diagnostic}")
        return status
    raise RuntimeError(f"The worker did not become ready. {diagnostic}")


def set_autostart(enabled: bool) -> None:
    if not USER_UNIT.exists():
        raise RuntimeError("Install the Ubuntu package to enable automatic startup.")
    command = ["systemctl", "--user", "enable" if enabled else "disable", SERVICE]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not update startup settings.")


def autostart_enabled() -> bool:
    try:
        result = subprocess.run(["systemctl", "--user", "is-enabled", SERVICE],
                                capture_output=True, timeout=3)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
