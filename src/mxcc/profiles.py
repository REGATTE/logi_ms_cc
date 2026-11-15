"""Validate and import declarative shortcut profiles; never execute profile data."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from . import paths
from .shortcuts import normalize_shortcut

LOG = logging.getLogger(__name__)
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
DESKTOP_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+ -]{0,246}\.desktop")
ICON_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}")
MAX_PROFILE_BYTES = 64 * 1024


class ProfileError(ValueError):
    """A profile cannot be safely loaded or imported."""


class ProfileExistsError(ProfileError):
    """Import would overwrite an existing profile without explicit approval."""


def _text(value: Any, field: str, limit: int = 80) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ProfileError(f"{field} must be nonempty text of at most {limit} characters")
    return value.strip()


def _fields(value: Any, required: set[str], optional: set[str], name: str) -> dict:
    if not isinstance(value, dict):
        raise ProfileError(f"{name} must be an object")
    missing, extra = required - value.keys(), value.keys() - required - optional
    if missing:
        raise ProfileError(f"{name} is missing: {', '.join(sorted(missing))}")
    if extra:
        raise ProfileError(f"Unsupported {name} fields: {', '.join(map(str, sorted(extra, key=str)))}")
    return value


def validate_profile(data: Any) -> dict:
    """Validate schema version 1 and return an independent, normalized copy."""
    data = _fields(data, {"version", "id", "name", "shortcuts"}, {"desktop_ids", "wm_classes"}, "profile")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ProfileError("Profile version must be 1")
    profile_id = data["id"]
    if not isinstance(profile_id, str) or not ID_PATTERN.fullmatch(profile_id):
        raise ProfileError("Profile ID must use 1–64 lowercase letters, digits, hyphens or underscores")
    normalized = {"version": 1, "id": profile_id, "name": _text(data["name"], "Profile name")}
    if len(normalized["name"].encode("utf-8")) > 128:
        raise ProfileError("Profile name must be at most 128 UTF-8 bytes")
    for field in ("desktop_ids", "wm_classes"):
        values = data.get(field, [])
        if not isinstance(values, list) or len(values) > 32:
            raise ProfileError(f"{field} must be a list of at most 32 strings")
        normalized[field] = []
        for value in values:
            value = _text(value, field, 255)
            if len(value.encode("utf-8")) > 255:
                raise ProfileError(f"{field} entries must be at most 255 UTF-8 bytes")
            if field == "desktop_ids" and not DESKTOP_PATTERN.fullmatch(value):
                raise ProfileError(f"Invalid desktop entry ID: {value}")
            if field == "wm_classes":
                value = value.casefold()
            if value not in normalized[field]:
                normalized[field].append(value)
    shortcuts = data["shortcuts"]
    if not isinstance(shortcuts, list) or len(shortcuts) > 9:
        raise ProfileError("shortcuts must contain at most nine entries")
    normalized["shortcuts"] = []
    slots: set[int] = set()
    for shortcut in shortcuts:
        shortcut = _fields(shortcut, {"slot", "label", "keys"}, {"icon"}, "shortcut")
        slot = shortcut["slot"]
        if type(slot) is not int or not 1 <= slot <= 9 or slot in slots:
            raise ProfileError("Shortcut slots must be unique integers from 1 to 9")
        slots.add(slot)
        icon = shortcut.get("icon", "input-keyboard")
        if not isinstance(icon, str) or not ICON_PATTERN.fullmatch(icon):
            raise ProfileError("Shortcut icon must be an icon theme name, without a path")
        try:
            keys = normalize_shortcut(shortcut["keys"])
        except ValueError as error:
            raise ProfileError(str(error)) from error
        normalized["shortcuts"].append({"slot": slot, "label": _text(shortcut["label"], "Shortcut label", 48), "keys": keys, "icon": icon})
    normalized["shortcuts"].sort(key=lambda shortcut: shortcut["slot"])
    return normalized


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def read_profile(path: Path) -> dict:
    try:
        with Path(path).open("rb") as source:
            contents = source.read(MAX_PROFILE_BYTES + 1)
        if len(contents) > MAX_PROFILE_BYTES:
            raise ProfileError("Profile exceeds the 64 KiB limit")
        return validate_profile(json.loads(contents.decode("utf-8"), object_pairs_hook=_unique_object))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ProfileError(f"Cannot read profile {Path(path).name}: {error}") from error


def load_profiles() -> dict[str, dict]:
    """Load shipped and user profiles, with valid user files taking precedence.

    A malformed manually copied file is logged and skipped, so the settings
    window can still open and offer an import of its corrected replacement.
    """
    result: dict[str, dict] = {}
    for directory in (paths.bundled_profiles_dir(), paths.profiles_dir()):
        seen: set[str] = set()
        for path in sorted(directory.glob("*.json")):
            try:
                profile = read_profile(path)
                if profile["id"] in seen:
                    raise ProfileError(f"Duplicate profile ID in {directory}: {profile['id']}")
                seen.add(profile["id"])
                result[profile["id"]] = profile
            except ProfileError as error:
                LOG.warning("Skipping %s: %s", path, error)
    return result


def atomic_json(path: Path, data: Any, *, overwrite: bool = True) -> None:
    """Write complete UTF-8 JSON with user-only permissions, then publish it."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # The exclusive hard link publishes a complete file atomically and
            # avoids overwriting a profile created after the collision check.
            os.link(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def import_profile(path: Path, *, overwrite: bool = False) -> dict:
    """Validate a profile and atomically install it into shortcuts/config.

    The caller must explicitly opt in before replacing a bundled or user
    profile with the same ID. Validation and collision errors leave it intact.
    """
    profile = read_profile(path)
    destination = paths.profiles_dir() / f"{profile['id']}.json"
    if not overwrite and (destination.exists() or profile["id"] in load_profiles()):
        raise ProfileExistsError(f"A profile named '{profile['id']}' already exists; choose a new ID or explicitly replace it")
    try:
        atomic_json(destination, profile, overwrite=overwrite)
    except FileExistsError as error:
        raise ProfileExistsError(f"A profile named '{profile['id']}' already exists; choose a new ID or explicitly replace it") from error
    except OSError as error:
        raise ProfileError(f"Cannot import profile: {error}") from error
    return profile
