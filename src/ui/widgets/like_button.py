"""The heart in the player bar: an eased colour change instead of a jump.

A liked track has to be recognisable at a glance, but a hard switch from a grey
outline to a bright red fill reads as a glitch. The button therefore animates
between the two palette colours and fades a soft glow in behind the glyph.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPushButton

from ui.theme import LIKE_ACTIVE, SURFACE_HOVER, TEXT_DIM

ANIMATION_MS = 220
GLOW_ALPHA = 56
SIZE = 32


def blend(start: str, end: str, mix: float) -> str:
    """``mix`` 0 gives ``start``, 1 gives ``end``; used for the heart colour."""
    first, second = QColor(start), QColor(end)
    return QColor(
        round(first.red() + (second.red() - first.red()) * mix),
        round(first.green() + (second.green() - first.green()) * mix),
        round(first.blue() + (second.blue() - first.blue()) * mix),
    ).name()


class LikeButton(QPushButton):
    """Checkable heart that eases its colour on every toggle."""

    def __init__(self, glyph: str = "♥", parent=None) -> None:
        super().__init__(glyph, parent)
        self.setObjectName("LikeButton")
        self.setCheckable(True)
        self.setToolTip("Нравится")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(SIZE, SIZE)
        self._mix = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_value)
        self.toggled.connect(self._on_toggled)
        self._repaint()

    @property
    def mix(self) -> float:
        """0 while idle, 1 while liked; the animation drives it."""
        return self._mix

    @property
    def animating(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    # -- internals ---------------------------------------------------------

    def _on_toggled(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._mix)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _on_value(self, value: object) -> None:
        self._mix = float(value)
        self._repaint()

    def _repaint(self) -> None:
        colour = blend(TEXT_DIM, LIKE_ACTIVE, self._mix)
        glow = round(GLOW_ALPHA * self._mix)
        self.setStyleSheet(
            "QPushButton#LikeButton {"
            f" color: {colour};"
            f" background: rgba(255, 51, 102, {glow});"
            " border: none; border-radius: 16px; }"
            f"QPushButton#LikeButton:hover {{ background: {SURFACE_HOVER}; color: {colour}; }}"
        )


__all__ = ["ANIMATION_MS", "SIZE", "LikeButton", "blend"]
