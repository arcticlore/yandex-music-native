"""Vector icons painted at runtime, so the UI never depends on emoji fonts.

An emoji like 🔍 or 📊 is drawn by whatever font the desktop happens to have, and
on a system without the right colour font it comes out as an empty box.  Every
glyph in the player bar is therefore a few lines of :class:`QPainter` code that
renders identically everywhere, takes its colour from :mod:`ui.theme` and can be
tested by comparing pixels rather than by reading a string.

The functions all return a :class:`QIcon` built on a transparent pixmap; the
painting helpers take an explicit rectangle so the same code can be used inline,
as the list delegate does for the equaliser glyph.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget

from ui.theme import ACCENT, TEXT, TEXT_DIM

ICON_SIZE = 20
STROKE = 1.7


def _canvas(size: int, scale: float = 1.0) -> tuple[QPixmap, QPainter]:
    """A transparent pixmap of ``size`` px plus a painter ready to draw on it."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if scale != 1.0:
        painter.scale(scale, scale)
    painter.setPen(QPen(Qt.PenStyle.NoPen))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    return pixmap, painter


def _finish(pixmap: QPixmap, painter: QPainter) -> QIcon:
    painter.end()
    return QIcon(pixmap)


def _pen(colour: str, width: float = STROKE) -> QPen:
    pen = QPen(QColor(colour), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


# -- individual glyphs -------------------------------------------------------


def magnifier(size: int = ICON_SIZE, colour: str = TEXT_DIM) -> QIcon:
    """A search glyph: a lens and a handle."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(_pen(colour))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    centre = QPointF(box * 0.43, box * 0.43)
    radius = box * 0.27
    painter.drawEllipse(centre, radius, radius)
    painter.drawLine(
        QPointF(box * 0.63, box * 0.63),
        QPointF(box * 0.87, box * 0.87),
    )
    return _finish(pixmap, painter)


def shuffle(size: int = ICON_SIZE, colour: str = TEXT_DIM) -> QIcon:
    """Two crossing arrows: the classic shuffle mark."""
    pixmap, painter = _canvas(size)
    box = size
    top, bottom = box * 0.28, box * 0.72
    left, right = box * 0.2, box * 0.8
    painter.setPen(_pen(colour))
    path = QPainterPath()
    path.moveTo(left, top)
    path.lineTo(right - box * 0.18, top)
    path.cubicTo(
        QPointF(right - box * 0.02, top), QPointF(right - box * 0.02, bottom), QPointF(right, bottom)
    )
    path.moveTo(left, bottom)
    path.lineTo(right - box * 0.18, bottom)
    path.cubicTo(QPointF(right - box * 0.02, bottom), QPointF(right - box * 0.02, top), QPointF(right, top))
    painter.drawPath(path)
    head = box * 0.16
    for y in (top, bottom):
        painter.setBrush(QColor(colour))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(right, y),
                    QPointF(right - head, y - head * 0.55),
                    QPointF(right - head, y + head * 0.55),
                ]
            )
        )
    return _finish(pixmap, painter)


def repeat(size: int = ICON_SIZE, colour: str = TEXT_DIM) -> QIcon:
    """A closed loop with an arrowhead: the classic repeat mark."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(_pen(colour))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(box * 0.22, box * 0.34, box * 0.5, box * 0.32)
    painter.drawArc(rect, 30 * 16, 300 * 16)
    painter.setBrush(QColor(colour))
    head = box * 0.16
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(rect.right() - 1, rect.top() - 1),
                QPointF(rect.right() - head, rect.top() - head * 0.3),
                QPointF(rect.right() - head * 0.5, rect.top() + head * 0.4),
            ]
        )
    )
    return _finish(pixmap, painter)


def speaker(size: int = ICON_SIZE, colour: str = TEXT_DIM, muted: bool = False) -> QIcon:
    """A speaker body with sound waves, or a slash when muted."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(_pen(colour))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    body = QPolygonF(
        [
            QPointF(box * 0.16, box * 0.4),
            QPointF(box * 0.32, box * 0.4),
            QPointF(box * 0.5, box * 0.22),
            QPointF(box * 0.5, box * 0.78),
            QPointF(box * 0.32, box * 0.6),
            QPointF(box * 0.16, box * 0.6),
        ]
    )
    painter.drawPolygon(body)
    if muted:
        painter.drawLine(QPointF(box * 0.62, box * 0.4), QPointF(box * 0.88, box * 0.62))
        painter.drawLine(QPointF(box * 0.88, box * 0.4), QPointF(box * 0.62, box * 0.62))
        return _finish(pixmap, painter)
    for radius in (0.16, 0.28):
        centre = QPointF(box * 0.5, box * 0.5)
        painter.drawArc(
            QRectF(
                centre.x() - radius * box,
                centre.y() - radius * box,
                radius * 2 * box,
                radius * 2 * box,
            ),
            -55 * 16,
            110 * 16,
        )
    return _finish(pixmap, painter)


def bars(size: int = ICON_SIZE, colour: str = TEXT_DIM) -> QIcon:
    """Three spectrum bars: the «Спектр» mode."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    for x, height in ((0.22, 0.34), (0.45, 0.62), (0.68, 0.46)):
        width = box * 0.14
        painter.drawRoundedRect(
            QRectF(box * x - width / 2, box * 0.78 - box * height, width, box * height),
            width / 2,
            width / 2,
        )
    return _finish(pixmap, painter)


