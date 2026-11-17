"""Read focused ChatGPT web-document metadata through Linux accessibility.

The Rust worker embeds this standalone helper and starts it with system Python.
It reads no page text, input contents, history or hidden document URLs. Only a
configured hostname and focus revision leave the process, over private pipes.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import sys
import time
import warnings
from urllib.parse import urlsplit


MAX_LINE = 65536
MAX_ANCESTORS = 48
MAX_WINDOWS = 32
HEARTBEAT_MS = 100
RESOLVE_BUDGET = 0.2


def canonical_domain(value):
    return (isinstance(value, str) and 0 < len(value) <= 253
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in value.split(".")))


def document_domain(uri):
    if not isinstance(uri, str) or len(uri) > 16384:
        return None
    try:
        value = urlsplit(uri)
        domain = (value.hostname or "").lower().removesuffix(".")
        return domain if value.scheme in ("http", "https") and canonical_domain(domain) else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Target:
    target_revision: int = 0
    window: int = 0
    pid: int = 0
    domains: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != {
                "command", "target_revision", "window", "pid", "domains"} or value["command"] != "target":
            raise ValueError("Invalid embedded-browser target")
        for name, maximum in (("target_revision", 2**64 - 1), ("window", 2**32 - 1), ("pid", 2**31 - 1)):
            if type(value[name]) is not int or not 0 <= value[name] <= maximum:
                raise ValueError("Invalid embedded-browser target identity")
        domains = value["domains"]
        if (not isinstance(domains, list) or len(domains) > 128
                or not all(canonical_domain(domain) for domain in domains)
                or len(set(domains)) != len(domains)
                or bool(value["window"]) != bool(value["pid"])):
            raise ValueError("Invalid embedded-browser website list")
        return cls(value["target_revision"], value["window"], value["pid"], tuple(domains))


@dataclass
class DocumentFocus:
    source: object
    document: object
    frame: object
    domain: str


class FocusTracker:
    """A small dependency-injected state machine; fake metadata is used in tests."""

    def __init__(self, adapter, publish):
        self.adapter = adapter
        self.publish = publish
        self.target = Target()
        self.revision = 0
        self.source = None
        self.current = None
        self.ready = False
        self.error = None

    def emit(self):
        self.publish({"target_revision": self.target.target_revision,
                      "window": self.target.window, "pid": self.target.pid,
                      "revision": self.revision,
                      "domain": self.current.domain if self.current else None,
                      "ready": self.ready, "error": self.error})

    def clear(self, *, forget=True):
        self.revision += 1
        self.current = None
        if forget:
            self.source = None

    def set_target(self, target):
        if target.target_revision <= self.target.target_revision and target != self.target:
            raise ValueError("Embedded-browser target revision did not advance")
        if target == self.target:
            return
        self.target = target
        self.clear()
        self.ready = False
        self.error = None
        self.emit()  # Invalidate before registration, which may make bounded D-Bus calls.
        self.connect()

    def connect(self):
        try:
            self.ready = self.adapter.connect(self.target, self.on_focus, self.on_invalidated)
            self.error = None if self.ready or not self.target.window else (
                "ChatGPT accessibility is not ready. Click the video viewer to retry.")
        except Exception:
            self.ready = False
            self.error = "ChatGPT website detection is unavailable through Linux accessibility."
        self.emit()
        source = getattr(self.adapter, "last_source", None)
        if self.ready and self.target.window and source is not None:
            self.on_focus(source, True)

    def on_focus(self, source, focused):
        # Ignore late focus-loss notifications for an earlier element. A current
        # source is also checked afresh on each heartbeat, without a state cache.
        if not focused:
            if self.source == source:
                self.clear()
                self.emit()
            return
        self.clear()
        self.emit()  # Stop old shortcuts before any potentially slow metadata read.
        self.source = source
        self.refresh()

    def on_invalidated(self):
        self.clear()
        self.emit()

    def refresh(self):
        previous = self.current
        try:
            current = self.adapter.resolve(self.source, self.target) if self.source is not None else None
        except Exception:
            current = None
        if current is None:
            if self.source is not None or previous is not None:
                self.clear()
        else:
            if previous != current:
                self.revision += 1
            self.current = current
        self.emit()


class AtspiAdapter:
    def __init__(self, atspi):
        self.api = atspi
        self.app = None
        self.pid = 0
        self.listeners = []
        self.last_source = None

    def disconnect(self):
        for listener, event_type in self.listeners:
            try:
                listener.deregister(event_type)
            except Exception:
                pass
        self.listeners.clear()
        self.app = None
        self.pid = 0
        self.last_source = None

    def state(self, node, name):
        # Clear just this object's cached metadata. Disabling the entire app's
        # cache stalls AT-SPI dispatch with Ubuntu 24.04's Chromium bridge.
        node.clear_cache_single()
        return node.get_state_set().contains(getattr(self.api.StateType, name))

    def connect(self, target, on_focus, on_invalidated):
        if not target.window or not target.domains:
            # Keep only the last focus object's identity while another app is
            # active. No URI is read then; returning revalidates all metadata.
            return True
        if self.app is not None and self.pid == target.pid and len(self.listeners) == 4:
            try:
                if not self.state(self.app, "DEFUNCT"):
                    return True
            except Exception:
                pass
        self.disconnect()
        desktop = self.api.get_desktop(0)
        count = desktop.get_child_count()
        if not 0 <= count <= 256:
            return False
        for index in range(count):
            candidate = desktop.get_child_at_index(index)
            if candidate and candidate.get_process_id() == target.pid:
                self.app = candidate
                break
        if self.app is None:
            return False
        self.pid = target.pid
        # Chromium enables its normal web accessibility lazily when extended
        # metadata is requested. Discard these values; no UI text is needed.
        self.app.get_attributes()
        for frame in self.active_frames():
            frame.get_attributes()

        def focus_event(event, *_):
            if event.detail1:
                self.last_source = event.source
            elif self.last_source == event.source:
                self.last_source = None
            on_focus(event.source, bool(event.detail1))

        def invalidate_event(event, *_):
            if not event.type.startswith("window:"):
                self.last_source = None
            on_invalidated()

        for event_type, callback in (
                ("object:state-changed:focused", focus_event),
                ("window:deactivate", invalidate_event),
                ("document:load-complete", invalidate_event),
                ("document:reload", invalidate_event)):
            listener = self.api.EventListener.new(callback, None)
            if not listener.register_with_app(event_type, [], self.app):
                self.disconnect()
                return False
            self.listeners.append((listener, event_type))
        return True

    def active_frames(self):
        if self.app is None:
            return []
        count = self.app.get_child_count()
        if not 0 <= count <= MAX_WINDOWS:
            return []
        frames = []
        for index in range(count):
            frame = self.app.get_child_at_index(index)
            if (frame and self.state(frame, "ACTIVE") and self.state(frame, "SHOWING")
                    and not self.state(frame, "DEFUNCT")):
                frames.append(frame)
        return frames

    def resolve(self, source, target):
        started = time.monotonic()
        if (not source or not self.app or not target.window or target.pid != self.pid
                or source.get_application() != self.app
                or self.app.get_process_id() != target.pid
                or not self.state(source, "FOCUSED") or not self.state(source, "SHOWING")
                or self.state(source, "DEFUNCT") or self.state(source, "EDITABLE")):
            return None
        active = self.active_frames()
        if len(active) != 1:
            return None
        ancestors = []
        node = source
        for _ in range(MAX_ANCESTORS):
            if not node or node in ancestors or time.monotonic() - started > RESOLVE_BUDGET:
                return None
            ancestors.append(node)
            if node == active[0]:
                break
            node = node.get_parent()
        else:
            return None
        if ancestors[-1] != active[0]:
            return None
        # Read a URI only after proving that this is the focused source in the
        # unique active window of the target app. Never enumerate document URLs.
        for node in ancestors:
            document = node.get_document_iface()
            if document is not None:
                # Ubuntu 24.04's GI binding exposes only the older method name.
                read_attribute = getattr(document, "get_attribute_value", None)
                if read_attribute is None:
                    read_attribute = document.get_document_attribute_value
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    domain = document_domain(read_attribute("URI"))
                if domain not in target.domains:
                    return None
                # Focus may change during synchronous metadata reads. Do not
                # timestamp an old observation as fresh after a slow response.
                if (not self.state(source, "FOCUSED") or not self.state(source, "SHOWING")
                        or self.state(source, "DEFUNCT") or self.state(source, "EDITABLE")
                        or self.active_frames() != active
                        or time.monotonic() - started > RESOLVE_BUDGET):
                    return None
                return DocumentFocus(source, node, active[0], domain)
        return None


def main():
    def publish(value):
        print(json.dumps(value, separators=(",", ":")), flush=True)

    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi, GLib
    except (ImportError, ValueError):
        publish({"target_revision": 0, "window": 0, "pid": 0, "revision": 0,
                 "domain": None, "ready": False,
                 "error": "Install python3-gi and gir1.2-atspi-2.0 for ChatGPT website shortcuts."})
        return 1
    Atspi.set_timeout(100, 100)
    Atspi.init()
    Atspi.set_main_context(GLib.MainContext.default())
    loop = GLib.MainLoop()
    adapter = AtspiAdapter(Atspi)
    tracker = FocusTracker(adapter, publish)
    pending = bytearray()
    os.set_blocking(sys.stdin.fileno(), False)
    last_connect = [0.0]

    def stop():
        tracker.clear()
        tracker.ready = False
        tracker.emit()
        adapter.disconnect()
        loop.quit()
        return False

    def read_commands(_fd, condition):
        try:
            data = os.read(sys.stdin.fileno(), MAX_LINE + 1)
            if not data:
                return stop()
            pending.extend(data)
            if len(pending) > MAX_LINE:
                raise ValueError("Oversized target")
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending[:] = rest
                value = json.loads(line)
                if value == {"command": "stop"}:
                    return stop()
                tracker.set_target(Target.parse(value))
        except BlockingIOError:
            if condition & (GLib.IO_HUP | GLib.IO_ERR):
                return stop()
        except (ValueError, OSError, RecursionError):
            return stop()
        return True

    def heartbeat():
        if tracker.target.window and not tracker.ready and time.monotonic() - last_connect[0] >= 2:
            last_connect[0] = time.monotonic()
            tracker.connect()
        tracker.refresh()
        return True

    GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, read_commands)
    GLib.timeout_add(HEARTBEAT_MS, heartbeat)
    tracker.emit()
    try:
        loop.run()
    finally:
        adapter.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
