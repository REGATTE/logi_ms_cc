"""Portable user settings and compilation into the Rust worker's runtime JSON."""

from __future__ import annotations

import json
import hashlib
import re
from typing import Callable, Iterable

from . import paths
from .applications import Application, discover_applications
from .profiles import DESKTOP_PATTERN, ID_PATTERN, ProfileError, atomic_json, validate_profile
from .ring_configuration import compile_ring, default_ring, migrate_legacy_ring, validate_ring


class ConfigurationError(ValueError):
    """Settings need correction; never silently replace an unreadable file."""


_PREFERRED_APPS = [
    (("chatgpt.desktop", "com.openai.ChatGPT.desktop"), ("chatgpt",)),
    (("code.desktop", "code_code.desktop", "com.visualstudio.code.desktop", "codium.desktop"), ("visual studio code", "vscodium", "code")),
    (("bytedance-lark.desktop", "lark.desktop", "com.larksuite.Lark.desktop"), ("lark",)),
    (("firefox.desktop", "firefox_firefox.desktop", "org.mozilla.firefox.desktop"), ("firefox", "firefox web browser")),
    (("dev.warp.Warp.desktop", "warp-terminal.desktop", "warp.desktop"), ("warp",)),
]


def compact_launchers(launchers: list[str | None], apps: Iterable[Application]) -> list[str | None]:
    """Pack validated slots while retaining unavailable app IDs for portability.

    Available apps and Screenshot keep their relative order at the front.
    Unavailable apps follow them, then empty slots; the input is not mutated.
    """
    available = {app.id for app in apps} | {"@screenshot", "@action-ring"}
    active = [value for value in launchers if value in available]
    missing = [value for value in launchers if value is not None and value not in available]
    return active + missing + [None] * launchers.count(None)


def default_settings(apps: Iterable[Application]) -> dict:
    apps = list(apps)
    indexed = {app.id: app for app in apps}
    launchers = []
    for desktop_ids, names in _PREFERRED_APPS:
        chosen = next((desktop_id for desktop_id in desktop_ids if desktop_id in indexed), None)
        if chosen is None:
            chosen = next((app.id for app in apps if app.name.casefold() in names), None)
        launchers.append(chosen)
    launchers = compact_launchers(launchers + ["@screenshot", None, None, None], apps)
    return {
        "version": 1,
        "launchers": launchers,
        "profile_assignments": {},
        "auto_switch": True,
        "start_at_login": False,
        "show_labels": False,
        "theme": "system",
        "action_ring": default_ring(launchers),
    }


def validate_settings(settings: object) -> dict:
    required = {"version", "launchers", "profile_assignments", "auto_switch", "start_at_login", "show_labels"}
    if not isinstance(settings, dict) or not required.issubset(settings):
        raise ConfigurationError("Settings must contain version, launchers, profile_assignments, auto_switch, start_at_login and show_labels")
    if set(settings) - required - {"theme", "action_ring"}:
        raise ConfigurationError("Settings contain unsupported fields")
    if type(settings["version"]) is not int or settings["version"] != 1:
        raise ConfigurationError("Settings version must be 1")
    launchers = settings["launchers"]
    if not isinstance(launchers, list) or len(launchers) != 9:
        raise ConfigurationError("Exactly nine launcher slots are required")
    for value in launchers:
        if value is not None and value not in ("@screenshot", "@action-ring") and (not isinstance(value, str) or not DESKTOP_PATTERN.fullmatch(value)):
            raise ConfigurationError("Launcher slots must be desktop IDs, @screenshot, @action-ring or null")
    assignments = settings["profile_assignments"]
    if not isinstance(assignments, dict) or len(assignments) > 1024:
        raise ConfigurationError("profile_assignments must be an object of at most 1024 entries")
    for app_id, profile_id in assignments.items():
        if not isinstance(app_id, str) or not DESKTOP_PATTERN.fullmatch(app_id):
            raise ConfigurationError("Profile assignments require desktop entry IDs")
        if profile_id is not None and (not isinstance(profile_id, str) or not ID_PATTERN.fullmatch(profile_id)):
            raise ConfigurationError("Profile assignments require profile IDs or null")
    for field in ("auto_switch", "start_at_login", "show_labels"):
        if type(settings[field]) is not bool:
            raise ConfigurationError(f"{field} must be true or false")
    # Version 1 predates appearance settings; old files follow the system theme.
    theme = settings.get("theme", "system")
    if not isinstance(theme, str) or theme not in ("system", "light", "dark"):
        raise ConfigurationError("theme must be system, light or dark")
    try:
        ring = validate_ring(settings.get("action_ring", default_ring(launchers)))
    except ValueError as error:
        raise ConfigurationError(str(error)) from error
    return {**settings, "launchers": list(launchers), "profile_assignments": dict(assignments), "theme": theme, "action_ring": ring}


