"""Bounded Firefox native messaging host; forward only a hostname to the worker."""

from __future__ import annotations

import json
import re
import secrets
import signal
import struct
import sys

from . import ipc

HOST_NAME = "org.mxcreativecontrol.browser"
EXTENSION_ID = "website-shortcuts@mxcreativecontrol.local"
MAX_MESSAGE = 4096
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def valid_domain(value):
    return (isinstance(value, str) and 0 < len(value) <= 253 and value.isascii()
            and all(_LABEL.fullmatch(label) for label in value.split(".")))


def _read_exact(stream, size, *, eof=False):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            if not data and eof:
                return None
            raise ValueError("Incomplete browser message")
        data.extend(chunk)
    return bytes(data)


def read_message(stream):
    header = _read_exact(stream, 4, eof=True)
    if header is None:
        return None
    size = struct.unpack("=I", header)[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("Browser message exceeds the supported size")
    try:
        value = json.loads(_read_exact(stream, size).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Invalid browser message") from error
    if not isinstance(value, dict) or set(value) != {"domain", "focused", "window_id"}:
        raise ValueError("Browser messages must contain only a domain, focus state and window ID")
    if value["domain"] is not None and not valid_domain(value["domain"]):
        raise ValueError("Invalid website hostname")
    if type(value["focused"]) is not bool:
        raise ValueError("Invalid browser focus state")
    window_id = value["window_id"]
    if value["domain"] is None:
        if window_id is not None:
            raise ValueError("A missing website must have no browser window ID")
    elif type(window_id) is not int or not 1 <= window_id <= 2_147_483_647:
        raise ValueError("Invalid browser window ID")
    return value


def write_message(stream, value):
    payload = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(payload) > MAX_MESSAGE:
        raise ValueError("Browser response exceeds the supported size")
    stream.write(struct.pack("=I", len(payload)) + payload)
    stream.flush()


def serve(incoming, outgoing, *, request=None, session=None):
    request = ipc.request if request is None else request
    session = secrets.token_hex(16) if session is None else session
    if not re.fullmatch(r"[0-9a-f]{32}", session):
        raise ValueError("Invalid native host session")
    try:
        while True:
            try:
                message = read_message(incoming)
            except ValueError as error:
                write_message(outgoing, {"ok": False, "error": str(error)})
                return 1
            if message is None:
                return 0
            try:
                result = request("browser_context", browser="firefox", session=session,
                                 domain=message["domain"], focused=message["focused"],
                                 window_id=message["window_id"], timeout=1)
                if result.get("ok") is not True:
                    # Do not echo request values or worker internals to browser logs.
                    response = {"ok": False, "error": "Update or restart MX Creative Control to connect Firefox."}
                else:
                    response = {"ok": True}
            except (OSError, RuntimeError, ValueError):
                response = {"ok": False, "error": "Open MX Creative Control to connect Firefox."}
            write_message(outgoing, response)
    except (BrokenPipeError, OSError):
        return 1
    finally:
        try:
            request("browser_disconnect", browser="firefox", session=session, timeout=0.4)
        except (OSError, RuntimeError, ValueError):
            pass  # The worker also expires abandoned contexts after 15 seconds.


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    # Firefox supplies its native manifest path and the explicitly permitted ID.
    if arguments and (len(arguments) != 2 or arguments[1] != EXTENSION_ID):
        return 1

    def stop(_signal, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    return serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
