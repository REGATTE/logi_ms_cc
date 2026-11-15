"""Command entry point; Qt is only imported for the settings window."""

import argparse
import json
import os
import sys

from . import __version__


def configure() -> int:
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
    client.connectToServer(str(socket))
    if client.waitForConnected(250):
        client.write(b"activate\n")
        client.waitForBytesWritten(250)
        return 0
    QLocalServer.removeServer(str(socket))
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(str(socket)):
        QMessageBox.critical(None, "Could not open settings", server.errorString())
        return 1
    try:
        from .gui import MainWindow
        window = MainWindow()
    except (OSError, ValueError, RuntimeError) as exc:
        QMessageBox.critical(None, "Could not load configuration", str(exc))
        return 1

    def activate():
        connection = server.nextPendingConnection()
        if connection:
            connection.disconnectFromServer()
            connection.deleteLater()
        window.activate_settings()

    server.newConnection.connect(activate)
    app.setQuitOnLastWindowClosed(True)
    window.show()
    return app.exec()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Configure the MX Creative Keypad on Ubuntu")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("command", nargs="?", default="configure",
                        choices=("configure", "agent", "status", "pause", "resume", "quit"))
    args = parser.parse_args(argv)
    if args.command == "configure":
        return configure()
    from . import ipc, service
    try:
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
