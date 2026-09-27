"""Audio visualizers: bars, neon wave and the glowing radial stage.

The engine publishes spectrum and waveform frames from
:mod:`core.audio_engine`; this module turns them into something that can be
watched. Three details matter for a stable 60 FPS picture:

* every frame is passed through an attack/release follower (:class:`Smoother`),
  because raw FFT frames are noisy and jumpy at 60 Hz;
* one :class:`Smoothing` instance per visualizer keeps its own smoothed values
  and peak caps, so switching between the three styles does not reset motion;
* the repaint clock is a single 16 ms timer that is stopped while the widget is
  hidden, so a minimised window costs nothing.

Any style can be swapped for the next one with a click, which is why every
visualizer forwards :attr:`VisualizerBase.mode_clicked` to the stack.

The smoothing maths lives in plain functions and lists, independent of Qt, so it
can be tested without a running application.
"""

from __future__ import annotations

import math
from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui.theme import ACCENT, BORDER, PANEL, SURFACE, TEXT, WAVE_PINK, on_theme_changed, token

FRAME_INTERVAL_MS = round(1000 / 60)
MAX_VOLUME_COMPENSATION = 8.0
"""How far the level compensation may lift a quiet signal.

The tap reads the sink, so the frames arrive *after* mpv applied the volume
and a low slider really does shrink them.  Undoing that is a division by the
volume, and at 1% that is a 100x lift of whatever noise floor the stream has.
The cap keeps the picture honest instead of turning silence into a firework.
"""
FULL_SCALE_VOLUME = 100
"""The volume at which ``volume_compensation`` is a no-op.

Handed to the visualizer when normalisation is on: it asks the stage to divide
the level back out, and full scale is the level where there is nothing to undo.
"""
DEFAULT_BANDS = 64
WAVE_POINTS = 512
DEFAULT_ATTACK = 0.62
DEFAULT_RELEASE = 0.12
DEFAULT_PEAK_FALL = 0.014
BACKGROUND = QColor(PANEL)
BASELINE = QColor(BORDER)
ACCENT_LOW = QColor(WAVE_PINK)
# The spectrum heat ramp is the wave page's own look and stays local: only the
# frame around it (panel, baseline, peak cap) comes from the shared tokens.
ACCENT_MID = QColor("#ff8c42")
ACCENT_HIGH = QColor(ACCENT)
PEAK_COLOR = QColor(TEXT)
PEAK_COLOR.setAlpha(210)
IDLE_LEVEL = 0.045
IDLE_SPEED = 0.0016
MODE_ORDER = ("circular", "spectrum", "wave", "meters")
"""The order a click walks through, starting at the stage's own style."""


def refresh_theme_colours() -> None:
    """Re-read the frame colours the painters use from the active theme.

    These names are read in a dozen ``paintEvent`` methods, so they are module
    level rather than threaded through every call.  That makes them snapshots, so
    this is registered with the theme: a style switch has to re-read them or the
    canvas would stay Obsidian inside a Cyberpunk window.
    """
    global BACKGROUND, BASELINE, ACCENT_LOW, ACCENT_HIGH, PEAK_COLOR
    BACKGROUND = QColor(token("PANEL"))
    BASELINE = QColor(token("BORDER"))
    ACCENT_LOW = QColor(token("WAVE_PINK"))
    ACCENT_HIGH = QColor(token("ACCENT"))
    peak = QColor(token("TEXT"))
    peak.setAlpha(210)
    PEAK_COLOR = peak


on_theme_changed(lambda _name: refresh_theme_colours())

VU_DECIBELS = (-54.0, 6.0)
"""The dB window the meters show, in the order a mixing desk uses.

The scale is the one a hardware meter uses: the whole range sits near 0 dBFS so
the needle spends its travel where music actually is, and the bottom is not
minus infinity, because a meter whose bottom is silent teaches the eye nothing.
"""

