"""Audio visualizers: bars, neon wave and a radial spectrum around the cover.

The engine publishes spectrum and waveform frames from
:mod:`core.audio_engine`; this module turns them into something that can be
watched. Three details matter for a stable 60 FPS picture:

* every frame is passed through an attack/release follower (:class:`Smoother`),
  because raw FFT frames are noisy and jumpy at 60 Hz;
* one :class:`Smoothing` instance per visualizer keeps its own smoothed values
  and peak caps, so switching between the three styles does not reset motion;
* the repaint clock is a single 16 ms timer that is stopped while the widget is
  hidden, so a minimised window costs nothing.

The smoothing maths lives in plain functions and lists, independent of Qt, so it
can be tested without a running application.
"""

from __future__ import annotations

import math
from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QVBoxLayout, QWidget

FRAME_INTERVAL_MS = round(1000 / 60)
DEFAULT_BANDS = 64
WAVE_POINTS = 512
DEFAULT_ATTACK = 0.62
DEFAULT_RELEASE = 0.12
DEFAULT_PEAK_FALL = 0.014
BACKGROUND = QColor("#0f0f13")
BASELINE = QColor("#2a2a36")
ACCENT_LOW = QColor("#ff4d82")
ACCENT_MID = QColor("#ff8c42")
ACCENT_HIGH = QColor("#ffdb4d")
PEAK_COLOR = QColor(240, 240, 250, 210)
IDLE_LEVEL = 0.045
IDLE_SPEED = 0.0016


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Constrain ``value`` to the unit interval."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def smooth_step(current: float, target: float, attack: float, release: float) -> float:
    """One exponential step towards ``target``: fast rise, slow fall."""
    coefficient = attack if target > current else release
    return current + coefficient * (target - current)


def update_peaks(peaks: Sequence[float], values: Sequence[float], fall: float) -> list[float]:
    """Float peak caps: they latch onto a rising value and sink at ``fall``."""
    result: list[float] = []
    for index, value in enumerate(values):
        old = peaks[index] if index < len(peaks) else 0.0
        result.append(value if value >= old else max(value, old - fall))
    return result


def fit_bands(values: Sequence[float], size: int = DEFAULT_BANDS) -> list[float]:
    """Pad or truncate an FFT frame to exactly ``size`` values in ``[0, 1]``."""
    clipped = [clamp(float(value)) for value in values[:size]]
    if len(clipped) < size:
        clipped.extend([0.0] * (size - len(clipped)))
    return clipped


def fit_wave(values: Sequence[float], size: int = WAVE_POINTS) -> list[float]:
    """Pad or truncate a waveform frame to exactly ``size`` values."""
    clipped = [max(-1.0, min(1.0, float(value))) for value in values[:size]]
    if len(clipped) < size:
        clipped.extend([0.0] * (size - len(clipped)))
    return clipped


def idle_target(index: int, size: int, phase: float) -> float:
    """A slow breathing level used when nothing is playing."""
    if size <= 0:
        return 0.0
    position = index / size
    return IDLE_LEVEL * (0.5 + 0.5 * math.sin(position * math.tau + phase))


def accent_gradient(height: float) -> QLinearGradient:
    """Bottom-to-top accent used by the bars and the wave."""
    gradient = QLinearGradient(0, height, 0, 0)
    gradient.setColorAt(0.0, ACCENT_LOW)
    gradient.setColorAt(0.55, ACCENT_MID)
    gradient.setColorAt(1.0, ACCENT_HIGH)
    return gradient


