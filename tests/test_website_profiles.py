"""Exact-site overrides remain portable and cannot expand into executable code."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from mxcc import paths
from mxcc.applications import Application
from mxcc.configuration import ConfigurationError, compile_runtime, default_settings, load_settings, save_settings, validate_settings
from mxcc.transfer import export_bundle, read_bundle
from mxcc.website_profiles import (
    compile_website_profiles, domain_from_layout_id, normalize_domain,
    validate_domain, validate_website_profiles, website_layout_id,
)


def shortcut(keys="Ctrl+C"):
    return {"label": "Copy", "icon": "edit-copy", "action": {"type": "shortcut", "keys": keys}}


def assignment(**changes):
    return {"vendor_id": 0x046d, "product_id": 0xbc00, "control_id": "button:1", **shortcut(), **changes}


def profile(domain="example.com", **changes):
    return {"domain": domain, "device_actions": [assignment()], "ring": [shortcut()], **changes}


@pytest.mark.parametrize("value,expected", [
    (" example.com ", "example.com"), ("EXAMPLE.COM", "example.com"),
    ("https://Example.COM:8443/path?query=1#section", "example.com"),
    ("http://localhost:8000/editor", "localhost"), ("127.0.0.1", "127.0.0.1"),
    ("https://bücher.example/path", "xn--bcher-kva.example"),
    ("xn--fa-hia.de", "xn--fa-hia.de"),
])
def test_user_input_normalizes_only_the_website_host(value, expected):
    assert normalize_domain(value) == expected
    assert validate_domain(expected) == expected


@pytest.mark.parametrize("value", [
    None, "", "https://", "ftp://example.com", "javascript:alert(1)",
    "https://user:password@example.com", "https://example.com:bad/", "https://example.com:99999/",
    "example.com/path", "example.com:443", "*.example.com", "example.com.",
    "example.com\n", "https://example.com/\t", "[::1]", "https://[::1]/", "faß.de",
])
def test_invalid_or_ambiguous_user_input_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_domain(value)


@pytest.mark.parametrize("domain", [
    None, True, "", "EXAMPLE.com", "https://example.com", "example.com/path", "example.com:80",
    "example.com.", ".example.com", "a..example.com", "*.example.com", "x_y.example.com",
    "-x.example", "x-.example", "bücher.example", "example.com\0", "a" * 64 + ".example",
    ".".join(["a" * 63] * 4),
])
def test_stored_domains_must_be_canonical_for_exact_rust_matching(domain):
    with pytest.raises(ValueError):
        validate_website_profiles([profile(domain)])


def test_layout_identity_roundtrip_and_nonwebsite_ids():
    assert website_layout_id("example.com") == "website:example.com"
    assert domain_from_layout_id("website:example.com") == "example.com"
    assert domain_from_layout_id("firefox.desktop") is None
    assert domain_from_layout_id("default") is None
    assert domain_from_layout_id(None) is None
    for value in ("website:", "website:Example.com", "website:../../escape"):
        with pytest.raises(ValueError):
            domain_from_layout_id(value)


def test_profiles_are_independent_and_distinguish_inherited_from_empty_ring():
    original = [profile(ring=None), profile("sub.example.com", ring=[])]
    normalized = validate_website_profiles(original)
    assert normalized == original
    normalized[0]["device_actions"][0]["action"]["keys"] = "Ctrl+V"
    assert original[0]["device_actions"][0]["action"]["keys"] == "Ctrl+C"


@pytest.mark.parametrize("values", [
    None, {}, [profile(), profile()], [profile(**{"exec": "shell"})],
    [profile(device_actions=[assignment(), assignment()])],
    [profile(device_actions=[assignment(control_id="wheel:roller")])],
    [profile(ring=[shortcut()] * 8)],
    [profile(ring=[shortcut() | {"action": {"type": "open_ring"}}])],
    [profile(f"site{index}.example") for index in range(129)],
])
def test_invalid_profile_scopes_or_actions_are_rejected(values):
    with pytest.raises(ValueError):
        validate_website_profiles(values)


def test_runtime_compiles_site_overrides_and_preserves_global_assignments():
    app = Application("editor.desktop", "Editor", "/installed/editor.png", "editor", "Editor", Path("/installed/editor.desktop"))
    launch = {"label": "Editor", "icon": "application-x-executable", "action": {"type": "launch", "desktop_id": app.id}}
    missing = deepcopy(launch)
    missing["action"]["desktop_id"] = "missing.desktop"
    settings = default_settings([app])
    settings["device_actions"] = [assignment()]
    settings["website_profiles"] = [profile(
        device_actions=[assignment(**shortcut("Ctrl+V")), assignment(control_id="button:2", **launch),
                        assignment(control_id="button:3", **missing)],
        ring=[shortcut(), launch, missing]), profile("other.example", device_actions=[], ring=None)]
    before = deepcopy(settings)
    runtime = compile_runtime(settings, [app], {}, lambda *_: "face.jpg")
    assert settings == before
    assert runtime["device_actions"][0]["action"] == {"type": "shortcut", "keys": "Ctrl+C"}
    site, inherited = runtime["website_profiles"]
    assert site["domain"] == "example.com"
    assert [item["control_id"] for item in site["device_actions"]] == ["button:1", "button:2"]
    assert site["device_actions"][0]["action"] == {"type": "shortcut", "keys": "Ctrl+V"}
    assert site["device_actions"][1]["action"] == {
        "type": "launch", "desktop_id": app.id, "wm_classes": ["editor"], "profile_id": None}
    assert site["ring"]["id"] == "website:example.com"
    assert site["ring"]["wm_classes"] == []
    assert len(site["ring"]["slots"]) == 2
    assert site["ring"]["slots"][1]["icon"] == "/installed/editor.png"
    assert inherited["ring"] is None
    assert compile_website_profiles([profile(ring=[])], [], {})[0]["ring"]["slots"] == []


def test_saved_and_exported_profiles_roundtrip_without_changing_other_controls(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    settings = default_settings([])
    settings["launchers"][1] = "mxcc-data-2tb.desktop"
    settings["device_actions"] = [assignment(product_id=0xb042, control_id="hidpp:416",
                                             action={"type": "open_ring"})]
    settings["website_profiles"] = [profile(), profile("localhost", device_actions=[], ring=None)]
    save_settings(settings)
    assert load_settings([]) == settings
    exported = tmp_path / "export.json"
    export_bundle(exported, settings, {})
    restored, profiles = read_bundle(exported)
    assert restored == settings
    assert profiles == {}
    assert restored["device_actions"] == settings["device_actions"]
    assert restored["launchers"] == settings["launchers"]


def test_older_settings_and_exports_gain_empty_profiles_without_read_writes(monkeypatch, tmp_path):
    settings = default_settings([])
    assert settings.pop("website_profiles") == []
    assert validate_settings(settings)["website_profiles"] == []
    path = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    path.write_text(json.dumps(settings))
    before = path.read_bytes()
    assert load_settings([])["website_profiles"] == []
    assert path.read_bytes() == before
    exported = tmp_path / "legacy-export.json"
    exported.write_text(json.dumps({"version": 1, "settings": settings, "profiles": []}))
    assert read_bundle(exported)[0]["website_profiles"] == []


def test_oversized_website_settings_cannot_replace_a_readable_configuration(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    settings = default_settings([])
    save_settings(settings)
    before = path.read_bytes()
    long_action = shortcut() | {"label": "x" * 256}
    oversized = settings | {"website_profiles": [
        profile(f"site{index}.example", device_actions=[], ring=[long_action] * 7)
        for index in range(128)
    ]}
    with pytest.raises(ConfigurationError, match="256 KiB"):
        save_settings(oversized)
    assert path.read_bytes() == before
    assert load_settings([]) == settings
