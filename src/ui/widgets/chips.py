"""A row of exclusive chips, used by every settings selector in the GUI.

The values come from :mod:`core.station`, so a chip can only ever hold a string
the station API accepts: the previous ``bright``/``diverse``/``strict``/
``maximum`` options that made the API answer HTTP 400 are not representable.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.theme import SPACE_XS
from ui.widgets.flow_layout import SPACING, FlowLayout

Choice = tuple[str, str]


class ChipGroup(QWidget):
    """One titled block of mutually exclusive, checkable chips.

    The title sits on its own line above the chips rather than beside them, and
    the chips wrap: a block is asked to fit whatever width the page gives it, so
    a row of six mood chips in a half-width column has to become two rows of
    three, not six unreadable stumps.
    """

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        label = QLabel(title)
        label.setObjectName("Dim")
        layout.addWidget(label)

        self.flow = FlowLayout(spacing=SPACING)
        layout.addLayout(self.flow)
        for key, text in choices:
            button = QPushButton(text)
            button.setObjectName("Chip")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setProperty("value", key)
            # Fixed, not Preferred: a chip that may shrink is a chip whose
            # label gets clipped, and the layout below would rather wrap.
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.toggled.connect(lambda checked, name=key: self._on_clicked(name, checked))
            self.flow.addWidget(button)
            self._buttons[key] = button
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.set_value(value if value is not None else self.default)

    # -- wrapping ---------------------------------------------------------

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt naming
        """Tell the parent to ask for a height once it knows the width.

        Without this the block is sized by its unwrapped width - every chip on
        one line - and the page is left to shrink the chips instead. This is the
        whole reason the mood labels used to come out clipped.
        """
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt naming
        return self.layout().heightForWidth(width)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        # The useful minimum is one chip wide: a block narrower than its widest
        # chip still has to be able to wrap rather than truncate.
        widest = max((button.sizeHint().width() for button in self._buttons.values()), default=0)
        return QSize(min(widest, 200), super().minimumSizeHint().height())

    def chips(self) -> list[QPushButton]:
        """The chip buttons, in order; used by tests and by the settings cards."""
        return list(self._buttons.values())

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
