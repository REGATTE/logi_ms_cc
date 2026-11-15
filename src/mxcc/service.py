"""User-service lifecycle. Never elevate the UI or the hardware worker."""

import configparser
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
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
            "--property=PartOf=graphical-session.target",
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


def _autostart_path() -> Path:
    return config_dir().parent / "autostart" / "mx-creative-control.desktop"


def _legacy_autostart_enabled() -> bool:
    try:
        result = subprocess.run(["systemctl", "--user", "is-enabled", SERVICE],
                                capture_output=True, text=True, timeout=3)
        return result.returncode == 0 and result.stdout.strip() in ("enabled", "enabled-runtime")
    except (OSError, subprocess.TimeoutExpired):
        return False


def _desktop_entry(content: bytes) -> dict:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(content.decode("utf-8"))
        return dict(parser["Desktop Entry"])
    except (UnicodeError, configparser.Error, KeyError):
        return {}


def _startup_content(command: str) -> bytes:
    # Exec has its own quoting after desktop-string decoding, not shell quoting.
    if not Path(command).is_absolute() or "=" in command or any(ord(c) < 32 or ord(c) == 127 for c in command):
        raise RuntimeError("The app's launcher path cannot be used for automatic startup.")
    quoted = "".join("\\" + c if c in '\\"`$' else c for c in command.replace("%", "%%"))
    quoted = '"' + quoted.replace("\\", "\\\\") + '"'
    executable = command.replace("\\", "\\\\").replace(" ", "\\s")
    # GLib checks the first executable before expanding %% into a literal %.
    # env avoids that check for unusual checkout paths without invoking a shell.
    prefix = "/usr/bin/env -- " if "%" in command else ""
    return ("[Desktop Entry]\nType=Application\nName=MX Creative Control\n"
            "Comment=Start device controls and the panel icon when signing in\n"
            f"Exec={prefix}{quoted} startup\nTryExec={executable}\n"
            "Icon=mx-creative-control\nTerminal=false\nStartupNotify=false\n"
            "X-GNOME-Autostart-enabled=true\nX-MXCC-Autostart=true\n").encode("utf-8")


def _write_startup(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".mxcc-startup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def set_autostart(enabled: bool) -> None:
    """Configure this user's next graphical login without installing or restarting."""
    path = _autostart_path()
    if path.is_symlink():
        raise RuntimeError(f"The startup entry must be a regular file: {path}")
    previous = path.read_bytes() if path.exists() else None
    if previous is not None and _desktop_entry(previous).get("X-MXCC-Autostart") != "true":
        raise RuntimeError(f"A different startup entry already exists at {path}. Rename it before enabling this app.")
    content = None
    if enabled:
        if not runtime_path().is_file():
            raise RuntimeError("Open settings and save a device layout before enabling automatic startup.")
        worker = worker_binary()
        if not worker.is_file() or not os.access(worker, os.X_OK):
            raise RuntimeError("Build or install the device worker before enabling automatic startup.")
        command = str(Path(gui_command()).absolute())
        content = _startup_content(command)
        if not Path(command).is_file() or not os.access(command, os.X_OK):
            raise RuntimeError("The app launcher is missing or cannot be executed.")
    changed = content != previous
    if changed:
        _write_startup(path, content)
    try:
        # Replace the old login trigger without stopping the current worker.
        if _legacy_autostart_enabled():
            result = subprocess.run(["systemctl", "--user", "disable", SERVICE],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Could not replace the previous login startup setting.")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        try:
            if changed:
                _write_startup(path, previous)
        except OSError as restore_error:
            raise RuntimeError(f"Startup setup failed ({error}); the previous entry could not be restored: {restore_error}") from error
        raise RuntimeError(f"Could not update automatic startup: {error}") from error


def autostart_enabled() -> bool:
    """Report effective app-managed or legacy startup, rather than a stale preference."""
    path = _autostart_path()
    try:
        entry = _desktop_entry(path.read_bytes())
    except OSError:
        entry = {}
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    launcher = Path(re.sub(r"\\([sntr\\])", lambda match: escapes[match[1]], entry.get("TryExec", "")))
    managed = (entry.get("X-MXCC-Autostart") == "true"
               and entry.get("Type") == "Application"
               and entry.get("Exec", "").endswith(" startup")
               and launcher.is_absolute() and launcher.is_file() and os.access(launcher, os.X_OK)
               and entry.get("Hidden", "false").lower() != "true"
               and entry.get("X-GNOME-Autostart-enabled", "true").lower() != "false")
    return managed or _legacy_autostart_enabled()


def start_at_login() -> dict:
    """Short-lived, Qt-free login bootstrap using the current desktop session."""
    if USER_UNIT.is_file() and _user_manager_available():
        names = [name for name in SESSION_ENVIRONMENT if name in os.environ]
        if names:
            _run_managed(["systemctl", "--user", "import-environment", *names], SERVICE)
    return ensure_running()
