"""The synchronised lyric view: one row per line, the sung one lit up.

Two things make this feel like karaoke rather than a text file.  The current
line is drawn bigger, in white, with a glow behind it, so the eye lands on it
without hunting; and the view scrolls to keep that line in the middle, smoothly
enough that it moves while the song does.

The glow is painted rather than styled.  Qt's style sheets have no text shadow,
and a highlight that is only a colour change is easy to lose against a bright
cover behind the panel - so the label draws its text several times, each pass a
little wider and fainter, and the crisp glyphs go on top.

Everything expensive is optional.  With animations off the line changes
instantly and the scrollbar jumps, which is the point of the switch: no
property animation, no eased value, no repaint clock.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.lyrics import Lyrics
from ui.theme import ACCENT, TEXT as FG, TEXT_DIM as DIM

LINE_SPACING = 4
SCROLL_MS = 260
"""How long a scroll to the current line takes; long enough to glide."""
GLOW_RADIUS = 3
GLOW_PASSES = 3
GLOW_BASE_ALPHA = 70
GLOW_FALLOFF = 0.45
GLOW_COLOR = QColor(255, 255, 255)
SUNG_SCALE = 1.12
"""The current line is this much larger, which is most of the karaoke effect."""
PAST_SCALE = 0.96


class GlowLabel(QLabel):
    """A label whose text can carry a soft white halo.

    The passes are painted from widest and faintest to narrowest and brightest,
    so the halo never washes over the glyphs it is meant to sit behind.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._glow = 0.0
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    @property
    def glow(self) -> float:
        return self._glow

    def set_glow(self, strength: float) -> None:
        """Set the halo strength, 0 for none and 1 for the full glow."""
        value = max(0.0, min(1.0, float(strength)))
        if abs(value - self._glow) < 0.01:
            return
        self._glow = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._glow <= 0.01:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        metrics = QFontMetrics(self.font())
        text = self.text()
        # Laid out by hand rather than by the base class, so the halo and the
        # glyphs are guaranteed to sit on exactly the same line.
        rect = self.contentsRect()
        x = rect.x() + (rect.width() - metrics.horizontalAdvance(text)) // 2
        y = rect.y() + (rect.height() + metrics.ascent() - metrics.descent()) // 2
        for step in range(GLOW_PASSES, 0, -1):
            alpha = int(GLOW_BASE_ALPHA * self._glow * (GLOW_FALLOFF ** (step - 1)))
            colour = QColor(GLOW_COLOR)
            colour.setAlpha(max(0, min(255, alpha)))
            painter.setPen(QPen(colour, GLOW_RADIUS * step * 0.6))
            for dx, dy in _halo_offsets(step):
                painter.drawText(x + dx, y + dy, text)
        painter.setPen(QPen(QColor(FG)))
        painter.drawText(x, y, text)
        painter.end()


def _halo_offsets(step: int) -> tuple[tuple[int, int], ...]:
    """The eight directions plus the centre, so the halo has no flat side."""
    radius = GLOW_RADIUS * step
    return (
        (0, 0),
        (radius, 0),
        (-radius, 0),
        (0, radius),
        (0, -radius),
        (radius, radius),
        (-radius, -radius),
        (radius, -radius),
        (-radius, radius),
    )


