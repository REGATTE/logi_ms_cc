"""Event-driven Qt appearance; lives only while the settings process is open."""

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPalette, QPen


COLORS = {
    "light": {
        "background": "#e8ecf1", "text": "#252c37", "surface": "#edf1f5",
        "control": "#e8edf3", "alternate": "#e0e6ed", "sidebar": "#e8ecf1",
        "border": "#cbd3dd", "muted": "#606b7c", "status": "#536075",
        "selected": "#dce7f0", "selected_text": "#244b60",
        "accent": "#217c74", "accent_text": "#ffffff", "hover": "#f0f4f8",
        "focus": "#248c83", "disabled": "#e0e5eb", "disabled_text": "#7d8896",
        "light": "#ffffff", "raised": "#f5f7fa", "dark": "#b7c0cd",
        "shadow": "#9ca8b8", "inset": "#dce2ea",
    },
    "dark": {
        "background": "#282e37", "text": "#edf1f7", "surface": "#2e3540",
        "control": "#2d343e", "alternate": "#242a33", "sidebar": "#282e37",
        "border": "#414b58", "muted": "#aab5c4", "status": "#bbc8d8",
        "selected": "#294b4c", "selected_text": "#d7fff5",
        "accent": "#72d8c2", "accent_text": "#17332e", "hover": "#363f4b",
        "focus": "#78dfc9", "disabled": "#292f38", "disabled_text": "#8794a6",
        "light": "#4b5665", "raised": "#37404c", "dark": "#1b2028",
        "shadow": "#10151d", "inset": "#222831",
    },
}