def wave(size: int = ICON_SIZE, colour: str = TEXT_DIM) -> QIcon:
    """A sine stroke: the «Волна» mode."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(_pen(colour, STROKE * 1.1))
    path = QPainterPath()
    middle = box * 0.5
    amplitude = box * 0.2
    for step in range(41):
        x = box * 0.14 + step * (box * 0.72 / 40)
        y = middle - math.sin(step / 40 * math.tau) * amplitude
        if step == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    painter.drawPath(path)
    return _finish(pixmap, painter)


def ring(size: int = ICON_SIZE, colour: str = TEXT_DIM) -> QIcon:
    """A ring with a dot: the «Круг» mode."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(_pen(colour, STROKE * 1.1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(box * 0.5, box * 0.5), box * 0.3, box * 0.3)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawEllipse(QPointF(box * 0.5, box * 0.5), box * 0.09, box * 0.09)
    return _finish(pixmap, painter)


VISUALIZER_ICONS = {"spectrum": bars, "wave": wave, "circular": ring}


def visualizer_icon(mode: str, size: int = ICON_SIZE, colour: str = TEXT_DIM) -> QIcon:
    """The icon of a visualiser mode, falling back to the bars."""
    factory = VISUALIZER_ICONS.get(mode, bars)
    return factory(size, colour)


def play(size: int = ICON_SIZE, colour: str = ACCENT) -> QIcon:
    """A filled triangle."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(box * 0.32, box * 0.22),
                QPointF(box * 0.8, box * 0.5),
                QPointF(box * 0.32, box * 0.78),
            ]
        )
    )
    return _finish(pixmap, painter)


def pause(size: int = ICON_SIZE, colour: str = ACCENT) -> QIcon:
    """Two rounded bars."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    for x in (0.34, 0.54):
        painter.drawRoundedRect(
            QRectF(box * x, box * 0.24, box * 0.12, box * 0.52),
            box * 0.04,
            box * 0.04,
        )
    return _finish(pixmap, painter)


def skip(size: int = ICON_SIZE, colour: str = TEXT_DIM, backwards: bool = False) -> QIcon:
    """A double triangle with a bar: previous / next."""
    pixmap, painter = _canvas(size)
    box = size
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    points = [
        [QPointF(box * 0.28, box * 0.24), QPointF(box * 0.62, box * 0.5), QPointF(box * 0.28, box * 0.76)],
        [QPointF(box * 0.5, box * 0.24), QPointF(box * 0.84, box * 0.5), QPointF(box * 0.5, box * 0.76)],
    ]
    if backwards:
        points = [
            [QPointF(box - point.x(), point.y()) for point in reversed(triangle)]
            for triangle in reversed(points)
        ]
    for triangle in points:
        painter.drawPolygon(QPolygonF(triangle))
    bar_x = box * 0.9 if backwards else box * 0.16
    painter.drawRoundedRect(
        QRectF(bar_x - box * 0.03, box * 0.24, box * 0.06, box * 0.52),
        box * 0.03,
        box * 0.03,
    )
    return _finish(pixmap, painter)


def heart(size: int = ICON_SIZE, colour: str = TEXT_DIM, filled: bool = True) -> QIcon:
    """A heart for the like button, filled or outlined."""
    pixmap, painter = _canvas(size)
    box = size
    path = QPainterPath()
    path.moveTo(box * 0.5, box * 0.82)
    path.cubicTo(
        QPointF(box * 0.06, box * 0.5),
        QPointF(box * 0.22, box * 0.14),
        QPointF(box * 0.5, box * 0.34),
    )
    path.cubicTo(
        QPointF(box * 0.78, box * 0.14),
        QPointF(box * 0.94, box * 0.5),
        QPointF(box * 0.5, box * 0.82),
    )
    painter.setPen(_pen(colour, STROKE))
    if filled:
        painter.setBrush(QColor(colour))
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    return _finish(pixmap, painter)


# -- inline helpers ----------------------------------------------------------


def draw_equalizer(painter: QPainter, rect: QRectF, colour: str = ACCENT) -> None:
    """Four bars of a mini equaliser inside ``rect``, used by the row delegate.

    A playing row shows this instead of its number, so the eye finds the current
    track by shape rather than by reading.
    """
    bar_width = rect.width() / 7.0
    heights = (0.42, 0.95, 0.62, 0.3)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    for index, ratio in enumerate(heights):
        height = rect.height() * ratio
        painter.drawRoundedRect(
            QRectF(
                rect.left() + index * bar_width * 1.75,
                rect.bottom() - height,
                bar_width,
                height,
            ),
            bar_width / 2,
            bar_width / 2,
        )
    painter.restore()


def placeholder_pixmap(size: int, radius: int, top: str, bottom: str) -> QPixmap:
    """The gradient artwork placeholder used while a cover is loading.

    Rows must never flash an empty hole, so a missing cover becomes a soft
    two-stop gradient of the app's own surfaces with a note on top - visually
    quiet, unmistakably «no artwork yet».
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), radius, radius)
    painter.setClipPath(path)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor(top))
    gradient.setColorAt(1.0, QColor(bottom))
    painter.fillRect(QRectF(0, 0, size, size), gradient)
    painter.setPen(QPen(QColor(TEXT), 1))
    font = QFont(painter.font())
    font.setPixelSize(max(9, round(size * 0.4)))
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "♪")
    painter.end()
    return pixmap


def icon_size_for(widget: QWidget) -> int:
    """The glyph size that matches ``widget``'s font, used for inline icons."""
    return max(12, widget.fontMetrics().height())


__all__ = [
    "ICON_SIZE",
    "VISUALIZER_ICONS",
    "bars",
    "draw_equalizer",
    "heart",
    "icon_size_for",
    "magnifier",
    "pause",
    "placeholder_pixmap",
    "play",
    "repeat",
    "ring",
    "shuffle",
    "skip",
    "speaker",
    "visualizer_icon",
    "wave",
]