VU_FLOOR = 0.0
"""Linear amplitude the meter shows as its lowest tick."""

VU_CHANNELS = 2
"""Left and right; the engine publishes an interleaved stereo frame."""


def amplitude_to_decibels(amplitude: float) -> float:
    """Convert a linear amplitude to dBFS, where silence is minus infinity.

    ``math.log10(0.0)`` raises, and silence is the most common input a meter
    ever sees - between tracks, on a fade-out, on a gapless handover - so the
    floor is returned instead of a crash.
    """
    value = clamp(float(amplitude))
    if value <= VU_FLOOR:
        return math.inf
    return 20.0 * math.log10(value)


def decibel_to_level(decibels: float) -> float:
    """Map a dB reading onto the 0..1 travel of a meter needle.

    Clamped on both ends: below the floor the needle is at rest, above the
    ceiling it is pinned, and a value in between is placed by its position in
    the dB window rather than linearly in amplitude, because a linear amplitude
    meter spends nine tenths of its travel on the quietest two percent of the
    range.
    """
    low, high = VU_DECIBELS
    if not math.isfinite(decibels):
        return 0.0
    return clamp((float(decibels) - low) / (high - low))


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
    """Pad or truncate an FFT frame to exactly ``size`` values in ``[0, 1]``.

    A missing frame is silence: the engine can publish nothing when a stream
    stalls, and an empty meter is the right answer to that, not a traceback.
    """
    if values is None:
        values = []
    clipped = [clamp(float(value)) for value in values[:size]]
    if len(clipped) < size:
        clipped.extend([0.0] * (size - len(clipped)))
    return clipped


def fit_wave(values: Sequence[float], size: int = WAVE_POINTS) -> list[float]:
    """Pad or truncate a waveform frame to exactly ``size`` values.

    ``None`` is silence here for the same reason as in :func:`fit_bands`.
    """
    if values is None:
        values = []
    clipped = [max(-1.0, min(1.0, float(value))) for value in values[:size]]
    if len(clipped) < size:
        clipped.extend([0.0] * (size - len(clipped)))
    return clipped


