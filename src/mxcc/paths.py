"""Locations shared by the settings process and the background worker."""

from __future__ import annotations

import os
import stat
from pathlib import Path

APP_ID = "mx-creative-control"


def _xdg(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    # The XDG specification requires absolute paths; ignore relative values.
    return Path(value) if value and Path(value).is_absolute() else default


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / APP_ID


def cache_dir() -> Path:
    """Directory containing ready-to-upload key JPEGs (not the cache root)."""
    return _xdg("XDG_CACHE_HOME", Path.home() / ".cache") / APP_ID / "keys"


def profiles_dir() -> Path:
    return config_dir() / "shortcuts" / "config"


def runtime_path() -> Path:
    return config_dir() / "runtime.json"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def socket_path() -> Path:
    runtime = _xdg("XDG_RUNTIME_DIR", Path("/tmp") / f"mxcc-{os.getuid()}")
    return runtime / APP_ID / "control.sock"


def prepare_runtime_dir() -> Path:
    """Protect the predictable /tmp fallback and both GUI/worker socket paths."""
    directory = socket_path().parent
    for path in (directory.parent, directory):
        path.mkdir(parents=False, exist_ok=True, mode=0o700)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError(f"Runtime directory must be a real directory owned by you: {path}")
        path.chmod(0o700)
    return directory


def bundled_profiles_dir() -> Path:
    override = os.environ.get("MXCC_BUNDLED_PROFILES")
    if override:
        return Path(override).expanduser()
    source = Path(__file__).resolve().parents[2] / "shortcuts" / "config"
    if source.is_dir():
        return source
    return Path("/usr/share") / APP_ID / "shortcuts" / "config"
