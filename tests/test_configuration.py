import json
from pathlib import Path

import pytest

from mxcc import paths
from mxcc.applications import Application
from mxcc.configuration import ConfigurationError, compact_launchers, compile_runtime, default_settings, load_settings, save_settings, validate_settings
from mxcc.profiles import load_profiles


def app(desktop_id="firefox.desktop", name="Firefox", wm_class="firefox"):
    return Application(desktop_id, name, "original-app-icon", "unused %U", wm_class, Path("/unused") / desktop_id)


def render(label, icon, application):
    return "face.jpg"


def test_defaults_pack_installed_apps_in_requested_order_without_gaps():
    settings = default_settings([app(), app("dev.warp.Warp.desktop", "Warp")])
    assert settings["launchers"] == ["firefox.desktop", "dev.warp.Warp.desktop", "@screenshot"] + [None] * 6
    assert settings["theme"] == "system"


def test_compaction_preserves_order_and_unavailable_apps_without_mutation():
    original = ["missing.desktop", None, "@screenshot", "firefox.desktop", None,
                "other.desktop", "firefox.desktop", None, None]
    before = list(original)

    result = compact_launchers(original, iter([app()]))

    assert result == ["@screenshot", "firefox.desktop", "firefox.desktop",
                      "missing.desktop", "other.desktop"] + [None] * 4
    assert original == before
    assert compact_launchers(result, [app()]) == result


def test_compaction_with_no_installed_apps_retains_portable_configuration():
    launchers = ["missing.desktop", None, "@screenshot", None, "other.desktop"] + [None] * 4
    assert compact_launchers(launchers, []) == ["@screenshot", "missing.desktop", "other.desktop"] + [None] * 6
    assert compact_launchers([None] * 9, []) == [None] * 9