def load_settings(apps: Iterable[Application] | None = None) -> dict:
    path = paths.settings_path()
    try:
        with path.open("rb") as stream:
            contents = stream.read(256 * 1024 + 1)
        if len(contents) > 256 * 1024:
            raise ConfigurationError("Settings file exceeds 256 KiB")
        raw = json.loads(contents.decode("utf-8"))
        if isinstance(raw, dict) and "action_ring" in raw:
            raw["action_ring"] = migrate_legacy_ring(raw["action_ring"])
        settings = validate_settings(raw)
        settings["launchers"] = compact_launchers(
            settings["launchers"], discover_applications() if apps is None else apps,
        )
        return settings
    except FileNotFoundError:
        return default_settings(discover_applications() if apps is None else apps)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ConfigurationError(f"Cannot read settings: {error}") from error


def save_settings(settings: dict) -> None:
    data = validate_settings(settings)
    try:
        try:
            with paths.settings_path().open("rb") as stream:
                previous = stream.read(256 * 1024 + 1)
        except FileNotFoundError:
            previous = b""
        if previous and len(previous) <= 256 * 1024:
            try:
                original = json.loads(previous)
                ring = original.get("action_ring", {}) if isinstance(original, dict) else {}
                layouts = ring.get("layouts", {}) if isinstance(ring, dict) else {}
                needs_backup = isinstance(layouts, dict) and any(isinstance(slots, list) and len(slots) == 8
                                                               for slots in layouts.values())
            except (ValueError, UnicodeError, RecursionError):
                needs_backup = False
            if needs_backup:
                digest = hashlib.sha256(previous).hexdigest()[:12]
                backup = paths.settings_path().with_name(f"settings.before-seven-action-ring-{digest}.json")
                if not backup.exists():
                    atomic_json(backup, original)
        atomic_json(paths.settings_path(), data)
    except OSError as error:
        raise ConfigurationError(f"Cannot save settings: {error}") from error


def profile_for_app(app: Application, profiles: dict[str, dict], assignments: dict[str, str | None]) -> dict | None:
    """An explicit null disables auto matching; an absent entry uses matching."""
    if app.id in assignments:
        return profiles.get(assignments[app.id])
    for profile in profiles.values():
        if app.id in profile["desktop_ids"]:
            return profile
    wm_class = app.wm_class.casefold()
    if wm_class:
        for profile in profiles.values():
            if wm_class in profile["wm_classes"]:
                return profile
    return None


