"""Ring migrations, portable layouts and strict execution boundaries."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from mxcc.applications import Application
from mxcc.configuration import compile_runtime, default_settings, load_settings, save_settings, validate_settings
from mxcc import paths
from mxcc.ring_configuration import compile_ring, default_ring, validate_ring
from mxcc.transfer import export_bundle, read_bundle


def application(identifier="editor.desktop", icon="/local/editor.svg", wm_class="Editor"):
    return Application(identifier, "My Editor", icon, "editor", wm_class, Path("/unused/editor.desktop"))


def test_legacy_migration_retains_launchers_and_creates_unbound_usable_ring():
    legacy = default_settings([])
    legacy.pop("action_ring")
    legacy["launchers"] = ["editor.desktop", "@screenshot"] + [None] * 7
    original = deepcopy(legacy)
    result = validate_settings(legacy)
    assert legacy == original
    assert result["launchers"] == original["launchers"]
    assert result["action_ring"]["bindings"] == []
    assert len(result["action_ring"]["layouts"]["default"]) == 4
    assert result["action_ring"]["layouts"]["default"][0]["action"]["desktop_id"] == "editor.desktop"


def test_ring_compilation_resolves_original_icons_and_app_specific_identities():
    ring = default_ring(["editor.desktop", "absent.desktop"])
    ring["layouts"]["editor.desktop"] = []
    before = deepcopy(ring)
    runtime = compile_ring(ring, [application()], {"editor.desktop": {"id": "editor"}})
    global_layout, specific = runtime["layouts"]
    launch = global_layout["slots"][0]
    assert launch["icon"] == "/local/editor.svg"
    assert launch["label"] == "My Editor"
    assert launch["action"]["wm_classes"] == ["editor"]
    assert launch["action"]["profile_id"] == "editor"
    assert global_layout["slots"][1]["action"]["type"] == "shortcut"
    assert specific["slots"] == []
    assert specific["wm_classes"] == ["editor"]
    assert ring == before


def test_ring_launcher_packs_with_apps_without_changing_shortcut_page():
    settings = default_settings([])
    settings["launchers"] = [None, "@action-ring", "editor.desktop", "@screenshot"] + [None] * 5
    runtime = compile_runtime(settings, [application()], {}, lambda *_: "face.jpg")
    assert [key["action"]["type"] for key in runtime["pages"][0]["keys"][:3]] == ["open_ring", "launch", "screenshot"]
    assert runtime["pages"][1]["keys"] == [None] * 9


@pytest.mark.parametrize("action", [
    {"type": "open_ring"}, {"type": "shell", "command": "anything"},
    {"type": "shortcut", "keys": "Ctrl+A;Ctrl+C"},
    {"type": "launch", "desktop_id": "../escape.desktop"},
    {"type": "screenshot", "command": "anything"},
])
def test_ring_rejects_recursive_actions_shells_and_invalid_chords(action):
    ring = default_ring()
    ring["layouts"]["default"][0]["action"] = action
    with pytest.raises(ValueError):
        validate_ring(ring)


@pytest.mark.parametrize("field,value", [("layouts", {}), ("layouts", {"default": [None] * 9}),
                                          ("bindings", [{"vendor_id": 0x046d, "product_id": 0xc07e, "control_id": "button:1"}]),
                                          ("bindings", [{"vendor_id": 0x046d, "product_id": 0xbc00, "control_id": "dial:main"}])])
def test_invalid_layout_or_binding_rejected(field, value):
    with pytest.raises(ValueError):
        validate_ring(default_ring() | {field: value})


def test_ring_normalizes_chords_and_portable_bundle_retains_device_bindings(tmp_path):
    settings = default_settings([])
    ring = settings["action_ring"]
    ring["layouts"]["default"][0]["action"]["keys"] = "control+c"
    ring["bindings"] = [{"vendor_id": 0x046d, "product_id": 0xc07e, "control_id": "button:4"}]
    destination = tmp_path / "setup.json"
    export_bundle(destination, settings, {})
    imported, profiles = read_bundle(destination)
    assert imported["action_ring"]["layouts"]["default"][0]["action"]["keys"] == "Ctrl+C"
    assert imported["action_ring"]["bindings"] == ring["bindings"]
    assert profiles == {}
    assert "/local/" not in json.dumps(imported)
    ring["bindings"].append(dict(ring["bindings"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        validate_ring(ring)


def test_seven_action_limit_and_empty_compaction():
    entry = default_ring()["layouts"]["default"][0]
    for count in range(8):
        assert len(validate_ring({"layouts": {"default": [entry] * count}, "bindings": []})["layouts"]["default"]) == count
    with pytest.raises(ValueError, match="seven"):
        validate_ring({"layouts": {"default": [entry] * 8}, "bindings": []})
    assert validate_ring({"layouts": {"default": [None, entry, None]}, "bindings": []})["layouts"]["default"] == [entry]


def test_old_eight_actions_migrate_with_backup_before_first_save(monkeypatch, tmp_path):
    destination = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: destination)
    legacy = default_settings([])
    entry = legacy["action_ring"]["layouts"]["default"][0]
    legacy["action_ring"]["layouts"]["default"] = [{**entry, "label": f"Action {index}"} for index in range(8)]
    destination.write_text(json.dumps(legacy))
    previous = destination.read_bytes()
    loaded = load_settings([])
    assert len(loaded["action_ring"]["layouts"]["default"]) == 7
    assert destination.read_bytes() == previous
    assert len(list(tmp_path.iterdir())) == 1
    save_settings(loaded)
    backup, = tmp_path.glob("settings.before-seven-action-ring-*.json")
    assert json.loads(backup.read_text()) == legacy
    assert json.loads(destination.read_text()) == loaded
    save_settings(loaded)
    assert len(list(tmp_path.glob("settings.before-seven-action-ring-*.json"))) == 1


def test_migration_validates_overflow_action_and_backup_failure_keeps_old_settings(monkeypatch, tmp_path):
    destination = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: destination)
    legacy = default_settings([])
    entry = legacy["action_ring"]["layouts"]["default"][0]
    legacy["action_ring"]["layouts"]["default"] = [deepcopy(entry) for _ in range(8)]
    legacy["action_ring"]["layouts"]["default"][7]["action"] = {"type": "shell", "command": "invalid"}
    destination.write_text(json.dumps(legacy))
    with pytest.raises(ValueError):
        load_settings([])
    legacy["action_ring"]["layouts"]["default"][7] = entry
    destination.write_text(json.dumps(legacy))
    loaded = load_settings([])
    original = destination.read_bytes()
    def fail(*_):
        raise OSError("Backup unavailable")
    monkeypatch.setattr("mxcc.configuration.atomic_json", fail)
    with pytest.raises(ValueError, match="Backup unavailable"):
        save_settings(loaded)
    assert destination.read_bytes() == original
