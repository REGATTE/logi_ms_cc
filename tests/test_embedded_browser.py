"""Focused document detection uses fake metadata, never the desktop or a browser."""

from types import SimpleNamespace
import json

import pytest

from mxcc.embedded_browser import (
    AtspiAdapter, DocumentFocus, FocusTracker, Target, document_domain, enable_linux_accessibility,
)


class Node:
    def __init__(self, parent=None, *, flags=(), uri=None, pid=81):
        self.parent = parent
        self.flags = set(flags)
        self.uri = uri
        self.pid = pid
        self.children = []
        self.uri_reads = 0
        self.attribute_reads = 0
        self.cache_mask = None
        self.cache_clears = 0
        self.document_attribute = "URI"
        if parent is not None:
            parent.children.append(self)

    def get_state_set(self):
        return SimpleNamespace(contains=lambda value: value in self.flags)

    def get_application(self):
        current = self
        seen = set()
        while current.parent is not None and current not in seen:
            seen.add(current)
            current = current.parent
        return current

    def get_process_id(self):
        return self.pid

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]

    def get_parent(self):
        return self.parent

    def get_document_iface(self):
        return self if self.uri is not None else None

    def get_attribute_value(self, attribute):
        assert attribute == self.document_attribute
        self.uri_reads += 1
        return self.uri

    def get_attributes(self):
        self.attribute_reads += 1
        return {"unrelated-metadata": "must never be published"}

    def set_cache_mask(self, value):
        self.cache_mask = value

    def clear_cache_single(self):
        self.cache_clears += 1


def tree(uri="http://10.64.192.14:8800/#/batch?private=value"):
    app = Node()
    frame = Node(app, flags=("ACTIVE", "SHOWING"))
    document = Node(frame, uri=uri)
    source = Node(document, flags=("FOCUSED", "SHOWING"))
    api = SimpleNamespace(StateType=SimpleNamespace(**{name: name for name in
        ("ACTIVE", "FOCUSED", "SHOWING", "DEFUNCT", "EDITABLE")}))
    adapter = AtspiAdapter(api)
    adapter.app, adapter.pid = app, app.pid
    return adapter, app, frame, document, source


TARGET = Target(1, 42, 81, ("10.64.192.14", "example.com"), "chatgpt")


@pytest.mark.parametrize("uri,expected", [
    ("http://10.64.192.14:8800/#/batch?private=yes", "10.64.192.14"),
    ("https://EXAMPLE.COM./private", "example.com"),
    ("app://chat/editor", None), ("about:blank", None),
    ("file:///home/user/private", None), ("https://[invalid", None),
    ("https://example.com..", None), ("https://ß.de", None),
    ("https://example.com/" + "x" * 16384, None), (None, None),
])
def test_extracts_only_a_canonical_http_hostname(uri, expected):
    assert document_domain(uri) == expected


def test_target_messages_are_bounded_and_identified():
    value = {"command": "target", "target_revision": 3, "window": 42, "pid": 81,
             "domains": ["10.64.192.14", "example.com"], "browser": "chatgpt"}
    assert Target.parse(value) == Target(3, 42, 81, TARGET.domains, "chatgpt")
    for change in ({"window": True}, {"pid": 0}, {"target_revision": -1},
                   {"domains": ["example.com"] * 129}, {"domains": ["a.com", "a.com"]},
                   {"domains": ["http://example.com"]}, {"domains": "example.com"},
                   {"extra": 1}, {"browser": "chrome"}, {"browser": ""}):
        with pytest.raises(ValueError):
            Target.parse(value | change)


def test_reads_only_focused_document_hostname_after_ownership_checks():
    adapter, app, frame, document, source = tree()
    result = adapter.resolve(source, TARGET)
    assert result == DocumentFocus(source, document, frame, "10.64.192.14")
    assert document.uri_reads == 1


