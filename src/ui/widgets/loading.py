"""An indeterminate spinner shared by everything that waits on the network.

It lives here rather than in the login dialog because the collection, the
search and the login all say the same thing while they wait, and three copies
of the same 40-line arc is three places to fix the colour in.

The widget also honours the global animation switch.  With animations off the
arc is still drawn - a user needs to see that the app is busy, silence would
look like a hang - but it stops turning, because the whole point of the switch
is to stop a repaint clock from spinning at 16 FPS for as long as the request
takes.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ui.theme import ACCENT

SPIN_INTERVAL_MS = 60
SPIN_STEP_DEGREES = 30
ARC_SWEEP_DEGREES = 110
STATIC_ANGLE = 20
"""Where the frozen arc sits: still visibly a progress mark, not a full ring."""

DEFAULT_SIZE = 18
ARC_INSET = 3
ARC_WIDTH = 2.0


class Spinner(QWidget):
    """Lightweight indeterminate progress indicator."""

    def __init__(
        self,
        size: int = DEFAULT_SIZE,
        parent: QWidget | None = None,
        *,
        animations_enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        self._angle = 0
        self._size = size
        self._animated = bool(animations_enabled)
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(SPIN_INTERVAL_MS)
        self._timer.timeout.connect(self._advance)
        self.setVisible(False)

    def set_animations_enabled(self, enabled: bool) -> None:
        """Turn the rotation on or off, staying visible either way.

        Called live from the settings page, so the currently running animation
        has to be started or stopped to match the new setting rather than
        waiting for the next request to pick it up.
        """
        enabled = bool(enabled)
        if enabled == self._animated:
            return
        self._animated = enabled
        if not self.isVisible():
            return
        if enabled:
            self._timer.start()
        else:
            self._timer.stop()
            self._angle = 0
        self.update()

    def start(self) -> None:
        self.setVisible(True)
        if self._animated:
            self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def is_spinning(self) -> bool:
        return self._timer.isActive()

    def _advance(self) -> None:
        self._angle = (self._angle + SPIN_STEP_DEGREES) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(ACCENT), ARC_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = self.rect().adjusted(ARC_INSET, ARC_INSET, -ARC_INSET, -ARC_INSET)
        # Qt measures the arc counter-clockwise from 3 o'clock, hence the minus
        # signs: the sweep is meant to trail the leading end of the mark.
        angle = self._angle if self._animated else STATIC_ANGLE
        painter.drawArc(rect, -angle * 16, -ARC_SWEEP_DEGREES * 16)
        painter.end()


__all__ = ["DEFAULT_SIZE", "SPIN_INTERVAL_MS", "Spinner"]
