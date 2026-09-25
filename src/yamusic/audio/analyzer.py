"""FFT analyzer running in its own QThread.

Pulls PCM chunks from the :class:`AudioEngine` at ~60 FPS, computes spectrum
and oscilloscope arrays and emits plain-Python lists to the GUI thread
(queued connection → paint handlers never compute FFT themselves).
"""

from __future__ import annotations

import logging
import time
from collections import deque

import numpy as np
from PySide6.QtCore import QThread, QTimer, Signal

from yamusic.audio.engine import AudioEngine
from yamusic.audio.fft import (
    Smoother,
    oscilloscope,
    smooth_oscilloscope,
    spectrum_bands,
    to_mono,
)
from yamusic.constants import ANALYZER_FPS, SPECTRUM_BANDS, WAVE_POINTS

log = logging.getLogger(__name__)


class SpectrumAnalyzer(QThread):
    """60 FPS worker: PCM → FFT → ``spectrum``/``wave`` signals."""

    spectrum = Signal(list)  # list[float], len == SPECTRUM_BANDS
    wave = Signal(list)  # list[float], len == WAVE_POINTS, [-1..1]

    def __init__(self, engine: AudioEngine, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._running = True
        self._buffer = deque(maxlen=512)  # concatenated-arrival mono blocks
        self._sample_rate = 44100
        self._smoother = Smoother(SPECTRUM_BANDS)
        self._tail = np.zeros(0, dtype=np.float32)
        engine.add_pcm_consumer(self._on_pcm)

    # Called from GStreamer streaming thread — must stay lock-light.
    def _on_pcm(self, chunk: np.ndarray, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._buffer.append(chunk)

    def run(self) -> None:  # noqa: D102
        from PySide6.QtCore import QEventLoop, Qt

        interval_ms = max(1, int(1000 / ANALYZER_FPS))
        timer = QTimer()
        timer.setTimerType(Qt.TimerType.CoarseTimer)
        timer.setInterval(interval_ms)
        timer.timeout.connect(self._tick)
        timer.start()
        self._running = True
        loop = QEventLoop()
        while self._running:
            loop.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        timer.stop()

    def stop(self) -> None:
        self._running = False
        self.wait(2000)

    # ------------------------------------------------------------------

    def _collect(self, min_samples: int) -> np.ndarray | None:
        chunks: list[np.ndarray] = []
        total = self._tail.size
        while self._buffer:
            chunk = self._buffer.popleft()
            mono = to_mono(chunk)
            chunks.append(mono)
            total += mono.size
        if not chunks and self._tail.size == 0:
            return None
        if chunks:
            joined = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
            data = np.concatenate((self._tail, joined)) if self._tail.size else joined
        else:
            data = self._tail
        if data.size < min_samples:
            self._tail = data
            return None
        # keep overlap for smoother waveform continuity
        keep = min_samples
        self._tail = data[-keep:] if data.size > keep else data
        return data

    def _tick(self) -> None:
        block = self._collect(1024)
        if block is None:
            # idle: fall to silence so bars/waves fade out
            self._smoother.update(np.zeros(SPECTRUM_BANDS, dtype=np.float32))
            self.spectrum.emit([0.0] * SPECTRUM_BANDS)
            self.wave.emit([0.0] * WAVE_POINTS)
            return
        try:
            raw = spectrum_bands(block, self._sample_rate, SPECTRUM_BANDS)
            values, _peaks = self._smoother.update(raw)
            wave = smooth_oscilloscope(oscilloscope(block, WAVE_POINTS), passes=1)
            self.spectrum.emit([float(v) for v in values])
            self.wave.emit([float(v) for v in wave])
        except Exception:
            log.exception("fft tick failed")
            time.sleep(0.005)