@pytest.mark.parametrize("flag", ["EDITABLE", "DEFUNCT"])
def test_editable_or_defunct_focus_does_not_read_document_uri(flag):
    adapter, _, _, document, source = tree()
    source.flags.add(flag)
    assert adapter.resolve(source, TARGET) is None
    assert document.uri_reads == 0


@pytest.mark.parametrize("flag", ["FOCUSED", "SHOWING"])
def test_unfocused_or_hidden_source_does_not_read_document_uri(flag):
    adapter, _, _, document, source = tree()
    source.flags.remove(flag)
    assert adapter.resolve(source, TARGET) is None
    assert document.uri_reads == 0


def test_wrong_process_window_or_ambiguous_active_frames_do_not_read_uri():
    adapter, app, frame, document, source = tree()
    for target in (Target(1, 42, 99, TARGET.domains), Target(1, 0, 0, TARGET.domains)):
        assert adapter.resolve(source, target) is None
    other = Node(app, flags=("ACTIVE", "SHOWING"))
    assert adapter.resolve(source, TARGET) is None
    frame.flags.remove("ACTIVE")
    assert adapter.resolve(source, TARGET) is None
    assert document.uri_reads == 0


def test_nearest_document_wins_and_unconfigured_sites_stay_normal():
    adapter, _, _, outer, source = tree()
    inner = Node(outer, uri="https://other.example/video")
    source.parent = inner
    assert adapter.resolve(source, TARGET) is None
    assert inner.uri_reads == 1
    assert outer.uri_reads == 0


def test_chat_document_is_not_a_website_profile():
    adapter, _, _, document, source = tree("app://chat/index.html")
    assert adapter.resolve(source, TARGET) is None
    assert document.uri_reads == 1


def test_ubuntu_gi_document_attribute_method_name():
    adapter, _, _, document, source = tree()
    document.get_document_iface = lambda: SimpleNamespace(
        get_document_attribute_value=document.get_attribute_value)
    assert adapter.resolve(source, TARGET).domain == "10.64.192.14"


def test_focus_loss_during_document_read_cannot_be_published_as_fresh():
    adapter, _, _, document, source = tree()
    read = document.get_attribute_value
    def moved_focus(attribute):
        source.flags.remove("FOCUSED")
        return read(attribute)
    document.get_attribute_value = moved_focus
    assert adapter.resolve(source, TARGET) is None


def test_slow_metadata_read_is_not_published_with_a_fresh_timestamp(monkeypatch):
    adapter, _, _, document, source = tree()
    clock = [0.0]
    monkeypatch.setattr("mxcc.embedded_browser.time.monotonic", lambda: clock[0])
    read = document.get_attribute_value
    def slow(attribute):
        clock[0] += 0.21
        return read(attribute)
    document.get_attribute_value = slow
    assert adapter.resolve(source, TARGET) is None


def test_accessibility_cycle_is_bounded_before_uri_read():
    adapter, _, _, document, source = tree()
    # Preserve claimed application ownership to exercise the ancestor guard.
    source.get_application = lambda: adapter.app
    document.parent = source
    assert adapter.resolve(source, TARGET) is None
    assert document.uri_reads == 0


def tracker_fixture():
    adapter, app, frame, document, source = tree()
    adapter.connect = lambda *_: True
    reports = []
    tracker = FocusTracker(adapter, reports.append)
    tracker.set_target(TARGET)
    return tracker, reports, adapter, document, source


def test_new_focus_invalidates_before_resolving_and_keeps_only_hostname():
    tracker, reports, adapter, document, source = tracker_fixture()
    tracker.on_focus(source, True)
    assert reports[-1]["domain"] == "10.64.192.14"
    previous_revision = reports[-1]["revision"]
    other = Node(document, flags=("FOCUSED", "SHOWING"))
    resolve = adapter.resolve

    def inspect(*args):
        assert reports[-1]["domain"] is None
        assert reports[-1]["revision"] > previous_revision
        return resolve(*args)

    adapter.resolve = inspect
    tracker.on_focus(other, True)
    assert reports[-1]["domain"] == "10.64.192.14"
    serialized = json.dumps(reports)
    assert "private" not in serialized and "8800" not in serialized
    assert "must never" not in serialized
    for before, after in zip(reports, reports[1:]):
        if before["domain"] != after["domain"]:
            assert after["revision"] > before["revision"]