class Smoother:
    """Attack/release follower with latching peak caps, one value per band."""

    def __init__(
        self,
        size: int = DEFAULT_BANDS,
        attack: float = DEFAULT_ATTACK,
        release: float = DEFAULT_RELEASE,
        peak_fall: float = DEFAULT_PEAK_FALL,
    ) -> None:
        self.attack = attack
        self.release = release
        self.peak_fall = peak_fall
        self._size = max(1, int(size))
        self._values = [0.0] * self._size
        self._peaks = [0.0] * self._size

    @property
    def size(self) -> int:
        return self._size

    @property
    def values(self) -> list[float]:
        return self._values

    @property
    def peaks(self) -> list[float]:
        return self._peaks

    def resize(self, size: int) -> None:
        """Adapt to a new frame length, keeping the overlapping values."""
        target = max(1, int(size))
        if target == self._size:
            return
        if target > self._size:
            self._values.extend([0.0] * (target - self._size))
            self._peaks.extend([0.0] * (target - self._size))
        else:
            del self._values[target:]
            del self._peaks[target:]
        self._size = target

    def step(self, target: Sequence[float]) -> tuple[list[float], list[float]]:
        """Advance one frame and return the new ``(values, peaks)``."""
        if len(target) != self._size:
            self.resize(len(target))
        for index, value in enumerate(target):
            self._values[index] = smooth_step(self._values[index], value, self.attack, self.release)
        self._peaks = update_peaks(self._peaks, self._values, self.peak_fall)
        return self._values, self._peaks

    def adopt(self, values: Sequence[float], peaks: Sequence[float] | None = None) -> None:
        """Take over the motion state of another follower (style switches)."""
        target = max(1, len(values))
        self._values = [clamp(value) for value in values[:target]]
        self._values.extend([0.0] * (target - len(self._values)))
        source = list(peaks) if peaks is not None else self._values
        self._peaks = [clamp(value) for value in source[:target]]
        self._peaks.extend([0.0] * (target - len(self._peaks)))
        self._size = target

    def idle(self, phase: float) -> None:
        """Decay towards a slow breathing curve, used while nothing plays."""
        for index in range(self._size):
            level = idle_target(index, self._size, phase)
            self._values[index] = smooth_step(self._values[index], level, 0.2, 0.08)
        self._peaks = update_peaks(self._peaks, self._values, self.peak_fall)

    def reset(self) -> None:
        self._values = [0.0] * self._size
        self._peaks = [0.0] * self._size


class WaveSmoother:
    """The same idea for the time-domain waveform, centred on zero."""

    def __init__(
        self,
        size: int = WAVE_POINTS,
        attack: float = 0.7,
        release: float = 0.25,
    ) -> None:
        self.attack = attack
        self.release = release
        self._size = max(2, int(size))
        self._values = [0.0] * self._size

    @property
    def size(self) -> int:
        return self._size

    @property
    def values(self) -> list[float]:
        return self._values

    def resize(self, size: int) -> None:
        target = max(2, int(size))
        if target == self._size:
            return
        if target > self._size:
            self._values.extend([0.0] * (target - self._size))
        else:
            del self._values[target:]
        self._size = target

    def step(self, target: Sequence[float]) -> list[float]:
        if len(target) != self._size:
            self.resize(len(target))
        for index, value in enumerate(target):
            current = self._values[index]
            coefficient = self.attack if abs(value) > abs(current) else self.release
            self._values[index] = current + coefficient * (value - current)
        return self._values

    def adopt(self, values: Sequence[float]) -> None:
        """Take over the motion state of another follower (style switches)."""
        target = max(2, len(values))
        self._values = [max(-1.0, min(1.0, value)) for value in values[:target]]
        self._values.extend([0.0] * (target - len(self._values)))
        self._size = target

    def decay(self) -> list[float]:
        for index in range(self._size):
            self._values[index] = smooth_step(self._values[index], 0.0, 0.2, 0.12)
        return self._values

    def reset(self) -> None:
        self._values = [0.0] * self._size


