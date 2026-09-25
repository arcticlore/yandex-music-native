"""The small rounded cover in the player bar.

A ``QLabel`` with a style sheet cannot round a pixmap, and the 56x56 cover is
the one place where a hard rectangle would break the soft look of the bar. The
pixmap is scaled, clipped to a rounded path and given a soft drop shadow here,
with a note glyph as the idle state.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.theme import COVER_SIZE, SURFACE, TEXT_MUTED

BORDER = QColor(255, 255, 255, 18)
SHADOW = QColor(0, 0, 0, 120)
RADIUS = 8.0


class CoverFrame(QWidget):
    """Fixed size rounded cover with an idle placeholder."""

    def __init__(self, size: int = COVER_SIZE, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._size = size
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @property
    def side(self) -> int:
        """Edge length in pixels; ``QWidget.size`` stays a method."""
        return self._size

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        """Show ``pixmap`` scaled and cropped to a square, or the placeholder."""
        if pixmap is None or pixmap.isNull():
            self._pixmap = QPixmap()
        else:
            self._pixmap = pixmap.scaled(
                self._size,
                self._size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.update()

    def setPixmap(self, pixmap: QPixmap | None) -> None:  # noqa: N802
        """Alias for :meth:`set_pixmap` so the widget drops into QLabel slots."""
        self.set_pixmap(pixmap)

    def clear(self) -> None:
        self.set_pixmap(None)

    # -- painting ----------------------------------------------------------

    def _rounded_path(self, inset: float = 0.0) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(inset, inset, self._size - 2 * inset, self._size - 2 * inset),
            RADIUS,
            RADIUS,
        )
        return path

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = QRectF(0, 0, self._size, self._size)
        if self._pixmap.isNull():
            self._paint_placeholder(painter, target)
        else:
            self._paint_shadow(painter, target)
            painter.save()
            painter.setClipPath(self._rounded_path())
            painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
            painter.restore()
        painter.setPen(QPen(BORDER, 1))
        painter.drawPath(self._rounded_path(0.5))
        painter.end()

    def _paint_shadow(self, painter: QPainter, target: QRectF) -> None:
        """A two pixel drop under the cover, so it lifts off the bar."""
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(SHADOW)
        painter.translate(0, 2)
        painter.drawPath(self._rounded_path())
        painter.restore()

    def _paint_placeholder(self, painter: QPainter, target: QRectF) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(target, QColor(SURFACE))
        font = QFont(painter.font())
        font.setPixelSize(round(self._size * 0.42))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(target.translated(0, -1), Qt.AlignmentFlag.AlignCenter, "♪")
        painter.restore()


__all__ = ["BORDER", "RADIUS", "SHADOW", "CoverFrame"]