def bar_layout(count: int, width: float) -> tuple[float, float]:
    """Even ``(bar_width, gap)`` that fill ``width`` exactly.

    The gap used to come from a fixed divisor, so a narrow stage made the bars
    wider than their slot and pushed the top of the spectrum off the right edge.
    Deriving it from what is actually available keeps every bar on screen at any
    width, and dropping the outer margins lets the first and last bar reach the
    edges instead of leaving a fifth of the display unused. A stage too narrow
    to hold a pixel per band gets sub-pixel bars rather than bars that overrun it.
    """
    bars = max(1, int(count))
    room = max(float(width), 1.0)
    gap = min(max(room // 240.0, 0.0), room / bars * 0.4)
    bar_width = (room - gap * (bars - 1)) / bars
    return bar_width, gap


def mirrored_rays(
    values: Sequence[float],
    spin: float = 0.0,
    axis: float = 90.0,
) -> list[tuple[float, float]]:
    """``(angle, length)`` per band, mirrored about the axis through the bass.

    A ring that walks the spectrum once is a readable circle, but it spends half
    its circumference on the treble alone, so a bass-heavy track lights up a
    short arc and leaves the rest dark - the same dead half as the bars, just
    wrapped round a disc. Walking each band *and its partner from the other end*
    puts the full range on both sides of the axis, so the left half mirrors the
    right, the bass meets its treble on the axis and every band has a partner
    carrying the same length.
    """
    count = len(values)
    if count == 0:
        return []
    span = 360.0 / count
    start = axis + spin
    rays: list[tuple[float, float]] = []
    for index in range(count):
        partner = count - 1 - index
        pair = min(index, partner)
        # One length per pair: max, not mean, so a band does not get dimmed by
        # its quieter mirror and the two sides stay identical.
        length = max(clamp(float(values[index])), clamp(float(values[partner])))
        side = 1.0 if index <= partner else -1.0
        rays.append((start + side * pair * span, length))
    return rays


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


class LevelFollower:
    """Per-channel level meter state: smoothed value plus a held peak cap.

    A VU meter is not a spectrum.  It answers «how loud is it now», so the
    follower runs on the RMS of a frame rather than on a single bin, and it keeps
    a peak cap that falls slowly and a needle that falls faster - the same
    attack/release shape as :class:`Smoother`, on a dB scale.
    """

    PEAK_ATTACK = 0.9
    PEAK_FALL = 0.006
    REST = 0.0005
    """Below this a needle is sub-pixel, so it is snapped to true silence."""

    def __init__(self, channels: int = 2) -> None:
        self._channels = max(1, int(channels))
        self._values = [0.0] * self._channels
        self._peaks = [0.0] * self._channels
        self._decays = [0.0] * self._channels

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def values(self) -> list[float]:
        """The smoothed level of each channel, 0..1 on the meter scale."""
        return self._values

    @property
    def peaks(self) -> list[float]:
        return self._peaks

    @property
    def decays(self) -> list[float]:
        """The overlay segments lit while a level decays, 0..1 on the meter scale."""
        return self._decays

    def step(self, targets: Sequence[float]) -> list[float]:
        for index in range(self._channels):
            target = float(targets[index]) if index < len(targets) else 0.0
            target = clamp(target)
            current = self._values[index]
            coefficient = DEFAULT_ATTACK if target > current else DEFAULT_RELEASE
            self._values[index] = current + coefficient * (target - current)
            peak = self._peaks[index]
            self._peaks[index] = max(peak, self._values[index]) if target >= peak else peak - self.PEAK_FALL
            if self._values[index] > 0.0:
                self._decays[index] = min(1.0, self._decays[index] + 0.02)
            else:
                self._decays[index] = max(0.0, self._decays[index] - 0.05)
        self._rest()
        return self._values

    def adopt(self, other: "LevelFollower") -> None:
        """Take over another follower's motion state (style switches)."""
        self._channels = max(self._channels, other.channels)
        self._values = list(other._values) + [0.0] * (self._channels - len(other._values))
        self._peaks = list(other._peaks) + [0.0] * (self._channels - len(other._peaks))
        self._decays = list(other._decays) + [0.0] * (self._channels - len(other._decays))

    def decay(self) -> list[float]:
        for index in range(self._channels):
            self._values[index] = smooth_step(self._values[index], 0.0, 0.2, 0.12)
            self._peaks[index] = max(0.0, self._peaks[index] - self.PEAK_FALL)
            self._decays[index] = max(0.0, self._decays[index] - 0.05)
        self._rest()
        return self._values

    def _rest(self) -> None:
        """Snap a vanishing needle to silence instead of chasing denormals.

        An exponential release only approaches zero; left alone it would keep a
        meter reading a level of 1e-30 forever, which is what «is the track
        really silent?» should answer exactly.
        """
        for index in range(self._channels):
            if self._values[index] < self.REST:
                self._values[index] = VU_FLOOR
            if self._peaks[index] < self.REST:
                self._peaks[index] = VU_FLOOR
            if self._decays[index] < self.REST:
                self._decays[index] = VU_FLOOR

    def reset(self) -> None:
        self._values = [0.0] * self._channels
        self._peaks = [0.0] * self._channels
        self._decays = [0.0] * self._channels


def frame_rms(values: Sequence[float]) -> float:
    """Root-mean-square amplitude of one time-domain frame, 0..1.

    RMS and not the peak sample: a needle that follows the tallest spike in a
    512-sample window sits at full scale for almost any music, and reads as
    broken.  The average power is what a listener perceives as loudness, and it
    is the number a mixing desk shows.
    """
    if values is None:
        return 0.0
    count = len(values)
    if count == 0:
        return 0.0
    total = 0.0
    for value in values:
        sample = float(value)
        total += sample * sample
    return clamp(math.sqrt(total / count))


def channel_levels(values: Sequence[float], channels: int = 2) -> list[float]:
    """Split one interleaved frame into per-channel dB levels.

    Interleaving is the layout the engine publishes (``L R L R``), so an odd
    count is a dropped final sample rather than a reason to shift every later
    sample by one; the tail sample is counted into the last channel instead.
    """
    total = max(1, int(channels))
    # ``if not values`` looks harmless and is not: the engine hands over a
    # numpy array, and the truth value of a multi-element array raises
    # ValueError instead of answering. Comparing against None and asking for
    # the length works for every sequence that has one, arrays included.
    if values is None or len(values) == 0:
        return [VU_FLOOR] * total
    samples = [float(value) for value in values]
    buckets: list[list[float]] = [[] for _ in range(total)]
    for index, sample in enumerate(samples):
        buckets[index % total].append(sample)
    levels = [amplitude_to_decibels(frame_rms(bucket)) for bucket in buckets]
    return [decibel_to_level(level) for level in levels]


def volume_compensation(volume: int) -> float:
    """The factor that puts a ``volume``-scaled frame back at full scale.

    100% and above needs nothing, 50% doubles, 12% is the cap.  It is a
    multiplier, not a normaliser: the shape of the frame is preserved, so a
    quiet passage still looks quiet - only the listener's own knob stops
    changing what the stage shows.
    """
    level = max(0, min(100, int(volume))) / 100.0
    if level >= 0.999:
        return 1.0
    return min(1.0 / max(level, 1.0 / MAX_VOLUME_COMPENSATION), MAX_VOLUME_COMPENSATION)


def apply_compensation(values: Sequence[float] | None, factor: float) -> list[float]:
    """Scale a frame, clamped, so a boosted quiet frame cannot exceed 1.0.

    ``None`` passes straight through: the tap can hand over a frame with
    nothing in it, and the callers below already know how to treat that.
    """
    if values is None:
        return []
    if factor == 1.0:
        return list(values)
    return [max(-1.0, min(1.0, float(value) * factor)) for value in values]


class VisualizerBase(QWidget):
    """Data plumbing plus the 60 FPS repaint clock shared by all styles."""

    mode = "spectrum"
    mode_clicked = Signal()

    def __init__(self, parent: QWidget | None = None, bands: int = DEFAULT_BANDS) -> None:
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Сменить визуализатор")
        self._spectrum = Smoother(bands)
        self._wave = WaveSmoother()
        self._target_bands: list[float] = [0.0] * bands
        self._target_wave: list[float] = [0.0] * WAVE_POINTS
        self._target_levels: list[float] = [VU_FLOOR] * VU_CHANNELS
        self._compensation = 1.0
        self._animations_enabled = True
        self._phase = 0.0
        self._active = False
        self._pending = False
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    # -- data ---------------------------------------------------------

    def set_spectrum(self, values: Sequence[float]) -> None:
        """Feed an FFT frame; rendering happens on the next clock tick."""
        self._target_bands = fit_bands(apply_compensation(values, self._compensation), self._spectrum.size)
        self._active = True
        self._pending = True

    def set_waveform(self, values: Sequence[float]) -> None:
        """Feed a time-domain frame."""
        scaled = apply_compensation(values, self._compensation)
        self._target_wave = fit_wave(scaled, self._wave.size)
        self._target_levels = channel_levels(scaled, VU_CHANNELS)
        self._active = True
        self._pending = True

    def set_compensation(self, factor: float) -> float:
        """Set the factor that undoes the listener's volume, or 1.0 for none.

        Kept as a plain factor rather than a percentage so the stack can hand
        over :func:`volume_compensation` and the maths stays in one place.
        """
        self._compensation = max(1.0, float(factor))
        return self._compensation

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
        self._target_levels = list(other._target_levels)
        self._phase = other._phase
        self._active = other._active
        self._pending = other._pending
        levels = getattr(self, "_levels", None)
        if isinstance(levels, LevelFollower) and isinstance(getattr(other, "_levels", None), LevelFollower):
            levels.adopt(other._levels)
        self.update()

    @property
    def active(self) -> bool:
        return self._active

    def reset_data(self) -> None:
        self._spectrum.reset()
        self._wave.reset()
        self._target_bands = [0.0] * self._spectrum.size
        self._target_wave = [0.0] * self._wave.size
        self._target_levels = [VU_FLOOR] * VU_CHANNELS
        levels = getattr(self, "_levels", None)
        if isinstance(levels, LevelFollower):
            levels.reset()
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
        if self._animations_enabled and not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def animations_enabled(self) -> bool:
        return self._animations_enabled

    def set_animations_enabled(self, enabled: bool) -> bool:
        """Hold the repaint clock while animations are off.

        The frames still arrive from the tap; they are simply not drawn, which
        is the whole saving - a 60 FPS clock repainting an unchanged picture is
        what a quiet machine cannot afford.  Turning the switch back on resumes
        at once rather than waiting for the next track.
        """
        enabled = bool(enabled)
        if enabled == self._animations_enabled:
            return enabled
        self._animations_enabled = enabled
        if enabled:
            if self.isVisible():
                self.start()
        else:
            self.stop()
        return enabled

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.stop()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """A click anywhere on the stage asks the stack for the next style."""
        super().mousePressEvent(event)
        self.mode_clicked.emit()

    def _tick(self) -> None:
        self.advance()
        self.update()

    def advance(self) -> None:
        """Move the animation one frame: towards the data, or breathing."""
        self._phase += IDLE_SPEED
        levels = getattr(self, "_levels", None)
        if self._active:
            self._spectrum.step(self._target_bands)
            self._wave.step(self._target_wave)
            if isinstance(levels, LevelFollower):
                levels.step(self._target_levels)
        else:
            self._spectrum.idle(self._phase)
            self._wave.decay()
            if isinstance(levels, LevelFollower):
                levels.decay()
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
        bar_width, gap = bar_layout(count, width)
        gradient = accent_gradient(float(height))
        painter.setPen(Qt.PenStyle.NoPen)
        for index, value in enumerate(values):
            x = index * (bar_width + gap)
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


def ray_gradient(center: QPointF, angle: float, alpha: int) -> QConicalGradient:
    """The gold-to-pink sweep the rays ride on, dimmed to ``alpha``.

    A single conic gradient spans the whole stage, so neighbouring rays meet in
    one continuous sweep of colour instead of each carrying its own ramp; the
    wide pass of a ray is the same gradient at a low alpha, which is what turns
    a line into light.
    """
    gradient = QConicalGradient(center, angle)
    for position, colour in ((0.0, ACCENT_HIGH), (0.34, ACCENT_MID), (0.68, ACCENT_LOW), (1.0, ACCENT_HIGH)):
        tinted = QColor(colour)
        tinted.setAlpha(alpha)
        gradient.setColorAt(position, tinted)
    return gradient


class RadialVisualizer(VisualizerBase):
    """The wave page's own look: a lit core with neon rays around it.

    A soft halo sits under the album art, and every band becomes one ray that
    starts at the edge of the disc and reaches out into the dark. Each ray is
    drawn twice - a wide, low-alpha pass and a thin, bright one - so the burst
    glows instead of looking like a star chart, and the whole fan turns slowly so
    the stage still lives while nothing is playing.
    """

    mode = "circular"
    CORE_RATIO = 0.5
    """The album art disc, as a fraction of the half-height."""
    RAY_INSET = 1.04
    """Where a ray starts, just outside the disc."""
    RAY_REACH = 0.66
    """How far the loudest band reaches, as a fraction of the half-height."""
    RAY_STEPS = 2
    """Two passes: the glow, then the core."""
    SPIN = 0.00042
    """Rotation per frame, so the sweep never freezes into a static starburst."""
    MIRROR_AXIS = 90.0
    """Degrees, pointing down in Qt: the bass sits on the axis and the fan
    mirrors itself around it, so both sides of the ring carry the whole range."""

    def __init__(self, parent: QWidget | None = None, bands: int = DEFAULT_BANDS) -> None:
        super().__init__(parent, bands)
        self._cover: QPixmap | None = None
        self._rotation = 0.0

    def set_cover(self, pixmap: QPixmap | None) -> None:
        """Draw the cover in the middle of the stage."""
        self._cover = pixmap
        self.update()

    def clear_cover(self) -> None:
        self.set_cover(None)

    def advance(self) -> None:
        super().advance()
        self._rotation = (self._rotation + self.SPIN) % 1.0

    def _paint(self, painter: QPainter) -> None:
        values = self._spectrum.values
        count = len(values)
        if count == 0:
            return
        height = self.height()
        width = self.width()
        center = QPointF(width / 2.0, height / 2.0)
        limit = min(width, height) / 2.0
        disc = max(20.0, limit * self.CORE_RATIO)
        spin = -90.0 + self._rotation * 360.0
        inner = disc * self.RAY_INSET
        reach = (limit - inner) * self.RAY_REACH * 1.5
        self._paint_halo(painter, center, limit)
        self._paint_rays(painter, center, values, inner, reach, spin)
        self._paint_core(painter, center, disc, spin)

    def _paint_halo(self, painter: QPainter, center: QPointF, limit: float) -> None:
        """A wide soft gradient that lights the middle of the stage."""
        extent = limit * 0.92
        halo = QRadialGradient(center, extent)
        for position, colour, alpha in ((0.0, ACCENT_HIGH, 105), (0.5, ACCENT_MID, 40), (1.0, ACCENT_LOW, 0)):
            tinted = QColor(colour)
            tinted.setAlpha(alpha)
            halo.setColorAt(position, tinted)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, extent, extent)

    def _paint_rays(
        self,
        painter: QPainter,
        center: QPointF,
        values: Sequence[float],
        inner: float,
        reach: float,
        spin: float,
    ) -> None:
        """The burst: one ray per band, glow pass first, then the bright core."""
        rays = mirrored_rays(values, spin, self.MIRROR_AXIS)
        if not rays:
            return
        passes = ((7.0, 46), (2.0, 235))
        for thickness, alpha in passes:
            painter.setPen(
                QPen(
                    ray_gradient(center, spin, alpha),
                    thickness,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            for angle_degrees, value in rays:
                angle = math.radians(angle_degrees)
                length = inner + max(0.0, min(1.0, value)) * reach
                painter.drawLine(
                    center,
                    QPointF(center.x() + length * math.cos(angle), center.y() + length * math.sin(angle)),
                )

    def _paint_core(self, painter: QPainter, center: QPointF, disc: float, spin: float) -> None:
        """The album art as a disc, or a soft accent ball when there is none."""
        path = QPainterPath()
        path.addEllipse(center, disc, disc)
        if self._cover is not None and not self._cover.isNull():
            art = self._cover.scaled(
                int(disc * 2),
                int(disc * 2),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(QPointF(center.x() - disc, center.y() - disc), art)
            painter.restore()
        else:
            painter.save()
            painter.setBrush(QColor(SURFACE))
            painter.setPen(QPen(ray_gradient(center, spin, 120), 1.5))
            painter.drawPath(path)
            painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ray_gradient(center, spin, 170), 1.0))
        painter.drawPath(path)


class MetersVisualizer(VisualizerBase):
    """A stereo level meter: one bar per channel on a dB scale.

    The other three styles show *what* the music contains - its spectrum, its
    shape, its spread.  This one shows *how loud* it is, which is the question a
    listener asks when a mix feels wrong, so it is drawn as the instrument it
    imitates: a horizontal scale with ticks, a lit bar per channel, a held peak
    cap and a decaying trail behind it.
    """

    mode = "meters"
    CHANNELS = ("L", "R")
    SEGMENTS = 28
    TICK_DECIBELS = (-48.0, -36.0, -24.0, -18.0, -12.0, -6.0, 0.0)
    PADDING = 18.0
    ROW_GAP = 10.0
    LABEL_WIDTH = 18.0

    def __init__(self, parent: QWidget | None = None, bands: int = DEFAULT_BANDS) -> None:
        super().__init__(parent, bands)
        self._levels = LevelFollower(len(self.CHANNELS))

    def _paint(self, painter: QPainter) -> None:
        width = float(self.width())
        height = float(self.height())
        left = self.PADDING + self.LABEL_WIDTH
        right = width - self.PADDING
        span = right - left
        if span <= 0.0:
            return
        channels = self._levels.channels
        row = (height - self.ROW_GAP * (channels - 1)) / channels
        if row < 12.0:
            return
        for index in range(channels):
            top = index * (row + self.ROW_GAP)
            track = QRectF(left, top, span, row)
            self._paint_scale(painter, track)
            self._paint_channel(painter, track, index)
            self._paint_label(painter, top, row, self.CHANNELS[index] if index < len(self.CHANNELS) else "")

    def _paint_scale(self, painter: QPainter, track: QRectF) -> None:
        """The dB ticks behind the bar: the ruler that makes it a meter."""
        painter.setPen(QPen(BASELINE, 1.0))
        painter.drawRect(track.adjusted(0.0, 0.0, -1.0, -1.0))
        for decibels in self.TICK_DECIBELS:
            x = track.left() + track.width() * decibel_to_level(decibels)
            painter.setPen(QPen(QColor(BORDER), 1.0))
            painter.drawLine(int(x), int(track.top() + 1), int(x), int(track.bottom() - 1.0))

    def _paint_channel(self, painter: QPainter, track: QRectF, index: int) -> None:
        """One channel: lit segments, the decaying trail and the held peak cap."""
        value = self._levels.values[index]
        decay = self._levels.decays[index]
        peak = self._levels.peaks[index]
        step_width = track.width() / self.SEGMENTS
        painter.save()
        painter.setClipRect(track.adjusted(1.0, 1.0, -1.0, -1.0))
        for step in range(self.SEGMENTS):
            edge = (step + 1) / self.SEGMENTS
            filled = edge <= value
            trailing = not filled and edge <= decay
            if not filled and not trailing:
                continue
            cell = QRectF(
                track.left() + step * step_width,
                track.top() + 1.0,
                max(1.0, step_width - 1.5),
                max(1.0, track.height() - 2.0),
            )
            painter.setBrush(self._segment_colour(step / self.SEGMENTS, trailing))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(cell, 2.0, 2.0)
        painter.restore()
        if peak > 0.01:
            x = track.left() + track.width() * clamp(peak)
            painter.setPen(QPen(PEAK_COLOR, 1.6))
            painter.drawLine(int(x), int(track.top()), int(x), int(track.bottom()))

    @staticmethod
    def _segment_colour(fraction: float, trailing: bool) -> QColor:
        """The heat ramp of one segment, dimmed when it is only a trail."""
        if fraction < 0.62:
            colour = QColor(ACCENT)
        elif fraction < 0.85:
            colour = QColor(ACCENT_MID)
        else:
            colour = QColor(WAVE_PINK)
        colour.setAlpha(90 if trailing else 225)
        return colour

    def _paint_label(self, painter: QPainter, top: float, row: float, name: str) -> None:
        """The channel letter in the gutter the rows leave on their left."""
        if not name or row < 20.0:
            return
        painter.setPen(QPen(QColor(TEXT)))
        painter.drawText(
            QRectF(self.PADDING - 6.0, top, self.LABEL_WIDTH, row),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            name,
        )


VISUALIZERS: dict[str, type[VisualizerBase]] = {
    BarsVisualizer.mode: BarsVisualizer,
    WaveVisualizer.mode: WaveVisualizer,
    RadialVisualizer.mode: RadialVisualizer,
    MetersVisualizer.mode: MetersVisualizer,
}


def create_visualizer(kind: str, parent: QWidget | None = None) -> VisualizerBase:
    """Build the visualizer named by :data:`core.config_manager.VALID_VISUALIZERS`."""
    factory = VISUALIZERS.get(kind, BarsVisualizer)
    return factory(parent)


class VisualizerStack(QWidget):
    """Holds every style, feeds only the visible one and switches modes."""

    mode_changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        mode: str = RadialVisualizer.mode,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._visualizers: dict[str, VisualizerBase] = {}
        self._mode = mode if mode in VISUALIZERS else RadialVisualizer.mode
        for kind, factory in VISUALIZERS.items():
            widget = factory(self)
            widget.hide()
            widget.mode_clicked.connect(self.cycle_mode)
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
        target = self._visualizers[mode]
        previous = self._visualizers[self._mode]
        self._mode = mode
        if target is not previous:
            target.seed_from(previous)
            previous.hide()
            previous.stop()
            target.show()
            target.start()
        self.mode_changed.emit(mode)
        return self._mode

    def cycle_mode(self) -> str:
        """Advance to the next style; this is what a click on the stage does."""
        order = [kind for kind in MODE_ORDER if kind in self._visualizers]
        if self._mode not in order:
            return self.set_mode(order[0]) if order else self._mode
        return self.set_mode(order[(order.index(self._mode) + 1) % len(order)])

    def set_spectrum(self, values: Sequence[float]) -> None:
        self.current.set_spectrum(values)

    def set_waveform(self, values: Sequence[float]) -> None:
        self.current.set_waveform(values)

    def set_volume(self, volume: int) -> float:
        """Tell every style what the volume is, so the picture survives it.

        The stage is a picture of the music, not of the knob: at 20% it should
        look the same as at 100%, otherwise «turn it down a bit» is a visible
        change of scene.  All styles are told, not just the visible one, so
        switching modes mid-song does not reveal a dimmer stage.
        """
        factor = volume_compensation(volume)
        for widget in self._visualizers.values():
            widget.set_compensation(factor)
        return factor

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

    def set_animations_enabled(self, enabled: bool) -> bool:
        """Tell every style, so switching modes never reveals a stopped clock."""
        enabled = bool(enabled)
        for widget in self._visualizers.values():
            widget.set_animations_enabled(enabled)
        return enabled

    @property
    def animations_enabled(self) -> bool:
        return self.current.animations_enabled

    def stop_all(self) -> None:
        """Stop every clock, including the hidden ones, for a clean exit."""
        for widget in self._visualizers.values():
            widget.stop()


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
    "FULL_SCALE_VOLUME",
    "LevelFollower",
    "MODE_ORDER",
    "MetersVisualizer",
    "RadialVisualizer",
    "Smoother",
    "apply_compensation",
    "VISUALIZERS",
    "volume_compensation",
    "VU_CHANNELS",
    "VU_DECIBELS",
    "VisualizerBase",
    "VisualizerStack",
    "WAVE_POINTS",
    "WaveSmoother",
    "WaveVisualizer",
    "accent_gradient",
    "bar_layout",
    "channel_levels",
    "clamp",
    "create_visualizer",
    "decibel_to_level",
    "fit_bands",
    "fit_wave",
    "frame_rms",
    "idle_target",
    "mirrored_rays",
    "ray_gradient",
    "smooth_step",
    "update_peaks",
]
