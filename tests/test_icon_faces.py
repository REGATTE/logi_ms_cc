"""Physical JPEG faces preserve original artwork without a backing tile."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from mxcc.icons import FaceRenderer
from mxcc.paths import cache_dir

_APP = None


@pytest.fixture
def artwork(monkeypatch, tmp_path):
    global _APP
    _APP = QApplication.instance() or QApplication(["mxcc-icon-faces-test"])
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    def create(rect=QRect(40, 40, 80, 80), *, circle=False, background=Qt.GlobalColor.transparent):
        image = QImage(160, 160, QImage.Format.Format_ARGB32)
        image.fill(background)
        painter = QPainter(image)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e02020"))
        if circle:
            painter.drawEllipse(rect)
        else:
            painter.drawRect(rect)
        painter.end()
        path = tmp_path / "original.png"
        assert image.save(str(path))
        return str(path)

    return create


def rendered(renderer, path, label="Original app", application=None):
    filename = renderer(label, path, application)
    image_path = cache_dir() / filename
    assert image_path.read_bytes().startswith(b"\xff\xd8")
    image = QImage(str(image_path))
    assert (image.width(), image.height()) == (118, 118)
    return image, filename


def red_bounds(image):
    points = [(x, y) for y in range(image.height()) for x in range(image.width())
              if (c := image.pixelColor(x, y)).red() > 160 and c.green() < 80 and c.blue() < 80]
    return (min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points) + 1, max(y for _, y in points) + 1)


def test_padded_original_app_icon_fills_key_inside_narrow_black_border(artwork):
    path = artwork()
    app = SimpleNamespace(icon=path)
    renderer = FaceRenderer(False)
    image, filename = rendered(renderer, "ignored-icon-name", application=app)
    assert red_bounds(image) == (5, 5, 113, 113)
    assert image.pixelColor(0, 0).lightness() < 15
    assert image.pixelColor(1, 59).lightness() < 20
    assert image.pixelColor(116, 59).lightness() < 20
    before = (cache_dir() / filename).stat().st_mtime_ns
    # Labels do not leak into an icons-only face, and identical faces are reused.
    assert renderer("Other label", "ignored-icon-name", app) == filename
    assert (cache_dir() / filename).stat().st_mtime_ns == before


def test_non_square_artwork_keeps_aspect_ratio_and_transparent_space_is_black(artwork):
    image, _ = rendered(FaceRenderer(False), artwork(QRect(40, 60, 80, 40)))
    assert red_bounds(image) == (5, 32, 113, 86)
    assert image.pixelColor(59, 20).lightness() < 15
    assert image.pixelColor(59, 100).lightness() < 15


def test_transparent_artwork_has_no_added_white_backing(artwork):
    image, _ = rendered(FaceRenderer(False), artwork(circle=True))
    assert image.pixelColor(12, 12).lightness() < 15
    assert image.pixelColor(105, 105).lightness() < 15
    left, top, right, bottom = red_bounds(image)
    assert right - left >= 106 and bottom - top >= 106


def test_white_pixels_in_original_artwork_are_preserved(artwork):
    image, _ = rendered(FaceRenderer(False), artwork(background=Qt.GlobalColor.white))
    assert image.pixelColor(12, 12).lightness() > 240
    assert image.pixelColor(59, 59).red() > 180
    assert image.pixelColor(0, 0).lightness() < 15


def test_labels_have_separate_readable_space_below_artwork(artwork):
    path = artwork()
    image, filename = rendered(FaceRenderer(True), path, label="Editor")
    assert red_bounds(image) == (21, 5, 97, 81)
    assert any(image.pixelColor(x, y).lightness() > 180
               for y in range(87, 116) for x in range(5, 113))
    without_labels, other_filename = rendered(FaceRenderer(False), path)
    assert other_filename != filename
    assert red_bounds(without_labels) == (5, 5, 113, 113)
