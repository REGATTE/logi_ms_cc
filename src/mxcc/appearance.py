"""Event-driven Qt appearance; lives only while the settings process is open."""

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QPalette


COLORS = {
    "light": {
        "background": "#f5f5f8", "text": "#252432", "surface": "#ffffff",
        "control": "#ffffff", "alternate": "#faf9fc", "sidebar": "#eeedf3",
        "border": "#d8d3e1", "muted": "#696476", "status": "#615a75",
        "selected": "#ded4f8", "selected_text": "#5e368d",
        "accent": "#7650b3", "accent_text": "#ffffff", "hover": "#f6f0ff",
        "focus": "#9c76c9", "disabled": "#eeedf0", "disabled_text": "#847d90",
    },
    "dark": {
        "background": "#18191f", "text": "#eeeef4", "surface": "#22242c",
        "control": "#2b2d36", "alternate": "#272933", "sidebar": "#202129",
        "border": "#494653", "muted": "#b4afc0", "status": "#cbb9e7",
        "selected": "#47365f", "selected_text": "#f0e6ff",
        "accent": "#b896ec", "accent_text": "#20152f", "hover": "#37303f",
        "focus": "#b99adf", "disabled": "#282831", "disabled_text": "#96909f",
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
    return f"""QMainWindow, QWidget#workspace, QDialog {{ background: {c['background']}; }}
QWidget {{ font-family: 'Inter', 'DejaVu Sans'; font-size: 13px; color: {c['text']}; }}
QLabel#title {{ font-size: 25px; font-weight: 600; }}
QLabel#section {{ font-size: 16px; font-weight: 600; }}
QLabel#muted {{ color: {c['muted']}; }}
QLabel#status {{ color: {c['status']}; padding: 6px; }}
QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#device {{ background: #34333a; border: 8px solid #44434a; border-radius: 22px; }}
QListWidget#navigation {{ background: {c['sidebar']}; border: none; padding: 12px; outline: 0; }}
QListWidget#navigation::item {{ padding: 14px 10px; margin: 3px 0; border-radius: 9px; }}
QListWidget#navigation::item:selected {{ background: {c['selected']}; color: {c['selected_text']}; }}
QPushButton {{ background: {c['control']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 9px 13px; }}
QPushButton:hover {{ border-color: {c['focus']}; background: {c['hover']}; }}
QPushButton:checked, QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['disabled_text']}; background: {c['disabled']}; }}
QComboBox, QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{ background: {c['control']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 8px; selection-background-color: {c['selected']}; selection-color: {c['selected_text']}; }}
QComboBox:focus, QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {c['focus']}; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {c['disabled_text']}; background: {c['disabled']}; }}
QAbstractItemView {{ background: {c['control']}; alternate-background-color: {c['alternate']}; border: 1px solid {c['border']}; selection-background-color: {c['selected']}; selection-color: {c['selected_text']}; }}
QTableWidget {{ gridline-color: {c['border']}; }}
QHeaderView::section {{ background: {c['sidebar']}; border: none; padding: 9px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['sidebar']}; border: none; }}
QToolButton#key {{ background: #202126; color: white; border: 2px solid #49484d; border-radius: 10px; }}
QToolButton#key:checked {{ border: 3px solid #ba94ef; }}
QToolButton#key:hover {{ border-color: #c9b6e8; }}
QCheckBox {{ spacing: 10px; padding: 5px 0; }}
QCheckBox:disabled {{ color: {c['disabled_text']}; }}
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
