"""Album cover widget with graceful placeholder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class CoverView(QWidget):
    """Rounded-corner album art; shows a neutral placeholder when empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._radius = 8
        self.setMinimumSize(48, 48)

    def set_image_path(self, path: str) -> None:
        pm = QPixmap(path)
        if pm.isNull():
            self._pixmap = None
        else:
            self._pixmap = pm
        self.update()

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self.update()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def set_radius(self, radius: int) -> None:
        self._radius = radius
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        if self._pixmap is None or self._pixmap.isNull():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(29, 29, 38))
            painter.drawRoundedRect(rect, self._radius, self._radius)
            painter.setPen(QColor(90, 90, 110))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♪")
        else:
            scaled = self._pixmap.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            # centre-crop
            x = (scaled.width() - rect.width()) // 2
            y = (scaled.height() - rect.height()) // 2
            painter.setClipRect(rect)
            painter.drawPixmap(rect.topLeft(), scaled, scaled.rect().adjusted(x, y, -x, -y))
        painter.end()


def load_cover(path: str | Path) -> QPixmap | None:
    if not path:
        return None
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else None