class VisualizerBase(QWidget):
    """Data plumbing plus the 60 FPS repaint clock shared by all styles."""

    mode = "spectrum"

    def __init__(self, parent: QWidget | None = None, bands: int = DEFAULT_BANDS) -> None:
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._spectrum = Smoother(bands)
        self._wave = WaveSmoother()
        self._target_bands: list[float] = [0.0] * bands
        self._target_wave: list[float] = [0.0] * WAVE_POINTS
        self._phase = 0.0
        self._active = False
        self._pending = False
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    # -- data ---------------------------------------------------------

    def set_spectrum(self, values: Sequence[float]) -> None:
        """Feed an FFT frame; rendering happens on the next clock tick."""
        self._target_bands = fit_bands(values, self._spectrum.size)
        self._active = True
        self._pending = True

    def set_waveform(self, values: Sequence[float]) -> None:
        """Feed a time-domain frame."""
        self._target_wave = fit_wave(values, self._wave.size)
        self._active = True
        self._pending = True

    def set_idle(self) -> None:
        """No audio is playing: decay and breathe instead of freezing."""
        self._active = False
        self._pending = False

    def set_active(self, active: bool) -> None:
        """Force the idle/animation state, used when styles are switched."""
        self._active = bool(active)

    def seed_from(self, other: VisualizerBase) -> None:
        """Adopt the motion state of ``other`` so a style switch does not restart."""
        self._spectrum.adopt(other._spectrum.values, other._spectrum.peaks)
        self._wave.adopt(other._wave.values)
        self._target_bands = list(other._target_bands)
        self._target_wave = list(other._target_wave)
        self._phase = other._phase
        self._active = other._active
        self._pending = other._pending
        self.update()

    @property
    def active(self) -> bool:
        return self._active

    def reset_data(self) -> None:
        self._spectrum.reset()
        self._wave.reset()
        self._target_bands = [0.0] * self._spectrum.size
        self._target_wave = [0.0] * self._wave.size
        self._phase = 0.0
        self.update()

    def set_bands(self, bands: int) -> None:
        self._spectrum.resize(bands)
        self._target_bands = [0.0] * self._spectrum.size
        self.update()

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    def frame_interval(self) -> int:
        return self._timer.interval()

    # -- clock --------------------------------------------------------

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.stop()

    def _tick(self) -> None:
        self.advance()
        self.update()

    def advance(self) -> None:
        """Move the animation one frame: towards the data, or breathing."""
        self._phase += IDLE_SPEED
        if self._active:
            self._spectrum.step(self._target_bands)
            self._wave.step(self._target_wave)
        else:
            self._spectrum.idle(self._phase)
            self._wave.decay()
        self._pending = False

    def _paint_background(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), BACKGROUND)

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_background(painter)
        self._paint(painter)
        painter.end()

    def _paint(self, painter: QPainter) -> None:
        raise NotImplementedError