def compile_runtime(settings: dict, apps: Iterable[Application], profiles: dict[str, dict], render_face: Callable[[str, str, Application | None], str]) -> dict:
    """Compile launcher and focused-app pages; render only their actual faces."""
    settings = validate_settings(settings)
    apps = list(apps)
    indexed = {app.id: app for app in apps}
    try:
        profiles = {key: validate_profile(value) for key, value in profiles.items()}
    except ProfileError as error:
        raise ConfigurationError(str(error)) from error

    def face(label: str, icon: str, app: Application | None = None) -> str:
        filename = render_face(label, icon, app)
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.jpg", filename) or len(filename) > 255:
            raise ConfigurationError("Rendered key image must be a simple JPEG filename")
        return filename

    def shortcut_page(profile: dict | None, name: str, app: Application | None = None) -> dict:
        keys: list[dict | None] = [None] * 9
        if profile:
            for entry in profile["shortcuts"]:
                keys[entry["slot"] - 1] = {"label": entry["label"], "action": {"type": "shortcut", "keys": entry["keys"]}, "image": face(entry["label"], entry["icon"])}
        return {"name": name, "keys": keys}

    selections = {app.id: profile_for_app(app, profiles, settings["profile_assignments"]) for app in apps}
    launcher_keys: list[dict | None] = []
    for app_id in compact_launchers(settings["launchers"], apps):
        if app_id == "@action-ring":
            launcher_keys.append({"label": "Action Ring", "action": {"type": "open_ring"}, "image": face("Action Ring", "view-grid")})
        elif app_id == "@screenshot":
            launcher_keys.append({"label": "Screenshot", "action": {"type": "screenshot"}, "image": face("Screenshot", "applets-screenshooter")})
        elif app_id in indexed:
            app = indexed[app_id]
            # Launcher identity belongs to this app, not all the apps that may
            # share its shortcut profile. Never focus another app by profile alias.
            classes = list(dict.fromkeys(value.casefold() for value in
                           (app.wm_class, app.id.removesuffix(".desktop")) if value))
            if any(len(value.encode("utf-8")) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in value)
                   for value in classes):
                raise ConfigurationError("Application window class is invalid")
            profile = selections[app.id]
            launcher_keys.append({"label": app.name,
                                  "action": {"type": "launch", "desktop_id": app.id,
                                             "wm_classes": classes,
                                             "profile_id": profile["id"] if profile else None},
                                  "image": face(app.name, app.icon, app)})
        else:
            launcher_keys.append(None)
    pages = [{"name": "Apps", "keys": launcher_keys}, shortcut_page(profiles.get("default"), "Shortcuts")]
    runtime_profiles: dict[str, dict] = {}
    class_owners = {}
    for app in apps:
        selection = selections[app.id]
        for value in (app.wm_class, app.id.removesuffix(".desktop")):
            if value:
                class_owners[value.casefold()] = selection["id"] if selection else None
    for app in apps:
        profile = selections[app.id]
        if profile is None or profile["id"] == "default":
            continue
        profile_id = profile["id"]
        if profile_id not in runtime_profiles:
            runtime_profiles[profile_id] = {"id": profile_id, "wm_classes": [], "page": shortcut_page(profile, profile["name"], app)}
        # StartupWMClass is authoritative where available. A desktop ID stem
        # and explicitly supplied profile classes cover applications that omit it.
        classes = [app.wm_class, app.id.removesuffix(".desktop"), *profile["wm_classes"]]
        for wm_class in classes:
            wm_class = wm_class.casefold()
            # An alias in a shared profile must not reclaim an installed app
            # that has explicitly disabled profiles or selected another one.
            if wm_class in class_owners and class_owners[wm_class] != profile_id:
                continue
            if wm_class and wm_class not in runtime_profiles[profile_id]["wm_classes"]:
                runtime_profiles[profile_id]["wm_classes"].append(wm_class)
    if len(runtime_profiles) > 256 or any(len(profile["wm_classes"]) > 64 for profile in runtime_profiles.values()):
        raise ConfigurationError("Runtime supports at most 256 profiles and 64 window classes per profile")
    return {"version": 1, "auto_switch": settings["auto_switch"], "pages": pages, "profiles": list(runtime_profiles.values()),
            "action_ring": compile_ring(settings["action_ring"], apps, selections)}
