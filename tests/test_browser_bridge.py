"""Native messaging boundaries; these tests never contact a browser or worker."""

import io
import json
import struct
from unittest.mock import Mock

import pytest

from mxcc import browser_bridge as bridge


def frame(value):
    data = json.dumps(value).encode("utf-8")
    return struct.pack("=I", len(data)) + data


def responses(data):
    incoming = io.BytesIO(data)
    result = []
    while header := incoming.read(4):
        result.append(json.loads(incoming.read(struct.unpack("=I", header)[0])))
    return result


class Chunked(io.BytesIO):
    def read(self, count=-1):
        return super().read(min(count, 2))


def test_partial_reads_forward_only_hostname_focus_and_host_generated_identity():
    output = io.BytesIO()
    request = Mock(return_value={"ok": True})
    session = "a" * 32
    incoming = Chunked(frame({"domain": "example.com", "focused": True, "window_id": 7})
                       + frame({"domain": None, "focused": False, "window_id": None}))
    assert bridge.serve(incoming, output, request=request, session=session) == 0
    assert responses(output.getvalue()) == [{"ok": True}, {"ok": True}]
    assert request.call_args_list[0].kwargs == {
        "browser": "firefox", "session": session, "domain": "example.com",
        "focused": True, "window_id": 7, "timeout": 1,
    }
    assert request.call_args_list[-1].kwargs == {
        "browser": "firefox", "session": session, "timeout": 0.4,
    }
    assert request.call_args_list[-1].args == ("browser_disconnect",)


@pytest.mark.parametrize("domain", ["example.com", "localhost", "127.0.0.1", "xn--bcher-kva.example"])
def test_canonical_domains(domain):
    assert bridge.read_message(io.BytesIO(frame({"domain": domain, "focused": True, "window_id": 7})))["domain"] == domain


@pytest.mark.parametrize("domain", ["", "Example.com", "https://example.com/private", "example.com:8080",
    "*.example.com", "example.com.", "example..com", "-example.com", "example-.com", "a"*64+".com",
    "bücher.example", "example.com\n", "[::1]", 42, True, ["example.com"]])
def test_domains_cannot_smuggle_urls_or_other_fields(domain):
    with pytest.raises(ValueError):
        bridge.read_message(io.BytesIO(frame({"domain": domain, "focused": True, "window_id": 7})))


@pytest.mark.parametrize("value", [None, [], {}, {"domain": "example.com"},
    {"domain": "example.com", "focused": 1},
    {"domain": "example.com", "focused": True, "session": "forged"},
    {"domain": "example.com", "focused": True, "url": "https://example.com/secret"}])
def test_strict_payload(value):
    with pytest.raises(ValueError):
        bridge.read_message(io.BytesIO(frame(value)))


@pytest.mark.parametrize("window_id", [None, True, False, 0, -1, 2_147_483_648, 1.5, "7", []])
def test_present_website_requires_positive_integer_window_id(window_id):
    with pytest.raises(ValueError, match="window ID"):
        bridge.read_message(io.BytesIO(frame({"domain": "example.com", "focused": True,
                                              "window_id": window_id})))


def test_missing_website_requires_null_window_id():
    with pytest.raises(ValueError, match="window ID"):
        bridge.read_message(io.BytesIO(frame({"domain": None, "focused": False, "window_id": 7})))


@pytest.mark.parametrize("data", [b"\x01", struct.pack("=I", 0), struct.pack("=I", 4097),
    struct.pack("=I", 5) + b"{}", struct.pack("=I", 2) + b"\xff\xff"])
def test_malformed_framing_clears_existing_session(data):
    output, request = io.BytesIO(), Mock(return_value={"ok": True})
    assert bridge.serve(io.BytesIO(data), output, request=request) == 1
    assert responses(output.getvalue())[0]["ok"] is False
    assert request.call_count == 1 and request.call_args.args == ("browser_disconnect",)


def test_worker_restart_can_recover_on_next_heartbeat_without_browser_reload():
    output = io.BytesIO()
    request = Mock(side_effect=[bridge.ipc.WorkerUnavailable("unavailable"), {"ok": True}, {"ok": True}])
    message = frame({"domain": "example.com", "focused": True, "window_id": 7})
    assert bridge.serve(io.BytesIO(message * 2), output, request=request) == 0
    result = responses(output.getvalue())
    assert result[0]["ok"] is False and result[1] == {"ok": True}
    assert "example.com" not in output.getvalue().decode("utf-8")


def test_untrusted_worker_error_is_not_echoed():
    output = io.BytesIO()
    request = Mock(return_value={"ok": False, "error": "secret data from a URL"})
    bridge.serve(io.BytesIO(frame({"domain": "example.com", "focused": False, "window_id": 7})), output, request=request)
    assert b"secret" not in output.getvalue()


def test_wrong_extension_cannot_enter_native_host_loop(monkeypatch):
    serve = Mock()
    monkeypatch.setattr(bridge, "serve", serve)
    assert bridge.main(["/tmp/manifest.json", "other@example.com"]) == 1
    serve.assert_not_called()