def palette_for(theme: str) -> QPalette:
    colors = COLORS[theme]
    palette = QPalette(QColor(colors["control"]))
    roles = {
        QPalette.ColorRole.Window: "background",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "control",
        QPalette.ColorRole.AlternateBase: "alternate",
        QPalette.ColorRole.ToolTipBase: "surface",
        QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "control",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.BrightText: "accent_text",
        QPalette.ColorRole.Highlight: "selected",
        QPalette.ColorRole.HighlightedText: "selected_text",
        QPalette.ColorRole.Link: "accent",
        QPalette.ColorRole.LinkVisited: "status",
        QPalette.ColorRole.PlaceholderText: "muted",
        QPalette.ColorRole.Accent: "accent",
        QPalette.ColorRole.Light: "light",
        QPalette.ColorRole.Midlight: "raised",
        QPalette.ColorRole.Mid: "border",
        QPalette.ColorRole.Dark: "dark",
        QPalette.ColorRole.Shadow: "shadow",
    }
    for role, name in roles.items():
        palette.setColor(role, QColor(colors[name]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText, QPalette.ColorRole.PlaceholderText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled_text"]))
    for role in (QPalette.ColorRole.Button, QPalette.ColorRole.Base):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    return palette


def paint_neumorphic_surface(painter, rect, palette, *, radius=18, pressed=False, focused=False):
    """Paint a soft surface with shadow room included in ``rect``; no effects or timers."""
    painter.save()
    area = QRectF(rect).adjusted(7, 7, -7, -7)
    if area.isEmpty():
        painter.restore()
        return
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if not pressed:
        for spread in range(6, 0, -1):
            for role, offset, opacity in ((QPalette.ColorRole.Shadow, 3, 20),
                                           (QPalette.ColorRole.Light, -3, 28)):
                color = palette.color(role)
                color.setAlpha(opacity)
                painter.setPen(QPen(color, spread * 1.4))
                painter.drawRoundedRect(area.translated(offset, offset), radius, radius)
    surface = QLinearGradient(area.topLeft(), area.bottomRight())
    surface.setColorAt(0, palette.color(QPalette.ColorRole.Window).darker(106)
                       if pressed else palette.color(QPalette.ColorRole.Midlight))
    surface.setColorAt(1, palette.color(QPalette.ColorRole.Button).lighter(103)
                       if pressed else palette.color(QPalette.ColorRole.Window))
    edge = QLinearGradient(area.topLeft(), area.bottomRight())
    edge.setColorAt(0, palette.color(QPalette.ColorRole.Dark if pressed else QPalette.ColorRole.Light))
    edge.setColorAt(1, palette.color(QPalette.ColorRole.Light if pressed else QPalette.ColorRole.Mid))
    painter.setBrush(surface)
    painter.setPen(QPen(palette.color(QPalette.ColorRole.Accent), 1.5) if focused
                   else QPen(QBrush(edge), 1))
    painter.drawRoundedRect(area, radius, radius)
    painter.restore()


def stylesheet_for(theme: str) -> str:
    c = COLORS[theme]
    raised = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['raised']}, stop:1 {c['control']})"
    inset = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['inset']}, stop:1 {c['surface']})"
    raised_edge = f"border: 1px solid {c['border']}; border-top-color: {c['light']}; border-left-color: {c['light']};"
    inset_edge = f"border: 1px solid {c['light']}; border-top-color: {c['border']}; border-left-color: {c['border']};"
    return f"""QMainWindow, QDialog,
QWidget#workspace, QWidget#deviceHome, QWidget#deviceGallery, QWidget#consoleDevices,
QWidget#editorPage, QWidget#editorHeader, QWidget#editorStage,
QWidget#applicationsPage, QWidget#applicationCards,
QWidget#deviceDetails, QWidget#settingsPage, QWidget#editorSidebar {{ background: {c['background']}; }}
QMainWindow#appWindow {{ background: transparent; }}
QWidget {{ font-family: 'Inter', 'DejaVu Sans'; font-size: 13px; color: {c['text']}; }}
QLabel#title, QLabel#homeTitle, QLabel#deviceTitle {{ font-size: 24px; font-weight: 600; }}
QLabel#section {{ font-size: 15px; font-weight: 600; }}
QLabel#muted, QLabel#homeSubtitle {{ color: {c['muted']}; }}
QLabel#status, QLabel#homeNotice {{ color: {c['status']}; padding: 6px; }}
QLabel#deviceName {{ font-size: 15px; font-weight: 600; }}
QLabel#deviceConnection, QLabel#deviceSupport {{ font-size: 12px; color: {c['muted']}; }}
QLabel#homeEmpty {{ font-size: 15px; color: {c['muted']}; }}
QFrame#card, QFrame#actionInspector, QFrame#deviceMappings, QFrame#deviceFacts {{ background: {raised}; {raised_edge} border-radius: 20px; }}
QFrame#deviceControlRow {{ background: {raised}; {raised_edge} border-radius: 12px; }}
QFrame#deviceControlRow[selected="true"] {{ background: {inset}; border: 1px solid {c['focus']}; }}
QFrame#deviceControlRow[selected="true"] QLabel {{ color: {c['selected_text']}; }}
QLabel#deviceSelection {{ color: {c['status']}; font-weight: 600; }}
QFrame#deviceFacts QLabel, QFrame#deviceControlRow QLabel {{ background: transparent; }}
QScrollArea#deviceControlsScroll, QScrollArea#deviceControlsScroll > QWidget > QWidget {{ background: {c['surface']}; border: none; }}
QScrollArea#deviceControlsScroll QScrollBar:vertical {{ background: transparent; width: 8px; margin: 3px 0; }}
QScrollArea#deviceControlsScroll QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 32px; }}
QScrollArea#deviceControlsScroll QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollArea#deviceControlsScroll QScrollBar::add-line:vertical, QScrollArea#deviceControlsScroll QScrollBar::sub-line:vertical {{ height: 0; border: none; background: transparent; }}
QScrollArea#deviceControlsScroll QScrollBar::add-page:vertical, QScrollArea#deviceControlsScroll QScrollBar::sub-page:vertical {{ background: transparent; }}
QFrame#applicationCard, QFrame#applicationsEmpty {{ background: {raised}; {raised_edge} border-radius: 20px; }}
QFrame#applicationCard QLabel, QFrame#applicationsEmpty QLabel {{ background: transparent; border: none; }}
QLabel#applicationName {{ font-size: 15px; font-weight: 600; }}
QLabel#applicationSummary {{ color: {c['muted']}; font-size: 12px; }}
QLabel#applicationCount {{ background: {c['selected']}; color: {c['selected_text']}; border-radius: 12px; padding: 6px 12px; }}
QFrame#device {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #60666d, stop:0.45 #444a51, stop:1 #292e34); border: 7px solid #4b525a; border-top-color: #899198; border-left-color: #69727a; border-bottom-color: #242a30; border-radius: 26px; }}
QScrollArea#deviceScroll, QScrollArea#editorScroll {{ background: {c['background']}; border: none; }}
QScrollArea#deviceScroll > QWidget > QWidget, QScrollArea#editorScroll > QWidget > QWidget {{ background: {c['background']}; }}
QScrollArea#applicationsScroll, QScrollArea#applicationsScroll > QWidget > QWidget {{ background: {c['background']}; border: none; }}
QScrollArea#applicationsScroll QScrollBar:vertical {{ background: transparent; width: 8px; margin: 3px 0; }}
QScrollArea#applicationsScroll QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 32px; }}
QScrollArea#applicationsScroll QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollArea#applicationsScroll QScrollBar::add-line:vertical, QScrollArea#applicationsScroll QScrollBar::sub-line:vertical {{ height: 0; border: none; background: transparent; }}
QScrollArea#applicationsScroll QScrollBar::add-page:vertical, QScrollArea#applicationsScroll QScrollBar::sub-page:vertical {{ background: transparent; }}
QListWidget#navigation {{ background: {c['sidebar']}; border: none; padding: 16px 8px; outline: 0; }}
QListWidget#navigation::item {{ padding: 12px 14px; margin: 4px 0; border-radius: 12px; }}
QListWidget#navigation::item:hover {{ background: {c['hover']}; }}
QListWidget#navigation::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
QListWidget#editorTabs {{ background: transparent; border: none; outline: 0; }}
QListWidget#editorTabs::item {{ padding: 6px 12px; border: 1px solid transparent; border-radius: 12px; }}
QListWidget#editorTabs::item:hover {{ background: {c['alternate']}; }}
QListWidget#editorTabs::item:selected {{ background: {inset}; color: {c['selected_text']}; {inset_edge} border-bottom-color: {c['focus']}; }}
QPushButton {{ background: {raised}; {raised_edge} border-radius: 12px; padding: 9px 13px; }}
QPushButton:hover {{ border-color: {c['focus']}; background: {c['hover']}; }}
QPushButton:focus {{ border-color: {c['focus']}; }}
QPushButton:pressed {{ background: {inset}; {inset_edge} }}
QPushButton:checked, QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['disabled_text']}; background: {c['disabled']}; }}
QPushButton#topbarButton, QToolButton#topbarButton {{ background: transparent; border: 1px solid transparent; border-radius: 12px; padding: 8px 10px; }}
QPushButton#topbarButton:hover, QToolButton#topbarButton:hover {{ background: {c['hover']}; }}
QPushButton#topbarButton:focus, QToolButton#topbarButton:focus {{ border-color: {c['focus']}; }}
QPushButton#topbarButton:disabled, QToolButton#topbarButton:disabled {{ color: {c['disabled_text']}; }}
QToolButton#profileChip {{ background: transparent; border: 1px solid transparent; border-radius: 10px; padding: 6px; }}
QToolButton#profileChip:hover {{ background: {c['hover']}; }}
QToolButton#profileChip:checked {{ background: {inset}; border-color: {c['focus']}; }}
QToolButton#profileChip:focus {{ border-color: {c['focus']}; }}
QComboBox, QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{ background: {inset}; {inset_edge} border-radius: 12px; padding: 8px; selection-background-color: {c['selected']}; selection-color: {c['selected_text']}; }}
QComboBox:focus, QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {c['focus']}; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {c['disabled_text']}; background: {c['disabled']}; }}
QComboBox::drop-down {{ border: none; width: 24px; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 3px 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0 3px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {c['muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QLineEdit#applicationSearch {{ border-radius: 12px; padding: 11px 14px; }}
QComboBox#applicationProfile {{ border-radius: 10px; padding: 8px 28px 8px 11px; }}
QComboBox#applicationProfile::drop-down {{ border: none; width: 24px; background: transparent; }}
QComboBox#applicationProfile::down-arrow {{ image: none; }}
QComboBox#deviceControlAssignment {{ border-radius: 10px; padding: 8px 28px 8px 11px; }}
QComboBox#deviceControlAssignment::drop-down {{ border: none; width: 24px; background: transparent; }}
QComboBox#deviceControlAssignment::down-arrow {{ image: none; }}
QPushButton#applicationAction {{ border-radius: 10px; padding: 9px 12px; }}
QAbstractItemView {{ background: {inset}; alternate-background-color: {c['alternate']}; {inset_edge} border-radius: 12px; selection-background-color: {c['selected']}; selection-color: {c['selected_text']}; }}
QTableWidget {{ gridline-color: {c['border']}; }}
QHeaderView::section {{ background: {c['alternate']}; border: none; padding: 10px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['sidebar']}; border: none; }}
QToolButton#key {{ background: #000000; color: white; border: 2px solid #49484d; border-radius: 10px; }}
QToolButton#key:checked {{ border: 3px solid {c['accent']}; }}
QToolButton#key:hover {{ border-color: {c['focus']}; }}
QToolButton#key[dropTarget="true"] {{ border: 3px solid #71e4bc; }}
QCheckBox {{ spacing: 10px; padding: 5px 0; }}
QCheckBox:disabled {{ color: {c['disabled_text']}; }}
QGroupBox {{ background: {raised}; {raised_edge} border-radius: 16px; margin-top: 12px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QMenu {{ background: {raised}; color: {c['text']}; {raised_edge} border-radius: 12px; padding: 6px; }}
QMenu::item {{ border-radius: 7px; padding: 7px 18px; }}
QMenu::item:selected {{ background: {inset}; color: {c['selected_text']}; }}
QMenu::item:disabled {{ color: {c['disabled_text']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px; }}
QToolButton#windowControl {{ background: transparent; border: 1px solid transparent; border-radius: 12px; padding: 7px; }}
QToolButton#windowControl:hover {{ background: {raised}; {raised_edge} }}
QToolButton#windowControl:pressed {{ background: {inset}; {inset_edge} }}
QToolButton#windowControl:focus {{ border-color: {c['focus']}; }}
"""


