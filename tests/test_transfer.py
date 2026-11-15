import copy
import json

import pytest

from mxcc import paths
from mxcc.configuration import default_settings
from mxcc.profiles import load_profiles
from mxcc.transfer import LIMIT, export_bundle, read_bundle, write_imported_profiles


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS"):
        directory = tmp_path / name.lower()
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(name, str(directory))
    monkeypatch.delenv("MXCC_BUNDLED_PROFILES", raising=False)
    return default_settings([]), load_profiles()


def test_portable_configuration_round_trip(isolated_settings, tmp_path):
    settings, profiles = isolated_settings
    settings["launchers"][0] = "not-installed-here.desktop"
    settings["profile_assignments"]["not-installed-here.desktop"] = "vscode"
    destination = tmp_path / "export.json"
    export_bundle(destination, settings, profiles)
    restored_settings, restored_profiles = read_bundle(destination)
    assert restored_settings == settings
    assert restored_profiles == profiles
    assert not paths.settings_path().exists()
    write_imported_profiles(restored_profiles)
    assert {file.stem for file in paths.profiles_dir().glob("*.json")} == set(profiles)
    assert load_profiles() == profiles


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"command": "shell"},
    {"profiles": "not a list"}, pytest.param({"profiles": [{}] * 257}, id="profile-count-limit"),
])
def test_bundle_schema_rejects_unsupported_data(isolated_settings, tmp_path, change):
    settings, profiles = isolated_settings
    data = {"version": 1, "settings": settings, "profiles": list(profiles.values())}
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(data | change))
    with pytest.raises(ValueError):
        read_bundle(source)
    assert not paths.profiles_dir().exists()


def test_bundle_rejects_duplicate_profiles_and_path_traversal(isolated_settings, tmp_path):
    settings, profiles = isolated_settings
    source = tmp_path / "invalid.json"
    profile = profiles["vscode"]
    for entries in ([profile, profile], [profile | {"id": "../../escape"}], [profile | {"exec": "arbitrary command"}]):
        source.write_text(json.dumps({"version": 1, "settings": settings, "profiles": entries}))
        with pytest.raises(ValueError):
            read_bundle(source)
    assert not paths.profiles_dir().exists()


def test_bundle_size_limit(isolated_settings, tmp_path):
    source = tmp_path / "oversized.json"
    source.write_bytes(b" " * (LIMIT + 1))
    with pytest.raises(ValueError, match="2 MiB"):
        read_bundle(source)


def test_invalid_later_profile_does_not_partially_import(isolated_settings):
    _, profiles = isolated_settings
    valid = copy.deepcopy(profiles["vscode"])
    invalid = copy.deepcopy(profiles["firefox"])
    invalid["shortcuts"][0]["keys"] = "unsupported-command"
    with pytest.raises(ValueError):
        write_imported_profiles({"vscode": valid, "firefox": invalid})
    assert not paths.profiles_dir().exists()