def test_heartbeat_rechecks_focus_and_cannot_revive_it_without_a_new_event():
    tracker, reports, _, _, source = tracker_fixture()
    tracker.on_focus(source, True)
    saved = dict(reports[-1])
    tracker.refresh()
    assert reports[-1] == saved
    source.flags.remove("FOCUSED")
    tracker.refresh()
    assert reports[-1]["domain"] is None
    source.flags.add("FOCUSED")
    tracker.refresh()
    assert reports[-1]["domain"] is None
    tracker.on_focus(source, True)
    assert reports[-1]["domain"] == "10.64.192.14"
    assert reports[-1]["revision"] > saved["revision"]


def test_document_change_or_reload_changes_revision_and_drops_old_context():
    tracker, reports, _, document, source = tracker_fixture()
    tracker.on_focus(source, True)
    saved = reports[-1]["revision"]
    document.uri = "https://example.com/"
    tracker.refresh()
    assert reports[-1]["domain"] == "example.com"
    assert reports[-1]["revision"] > saved
    tracker.on_invalidated()
    assert reports[-1]["domain"] is None
    tracker.refresh()
    assert reports[-1]["domain"] is None


def test_target_return_uses_new_epoch_and_never_retains_old_document():
    tracker, reports, _, _, source = tracker_fixture()
    tracker.on_focus(source, True)
    tracker.set_target(Target(2, 0, 0, ()))
    tracker.set_target(Target(3, 42, 81, TARGET.domains, "chatgpt"))
    assert reports[-1]["target_revision"] == 3
    assert reports[-1]["domain"] is None
    with pytest.raises(ValueError):
        tracker.set_target(TARGET)


def test_delayed_focus_loss_does_not_replace_new_source():
    tracker, reports, _, document, source = tracker_fixture()
    other = Node(document, flags=("FOCUSED", "SHOWING"))
    tracker.on_focus(source, True)
    tracker.on_focus(other, True)
    tracker.on_focus(source, False)
    assert reports[-1]["domain"] == "10.64.192.14"
    tracker.on_focus(other, False)
    assert reports[-1]["domain"] is None


def test_transport_error_discards_source_and_context():
    tracker, reports, adapter, _, source = tracker_fixture()
    tracker.on_focus(source, True)
    def fail(*_):
        raise RuntimeError("D-Bus disappeared")
    adapter.resolve = fail
    tracker.refresh()
    assert reports[-1]["domain"] is None
    assert tracker.source is None


def test_registration_uses_uncached_metadata_and_only_target_app_events():
    adapter, app, frame, _, _ = tree()
    desktop = Node()
    desktop.children = [Node(pid=99), app]
    calls = []
    class Listener:
        def register_with_app(self, kind, properties, target):
            calls.append((kind, properties, target))
            return True
        def deregister(self, kind):
            calls.append(("stop", kind))
    adapter.api.Cache = SimpleNamespace(NONE="none")
    adapter.api.get_desktop = lambda _: desktop
    adapter.api.EventListener = SimpleNamespace(new=lambda *_: Listener())
    assert adapter.connect(TARGET, lambda *_: None, lambda: None)
    assert app.cache_mask is None  # Global Cache.NONE stalls this platform's event loop.
    assert frame.cache_clears > 0
    assert app.attribute_reads == 1 and frame.attribute_reads == 1
    assert all(call[1:] == ([], app) for call in calls)
    assert not any("key" in call[0] or "text" in call[0] for call in calls)
    adapter.disconnect()
    assert len([call for call in calls if call[0] == "stop"]) == 4


