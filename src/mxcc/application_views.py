"""Compact native cards for applications assigned to the keypad."""

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)


class _ShortcutComboBox(QComboBox):
    """Keep a clear dropdown affordance with the rounded, themed control."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Text), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x, y = self.width() - 14, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5)]))
        painter.end()


class ApplicationCard(QFrame):
    profile_changed = Signal(object)
    import_requested = Signal()
    edit_requested = Signal()

    def __init__(self, identifier, name, icon, slots, *, system_action=False, parent=None):
        super().__init__(parent)
        self.setObjectName("applicationCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setAccessibleName(name)
        self.application_name = name
        self.search_text = f"{name} {identifier}".casefold()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(16)
        artwork = QLabel()
        artwork.setFixedSize(44, 44)
        artwork.setPixmap(icon.pixmap(QSize(44, 44)))
        artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(artwork)
        identity = QVBoxLayout()
        identity.setSpacing(5)
        self.name_label = QLabel(name)
        self.name_label.setObjectName("applicationName")
        self.name_label.setWordWrap(True)
        self.name_label.setMinimumWidth(90)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name_label.setToolTip(name)
        self.key_label = QLabel(f"{'Key' if len(slots) == 1 else 'Keys'} " + ", ".join(map(str, slots)))
        self.key_label.setObjectName("muted")
        identity.addWidget(self.name_label)
        identity.addWidget(self.key_label)
        layout.addLayout(identity, 1)
        self.profile_combo = self.import_button = self.edit_button = None
        self.profile_summary = QLabel()
        self.profile_summary.setObjectName("applicationSummary")
        if system_action:
            self.profile_summary.setText("System action")
            layout.addWidget(self.profile_summary)
            self.setMinimumHeight(82)
            return

        shortcuts = QWidget()
        shortcuts.setFixedWidth(206)
        shortcut_layout = QVBoxLayout(shortcuts)
        shortcut_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_layout.setSpacing(5)
        shortcut_layout.addWidget(self.profile_summary)
        self.profile_combo = _ShortcutComboBox()
        self.profile_combo.setObjectName("applicationProfile")
        self.profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(12)
        self.profile_combo.setAccessibleName(f"Shortcuts for {name}")
        self.profile_combo.currentIndexChanged.connect(lambda: self.profile_changed.emit(self.profile_combo.currentData()))
        shortcut_layout.addWidget(self.profile_combo)
        layout.addWidget(shortcuts)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        for attr, title, signal in (("import_button", "Import…", self.import_requested),
                                    ("edit_button", "Edit", self.edit_requested)):
            control = QPushButton(title)
            control.setObjectName("applicationAction")
            if attr == "edit_button":
                control.setFixedWidth(76)
            control.setAccessibleName(f"{title.rstrip('…')} shortcuts for {name}")
            control.clicked.connect(signal)
            actions.addWidget(control)
            setattr(self, attr, control)
        layout.addLayout(actions)
        self.set_profile_summary(None)

    def set_profile_summary(self, profile):
        if self.profile_combo is None:
            return
        count = len(profile["shortcuts"]) if profile else 0
        self.profile_summary.setText(f"{count} {'shortcut' if count == 1 else 'shortcuts'}" if profile else "No shortcuts assigned")
        self.profile_summary.setToolTip(profile["name"] if profile else "Import a shortcut file or create your own.")
        self.profile_combo.setToolTip(profile["name"] if profile else "Choose a shortcut profile")
        self.edit_button.setText("Edit" if profile else "Set up")
        self.edit_button.setAccessibleName(f"{'Edit' if profile else 'Set up'} shortcuts for {self.application_name}")
        self.edit_button.setToolTip("Edit shortcuts" if profile else "Create shortcuts for this app")
