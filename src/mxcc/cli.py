"""Command entry point; Qt is only imported for the settings window."""

import argparse
import json
import os
import sys

from . import __version__


def activation_request(raw: bytes) -> dict:
    """Bounded single-instance navigation protocol, also accepting old tray opens."""
    from .profiles import DESKTOP_PATTERN
    if len(raw) > 4096:
        raise ValueError("Settings request is too large")
    if raw.strip() == b"activate":
        return {"ring_layout": None, "add_ring_action": False}
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"ring_layout", "add_ring_action"}:
        raise ValueError("Invalid settings request")
    identifier = data["ring_layout"]
    if identifier is not None and (not isinstance(identifier, str) or
                                   identifier != "default" and not DESKTOP_PATTERN.fullmatch(identifier)):
        raise ValueError("Invalid ring application ID")
    if type(data["add_ring_action"]) is not bool or data["add_ring_action"] and identifier is None:
        raise ValueError("Add action requires a ring layout")
    return data


def configure(*, ring_layout=None, add_ring_action=False) -> int:
    from PySide6.QtCore import QElapsedTimer, QTimer
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .icons import application_icon
    from .paths import prepare_runtime_dir, socket_path

    app = QApplication(sys.argv[:1])
    app.setApplicationName("mx-creative-control")
    app.setApplicationDisplayName("MX Creative Control")
    app.setDesktopFileName("mx-creative-control")
    app.setWindowIcon(application_icon())
    socket = socket_path().with_name("settings.sock")
    try:
        prepare_runtime_dir()
    except (OSError, RuntimeError) as exc:
        QMessageBox.critical(None, "Could not open settings", str(exc))
        return 1
    client = QLocalSocket()
    client.setReadBufferSize(4097)
    client.connectToServer(str(socket))
    if client.waitForConnected(250):
        payload = json.dumps({"ring_layout": ring_layout, "add_ring_action": add_ring_action}).encode() + b"\n"
        client.write(payload)
        client.waitForBytesWritten(250)
        if ring_layout is not None:
            try:
                deadline = QElapsedTimer()
                deadline.start()
                received = bytearray()
                while b"\n" not in received:
                    received.extend(bytes(client.read(4097 - len(received))))
                    if len(received) > 4096:
                        raise ValueError("Settings response is too large")
                    if b"\n" in received:
                        break
                    remaining = 1500 - deadline.elapsed()
                    if remaining <= 0 or not client.waitForReadyRead(remaining):
                        raise ValueError("Reopen the updated settings app to configure this ring.")
                response = json.loads(received)
                if response.get("ok") is not True:
                    raise ValueError(response.get("error", "Could not open ring settings"))
            except (ValueError, AttributeError) as error:
                print(str(error), file=sys.stderr)
                return 1
        return 0
    QLocalServer.removeServer(str(socket))
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(str(socket)):
        QMessageBox.critical(None, "Could not open settings", server.errorString())
        return 1
    try:
        from .gui import MainWindow
        window = MainWindow(ring_layout=ring_layout, add_ring_action=add_ring_action)
    except (OSError, ValueError, RuntimeError) as exc:
        QMessageBox.critical(None, "Could not load configuration", str(exc))
        return 1

    connections = set()

    def activate():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.setReadBufferSize(4097)
            connections.add(connection)
            timer = QTimer(connection)
            timer.setSingleShot(True)
            timer.setInterval(1500)
            pending = bytearray()
            handled = [False]

            def receive(conn=connection, timeout=timer, buffer=pending, done=handled):
                if done[0]:
                    return
                buffer.extend(bytes(conn.read(4097 - len(buffer))))
                if b"\n" not in buffer and len(buffer) <= 4096:
                    return
                done[0] = True
                timeout.stop()
                try:
                    request = activation_request(bytes(buffer))
                    window.activate_settings(**request)
                    response = {"ok": True}
                except (ValueError, UnicodeError, RecursionError) as error:
                    response = {"ok": False, "error": str(error)}
                conn.write(json.dumps(response).encode() + b"\n")
                conn.disconnectFromServer()

            def finished(conn=connection):
                connections.discard(conn)
                conn.deleteLater()

            connection.readyRead.connect(receive)
            connection.disconnected.connect(finished)
            timer.timeout.connect(connection.disconnectFromServer)
            timer.start()
            receive()

    server.newConnection.connect(activate)
    app.setQuitOnLastWindowClosed(True)
    window.show()
    return app.exec()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Configure the MX Creative Keypad on Ubuntu")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("command", nargs="?", default="configure",
                        choices=("configure", "ring", "agent", "startup", "status", "pause", "resume", "quit"))
    parser.add_argument("--ring-layout", help="Open the Action Ring editor for a desktop ID or default")
    parser.add_argument("--add-ring-action", action="store_true", help="Open the ring's action picker")
    args = parser.parse_args(argv)
    if args.ring_layout is not None or args.add_ring_action:
        try:
            activation_request(json.dumps({"ring_layout": args.ring_layout, "add_ring_action": args.add_ring_action}).encode())
        except ValueError as error:
            parser.error(str(error))
        if args.command != "configure":
            parser.error("Ring editor options require configure")
    if args.command == "configure":
        return configure(ring_layout=args.ring_layout, add_ring_action=args.add_ring_action)
    if args.command == "ring":
        from .action_ring import run_ring
        return run_ring()
    from . import ipc, service
    try:
        if args.command == "startup":
            status = service.start_at_login()
            if not status.get("ok"):
                raise RuntimeError(status.get("error", "Could not start the background app."))
            return 0
        if args.command == "agent":
            command = service.agent_arguments()
            os.execv(command[0], command)
        command = {"quit": "shutdown", "resume": "pause"}.get(args.command, args.command)
        values = {"value": args.command == "pause"} if command == "pause" else {}
        response = ipc.request(command, **values)
        print(json.dumps(response, indent=2))
        return 0 if response.get("ok") else 1
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