def test_return_to_chatgpt_revalidates_retained_focus_and_reload_forgets_it():
    adapter, app, _, document, source = tree()
    desktop = Node()
    desktop.children = [app]
    callbacks = {}
    class Listener:
        def __init__(self, callback):
            self.callback = callback
        def register_with_app(self, kind, _properties, _target):
            callbacks[kind] = self.callback
            return True
        def deregister(self, kind):
            callbacks.pop(kind, None)
    adapter.api.Cache = SimpleNamespace(NONE="none")
    adapter.api.get_desktop = lambda _: desktop
    adapter.api.EventListener = SimpleNamespace(new=lambda callback, _: Listener(callback))
    reports = []
    tracker = FocusTracker(adapter, reports.append)
    tracker.set_target(TARGET)
    event = SimpleNamespace(source=source, detail1=1)
    callbacks["object:state-changed:focused"](event)
    assert reports[-1]["domain"] == "10.64.192.14"
    original_revision = reports[-1]["revision"]
    tracker.set_target(Target(2, 0, 0, ()))
    reads = document.uri_reads
    callbacks["object:state-changed:focused"](event)
    tracker.refresh()
    assert document.uri_reads == reads
    assert reports[-1]["domain"] is None
    tracker.set_target(Target(3, 42, 81, TARGET.domains, "chatgpt"))
    assert reports[-1]["domain"] == "10.64.192.14"
    assert reports[-1]["revision"] > original_revision
    callbacks["document:reload"](SimpleNamespace(type="document:reload"))
    tracker.set_target(Target(4, 0, 0, ()))
    tracker.set_target(Target(5, 42, 81, TARGET.domains))
    assert reports[-1]["domain"] is None
    callbacks["object:state-changed:focused"](event)
    tracker.set_target(Target(6, 0, 0, ()))
    source.flags.remove("FOCUSED")
    tracker.set_target(Target(7, 42, 81, TARGET.domains))
    assert reports[-1]["domain"] is None


FIREFOX_TARGET = Target(1, 42, 81, TARGET.domains, "firefox")
BROWSER_TARGET = Target(1, 42, 81, TARGET.domains, "browser")


def test_firefox_uses_docurl_and_never_borrows_chromium_uri():
    adapter, _, _, document, source = tree()
    document.document_attribute = "DocURL"
    result = adapter.resolve(source, FIREFOX_TARGET)
    assert result.domain == "10.64.192.14"
    assert document.uri_reads == 1


def firefox_collection(adapter, frame, found):
    api = adapter.api
    api.CollectionMatchType = SimpleNamespace(ALL="all")
    api.CollectionSortOrder = SimpleNamespace(CANONICAL="canonical")
    api.StateSet = SimpleNamespace(new=lambda states: states)
    api.MatchRule = SimpleNamespace(new=lambda *args: args)
    def matches(rule, order, limit, traverse):
        assert rule[0] == ["FOCUSED", "SHOWING"]
        assert order == "canonical" and limit == 2 and traverse
        return found
    frame.get_collection_iface = lambda: SimpleNamespace(get_matches=matches)


def test_firefox_startup_recovers_only_unique_focused_source_without_reading_urls():
    adapter, _, frame, document, source = tree()
    found = [source]
    firefox_collection(adapter, frame, found)
    assert adapter.focused_source() is source
    assert document.uri_reads == 0
    found.append(Node(frame, flags=("FOCUSED", "SHOWING")))
    assert adapter.focused_source() is None
    assert document.uri_reads == 0


