"""Shared preview stage and action sidebar for native customization pages."""

from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLayout, QVBoxLayout, QWidget

from .appearance import paint_neumorphic_surface


CUSTOMIZATION_STYLE = """
    QWidget#customizationWorkspace { background: palette(window); }
    QFrame#customizationStage, QFrame#customizationLibrary { background: transparent; border: none; }
    QFrame#customizationLibrary QLabel, QWidget#ringCategories { background: transparent; }
    QToolButton#ringProfile, QToolButton#editorProfile { background: transparent; border: 1px solid transparent; border-radius: 11px; padding: 6px; }
    QToolButton#ringProfile:checked, QToolButton#editorProfile:checked { border-color: palette(accent); background: palette(alternate-base); }
    QToolButton#ringProfile:hover, QToolButton#editorProfile:hover { background: palette(alternate-base); }
    QToolButton#ringCategory, QToolButton#editorCategory { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 palette(midlight), stop:1 palette(button)); border: 1px solid palette(mid); border-top-color: palette(light); border-left-color: palette(light); border-radius: 12px; padding: 12px; text-align: left; }
    QToolButton#ringCategory:checked, QToolButton#editorCategory:checked { background: palette(alternate-base); border-color: palette(accent); }
    QToolButton#ringCategory:hover, QToolButton#editorCategory:hover { border-color: palette(accent); }
    QPushButton#ringApply, QPushButton#editorApply { background: palette(accent); color: palette(bright-text); border: none; border-radius: 12px; padding: 10px; }
    QPushButton#ringApply:disabled, QPushButton#editorApply:disabled { background: palette(button); color: palette(mid); }
    QPushButton#editorPageButton:checked { background: palette(alternate-base); color: palette(text); border-color: palette(accent); }
    QToolButton#key:checked { border-color: palette(accent); }
    QScrollArea#editorProfiles { border: none; background: transparent; }
    QScrollArea#editorProfiles QScrollBar:horizontal { height: 4px; background: transparent; }
    QScrollArea#editorProfiles QScrollBar::handle:horizontal { background: palette(mid); border-radius: 2px; min-width: 18px; }
    QScrollArea#editorProfiles QScrollBar::add-line:horizontal, QScrollArea#editorProfiles QScrollBar::sub-line:horizontal { width: 0; }
    QScrollArea#editorProfiles QScrollBar::add-page:horizontal, QScrollArea#editorProfiles QScrollBar::sub-page:horizontal { background: transparent; }
    QLineEdit#ringSearch, QLineEdit#editorSearch { border-radius: 12px; padding: 9px 11px; }
    QListWidget#ringApps, QListWidget#editorApps { border: none; background: transparent; outline: 0; }
    QListWidget#ringApps::item, QListWidget#editorApps::item { padding: 7px 4px; border-radius: 9px; }
    QListWidget#ringApps::item:hover, QListWidget#editorApps::item:hover { background: palette(midlight); }
    QListWidget#ringApps::item:selected, QListWidget#editorApps::item:selected { background: palette(alternate-base); color: palette(text); border: 1px solid palette(accent); }
    QFrame#deviceControlRow { background: palette(alternate-base); border: 1px solid palette(mid); border-radius: 12px; }
    QFrame#deviceControlRow[selected="true"] { background: palette(alternate-base); border: 1px solid palette(accent); }
    QFrame#deviceControlRow[selected="true"] QLabel { color: palette(text); }
    QScrollArea#ringLibraryScroll, QScrollArea#editorScroll,
    QScrollArea#ringLibraryScroll > QWidget > QWidget, QScrollArea#editorScroll > QWidget > QWidget { background: transparent; border: none; }
    QScrollArea#ringLibraryScroll QScrollBar:vertical, QScrollArea#editorScroll QScrollBar:vertical { width: 7px; background: transparent; }
    QScrollArea#ringLibraryScroll QScrollBar::handle:vertical, QScrollArea#editorScroll QScrollBar::handle:vertical { background: palette(mid); border-radius: 3px; min-height: 24px; }
    QScrollArea#ringLibraryScroll QScrollBar::add-line:vertical, QScrollArea#ringLibraryScroll QScrollBar::sub-line:vertical,
    QScrollArea#editorScroll QScrollBar::add-line:vertical, QScrollArea#editorScroll QScrollBar::sub-line:vertical { height: 0; }
    QScrollArea#ringLibraryScroll QScrollBar::add-page:vertical, QScrollArea#ringLibraryScroll QScrollBar::sub-page:vertical,
    QScrollArea#editorScroll QScrollBar::add-page:vertical, QScrollArea#editorScroll QScrollBar::sub-page:vertical { background: transparent; }
"""


class _NeumorphicPanel(QFrame):
    def __init__(self, parent=None, *, recessed=False):
        super().__init__(parent)
        self.recessed = recessed

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_neumorphic_surface(painter, self.rect(), self.palette(), radius=20,
                                 pressed=self.recessed)


class CustomizationWorkspace(QWidget):
    """Consistent two-column surface; each editor supplies its own controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("customizationWorkspace")
        self.setStyleSheet(CUSTOMIZATION_STYLE)
        shell = QHBoxLayout(self)
        shell.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(16)

        self.stage = _NeumorphicPanel(recessed=True)
        self.stage.setObjectName("customizationStage")
        self.stage.setMinimumWidth(440)
        self.stage_layout = QVBoxLayout(self.stage)
        self.stage_layout.setContentsMargins(16, 10, 16, 12)
        self.stage_layout.setSpacing(8)
        shell.addWidget(self.stage, 1)

        self.library = _NeumorphicPanel()
        self.library.setObjectName("customizationLibrary")
        self.library.setFixedWidth(320)
        self.panel = QVBoxLayout(self.library)
        self.panel.setContentsMargins(18, 20, 18, 18)
        self.panel.setSpacing(13)
        shell.addWidget(self.library)