def test_load_compacts_legacy_gaps_using_discovery_without_rewriting_file(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    monkeypatch.setattr("mxcc.configuration.discover_applications", lambda: [app()])
    settings = default_settings([])
    settings["launchers"] = [None, "missing.desktop", None, "firefox.desktop", "@screenshot"] + [None] * 4
    path.write_text(json.dumps(settings))
    before = path.read_bytes()

    result = load_settings()

    assert result["launchers"] == ["firefox.desktop", "@screenshot", "missing.desktop"] + [None] * 6
    assert path.read_bytes() == before
    save_settings(result)
    assert json.loads(path.read_text())["launchers"] == result["launchers"]


def test_compile_two_pages_uses_original_icon_and_installed_apps(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "profiles_dir", lambda: tmp_path)
    profiles = load_profiles()
    firefox = app()
    settings = default_settings([firefox])
    settings["theme"] = "dark"
    settings["launchers"] = ["missing.desktop", None, None, "firefox.desktop", None, "@screenshot", None, None, None]
    original_launchers = list(settings["launchers"])
    calls = []
    def record(label, icon, application):
        calls.append((label, icon, application))
        return "face.jpg"
    runtime = compile_runtime(settings, [firefox], profiles, record)
    assert len(runtime["pages"]) == 2
    assert all(len(page["keys"]) == 9 for page in runtime["pages"])
    assert runtime["pages"][0]["keys"][0]["action"] == {
        "type": "launch", "desktop_id": "firefox.desktop", "wm_classes": ["firefox"], "profile_id": "firefox"}
    assert runtime["pages"][0]["keys"][1]["action"] == {"type": "screenshot"}
    assert runtime["pages"][0]["keys"][2:] == [None] * 7
    assert settings["launchers"] == original_launchers
    assert ("Firefox", "original-app-icon", firefox) in calls
    assert ("New tab", "tab-new", None) in calls
    assert [profile["id"] for profile in runtime["profiles"]] == ["firefox"]
    assert runtime["pages"][1]["keys"][0]["action"] == {"type": "shortcut", "keys": "Ctrl+C"}
    assert set(runtime) == {"version", "auto_switch", "pages", "profiles"}


def test_launcher_compaction_does_not_move_shortcut_slots():
    profile = {"version": 1, "id": "default", "name": "General", "desktop_ids": [],
               "wm_classes": [], "shortcuts": [
                   {"slot": 8, "label": "Copy", "keys": "Ctrl+C", "icon": "edit-copy"},
               ]}
    runtime = compile_runtime(default_settings([]), [], {"default": profile}, render)
    keys = runtime["pages"][1]["keys"]
    assert keys[:7] == [None] * 7
    assert keys[7]["action"] == {"type": "shortcut", "keys": "Ctrl+C"}
    assert keys[8] is None


def test_manual_assignment_and_explicit_none(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "profiles_dir", lambda: tmp_path)
    profiles = load_profiles()
    firefox = app()
    settings = default_settings([firefox])
    settings["profile_assignments"][firefox.id] = None
    assert compile_runtime(settings, [firefox], profiles, render)["profiles"] == []
    settings["profile_assignments"][firefox.id] = "vscode"
    result, = compile_runtime(settings, [firefox], profiles, render)["profiles"]
    assert result["id"] == "vscode"
    assert "firefox" in result["wm_classes"]


def test_match_by_window_class_for_alternative_desktop_id(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "profiles_dir", lambda: tmp_path)
    application = app("vendor.browser.desktop", "Web", "Firefox")
    runtime = compile_runtime(default_settings([application]), [application], load_profiles(), render)
    result, = runtime["profiles"]
    assert result["id"] == "firefox"
    assert "vendor.browser" in result["wm_classes"]


def test_shared_profile_aliases_respect_disabled_app(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "profiles_dir", lambda: tmp_path)
    profiles = load_profiles()
    applications = [app("code.desktop", "VS Code", "code"), app("codium.desktop", "VSCodium", "vscodium")]
    settings = default_settings(applications)
    settings["profile_assignments"]["codium.desktop"] = None
    result, = compile_runtime(settings, applications, profiles, render)["profiles"]
    assert "code" in result["wm_classes"]
    assert "vscodium" not in result["wm_classes"]
    assert "codium" not in result["wm_classes"]


@pytest.mark.parametrize("filename", ["../escape.jpg", "/tmp/file.jpg", "folder/image.jpg", "a\\image.jpg", "image.png"])
def test_renderer_cannot_escape_image_cache(filename):
    with pytest.raises(ConfigurationError, match="JPEG filename"):
        compile_runtime(default_settings([]), [], {}, lambda *_: filename)


@pytest.mark.parametrize("theme", ["system", "light", "dark"])
def test_settings_round_trip_and_invalid_save_preserves_previous(monkeypatch, tmp_path, theme):
    path = tmp_path / "config/settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    settings = load_settings([app()])
    assert settings == default_settings([app()])
    assert not path.exists()
    settings["theme"] = theme
    save_settings(settings)
    assert load_settings([app()]) == settings
    before = path.read_bytes()
    with pytest.raises(ConfigurationError):
        save_settings(settings | {"launchers": [None]})
    assert path.read_bytes() == before
    path.write_text("{broken")
    with pytest.raises(ConfigurationError, match="Cannot read settings"):
        load_settings()


def test_legacy_v1_settings_gain_system_theme_without_rewriting_on_read(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    legacy = default_settings([app()])
    legacy.pop("theme")
    path.write_text(json.dumps(legacy))
    original = path.read_bytes()

    settings = load_settings([app()])

    assert settings == legacy | {"theme": "system"}
    assert path.read_bytes() == original
    save_settings(settings)
    assert json.loads(path.read_text())["theme"] == "system"


@pytest.mark.parametrize("theme", ["sepia", None, True, [], {}])
def test_invalid_theme_is_rejected(theme):
    with pytest.raises(ConfigurationError, match="theme must be"):
        validate_settings(default_settings([]) | {"theme": theme})


def test_theme_extension_does_not_allow_unknown_settings_fields():
    with pytest.raises(ConfigurationError, match="unsupported fields"):
        validate_settings(default_settings([]) | {"themes": "dark"})


def test_failed_atomic_replace_keeps_previous_file(monkeypatch, tmp_path):
    import mxcc.profiles
    path = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    settings = default_settings([])
    save_settings(settings)
    def fail(*_):
        raise OSError("disk unavailable")
    monkeypatch.setattr(mxcc.profiles.os, "replace", fail)
    with pytest.raises(ConfigurationError, match="disk unavailable"):
        save_settings(settings | {"auto_switch": False})
    assert json.loads(path.read_text()) == settings
    assert list(tmp_path.iterdir()) == [path]


def test_launcher_identity_is_app_specific_even_with_a_shared_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, 'profiles_dir', lambda: tmp_path)
    profiles = load_profiles()
    code = app('code.desktop', 'VS Code', 'Code')
    codium = app('codium.desktop', 'VSCodium', 'VSCodium')
    settings = default_settings([code, codium])
    settings['launchers'] = [code.id, codium.id] + [None] * 7
    settings['profile_assignments'][codium.id] = 'vscode'
    runtime = compile_runtime(settings, [code, codium], profiles, render)
    first, second = [key['action'] for key in runtime['pages'][0]['keys'][:2]]
    assert first['wm_classes'] == ['code']
    assert second['wm_classes'] == ['vscodium', 'codium']
    assert first['profile_id'] == second['profile_id'] == 'vscode'
    settings['profile_assignments'][code.id] = None
    settings['profile_assignments'][codium.id] = 'default'
    runtime = compile_runtime(settings, [code, codium], profiles, render)
    assert runtime['pages'][0]['keys'][0]['action']['profile_id'] is None
    assert runtime['pages'][0]['keys'][1]['action']['profile_id'] == 'default'


def test_launcher_without_startup_class_uses_desktop_identity():
    application = app('org.example.Editor.desktop', 'Editor', '')
    settings = default_settings([])
    settings['launchers'] = [application.id] + [None] * 8
    action = compile_runtime(settings, [application], {}, render)['pages'][0]['keys'][0]['action']
    assert action['wm_classes'] == ['org.example.editor']
    assert action['profile_id'] is None