def test_startup_focus_query_rejects_window_switch_and_slow_results(monkeypatch):
    adapter, app, frame, document, source = tree()
    firefox_collection(adapter, frame, [source])
    collection = frame.get_collection_iface()
    def changed(*_):
        frame.flags.remove("ACTIVE")
        return [source]
    collection.get_matches = changed
    frame.get_collection_iface = lambda: collection
    assert adapter.focused_source() is None
    frame.flags.add("ACTIVE")
    clock = [0.0]
    monkeypatch.setattr("mxcc.embedded_browser.time.monotonic", lambda: clock[0])
    def slow(*_):
        clock[0] += 0.21
        return [source]
    collection.get_matches = slow
    assert adapter.focused_source() is None
    assert document.uri_reads == 0


@pytest.mark.parametrize("browser,attribute", [("firefox", "DocURL"), ("browser", "URI")])
def test_browser_connection_enables_bridge_and_recovers_after_browser_restart(browser, attribute):
    adapter, app, frame, document, source = tree()
    document.document_attribute = attribute
    firefox_collection(adapter, frame, [source])
    desktop = Node()
    desktop.children = [app]
    adapter.api.get_desktop = lambda _: desktop
    class Listener:
        def register_with_app(self, *_): return True
        def deregister(self, *_): pass
    adapter.api.EventListener = SimpleNamespace(new=lambda *_: Listener())
    enabled = []
    adapter.enable_accessibility = lambda: enabled.append(True)
    reports = []
    tracker = FocusTracker(adapter, reports.append)
    tracker.set_target(Target(1, 42, 81, TARGET.domains, browser))
    assert enabled == [True]
    assert reports[-1]["domain"] == "10.64.192.14"
    # New PID and window: no old node or document may survive the transition.
    app.pid = 82
    target = Target(2, 43, 82, TARGET.domains, browser)
    tracker.set_target(target)
    assert reports[-1]["pid"] == 82 and reports[-1]["window"] == 43
    assert reports[-1]["domain"] == "10.64.192.14"
    tracker.set_target(Target(3))
    assert enabled == [True, True]
    assert reports[-1]["domain"] is None


def test_standard_accessibility_enablement_does_not_enable_screen_reader():
    calls = []
    enabled = [False]
    class Variant:
        def __init__(self, signature, value): self.value = value
        def unpack(self): return self.value
    def call(*args):
        calls.append(args)
        if args[3] == "Get":
            assert args[4].value == ("org.a11y.Status", "IsEnabled")
            return Variant("(v)", (enabled[0],))
        assert args[3] == "Set"
        interface, prop, value = args[4].value
        assert (interface, prop, value.value) == ("org.a11y.Status", "IsEnabled", True)
        enabled[0] = True
    gio = SimpleNamespace(BusType=SimpleNamespace(SESSION="session"),
                          DBusCallFlags=SimpleNamespace(NONE=0),
                          bus_get_sync=lambda *_: SimpleNamespace(call_sync=call))
    glib = SimpleNamespace(Variant=Variant)
    enable_linux_accessibility(gio, glib)
    enable_linux_accessibility(gio, glib)
    assert [call[3] for call in calls] == ["Get", "Set", "Get"]
    assert all(call[-2] == 200 for call in calls)


@pytest.mark.parametrize("browser,attribute", [("firefox", "DocURL"), ("browser", "URI")])
def test_browsers_recover_after_background_load_and_window_return(monkeypatch, browser, attribute):
    adapter, _, frame, document, source = tree()
    document.document_attribute = attribute
    firefox_collection(adapter, frame, [source])
    adapter.connect = lambda *_: True
    clock = [0.0]
    monkeypatch.setattr("mxcc.embedded_browser.time.monotonic", lambda: clock[0])
    reports = []
    tracker = FocusTracker(adapter, reports.append)
    tracker.set_target(Target(1, 42, 81, TARGET.domains, browser))
    tracker.refresh()
    assert reports[-1]["domain"] == "10.64.192.14"
    tracker.on_invalidated()  # A background document load does not emit focus.
    assert reports[-1]["domain"] is None
    clock[0] = 0.6
    tracker.refresh()
    assert reports[-1]["domain"] == "10.64.192.14"
    tracker.set_target(Target(2))
    tracker.set_target(Target(3, 43, 81, TARGET.domains, browser))
    tracker.refresh()
    assert reports[-1]["window"] == 43 and reports[-1]["domain"] == "10.64.192.14"


