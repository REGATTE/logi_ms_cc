"""Validate the settings navigation protocol without accessing the desktop."""

import json

import pytest

from mxcc.cli import activation_request, main


def test_settings_navigation_accepts_ring_context_and_old_tray_activation():
    assert activation_request(b"activate\n") == {"ring_layout": None, "add_ring_action": False}
    expected = {"ring_layout": "editor.desktop", "add_ring_action": True}
    assert activation_request(json.dumps(expected).encode() + b"\n") == expected


@pytest.mark.parametrize("data", [
    {"ring_layout": "../../escape", "add_ring_action": True},
    {"ring_layout": None, "add_ring_action": True},
    {"ring_layout": "default", "add_ring_action": "yes"},
    {"ring_layout": "default", "add_ring_action": True, "command": "ignored"},
    [],
])
def test_settings_navigation_rejects_invalid_context(data):
    with pytest.raises(ValueError):
        activation_request(json.dumps(data).encode())
    with pytest.raises(ValueError):
        activation_request(b" " * 4097)


def test_ring_editor_cli_only_applies_to_configure(monkeypatch):
    calls = []
    monkeypatch.setattr("mxcc.cli.configure", lambda **kwargs: calls.append(kwargs) or 0)
    assert main(["configure", "--ring-layout", "default", "--add-ring-action"]) == 0
    assert calls == [{"ring_layout": "default", "add_ring_action": True}]
    with pytest.raises(SystemExit):
        main(["status", "--ring-layout", "default"])
