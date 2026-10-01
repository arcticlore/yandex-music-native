"""The hearts in the player bar: an eased colour change instead of a jump.

A marked track has to be recognisable at a glance, but a hard switch from a grey
outline to a bright red fill reads as a glitch. The buttons therefore animate
between the two palette colours and fade a soft glow in behind the glyph.

:class:`DislikeButton` is the same button with a different mark, so the easing
is written once and inherited rather than pasted a second time with a slightly
different bug in each copy.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, QEasingCurve, Qt, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPushButton

from ui.theme import (
    DANGER,
    DISLIKE_GLOW,
    ICON_BUTTON,
    LIKE_ACTIVE,
    LIKE_GLOW,
    SURFACE_HOVER,
    TEXT_DIM,
    pill_radius,
)
from ui.widgets.icons import dislike_icon

ANIMATION_MS = 220
SIZE = ICON_BUTTON


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

    OBJECT_NAME = "LikeButton"
    """The name the inline sheet below is written against."""

    ACTIVE_COLOUR = LIKE_ACTIVE
    GLOW = LIKE_GLOW
    """The colour the mark eases towards and the wash it sits on."""

    TOOLTIP = "Нравится"

    def __init__(self, glyph: str = "♥", parent=None) -> None:
        super().__init__(glyph, parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setCheckable(True)
        self.setToolTip(self.TOOLTIP)
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
        colour = blend(TEXT_DIM, self.ACTIVE_COLOUR, self._mix)
        radius = pill_radius(ICON_BUTTON)
        name = self.OBJECT_NAME
        self.setStyleSheet(
            f"QPushButton#{name} {{"
            f" color: {colour};"
            " border: none; }"
            f"QPushButton#{name}:hover {{ background: {SURFACE_HOVER}; color: {colour}; }}"
            f"QPushButton#{name}:checked {{ background: {self.GLOW}; border-radius: {radius}px; }}"
        )
        self._apply_glyph(colour)

    def _apply_glyph(self, colour: str) -> None:
        """Hook for a subclass whose mark is drawn rather than typed."""


class DislikeButton(LikeButton):
    """The other half of the pair: a broken heart, marked with the same easing.

    The mark is a drawn glyph rather than a typed one because there is no font
    character for a broken heart that renders the same everywhere - the emoji
    fallbacks on Linux desktops range from a torn page to a monochrome outline.
    Drawing it also means the crack stays a hole while the fill animates in.
    """

    OBJECT_NAME = "DislikeButton"
    ACTIVE_COLOUR = DANGER
    GLOW = DISLIKE_GLOW
    TOOLTIP = "Не нравится — больше не предлагать"

    def __init__(self, parent=None) -> None:
        # No text: the icon is the whole mark, and a text glyph beside it would
        # only make the button read as two things.
        super().__init__("", parent)

    def _apply_glyph(self, colour: str) -> None:
        # ``active`` flips halfway through the ease, so the fill arrives with
        # the colour rather than after it.
        self.setIcon(dislike_icon(SIZE, colour=colour, active=self._mix >= 0.5))
        # A button with no text but an icon would otherwise take its height
        # from the icon and the frame, and read taller than the heart beside
        # it.  The square itself is pinned by the sheet, which beats
        # setFixedSize, so this only has to stop the icon from asking for more.
        self.setIconSize(QSize(SIZE, SIZE))


__all__ = ["ANIMATION_MS", "SIZE", "DislikeButton", "LikeButton", "blend"]
