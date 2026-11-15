import json
from pathlib import Path

import pytest

from mxcc import paths
from mxcc.applications import Application
from mxcc.configuration import ConfigurationError, compile_runtime, default_settings, load_settings, save_settings
from mxcc.profiles import load_profiles


def app(desktop_id="firefox.desktop", name="Firefox", wm_class="firefox"):
    return Application(desktop_id, name, "original-app-icon", "unused %U", wm_class, Path("/unused") / desktop_id)


def render(label, icon, application):
    return "face.jpg"


def test_defaults_keep_requested_positions_without_inventing_installed_apps():
    settings = default_settings([app(), app("dev.warp.Warp.desktop", "Warp")])
    assert settings["launchers"] == [None, None, None, "firefox.desktop", "dev.warp.Warp.desktop", "@screenshot", None, None, None]


def test_compile_two_pages_uses_original_icon_and_installed_apps(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "profiles_dir", lambda: tmp_path)
    profiles = load_profiles()
    firefox = app()
    settings = default_settings([firefox])
    settings["launchers"][0] = "missing.desktop"
    calls = []
    def record(label, icon, application):
        calls.append((label, icon, application))
        return "face.jpg"
    runtime = compile_runtime(settings, [firefox], profiles, record)
    assert len(runtime["pages"]) == 2
    assert all(len(page["keys"]) == 9 for page in runtime["pages"])
    assert runtime["pages"][0]["keys"][0] is None
    assert runtime["pages"][0]["keys"][3]["action"] == {"type": "launch", "desktop_id": "firefox.desktop"}
    assert runtime["pages"][0]["keys"][5]["action"] == {"type": "screenshot"}
    assert ("Firefox", "original-app-icon", firefox) in calls
    assert ("New tab", "tab-new", None) in calls
    assert [profile["id"] for profile in runtime["profiles"]] == ["firefox"]
    assert runtime["pages"][1]["keys"][0]["action"] == {"type": "shortcut", "keys": "Ctrl+C"}


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


def test_settings_round_trip_and_invalid_save_preserves_previous(monkeypatch, tmp_path):
    path = tmp_path / "config/settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    settings = load_settings([app()])
    assert settings == default_settings([app()])
    assert not path.exists()
    save_settings(settings)
    assert load_settings() == settings
    before = path.read_bytes()
    with pytest.raises(ConfigurationError):
        save_settings(settings | {"launchers": [None]})
    assert path.read_bytes() == before
    path.write_text("{broken")
    with pytest.raises(ConfigurationError, match="Cannot read settings"):
        load_settings()


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
