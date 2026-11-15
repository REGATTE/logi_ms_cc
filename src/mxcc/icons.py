"""Resolve original desktop icons and render cached physical keypad faces."""

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle

from .paths import cache_dir


def application_icon() -> QIcon:
    """Use the same canonical artwork as the desktop launcher and Rust tray."""
    source = Path(__file__).resolve().parents[2] / "packaging/mx-creative-control.svg"
    installed = Path("/usr/share/icons/hicolor/scalable/apps/mx-creative-control.svg")
    for path in (source, installed):
        if path.is_file():
            return QIcon(str(path))
    return QIcon.fromTheme("mx-creative-control")


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


def _trim_transparent_padding(image: QImage) -> QImage:
    """Remove only fully transparent outer pixels, including in padded PNGs."""
    if not image.hasAlphaChannel():
        return image
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = rgba.constBits()
    width, height = rgba.width(), rgba.height()
    left, top, right, bottom = width, height, 0, 0
    for y in range(height):
        offset = y * rgba.bytesPerLine()
        alpha = bytes(pixels[offset + 3:offset + width * 4:4])
        end = len(alpha.rstrip(b"\0"))
        if end:
            left = min(left, width - len(alpha.lstrip(b"\0")))
            right = max(right, end)
            top = min(top, y)
            bottom = y + 1
    if right == 0:
        return QImage()
    return image.copy(left, top, right - left, bottom - top)


class FaceRenderer:
    """Runs in the settings process only; the worker reads already encoded JPEGs."""

    def __init__(self, show_labels: bool = True):
        self.show_labels = show_labels

    def __call__(self, label: str, icon_name: str, application=None) -> str:
        image = QImage(118, 118, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.black)
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
            # Resolve a larger source before fitting the original artwork. Only
            # transparent padding is trimmed; no backing tile or crop is added.
            artwork = _trim_transparent_padding(icon.pixmap(QSize(216, 216)).toImage())
            if not artwork.isNull():
                area = QRect(5, 5, 108, 76 if self.show_labels else 108)
                artwork = artwork.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                artwork.setDevicePixelRatio(1)
                painter.drawImage(area.x() + (area.width() - artwork.width()) // 2,
                                  area.y() + (area.height() - artwork.height()) // 2,
                                  artwork)
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
