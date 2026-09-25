"""NumPy FFT utilities for the visualizers.

Pure functions — unit-testable without Qt or audio hardware.
"""

from __future__ import annotations

import numpy as np

from yamusic.constants import FFT_SIZE, SPECTRUM_BANDS, WAVE_POINTS

_HANN = np.hanning(FFT_SIZE).astype(np.float32)


def to_mono(block: np.ndarray) -> np.ndarray:
    """Collapse multi-channel PCM (N, ch) to mono float32.

    1-D input is treated as already-mono. Stereo interleaving is handled by
    the engine, which reshapes buffers to (frames, channels) at capture time.
    """
    if block.ndim == 2:
        return block.mean(axis=1, dtype=np.float32)
    return block.astype(np.float32, copy=False)


def ensure_window(block: np.ndarray, size: int = FFT_SIZE) -> np.ndarray:
    """Right-align and pad/trim to exactly ``size`` samples."""
    if block.size >= size:
        out = block[-size:]
    else:
        out = np.zeros(size, dtype=np.float32)
        out[-block.size :] = block
    return out.astype(np.float32, copy=False)


def band_edges(n_bands: int, low: float = 40.0, high: float = 16_000.0) -> np.ndarray:
    """Log-spaced band edges in Hz (length n_bands + 1)."""
    return np.geomspace(low, high, n_bands + 1)


def spectrum_bands(
    block: np.ndarray,
    sample_rate: int,
    n_bands: int = SPECTRUM_BANDS,
) -> np.ndarray:
    """Magnitude spectrum → normalised [0, 1] log-frequency bands."""
    mono = ensure_window(to_mono(block))
    windowed = mono * _HANN
    spec = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(FFT_SIZE, d=1.0 / sample_rate)

    edges = band_edges(n_bands)
    bands = np.zeros(n_bands, dtype=np.float32)
    idx = np.searchsorted(freqs, edges)
    for i in range(n_bands):
        start, end = int(idx[i]), int(idx[i + 1])
        if end <= start:
            end = start + 1
        end = min(end, spec.size)
        start = min(start, spec.size - 1)
        bands[i] = spec[start:end].max() if end > start else 0.0

    # dB scale: relative window around the frame peak (54 dB) × absolute
    # loudness factor, so quiet passages stay dim and peaks are unique.
    db = 20.0 * np.log10(bands + 1e-9)
    max_db = float(db.max())
    if max_db < -90.0:
        return np.zeros(n_bands, dtype=np.float32)
    loud = float(np.clip((max_db + 72.0) / 66.0, 0.0, 1.0))
    rel = np.clip((db - max_db + 54.0) / 54.0, 0.0, 1.0)
    tilt = np.linspace(0.0, 0.12, n_bands, dtype=np.float32)
    out = rel * (0.15 + 0.85 * loud)
    out = np.clip(out + tilt * out, 0.0, 1.0)
    return out.astype(np.float32)


def oscilloscope(
    block: np.ndarray,
    points: int = WAVE_POINTS,
) -> np.ndarray:
    """Decimate a mono block to ``points`` values in [-1, 1]."""
    mono = ensure_window(to_mono(block))
    # simple box decimation keeps peaks visible
    n = mono.size // points
    if n < 1:
        return mono[:points] if mono.size >= points else np.pad(
            mono, (0, points - mono.size)
        ).astype(np.float32)
    trimmed = mono[: points * n].reshape(points, n)
    # alternate sign-preserving peak
    peaks = trimmed[np.arange(points), np.argmax(np.abs(trimmed), axis=1)]
    return peaks.astype(np.float32)


def smooth_oscilloscope(wave: np.ndarray, passes: int = 2) -> np.ndarray:
    """Moving-average smoothing for the neon waveform."""
    out = wave.astype(np.float32, copy=True)
    for _ in range(passes):
        if out.size < 3:
            break
        out = (
            np.concatenate(([out[0]], out[:-2] + out[1:-1] * 2.0 + out[2:], [out[-1]]))
            / 4.0
        ).astype(np.float32)
    return out


class Smoother:
    """Attack/release envelope follower for spectrum bars."""

    __slots__ = ("_value", "_peaks", "attack", "release", "peak_fall")

    def __init__(self, n: int, attack: float = 0.55, release: float = 0.10, peak_fall: float = 0.012) -> None:
        self._value = np.zeros(n, dtype=np.float32)
        self._peaks = np.zeros(n, dtype=np.float32)
        self.attack = attack
        self.release = release
        self.peak_fall = peak_fall

    @property
    def value(self) -> np.ndarray:
        return self._value

    @property
    def peaks(self) -> np.ndarray:
        return self._peaks

    def update(self, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        t = target.astype(np.float32, copy=False)
        coeff = np.where(t > self._value, self.attack, self.release).astype(np.float32)
        self._value = self._value + coeff * (t - self._value)
        self._peaks = np.maximum(self._peaks - self.peak_fall, self._value)
        return self._value, self._peaks