@pytest.mark.parametrize("browser,attribute", [("firefox", "DocURL"), ("browser", "URI")])
def test_browser_focus_recovery_is_rate_limited_and_ignores_editable_focus(monkeypatch, browser, attribute):
    adapter, _, frame, document, source = tree()
    source.flags.add("EDITABLE")
    document.document_attribute = attribute
    firefox_collection(adapter, frame, [source])
    adapter.connect = lambda *_: True
    calls = []
    discover = adapter.focused_source
    def counted():
        calls.append(True)
        return discover()
    adapter.focused_source = counted
    clock = [0.0]
    monkeypatch.setattr("mxcc.embedded_browser.time.monotonic", lambda: clock[0])
    reports = []
    tracker = FocusTracker(adapter, reports.append)
    tracker.set_target(Target(1, 42, 81, TARGET.domains, browser))
    for now in (0.0, 0.1, 0.2, 0.6):
        clock[0] = now
        tracker.refresh()
    assert len(calls) == 2
    assert document.uri_reads == 0
    assert all(report["domain"] is None for report in reports)


def test_generic_browser_protocol_uses_worker_owned_browser_kind():
    value = {"command": "target", "target_revision": 1, "window": 42, "pid": 81,
             "domains": list(TARGET.domains), "browser": "browser"}
    assert Target.parse(value) == BROWSER_TARGET


@pytest.mark.parametrize("missing", [None, ""])
def test_generic_browser_accepts_gecko_docurl_only_when_uri_is_absent(missing):
    adapter, _, _, document, source = tree()
    calls = []
    def read(attribute):
        calls.append(attribute)
        return missing if attribute == "URI" else document.uri
    document.get_attribute_value = read
    assert adapter.resolve(source, BROWSER_TARGET).domain == "10.64.192.14"
    assert calls == ["URI", "DocURL"]


@pytest.mark.parametrize("uri", [
    "app://browser/index.html", "about:blank", "https://unconfigured.example/",
    "https://[invalid", "http://example.com..", 42,
])
def test_generic_browser_does_not_override_a_present_uri_with_docurl(uri):
    adapter, _, _, document, source = tree()
    calls = []
    def read(attribute):
        calls.append(attribute)
        return uri if attribute == "URI" else document.uri
    document.get_attribute_value = read
    assert adapter.resolve(source, BROWSER_TARGET) is None
    assert calls == ["URI"]


def test_generic_browser_does_not_read_outer_document_after_missing_inner_attributes():
    adapter, _, _, outer, source = tree()
    inner = Node(outer, uri="")
    calls = []
    inner.get_attribute_value = lambda attribute: calls.append(attribute) or ""
    source.parent = inner
    assert adapter.resolve(source, BROWSER_TARGET) is None
    assert calls == ["URI", "DocURL"]
    assert outer.uri_reads == 0


def test_generic_browser_metadata_failure_does_not_try_alternate_attribute():
    adapter, _, _, document, source = tree()
    calls = []
    def read(attribute):
        calls.append(attribute)
        raise RuntimeError("Provider disappeared")
    document.get_attribute_value = read
    with pytest.raises(RuntimeError):
        adapter.resolve(source, BROWSER_TARGET)
    assert calls == ["URI"]


def test_generic_attribute_fallback_stops_at_resolution_budget(monkeypatch):
    adapter, _, _, document, source = tree()
    clock = [0.0]
    monkeypatch.setattr("mxcc.embedded_browser.time.monotonic", lambda: clock[0])
    calls = []
    def read(attribute):
        calls.append(attribute)
        clock[0] += 0.21
        return ""
    document.get_attribute_value = read
    assert adapter.resolve(source, BROWSER_TARGET) is None
    assert calls == ["URI"]
