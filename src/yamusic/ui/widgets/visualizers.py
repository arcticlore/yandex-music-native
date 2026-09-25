"""Audio visualizers (QPainter, 60 FPS driven by analyzer signals).

1. ClassicSpectrumBars — logarithmic bars with floating peak caps.
2. NeonWaveform — smoothed glowing time-domain wave.
3. CircularVisualizer — radial spectrum around the album cover.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from yamusic.constants import SPECTRUM_BANDS, WAVE_POINTS


def _accent_gradient(height: float) -> QLinearGradient:
    grad = QColor(255, 219, 77)
    top = QColor(255, 77, 130)
    g = QLinearGradient(0, height, 0, 0)
    g.setColorAt(0.0, grad)
    g.setColorAt(0.55, QColor(255, 140, 66))
    g.setColorAt(1.0, top)
    return g


class _VisualizerBase(QWidget):
    """Common data plumbing: spectrum + wave lists from the analyzer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._spectrum: list[float] = [0.0] * SPECTRUM_BANDS
        self._wave: list[float] = [0.0] * WAVE_POINTS
        self._peaks: list[float] = [0.0] * SPECTRUM_BANDS
        self._bg = QColor("#0f0f13")

    def set_spectrum(self, values: list[float]) -> None:
        if len(values) != SPECTRUM_BANDS:
            values = (values + [0.0] * SPECTRUM_BANDS)[:SPECTRUM_BANDS]
        self._spectrum = values
        for i, v in enumerate(values):
            if v >= self._peaks[i]:
                self._peaks[i] = v
            else:
                self._peaks[i] = max(v, self._peaks[i] - 0.015)
        self.update()

    def set_wave(self, values: list[float]) -> None:
        if len(values) != WAVE_POINTS:
            values = (values + [0.0] * WAVE_POINTS)[:WAVE_POINTS]
        self._wave = values
        self.update()

    def reset_data(self) -> None:
        self._spectrum = [0.0] * SPECTRUM_BANDS
        self._wave = [0.0] * WAVE_POINTS
        self._peaks = [0.0] * SPECTRUM_BANDS
        self.update()


class SpectrumBars(_VisualizerBase):
    """Classic spectrum bars with floating peak markers."""

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._bg)

        w = self.width()
        h = self.height()
        n = SPECTRUM_BANDS
        gap = max(2, w // 240)
        bar_w = max(2.0, (w - gap * (n + 1)) / n)
        grad = _accent_gradient(float(h))

        for i, value in enumerate(self._spectrum):
            x = gap + i * (bar_w + gap)
            bar_h = max(2.0, value * (h - 16))
            rect = QRectF(x, h - bar_h - 4, bar_w, bar_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            painter.drawRoundedRect(rect, min(3.0, bar_w / 2), min(3.0, bar_w / 2))

            # floating peak
            peak = self._peaks[i]
            py = h - peak * (h - 16) - 4
            painter.setBrush(QColor(240, 240, 250, 220))
            painter.drawRect(QRectF(x, max(2.0, py - 2), bar_w, 2))

        # baseline
        painter.setPen(QPen(QColor(42, 42, 54), 1))
        painter.drawLine(0, h - 1, w, h - 1)
        painter.end()


class NeonWave(_VisualizerBase):
    """Smooth glowing waveform (time domain)."""

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._bg)

        w = float(self.width())
        h = float(self.height())
        mid = h / 2.0
        pts = len(self._wave)
        if pts < 2:
            painter.end()
            return

        path = QPainterPath()
        x_step = w / (pts - 1)
        first = True
        for i, value in enumerate(self._wave):
            x = i * x_step
            y = mid - value * (h * 0.42)
            if first:
                path.moveTo(x, y)
                first = False
            else:
                # smooth with simple quadratic through midpoints
                prev_x = (i - 1) * x_step
                prev_y = mid - self._wave[i - 1] * (h * 0.42)
                cx = (prev_x + x) / 2.0
                path.quadTo(prev_x, prev_y, cx, (prev_y + y) / 2.0)
                if i == pts - 1:
                    path.lineTo(x, y)

        # glow pass
        glow = QPen(QColor(255, 219, 77, 60), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(glow)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        mid_pen = QPen(QColor(255, 219, 77, 120), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(mid_pen)
        painter.drawPath(path)

        bright = QPen(QColor(255, 255, 255, 235), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(bright)
        painter.drawPath(path)

        # centre line
        painter.setPen(QPen(QColor(42, 42, 54), 1))
        painter.drawLine(QPointF(0, mid), QPointF(w, mid))
        painter.end()


class CircularVisualizer(_VisualizerBase):
    """Radial spectrum bars around a centred album cover."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cover: QPixmap | None = None

    def set_cover(self, pixmap: QPixmap) -> None:
        self._cover = pixmap
        self.update()

    def clear_cover(self) -> None:
        self._cover = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._bg)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        min_side = min(self.width(), self.height())
        cover_r = min_side * 0.22
        inner_r = cover_r + min_side * 0.045
        max_len = min_side * 0.18
        n = SPECTRUM_BANDS

        # cover with circular clip
        if self._cover is not None and not self._cover.isNull():
            target = QRectF(cx - cover_r, cy - cover_r, cover_r * 2, cover_r * 2)
            painter.save()
            path = QPainterPath()
            path.addEllipse(target)
            painter.setClipPath(path)
            scaled = self._cover.scaled(
                int(cover_r * 2), int(cover_r * 2),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(target.topLeft(), scaled)
            painter.restore()
            painter.setPen(QPen(QColor(255, 219, 77, 160), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(target)
        else:
            painter.setPen(QPen(QColor(42, 42, 54), 2))
            painter.setBrush(QColor(29, 29, 38))
            painter.drawEllipse(QPointF(cx, cy), cover_r, cover_r)

        # radial bars — mirrored spectrum (low freqs at bottom for symmetry)
        order = list(range(n))
        for i in order:
            value = self._spectrum[i]
            angle = (i / n) * 2.0 * math.pi - math.pi / 2.0
            length = 6.0 + value * max_len
            x1 = cx + math.cos(angle) * inner_r
            y1 = cy + math.sin(angle) * inner_r
            x2 = cx + math.cos(angle) * (inner_r + length)
            y2 = cy + math.sin(angle) * (inner_r + length)
            color = QColor.fromHsvF(
                (0.13 + 0.03 * math.sin(i * 0.4)) % 1.0,
                0.85,
                0.55 + 0.45 * value,
                0.35 + 0.65 * value,
            )
            pen = QPen(color, max(2.0, (2 * math.pi * inner_r / n) * 0.45))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        painter.end()