class BarsVisualizer(VisualizerBase):
    """Logarithmic spectrum bars with floating peak caps."""

    mode = "spectrum"

    def _paint(self, painter: QPainter) -> None:
        values = self._spectrum.values
        peaks = self._spectrum.peaks
        width = self.width()
        height = self.height()
        count = len(values)
        if count == 0:
            return
        gap = max(2, width // 240)
        bar_width = max(2.0, (width - gap * (count + 1)) / count)
        gradient = accent_gradient(float(height))
        painter.setPen(Qt.PenStyle.NoPen)
        for index, value in enumerate(values):
            x = gap + index * (bar_width + gap)
            bar_height = max(2.0, value * (height - 16))
            painter.setBrush(gradient)
            painter.drawRoundedRect(
                QRectF(x, height - bar_height - 4, bar_width, bar_height),
                min(3.0, bar_width / 2),
                min(3.0, bar_width / 2),
            )
            peak = peaks[index]
            peak_y = height - peak * (height - 16) - 4
            painter.setBrush(PEAK_COLOR)
            painter.drawRect(QRectF(x, max(2.0, peak_y - 2), bar_width, 2))
        painter.setPen(QPen(BASELINE, 1))
        painter.drawLine(0, height - 1, width, height - 1)


class WaveVisualizer(VisualizerBase):
    """Smoothed time-domain wave with a soft glow."""

    mode = "wave"

    def _paint(self, painter: QPainter) -> None:
        values = self._wave.values
        count = len(values)
        if count < 2:
            return
        width = self.width()
        height = self.height()
        middle = height / 2.0
        amplitude = (height / 2.0) - 6.0
        path = QPainterPath()
        for index, value in enumerate(values):
            x = index * (width / (count - 1))
            y = middle - value * amplitude
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        glow = QPen(QColor(255, 77, 130, 70), 7)
        glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        glow.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(glow)
        painter.drawPath(path)
        painter.setPen(QPen(QColor(255, 219, 77, 235), 2))
        painter.drawPath(path)
        painter.setPen(QPen(BASELINE, 1))
        painter.drawLine(0, height - 1, width, height - 1)


class RadialVisualizer(VisualizerBase):
    """Radial spectrum drawn around the current cover art."""

    mode = "circular"

    def __init__(self, parent: QWidget | None = None, bands: int = DEFAULT_BANDS) -> None:
        super().__init__(parent, bands)
        self._cover: QPixmap | None = None

    def set_cover(self, pixmap: QPixmap | None) -> None:
        """Draw the cover in the middle of the ring."""
        self._cover = pixmap
        self.update()

    def clear_cover(self) -> None:
        self.set_cover(None)

    def _paint(self, painter: QPainter) -> None:
        values = self._spectrum.values
        count = len(values)
        if count == 0:
            return
        width = self.width()
        height = self.height()
        center = QPointF(width / 2.0, height / 2.0)
        radius = max(24.0, min(width, height) / 2.0 - 26.0)
        thickness = max(4.0, radius * 0.14)
        start_angle = 90 * 16
        span = 360 * 16 / count
        gradient = QConicalGradient(center, -90)
        gradient.setColorAt(0.0, ACCENT_HIGH)
        gradient.setColorAt(0.5, ACCENT_MID)
        gradient.setColorAt(1.0, ACCENT_LOW)
        painter.setPen(QPen(gradient, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for index, value in enumerate(values):
            length = radius + thickness / 2.0 + value * (radius * 0.55)
            angle = (start_angle + index * span) % (360 * 16)
            painter.drawLine(
                center,
                QPointF(
                    center.x() + length * math.cos(math.radians(angle / 16.0)),
                    center.y() + length * math.sin(math.radians(angle / 16.0)),
                ),
            )
        if self._cover is not None and not self._cover.isNull():
            side = int(radius * 1.5)
            painter.drawPixmap(
                int(center.x() - side / 2),
                int(center.y() - side / 2),
                self._cover.scaled(
                    side,
                    side,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )


VISUALIZERS: dict[str, type[VisualizerBase]] = {
    BarsVisualizer.mode: BarsVisualizer,
    WaveVisualizer.mode: WaveVisualizer,
    RadialVisualizer.mode: RadialVisualizer,
}


def create_visualizer(kind: str, parent: QWidget | None = None) -> VisualizerBase:
    """Build the visualizer named by :data:`core.config_manager.VALID_VISUALIZERS`."""
    factory = VISUALIZERS.get(kind, BarsVisualizer)
    return factory(parent)


class VisualizerStack(QWidget):
    """Holds all three styles, feeds only the visible one and switches modes."""

    def __init__(
        self,
        parent: QWidget | None = None,
        mode: str = BarsVisualizer.mode,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._visualizers: dict[str, VisualizerBase] = {}
        self._mode = mode if mode in VISUALIZERS else BarsVisualizer.mode
        for kind, factory in VISUALIZERS.items():
            widget = factory(self)
            widget.hide()
            layout.addWidget(widget)
            self._visualizers[kind] = widget
        self._visualizers[self._mode].show()
        self.set_mode(self._mode)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def current(self) -> VisualizerBase:
        return self._visualizers[self._mode]

    def set_mode(self, mode: str) -> str:
        """Switch the visible visualizer, seeding it with the last frame."""
        if mode not in self._visualizers:
            return self._mode
        previous = self._visualizers[self._mode]
        target = self._visualizers[mode]
        if target is previous:
            self._mode = mode
            return self._mode
        target.seed_from(previous)
        previous.hide()
        previous.stop()
        target.show()
        target.start()
        self._mode = mode
        return self._mode

    def set_spectrum(self, values: Sequence[float]) -> None:
        self.current.set_spectrum(values)

    def set_waveform(self, values: Sequence[float]) -> None:
        self.current.set_waveform(values)

    def set_idle(self) -> None:
        for widget in self._visualizers.values():
            widget.set_idle()

    def set_cover(self, pixmap: QPixmap | None) -> None:
        radial = self._visualizers[RadialVisualizer.mode]
        if isinstance(radial, RadialVisualizer):
            radial.set_cover(pixmap)

    def reset_data(self) -> None:
        for widget in self._visualizers.values():
            widget.reset_data()


__all__ = [
    "ACCENT_HIGH",
    "ACCENT_LOW",
    "ACCENT_MID",
    "BACKGROUND",
    "BarsVisualizer",
    "DEFAULT_ATTACK",
    "DEFAULT_BANDS",
    "DEFAULT_PEAK_FALL",
    "DEFAULT_RELEASE",
    "FRAME_INTERVAL_MS",
    "RadialVisualizer",
    "Smoother",
    "VISUALIZERS",
    "VisualizerBase",
    "VisualizerStack",
    "WAVE_POINTS",
    "WaveSmoother",
    "WaveVisualizer",
    "accent_gradient",
    "clamp",
    "create_visualizer",
    "fit_bands",
    "fit_wave",
    "idle_target",
    "smooth_step",
    "update_peaks",
]