class KaraokeView(QScrollArea):
    """A scrollable column of lyric lines that follows the playback position."""

    def __init__(self, parent: QWidget | None = None, *, animations_enabled: bool = True) -> None:
        super().__init__(parent)
        self._lyrics = Lyrics()
        self._lines: list[GlowLabel] = []
        self._current = -1
        self._animations_enabled = bool(animations_enabled)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.viewport().setStyleSheet("background: transparent;")
        self._holder = QWidget()
        self._holder.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._holder)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(LINE_SPACING)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self._holder)
        self._scroll = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll.setDuration(SCROLL_MS)
        self._scroll.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # -- content -------------------------------------------------------------

    @property
    def lyrics(self) -> Lyrics:
        return self._lyrics

    @property
    def current_index(self) -> int:
        return self._current

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def set_lyrics(self, lyrics: Lyrics) -> None:
        """Replace the text; the position is re-applied by the next update."""
        self._lyrics = lyrics
        for label in self._lines:
            self._layout.removeWidget(label)
            label.deleteLater()
        self._lines = []
        self._current = -1
        plain = not lyrics.synchronized
        for line in lyrics.lines:
            label = GlowLabel(line.text)
            label.setObjectName("KaraokeLine")
            label.setProperty("current", False)
            label.setTextFormat(Qt.TextFormat.PlainText)
            self._layout.addWidget(label)
            self._lines.append(label)
        if plain:
            # Untimed text has no current line, so it is shown as one block at
            # the reading size rather than pretending to follow the song.
            for label in self._lines:
                self._style_line(label, current=False, sung=True)
        self._holder.setStyleSheet("background: transparent;")
        self.verticalScrollBar().setValue(0)

    def clear(self) -> None:
        self.set_lyrics(Lyrics())

    # -- following -----------------------------------------------------------

    @property
    def animations_enabled(self) -> bool:
        return self._animations_enabled

    def set_animations_enabled(self, enabled: bool) -> None:
        """Glide or jump; the switch from the settings page lands here."""
        enabled = bool(enabled)
        if enabled == self._animations_enabled:
            return
        self._animations_enabled = enabled
        if not enabled:
            self._scroll.stop()

    def set_position(self, position_ms: int) -> None:
        """Move the highlight to the line being sung at ``position_ms``."""
        if not self._lyrics.synchronized or not self._lines:
            return
        index = self._lyrics.index_at(position_ms)
        if index == self._current:
            return
        self._apply(index)

    def _apply(self, index: int) -> None:
        previous = self._current
        self._current = index
        for position, label in enumerate(self._lines):
            current = position == index
            sung = index >= 0 and position < index
            self._style_line(label, current=current, sung=sung)
        if index >= 0:
            self._scroll_to(self._lines[index])
        elif previous >= 0:
            self._scroll_to(self._lines[previous])

    def _style_line(self, label: GlowLabel, *, current: bool, sung: bool) -> None:
        """Give a line its size, colour and halo for the state it is in."""
        font = label.font()
        scale = SUNG_SCALE if current else (1.0 if not sung else PAST_SCALE)
        base = float(self.font().pointSizeF() or 10.0)
        target = round(base * scale, 1)
        if abs(font.pointSizeF() - target) > 0.01:
            font.setPointSizeF(target)
            label.setFont(font)
        if current:
            label.setStyleSheet(f"color: {FG}; background: transparent;")
            label.set_glow(1.0)
        elif sung:
            # Already sung: dimmed but still legible, the way a read-through
            # page greys out what is behind you.
            label.setStyleSheet(f"color: {DIM}; background: transparent;")
            label.set_glow(0.0)
        else:
            label.setStyleSheet(f"color: {ACCENT}; background: transparent;")
            label.set_glow(0.0)

    def _scroll_to(self, label: GlowLabel) -> None:
        """Put ``label`` near the middle of the viewport, gliding if allowed."""
        bar = self.verticalScrollBar()
        target = label.geometry().center().y() - self.viewport().height() // 2
        target = max(bar.minimum(), min(bar.maximum(), target))
        if not self._animations_enabled:
            self._scroll.stop()
            bar.setValue(target)
            return
        if abs(bar.value() - target) <= 1:
            return
        self._scroll.stop()
        self._scroll.setStartValue(bar.value())
        self._scroll.setEndValue(target)
        self._scroll.start()

    def sizeHint(self):  # noqa: N802 - Qt naming
        from PySide6.QtCore import QSize

        return QSize(280, 240)


__all__ = ["GlowLabel", "KaraokeView", "SCROLL_MS"]
