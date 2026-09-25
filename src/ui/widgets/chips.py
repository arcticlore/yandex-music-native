"""A row of exclusive chips, used by every settings selector in the GUI.

The values come from :mod:`core.station`, so a chip can only ever hold a string
the station API accepts: the previous ``bright``/``diverse``/``strict``/
``maximum`` options that made the API answer HTTP 400 are not representable.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

Choice = tuple[str, str]


class ChipGroup(QWidget):
    """One labelled row of mutually exclusive, checkable buttons."""

    value_changed = Signal(str)

    def __init__(
        self,
        title: str,
        choices: tuple[Choice, ...],
        value: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("Dim")
        layout.addWidget(label)
        for key, text in choices:
            button = QPushButton(text)
            button.setObjectName("Chip")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setProperty("value", key)
            button.toggled.connect(lambda checked, name=key: self._on_clicked(name, checked))
            layout.addWidget(button)
            self._buttons[key] = button
        layout.addStretch(1)
        self.set_value(value if value is not None else self.default)

    # -- values ---------------------------------------------------------

    @property
    def default(self) -> str:
        return next(iter(self._buttons), "")

    @property
    def value(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return self.default

    @property
    def values(self) -> list[str]:
        return list(self._buttons)

    def set_value(self, value: str | None) -> bool:
        """Check the chip named ``value``; no signal when nothing changes."""
        if value is not None and value not in self._buttons:
            return False
        key = value if value is not None else self.default
        if self.value == key:
            return False
        self._buttons[key].setChecked(True)
        return True

    # -- slots ----------------------------------------------------------

    def _on_clicked(self, key: str, checked: bool) -> None:
        if checked:
            self.value_changed.emit(key)


__all__ = ["ChipGroup", "Choice"]
