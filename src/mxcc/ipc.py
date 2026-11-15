"""Small, bounded client for the per-user worker socket."""

import json
import socket

from .paths import socket_path


class WorkerUnavailable(RuntimeError):
    pass


def request(command: str, *, timeout: float = 0.4, **values) -> dict:
    payload = json.dumps({"command": command, **values}).encode() + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(socket_path()))
            client.sendall(payload)
            data = bytearray()
            while b"\n" not in data:
                chunk = client.recv(4096)
                if not chunk:
                    raise WorkerUnavailable("The keypad service closed its connection.")
                data.extend(chunk)
                if len(data) > 65536:
                    raise WorkerUnavailable("The keypad service returned an oversized response.")
            result = json.loads(data.split(b"\n", 1)[0])
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
                raise ValueError("invalid response")
            return result
    except WorkerUnavailable:
        raise
    except (OSError, ValueError) as exc:
        raise WorkerUnavailable(f"Keypad service unavailable: {exc}") from exc
