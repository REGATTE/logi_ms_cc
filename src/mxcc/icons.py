"""Resolve original desktop icons and render cached physical keypad faces."""

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle

from .paths import cache_dir


def setup_icon_theme() -> None:
    roots = [str(Path.home() / ".local/share/icons"), str(Path.home() / ".icons"),
             "/usr/local/share/icons", "/usr/share/icons", "/usr/share/pixmaps",
             "/var/lib/flatpak/exports/share/icons",
             str(Path.home() / ".local/share/flatpak/exports/share/icons")]
    QIcon.setThemeSearchPaths(list(dict.fromkeys(QIcon.themeSearchPaths() + roots)))
    if not QIcon.themeName():
        QIcon.setThemeName("Adwaita")
    QIcon.setFallbackThemeName("hicolor")


def original_icon(name: str) -> QIcon:
    if not name:
        return QIcon()
    path = Path(name)
    if path.is_absolute():
        return QIcon(str(path)) if path.is_file() else QIcon()
    icon = QIcon.fromTheme(name)
    if not icon.isNull():
        return icon
    # Desktop themes sometimes ship only the symbolic form of system actions.
    icon = QIcon.fromTheme(name + "-symbolic")
    if not icon.isNull():
        return icon
    for root in QIcon.themeSearchPaths():
        # Pixmaps is a flat fallback; theme lookup already covers icon themes.
        for suffix in ("", ".png", ".svg", ".xpm"):
            candidate = Path(root) / (name + suffix)
            if candidate.is_file():
                return QIcon(str(candidate))
    return QIcon()


def display_icon(name: str, *, application: bool = False) -> QIcon:
    icon = original_icon(name)
    if not icon.isNull():
        return icon
    standard = QStyle.StandardPixmap.SP_FileIcon if application else QStyle.StandardPixmap.SP_ComputerIcon
    return QApplication.style().standardIcon(standard)


class FaceRenderer:
    """Runs in the settings process only; the worker reads already encoded JPEGs."""

    def __init__(self, show_labels: bool = True):
        self.show_labels = show_labels

    def __call__(self, label: str, icon_name: str, application=None) -> str:
        image = QImage(118, 118, QImage.Format.Format_RGB32)
        image.fill(QColor("#202126"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        name = application.icon if application is not None else icon_name
        icon = original_icon(name)
        if icon.isNull():
            # A neutral placeholder keeps icons-only layouts free of text even
            # when an installed app or imported action has no available artwork.
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        if not icon.isNull():
            # A neutral inset keeps both light and dark original artwork legible.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#f4f4f6"))
            painter.drawRoundedRect(QRect(22, 10 if self.show_labels else 23, 74, 74), 12, 12)
            pixmap = icon.pixmap(QSize(64, 64))
            pixmap = pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((118 - pixmap.width()) // 2,
                               (15 if self.show_labels else 28) + (64 - pixmap.height()) // 2,
                               pixmap)
        if self.show_labels:
            painter.setPen(QColor("#f7f7fa"))
            font = QFont("Sans Serif")
            font.setPixelSize(12)
            painter.setFont(font)
            painter.drawText(QRect(5, 87, 108, 29),
                             int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
                             | int(Qt.TextFlag.TextWordWrap), label)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "JPEG", 92):
            raise RuntimeError("Qt could not encode a keypad image as JPEG.")
        data = bytes(buffer.data())
        filename = hashlib.sha256(data).hexdigest() + ".jpg"
        directory = cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / filename
        if not destination.exists():
            temporary = destination.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_bytes(data)
            temporary.replace(destination)
        return filename
