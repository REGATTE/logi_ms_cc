"""Portable settings archives: JSON only, with no extracted paths or code."""

import json
from pathlib import Path

from .configuration import validate_settings
from .paths import profiles_dir
from .profiles import atomic_json, validate_profile

LIMIT = 2 * 1024 * 1024


def export_bundle(destination: Path, settings: dict, profiles: dict[str, dict]) -> None:
    data = {"version": 1, "settings": validate_settings(settings),
            "profiles": [validate_profile(profile) for profile in profiles.values()]}
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_bundle(source: Path) -> tuple[dict, dict[str, dict]]:
    with source.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Configuration exceeds 2 MiB.")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or set(data) != {"version", "settings", "profiles"}:
        raise ValueError("This is not an MX Creative Control configuration export.")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported export version.")
    settings = validate_settings(data["settings"])
    if not isinstance(data["profiles"], list) or len(data["profiles"]) > 256:
        raise ValueError("The export has too many profiles.")
    profiles = {}
    for raw_profile in data["profiles"]:
        profile = validate_profile(raw_profile)
        if profile["id"] in profiles:
            raise ValueError("The export contains duplicate profile IDs.")
        profiles[profile["id"]] = profile
    return settings, profiles


def write_imported_profiles(profiles: dict[str, dict]) -> None:
    validated = [validate_profile(profile) for profile in profiles.values()]
    for profile in validated:
        atomic_json(profiles_dir() / (profile["id"] + ".json"), profile)