class AppearanceController(QObject):
    """Follow Qt's system color-scheme signal unless the user selects a mode.

    We deliberately do not override QStyleHints.colorScheme: keeping that hint
    native lets manual modes return to the latest OS appearance immediately.
    Application-wide palette/style updates cover dialogs and popup widgets too.
    """

    def __init__(self, application, *, hints=None, parent=None):
        super().__init__(parent)
        self.application = application
        self.hints = hints if hints is not None else application.styleHints()
        self.preference = "system"
        self.system_scheme = self.hints.colorScheme()
        self.fallback_dark = application.palette().color(QPalette.ColorRole.Window).lightness() < 128
        self.effective_theme = None
        self.hints.colorSchemeChanged.connect(self._system_changed)

    def set_preference(self, preference: str) -> None:
        if preference not in ("system", "light", "dark"):
            raise ValueError("Theme must be system, light or dark")
        self.preference = preference
        self._apply()

    def _system_changed(self, scheme) -> None:
        self.system_scheme = scheme
        if self.preference == "system":
            self._apply()

    def _apply(self) -> None:
        if self.preference == "system":
            dark = (self.system_scheme == Qt.ColorScheme.Dark or
                    (self.system_scheme == Qt.ColorScheme.Unknown and self.fallback_dark))
            theme = "dark" if dark else "light"
        else:
            theme = self.preference
        if theme == self.effective_theme:
            return
        self.effective_theme = theme
        self.application.setPalette(palette_for(theme))
        self.application.setStyleSheet(stylesheet_for(theme))
