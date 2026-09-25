"""«Настройки»: everything that lives in :class:`core.config_manager.ConfigManager`.

The page is the single writer for the persisted preferences; the shell reads
them once at start-up, so a change takes effect on the next launch except for
the visualizer, which is applied immediately.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config_manager import VALID_QUALITIES, VALID_VISUALIZERS, ConfigManager
from ui.widgets.chips import ChipGroup

VISUALIZER_CHOICES = (
    ("spectrum", "Спектр"),
    ("wave", "Волна"),
    ("circular", "Круг"),
)
QUALITY_CHOICES = (
    ("auto", "Авто"),
    ("lossless", "Без потерь"),
    ("320", "320 кбит/с"),
    ("192", "192 кбит/с"),
)
THEME_CHOICES = (("dark", "Тёмная"), ("light", "Светлая"))
TRAY_CHOICES = (("always", "Всегда"), ("playing", "Только при игре"), ("never", "Не показывать"))


def _filtered(values: tuple[str, ...], choices: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    allowed = set(values)
    return tuple((key, label) for key, label in choices if key in allowed)


class SettingsPage(QWidget):
    """Chips for the enumerations, switches for the booleans."""

    visualizer_changed = Signal(str)
    quality_changed = Signal(str)
    theme_changed = Signal(str)
    notifications_changed = Signal(bool)
    tray_changed = Signal(str)

    def __init__(
        self,
        config: ConfigManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._build_ui()
        self.load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)
        title = QLabel("Настройки")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        self.visualizer_group = ChipGroup("Визуализатор", _filtered(VALID_VISUALIZERS, VISUALIZER_CHOICES))
        self.quality_group = ChipGroup("Качество", _filtered(VALID_QUALITIES, QUALITY_CHOICES))
        self.theme_group = ChipGroup("Тема", THEME_CHOICES)
        self.tray_group = ChipGroup("Значок в трее", TRAY_CHOICES)
        for group in (
            self.visualizer_group,
            self.quality_group,
            self.theme_group,
            self.tray_group,
        ):
            root.addWidget(group)

        row = QHBoxLayout()
        self.notifications_switch = QCheckBox()
        self.notifications_switch.setObjectName("SettingSwitch")
        row.addWidget(QLabel("Уведомления «сейчас играет»"))
        row.addStretch(1)
        row.addWidget(self.notifications_switch)
        root.addLayout(row)

        buttons = QHBoxLayout()
        self.reset_button = QPushButton("Сбросить настройки")
        self.reset_button.clicked.connect(self.reset)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Dim")
        root.addWidget(self.status_label)
        root.addStretch(1)

        self.visualizer_group.value_changed.connect(self._on_visualizer)
        self.quality_group.value_changed.connect(self._on_quality)
        self.theme_group.value_changed.connect(self._on_theme)
        self.tray_group.value_changed.connect(self._on_tray)
        self.notifications_switch.toggled.connect(self._on_notifications)

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        """Show the stored values without emitting change signals."""
        for group, value in (
            (self.visualizer_group, self._config.get_visualizer()),
            (self.quality_group, self._config.get_quality()),
            (self.theme_group, self._config.get_theme()),
            (self.tray_group, self._config.get("tray", "always")),
        ):
            blocked = group.blockSignals(True)
            group.set_value(value)
            group.blockSignals(blocked)
        self.notifications_switch.blockSignals(True)
        self.notifications_switch.setChecked(self._config.get_notifications())
        self.notifications_switch.blockSignals(False)

    def reset(self) -> None:
        self._config.reset()
        self.load()
        self.status_label.setText("Настройки сброшены к значениям по умолчанию")
        self.visualizer_changed.emit(self._config.get_visualizer())

    # -- slots --------------------------------------------------------------

    def _on_visualizer(self, value: str) -> None:
        self._config.set_visualizer(value)
        self.status_label.setText(f"Визуализатор: {value}")
        self.visualizer_changed.emit(value)

    def _on_quality(self, value: str) -> None:
        self._config.set_quality(value)
        self.status_label.setText(f"Качество: {value}")
        self.quality_changed.emit(value)

    def _on_theme(self, value: str) -> None:
        self._config.set_theme(value)
        self.status_label.setText(f"Тема: {value}")
        self.theme_changed.emit(value)

    def _on_tray(self, value: str) -> None:
        self._config.set("tray", value)
        self.status_label.setText(f"Значок в трее: {value}")
        self.tray_changed.emit(value)

    def _on_notifications(self, checked: bool) -> None:
        self._config.set_notifications(checked)
        self.status_label.setText("Уведомления включены" if checked else "Уведомления выключены")
        self.notifications_changed.emit(checked)


__all__ = [
    "QUALITY_CHOICES",
    "THEME_CHOICES",
    "TRAY_CHOICES",
    "VISUALIZER_CHOICES",
    "SettingsPage",
]
