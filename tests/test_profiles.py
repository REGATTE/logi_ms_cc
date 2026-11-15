import copy
import json
import logging
import stat
from pathlib import Path

import pytest

from mxcc import paths
from mxcc.profiles import LOG, ProfileError, ProfileExistsError, import_profile, load_profiles, read_profile, validate_profile
from mxcc.shortcuts import normalize_shortcut


@pytest.fixture
def sample():
    return {"version": 1, "id": "custom", "name": "Custom", "desktop_ids": ["custom.desktop"], "wm_classes": ["Custom"], "shortcuts": [{"slot": 2, "label": "Save", "keys": "control+s", "icon": "document-save"}]}


@pytest.fixture
def locations(monkeypatch, tmp_path):
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    bundled.mkdir()
    user.mkdir()
    monkeypatch.setattr(paths, "bundled_profiles_dir", lambda: bundled)
    monkeypatch.setattr(paths, "profiles_dir", lambda: user)
    return bundled, user


def test_validation_normalizes_without_mutating_input(sample):
    original = copy.deepcopy(sample)
    data = validate_profile(sample)
    assert data["shortcuts"][0]["keys"] == "Ctrl+S"
    assert data["wm_classes"] == ["custom"]
    assert sample == original


@pytest.mark.parametrize("change", [
    {"id": "../../escape"}, {"version": True}, {"exec": "touch arbitrary-file"},
    {"desktop_ids": ["../escape.desktop"]},
    {"shortcuts": [{"slot": 1, "label": "Run", "keys": "Ctrl+C", "command": "shell"}]},
    {"shortcuts": [{"slot": True, "label": "Copy", "keys": "Ctrl+C"}]},
    {"shortcuts": [{"slot": 1, "label": "Copy", "keys": "Ctrl+C", "icon": "/etc/passwd"}]},
    {"shortcuts": [{"slot": 1, "label": "Copy", "keys": "Ctrl+C"}] * 2},
    {"shortcuts": [{"slot": 1, "label": "Sequence", "keys": "Ctrl+K Ctrl+S"}]},
])
def test_malformed_or_executable_profiles_are_rejected(sample, change):
    with pytest.raises(ProfileError):
        validate_profile(sample | change)


@pytest.mark.parametrize(("source", "expected"), [("alt+ctrl+shift+p", "Ctrl+Alt+Shift+P"), ("Ctrl+`", "Ctrl+Grave"), ("Super+Return", "Super+Enter"), ("Ctrl+Plus", "Ctrl+Plus"), ("f24", "F24")])
def test_supported_chord_spellings(source, expected):
    assert normalize_shortcut(source) == expected


@pytest.mark.parametrize("chord", ["", "Ctrl", "Ctrl+Control+C", "Ctrl+C+D", "Ctrl++", "F25", "Ctrl+$(command)"])
def test_invalid_chords(chord):
    with pytest.raises(ValueError):
        normalize_shortcut(chord)


def test_import_atomic_collision_and_replacement(sample, locations, tmp_path):
    bundled, user = locations
    source = tmp_path / "input.json"
    source.write_text(json.dumps(sample))
    expected = import_profile(source)
    output = user / "custom.json"
    assert json.loads(output.read_text()) == expected
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    before = output.read_bytes()
    with pytest.raises(ProfileExistsError, match="already exists"):
        import_profile(source)
    assert output.read_bytes() == before
    sample["name"] = "Replacement"
    source.write_text(json.dumps(sample))
    assert import_profile(source, overwrite=True)["name"] == "Replacement"
    assert not list(user.glob("*.tmp"))
    source.write_text('{"version":1,"version":1}')
    with pytest.raises(ProfileError, match="Duplicate JSON"):
        import_profile(source, overwrite=True)
    assert json.loads(output.read_text())["name"] == "Replacement"


def test_bundled_collisions_and_manually_dropped_overrides(sample, locations, tmp_path, caplog):
    bundled, user = locations
    source = bundled / "custom.json"
    source.write_text(json.dumps(sample))
    with pytest.raises(ProfileError, match="already exists"):
        import_profile(source)
    (user / "custom.json").write_text(json.dumps(sample | {"name": "User override"}))
    (user / "invalid.json").write_text("{")
    # ROS launch_testing can replace Logger globally with a non-propagating
    # subclass. Capture this module directly instead of relying on root routing.
    LOG.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger=LOG.name):
            assert load_profiles()["custom"]["name"] == "User override"
    finally:
        LOG.removeHandler(caplog.handler)
    assert any(
        record.name == LOG.name
        and record.levelno == logging.WARNING
        and str(user / "invalid.json") in record.getMessage()
        and "Cannot read profile invalid.json" in record.getMessage()
        for record in caplog.records
    )


def test_bundled_profiles_validate():
    directory = Path(__file__).resolve().parents[1] / "shortcuts/config"
    profiles = [read_profile(path) for path in directory.glob("*.json")]
    assert {profile["id"] for profile in profiles} == {"default", "chatgpt", "vscode", "lark", "firefox", "warp"}


def test_import_reports_collision_that_occurs_after_initial_check(sample, locations, tmp_path, monkeypatch):
    import mxcc.profiles
    source = tmp_path / "input.json"
    source.write_text(json.dumps(sample))
    def collision(*args, **kwargs):
        raise FileExistsError("created by another process")
    monkeypatch.setattr(mxcc.profiles, "atomic_json", collision)
    with pytest.raises(ProfileExistsError, match="already exists"):
        import_profile(source)
