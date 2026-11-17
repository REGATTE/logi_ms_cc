"""Validate the settings navigation protocol without accessing the desktop."""

import json

import pytest

from mxcc.cli import activation_request, main
from mxcc import service


def test_settings_navigation_accepts_ring_context_and_old_tray_activation():
    assert activation_request(b"activate\n") == {"ring_layout": None, "add_ring_action": False}
    expected = {"ring_layout": "editor.desktop", "add_ring_action": True}
    assert activation_request(json.dumps(expected).encode() + b"\n") == expected


@pytest.mark.parametrize("domain", ["example.com", "sub.example.com", "localhost", "xn--bcher-kva.example"])
def test_settings_navigation_accepts_exact_website_ring_context(domain, monkeypatch):
    expected = {"ring_layout": f"website:{domain}", "add_ring_action": True}
    assert activation_request(json.dumps(expected).encode() + b"\n") == expected
    calls = []
    monkeypatch.setattr("mxcc.cli.configure", lambda **kwargs: calls.append(kwargs) or 0)
    assert main(["configure", "--ring-layout", expected["ring_layout"], "--add-ring-action"]) == 0
    assert calls == [expected]


@pytest.mark.parametrize("identifier", [
    "website:", "website:EXAMPLE.com", "website:example.com/path", "website:example.com:443",
    "website:*.example.com", "website:example.com.", "website:../../escape", "website:example.com\n",
])
def test_settings_navigation_rejects_malformed_website_ring_ids(identifier, monkeypatch):
    with pytest.raises(ValueError):
        activation_request(json.dumps({"ring_layout": identifier, "add_ring_action": True}).encode())
    monkeypatch.setattr("mxcc.cli.configure", lambda **kwargs: pytest.fail("Invalid context cannot launch settings"))
    with pytest.raises(SystemExit):
        main(["configure", "--ring-layout", identifier])


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


def test_login_startup_starts_background_service_without_opening_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "start_at_login", lambda: calls.append("startup") or {"ok": True})
    monkeypatch.setattr("mxcc.cli.configure", lambda **kwargs: pytest.fail("Login startup must not open settings"))
    monkeypatch.setattr(service.ipc, "request", lambda *args, **kwargs: pytest.fail("CLI must use the startup lifecycle helper"))
    assert main(["startup"]) == 0
    assert calls == ["startup"]


def test_login_startup_failure_reports_error_without_launching_gui(monkeypatch, capsys):
    def fail():
        raise RuntimeError("Login service could not start")

    monkeypatch.setattr(service, "start_at_login", fail)
    monkeypatch.setattr("mxcc.cli.configure", lambda **kwargs: pytest.fail("Failure must not open settings"))
    assert main(["startup"]) == 1
    assert "Login service could not start" in capsys.readouterr().err
