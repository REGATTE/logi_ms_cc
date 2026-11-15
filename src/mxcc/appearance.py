"""Event-driven Qt appearance; lives only while the settings process is open."""

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QPalette


COLORS = {
    "light": {
        "background": "#fafafa", "text": "#29292d", "surface": "#ffffff",
        "control": "#ffffff", "alternate": "#f6f5f8", "sidebar": "#fafafa",
        "border": "#e2e0e7", "muted": "#727078", "status": "#625d6c",
        "selected": "#eee7fc", "selected_text": "#6230bc",
        "accent": "#8451f5", "accent_text": "#ffffff", "hover": "#f0eafb",
        "focus": "#9467f0", "disabled": "#f0eff2", "disabled_text": "#89858f",
    },
    "dark": {
        "background": "#1b1c21", "text": "#f0eff3", "surface": "#23242b",
        "control": "#2b2c34", "alternate": "#282930", "sidebar": "#1b1c21",
        "border": "#41414b", "muted": "#aba8b4", "status": "#c1b8d1",
        "selected": "#493565", "selected_text": "#f4edff",
        "accent": "#b695ff", "accent_text": "#251638", "hover": "#35303f",
        "focus": "#b495ed", "disabled": "#282930", "disabled_text": "#918c9b",
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
        QPalette.ColorRole.BrightText: "text",
        QPalette.ColorRole.Highlight: "selected",
        QPalette.ColorRole.HighlightedText: "selected_text",
        QPalette.ColorRole.Link: "accent",
        QPalette.ColorRole.LinkVisited: "status",
        QPalette.ColorRole.PlaceholderText: "muted",
        QPalette.ColorRole.Accent: "accent",
    }
    for role, name in roles.items():
        palette.setColor(role, QColor(colors[name]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText, QPalette.ColorRole.PlaceholderText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled_text"]))
    for role in (QPalette.ColorRole.Button, QPalette.ColorRole.Base):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    return palette


def stylesheet_for(theme: str) -> str:
    c = COLORS[theme]
    return f"""QMainWindow, QDialog,
QWidget#workspace, QWidget#deviceHome, QWidget#deviceGallery, QWidget#consoleDevices,
QWidget#editorPage, QWidget#editorHeader, QWidget#editorStage,
QWidget#applicationsPage, QWidget#applicationCards,
QWidget#deviceDetails, QWidget#settingsPage, QWidget#editorSidebar {{ background: {c['background']}; }}
QWidget {{ font-family: 'Inter', 'DejaVu Sans'; font-size: 13px; color: {c['text']}; }}
QLabel#title, QLabel#homeTitle, QLabel#deviceTitle {{ font-size: 24px; font-weight: 600; }}
QLabel#section {{ font-size: 15px; font-weight: 600; }}
QLabel#muted, QLabel#homeSubtitle {{ color: {c['muted']}; }}
QLabel#status, QLabel#homeNotice {{ color: {c['status']}; padding: 6px; }}
QLabel#deviceName {{ font-size: 15px; font-weight: 600; }}
QLabel#deviceConnection, QLabel#deviceSupport {{ font-size: 12px; color: {c['muted']}; }}
QLabel#homeEmpty {{ font-size: 15px; color: {c['muted']}; }}
QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame#actionInspector {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 18px; }}
QFrame#applicationCard, QFrame#applicationsEmpty {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 18px; }}
QFrame#applicationCard QLabel, QFrame#applicationsEmpty QLabel {{ background: transparent; border: none; }}
QLabel#applicationName {{ font-size: 15px; font-weight: 600; }}
QLabel#applicationSummary {{ color: {c['muted']}; font-size: 12px; }}
QLabel#applicationCount {{ background: {c['selected']}; color: {c['selected_text']}; border-radius: 12px; padding: 6px 12px; }}
QFrame#device {{ background: #444449; border: 7px solid #515156; border-radius: 24px; }}
QScrollArea#deviceScroll, QScrollArea#editorScroll {{ background: {c['background']}; border: none; }}
QScrollArea#deviceScroll > QWidget > QWidget, QScrollArea#editorScroll > QWidget > QWidget {{ background: {c['background']}; }}
QScrollArea#applicationsScroll, QScrollArea#applicationsScroll > QWidget > QWidget {{ background: {c['background']}; border: none; }}
QScrollArea#applicationsScroll QScrollBar:vertical {{ background: transparent; width: 8px; margin: 3px 0; }}
QScrollArea#applicationsScroll QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 32px; }}
QScrollArea#applicationsScroll QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollArea#applicationsScroll QScrollBar::add-line:vertical, QScrollArea#applicationsScroll QScrollBar::sub-line:vertical {{ height: 0; border: none; background: transparent; }}
QScrollArea#applicationsScroll QScrollBar::add-page:vertical, QScrollArea#applicationsScroll QScrollBar::sub-page:vertical {{ background: transparent; }}
QListWidget#navigation {{ background: {c['sidebar']}; border: none; padding: 16px 8px; outline: 0; }}
QListWidget#navigation::item {{ padding: 12px 14px; margin: 4px 0; border-radius: 5px; }}
QListWidget#navigation::item:hover {{ background: {c['hover']}; }}
QListWidget#navigation::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
QPushButton {{ background: {c['control']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 9px 13px; }}
QPushButton:hover {{ border-color: {c['focus']}; background: {c['hover']}; }}
QPushButton:focus {{ border-color: {c['focus']}; }}
QPushButton:checked, QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['disabled_text']}; background: {c['disabled']}; }}
QPushButton#topbarButton, QToolButton#topbarButton {{ background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 8px 10px; }}
QPushButton#topbarButton:hover, QToolButton#topbarButton:hover {{ background: {c['hover']}; }}
QPushButton#topbarButton:focus, QToolButton#topbarButton:focus {{ border-color: {c['focus']}; }}
QPushButton#topbarButton:disabled, QToolButton#topbarButton:disabled {{ color: {c['disabled_text']}; }}
QToolButton#profileChip {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 6px; }}
QToolButton#profileChip:hover {{ background: {c['hover']}; }}
QToolButton#profileChip:checked {{ background: {c['selected']}; border-color: {c['focus']}; }}
QToolButton#profileChip:focus {{ border-color: {c['focus']}; }}
QComboBox, QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{ background: {c['control']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 8px; selection-background-color: {c['selected']}; selection-color: {c['selected_text']}; }}
QComboBox:focus, QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {c['focus']}; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {c['disabled_text']}; background: {c['disabled']}; }}
QLineEdit#applicationSearch {{ border-radius: 12px; padding: 11px 14px; }}
QComboBox#applicationProfile {{ border-radius: 10px; padding: 8px 28px 8px 11px; }}
QComboBox#applicationProfile::drop-down {{ border: none; width: 24px; background: transparent; }}
QComboBox#applicationProfile::down-arrow {{ image: none; }}
QPushButton#applicationAction {{ border-radius: 10px; padding: 9px 12px; }}
QAbstractItemView {{ background: {c['control']}; alternate-background-color: {c['alternate']}; border: 1px solid {c['border']}; selection-background-color: {c['selected']}; selection-color: {c['selected_text']}; }}
QTableWidget {{ gridline-color: {c['border']}; }}
QHeaderView::section {{ background: {c['alternate']}; border: none; padding: 10px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['sidebar']}; border: none; }}
QToolButton#key {{ background: #000000; color: white; border: 2px solid #49484d; border-radius: 10px; }}
QToolButton#key:checked {{ border: 3px solid #ba94ef; }}
QToolButton#key:hover {{ border-color: #c9b6e8; }}
QToolButton#key[dropTarget="true"] {{ border: 3px solid #71e4bc; }}
QCheckBox {{ spacing: 10px; padding: 5px 0; }}
QCheckBox:disabled {{ color: {c['disabled_text']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 12px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QMenu {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; }}
QMenu::item:selected {{ background: {c['selected']}; color: {c['selected_text']}; }}
QMenu::item:disabled {{ color: {c['disabled_text']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
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
