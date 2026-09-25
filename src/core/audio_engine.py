"""libmpv audio engine with PCM capture and a 60 Hz Qt spectrum feed.

Playback goes through :mod:`mpv` (libmpv): native MP3 (320 kbps) and FLAC
decoding, direct output to PipeWire/PulseAudio, absolute/relative seeking and
soft volume. Streams are opened by libmpv itself, so plain ``https://`` URLs,
Yandex CDN links and local paths all work without a downloader.

Visualization needs the PCM that libmpv hands to the sound server. Two capture
backends are provided:

``monitor``
    ``parec`` records ``<default sink>.monitor`` (PulseAudio, or the Pulse
    compatibility layer of PipeWire). This is the default because it taps the
    real output signal, so ReplayGain/volume changes stay visible and the sound
    path is untouched.
``fifo``
    Raw s16le is read from a FIFO (``YML_PCM_FIFO``). Optionally a writer
    command (``YML_PCM_WRITER``, placeholders ``{fifo}`` and ``{url}``) is
    spawned, e.g. a second mpv with ``--ao=pcm --ao-pcm-file={fifo}`` for a
    capture taken on the mpv side instead of the sound server.

mpv itself exposes no audio callback (``libmpv`` renders video, not audio), so
the ``monitor``/``fifo`` taps are the supported ways to reach the samples. When
neither is available the engine keeps playing and the analyzer simply stays
silent, which is why UI code must handle :attr:`AudioEngine.pcm_active`.

Qt resets ``LC_NUMERIC`` to the user locale when ``QApplication`` is constructed,
and libmpv aborts on a non-C numeric locale, so the engine pins it back to
``C`` right before every ``mpv_create``.

The analyzer itself is pure NumPy: a Hann-windowed ``rfft`` folded into 32 or
64 logarithmic bands from 20 Hz to 20 kHz, emitted through
:attr:`AudioEngine.spectrum_ready` / :attr:`AudioEngine.waveform_ready` at 60 Hz
by a ``QTimer`` in the GUI thread.
"""

from __future__ import annotations

import ctypes.util
import logging
import locale
import os
import selectors
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

FFT_SIZE = 2048
MIN_FREQ = 20.0
MAX_FREQ = 20_000.0
BAND_CHOICES = (32, 64)
DEFAULT_BANDS = 64
WAVE_POINTS = 512
FRAME_INTERVAL_MS = round(1000 / 60)
TAP_RATE = 44100
TAP_CHANNELS = 2
BYTES_PER_FRAME = TAP_CHANNELS * 2
LOW_CUTOFF_HZ = 250.0
LOW_RATE_HZ = 800
LOW_FFT_SIZE = 512
LOW_TAPS_PER_RATE = 2

AO_ENV = "YML_AUDIO_AO"
HWACCEL_ENV = "YML_HWACCEL"
FIFO_ENV = "YML_PCM_FIFO"
FIFO_WRITER_ENV = "YML_PCM_WRITER"
TAP_MODE_ENV = "YML_PCM_TAP"

STATE_STOPPED = "stopped"
STATE_PLAYING = "playing"
STATE_PAUSED = "paused"


def ensure_libmpv() -> None:
    """Raise a readable RuntimeError when python-mpv/libmpv is unusable."""
    try:
        locale.setlocale(locale.LC_NUMERIC, "C")
    except locale.Error:
        log.debug("cannot force C numeric locale", exc_info=True)
    try:
        import mpv  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("python-mpv is not installed (pip install python-mpv)") from exc
    except OSError as exc:
        raise RuntimeError(
            "libmpv shared library not found: install mpv-libs / libmpv "
            "(dnf install mpv-libs, apt install libmpv2, pacman -S mpv)"
        ) from exc
    if ctypes.util.find_library("mpv") is None:
        raise RuntimeError("libmpv.so not found: install the mpv-libs/libmpv package")


def uri_to_path(uri: str) -> str:
    """Turn a ``file://`` URI into a filesystem path, pass everything else through."""
    if uri.startswith("file://"):
        return unquote(urlparse(uri).path)
    return uri


def detect_audio_output() -> str:
    """Pick the native sound server: PipeWire when present, else PulseAudio."""
    forced = os.environ.get(AO_ENV, "").strip()
    if forced:
        return forced
    try:
        runtime = Path(f"/run/user/{os.getuid()}")
        if (runtime / "pipewire-0").exists():
            return "pipewire"
    except (AttributeError, OSError):
        pass
    return "pulse"


def band_edges(
    n_bands: int,
    low: float = MIN_FREQ,
    high: float = MAX_FREQ,
) -> np.ndarray:
    """Log-spaced band edges in Hz, length ``n_bands + 1``."""
    if n_bands < 1:
        raise ValueError("n_bands must be >= 1")
    return np.geomspace(float(low), float(high), int(n_bands) + 1)


def normalize_bands(n_bands: int) -> int:
    """Clamp a requested band count to the supported 32/64 choices."""
    value = int(n_bands)
    if value <= 32:
        return 32
    if value <= 64:
        return 64
    raise ValueError("band count must be 32 or 64")


def _to_mono(block: np.ndarray) -> np.ndarray:
    if block.ndim == 2:
        return block.mean(axis=1, dtype=np.float32)
    return block.astype(np.float32, copy=False)


def _windowed(block: np.ndarray, size: int) -> np.ndarray:
    mono = _to_mono(block)
    if mono.size >= size:
        out = mono[-size:]
    else:
        out = np.zeros(size, dtype=np.float32)
        out[-mono.size :] = mono
    return out * np.hanning(size).astype(np.float32)


_FIR_CACHE: dict[tuple[int, int], np.ndarray] = {}


def _lowpass_kernel(factor: int) -> np.ndarray:
    """Windowed-sinc low-pass for integer decimation (cached per factor)."""
    key = (factor, LOW_TAPS_PER_RATE)
    cached = _FIR_CACHE.get(key)
    if cached is not None:
        return cached
    taps = factor * LOW_TAPS_PER_RATE
    n = np.arange(taps) - (taps - 1) / 2.0
    kernel = np.sinc(n / factor) * np.hanning(taps)
    kernel = kernel.astype(np.float32)
    total = float(kernel.sum())
    if total > 0:
        kernel /= total
    _FIR_CACHE[key] = kernel
    return kernel


def _low_band_spectrum(
    mono: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray, float]:
    """FFT of a decimated copy: ~1.5 Hz bins resolve 20-250 Hz bands."""
    factor = max(1, int(sample_rate // LOW_RATE_HZ))
    if factor == 1:
        return np.empty(0, dtype=np.float32), np.empty(0), float(sample_rate)
    filtered = np.convolve(mono, _lowpass_kernel(factor), mode="same")
    usable = (filtered.size // factor) * factor
    decimated = filtered[:usable:factor].astype(np.float32, copy=False)
    size = LOW_FFT_SIZE
    if decimated.size >= size:
        block = decimated[-size:]
    else:
        block = np.zeros(size, dtype=np.float32)
        block[-decimated.size :] = decimated
    windowed = block * np.hanning(size).astype(np.float32)
    magnitudes = np.abs(np.fft.rfft(windowed))
    effective_rate = sample_rate / factor
    freqs = np.fft.rfftfreq(size, d=1.0 / effective_rate)
    return magnitudes, freqs, effective_rate


def _fold_bands(
    magnitudes: np.ndarray,
    freqs: np.ndarray,
    edges: np.ndarray,
) -> np.ndarray:
    """Maximum magnitude per band over the FFT bins covered by each edge pair."""
    n_bands = edges.size - 1
    bands = np.zeros(n_bands, dtype=np.float32)
    index = np.searchsorted(freqs, edges)
    for band in range(n_bands):
        start = int(index[band])
        end = max(int(index[band + 1]), start + 1)
        end = min(end, magnitudes.size)
        start = min(start, magnitudes.size - 1)
        if end > start:
            bands[band] = magnitudes[start:end].max()
    return bands


def spectrum_bands(
    block: np.ndarray,
    sample_rate: int,
    n_bands: int = DEFAULT_BANDS,
    fft_size: int = FFT_SIZE,
) -> np.ndarray:
    """Fold an audio block into normalised log-frequency bands in [0, 1].

    Bands below :data:`LOW_CUTOFF_HZ` are measured on a decimated copy of the
    signal: a 2048-point FFT at 44.1 kHz has 21.5 Hz bins, which is wider than
    the first log bands (20-22.3 Hz at 64 bands) and would smear bass content
    into the wrong band.
    """
    mono = _to_mono(block)
    edges = band_edges(n_bands)
    split = int(np.searchsorted(edges, LOW_CUTOFF_HZ, side="right")) - 1
    split = max(0, min(n_bands, split))

    bands = np.zeros(n_bands, dtype=np.float32)
    if split > 0:
        low_mag, low_freqs, _rate = _low_band_spectrum(mono, sample_rate)
        if low_mag.size:
            bands[:split] = _fold_bands(low_mag, low_freqs, edges[: split + 1])

    windowed = _windowed(mono, fft_size)
    magnitudes = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / float(sample_rate))
    if split < n_bands:
        bands[split:] = _fold_bands(magnitudes, freqs, edges[split:])

    decibels = 20.0 * np.log10(bands + 1e-9)
    peak = float(decibels.max())
    if peak < -90.0:
        return np.zeros(n_bands, dtype=np.float32)
    loudness = float(np.clip((peak + 72.0) / 66.0, 0.0, 1.0))
    relative = np.clip((decibels - peak + 54.0) / 54.0, 0.0, 1.0)
    tilt = np.linspace(0.0, 0.12, n_bands, dtype=np.float32)
    return np.clip(relative * (0.15 + 0.85 * loudness) * (1.0 + tilt), 0.0, 1.0).astype(
        np.float32
    )


def oscilloscope(block: np.ndarray, points: int = WAVE_POINTS) -> np.ndarray:
    """Decimate an audio block to ``points`` peak-preserving samples in [-1, 1]."""
    mono = _to_mono(block)
    if mono.size < points:
        return np.pad(mono, (0, points - mono.size)).astype(np.float32)
    stride = mono.size // points
    frames = mono[: points * stride].reshape(points, stride)
    peaks = frames[np.arange(points), np.argmax(np.abs(frames), axis=1)]
    return np.clip(peaks, -1.0, 1.0).astype(np.float32)


class Envelope:
    """Attack/release follower so bars rise fast and fall smoothly."""

    __slots__ = ("_value", "attack", "release")

    def __init__(self, size: int, attack: float = 0.55, release: float = 0.10) -> None:
        self._value = np.zeros(size, dtype=np.float32)
        self.attack = attack
        self.release = release

    @property
    def value(self) -> np.ndarray:
        return self._value

    def update(self, target: np.ndarray) -> np.ndarray:
        current = target.astype(np.float32, copy=False)
        if current.size != self._value.size:
            self._value = np.zeros(current.size, dtype=np.float32)
        coeff = np.where(current > self._value, self.attack, self.release).astype(np.float32)
        self._value = self._value + coeff * (current - self._value)
        return self._value

    def reset(self) -> None:
        self._value[:] = 0.0


def _decode_s16le(data: bytes) -> np.ndarray:
    usable = len(data) - (len(data) % BYTES_PER_FRAME)
    if usable <= 0:
        return np.empty((0, TAP_CHANNELS), dtype=np.float32)
    samples = np.frombuffer(data[:usable], dtype=np.int16)
    frames = samples.reshape(-1, TAP_CHANNELS).astype(np.float32)
    frames /= 32768.0
    return frames


class _MonitorTap:
    """``parec`` reader on the default sink monitor (PulseAudio/PipeWire)."""

    mode = "monitor"

    def __init__(
        self,
        on_chunk: Callable[[np.ndarray, int], None],
        rate: int = TAP_RATE,
        channels: int = TAP_CHANNELS,
    ) -> None:
        self._on_chunk = on_chunk
        self._rate = rate
        self._channels = channels
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @staticmethod
    def default_sink() -> str | None:
        if shutil.which("pactl") is None:
            return None
        try:
            out = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
            info = subprocess.run(
                ["pactl", "info"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            for line in info.stdout.splitlines():
                if line.startswith("Default Sink:"):
                    return line.split(":", 1)[1].strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None

    @classmethod
    def available(cls) -> bool:
        return shutil.which("parec") is not None and cls.default_sink() is not None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, url: str | None = None) -> bool:
        if self.active:
            return True
        sink = self.default_sink()
        if sink is None or shutil.which("parec") is None:
            log.debug("monitor tap unavailable: no parec or no pulse sink")
            return False
        self._stop.clear()
        try:
            self._proc = subprocess.Popen(
                [
                    "parec",
                    "-d",
                    f"{sink}.monitor",
                    "--format=s16le",
                    f"--rate={self._rate}",
                    f"--channels={self._channels}",
                    "--raw",
                    "--latency=40ms",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self._rate * 4 // 10,
            )
        except OSError as exc:
            log.warning("parec start failed: %s", exc)
            self._proc = None
            return False
        self._thread = threading.Thread(
            target=self._loop, name="pcm-monitor", daemon=True
        )
        self._thread.start()
        log.debug("monitor tap on %s.monitor", sink)
        return True

    def stop(self) -> None:
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while not self._stop.is_set():
            try:
                data = proc.stdout.read(BYTES_PER_FRAME * 1024)
            except (OSError, ValueError):
                break
            if not data:
                break
            frames = _decode_s16le(data)
            if frames.size == 0:
                continue
            try:
                self._on_chunk(frames, self._rate)
            except Exception:
                log.exception("pcm consumer failed")


class _FifoTap:
    """Raw s16le reader for a FIFO written by an mpv-side PCM writer."""

    mode = "fifo"

    def __init__(
        self,
        on_chunk: Callable[[np.ndarray, int], None],
        path: str | None = None,
        rate: int = TAP_RATE,
        channels: int = TAP_CHANNELS,
        writer: str | None = None,
    ) -> None:
        self._on_chunk = on_chunk
        self._path = path or os.environ.get(FIFO_ENV, "") or ""
        self._rate = rate
        self._channels = channels
        self._writer_cmd = writer if writer is not None else os.environ.get(FIFO_WRITER_ENV, "")
        self._writer: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @classmethod
    def available(cls) -> bool:
        path = os.environ.get(FIFO_ENV, "")
        return bool(path) and os.path.exists(path)

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, url: str | None = None) -> bool:
        if self.active:
            return True
        if not self._path:
            log.debug("fifo tap unavailable: %s not set", FIFO_ENV)
            return False
        if not os.path.exists(self._path):
            log.debug("fifo tap unavailable: %s missing", self._path)
            return False
        if self._writer_cmd and url:
            command = self._writer_cmd.format(fifo=self._path, url=url)
            try:
                self._writer = subprocess.Popen(
                    shlex_split(command),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                log.warning("pcm writer failed: %s", exc)
                self._writer = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pcm-fifo", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        writer, self._writer = self._writer, None
        if writer is not None and writer.poll() is None:
            try:
                writer.terminate()
                writer.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    writer.kill()
                except OSError:
                    pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)

    def _open(self) -> int | None:
        while not self._stop.is_set():
            try:
                return os.open(self._path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                self._stop.wait(0.2)
        return None

    def _loop(self) -> None:
        fd = self._open()
        if fd is None:
            return
        selector = selectors.DefaultSelector()
        try:
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ)
            pending = b""
            while not self._stop.is_set():
                if not selector.select(timeout=0.2):
                    continue
                try:
                    chunk = os.read(fd, BYTES_PER_FRAME * 2048)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not chunk:
                    selector.unregister(fd)
                    pending = b""
                    continue
                pending += chunk
                usable = len(pending) - (len(pending) % BYTES_PER_FRAME)
                if usable <= 0:
                    continue
                block, pending = pending[:usable], pending[usable:]
                frames = _decode_s16le(block)
                if frames.size:
                    try:
                        self._on_chunk(frames, self._rate)
                    except Exception:
                        log.exception("pcm consumer failed")
        finally:
            selector.close()
            try:
                os.close(fd)
            except OSError:
                pass


def shlex_split(command: str) -> list[str]:
    """Minimal shell-like splitter (avoids a shell for the PCM writer)."""
    return shlex.split(command)


def create_tap(
    on_chunk: Callable[[np.ndarray, int], None],
    mode: str = "auto",
    rate: int = TAP_RATE,
    channels: int = TAP_CHANNELS,
) -> _MonitorTap | _FifoTap | None:
    """Build the best available PCM tap, or ``None`` when capture is impossible."""
    requested = (mode or os.environ.get(TAP_MODE_ENV, "auto")).strip().lower()
    if requested == "none":
        return None
    if requested == "fifo":
        return _FifoTap(on_chunk, rate=rate, channels=channels) if _FifoTap.available() else None
    if requested == "monitor":
        if _MonitorTap.available():
            return _MonitorTap(on_chunk, rate=rate, channels=channels)
        return _FifoTap(on_chunk, rate=rate, channels=channels) if _FifoTap.available() else None
    if _FifoTap.available() and os.environ.get(FIFO_ENV, "").strip():
        return _FifoTap(on_chunk, rate=rate, channels=channels)
    if _MonitorTap.available():
        return _MonitorTap(on_chunk, rate=rate, channels=channels)
    if _FifoTap.available():
        return _FifoTap(on_chunk, rate=rate, channels=channels)
    return None


class AudioEngine(QObject):
    """libmpv playback plus a 60 Hz NumPy analyzer for the visualizers."""

    spectrum_ready = Signal(object)
    waveform_ready = Signal(object)
    bands_changed = Signal(int)
    state_changed = Signal(str)
    duration_changed = Signal(float)
    playback_started = Signal(str)
    finished = Signal()
    error = Signal(str)
    pcm_state_changed = Signal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        ao: str | None = None,
        tap_mode: str = "auto",
        bands: int = DEFAULT_BANDS,
        hwaccel: str | None = None,
        sample_rate: int = TAP_RATE,
        channels: int = TAP_CHANNELS,
        volume: int = 80,
    ) -> None:
        super().__init__(parent)
        ensure_libmpv()
        import mpv

        self._bands = normalize_bands(bands)
        self._sample_rate = int(sample_rate)
        self._channels = int(channels)
        self._volume = max(0, min(100, int(volume)))
        self._url: str | None = None
        self._state = STATE_STOPPED
        self._want_playing = False
        self._shutting_down = False
        self._pcm_seen = False
        self._pending_seek_ms = 0
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._envelope = Envelope(self._bands)
        self._consumers: list[Callable[[np.ndarray, int], None]] = []

        self._tap = create_tap(
            self._on_pcm, mode=tap_mode, rate=self._sample_rate, channels=self._channels
        )
        self._player = self._create_player(mpv, ao=ao, hwaccel=hwaccel)
        self._wire_events()

        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._emit_frame)
        self._timer.start()

    # -- construction ----------------------------------------------------

    def _create_player(self, mpv_module: object, ao: str | None, hwaccel: str | None) -> object:
        try:
            locale.setlocale(locale.LC_NUMERIC, "C")
        except locale.Error:
            log.debug("cannot force C numeric locale", exc_info=True)
        output = ao or detect_audio_output()
        options: dict[str, object] = {
            "vo": "null",
            "ytdl": False,
            "osc": False,
            "idle": True,
            "keep_open": "yes",
            "audio_display": "no",
            "audio_device": "auto",
            "audio_channels": "stereo",
            "vd": "auto",
            "vd_lavc_threads": 0,
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "cache": "yes",
            "cache_secs": "30",
            "demuxer_max_bytes": "128MiB",
            "demuxer_readahead_secs": "10",
            "demuxer_lavf_o": "reconnect=1,reconnect_streamed=1,reconnect_delay_max=5",
            "user_agent": "yandex-music-linux/0.1",
            "volume": float(self._volume),
            "mute": False,
            "ao": output,
        }
        accel = hwaccel if hwaccel is not None else os.environ.get(HWACCEL_ENV, "")
        if accel:
            options["hwdec"] = accel
        try:
            return mpv_module.MPV(**options)  # type: ignore[attr-defined]
        except Exception as first:
            log.warning("libmpv init with full options failed (%s), retrying minimal", first)
            minimal: dict[str, object] = {
                "vo": "null",
                "ytdl": False,
                "ao": output,
                "volume": float(self._volume),
            }
            try:
                return mpv_module.MPV(**minimal)  # type: ignore[attr-defined]
            except Exception as exc:
                raise RuntimeError(f"cannot create libmpv instance: {exc}") from exc

    # -- properties ------------------------------------------------------

    @property
    def bands(self) -> int:
        return self._bands

    @property
    def audio_output(self) -> str:
        return str(self._player.ao) if getattr(self._player, "ao", "") else "unknown"

    @property
    def tap_mode(self) -> str:
        return self._tap.mode if self._tap is not None else "none"

    @property
    def pcm_active(self) -> bool:
        return self._tap is not None and self._tap.active

    @property
    def is_playing(self) -> bool:
        return self._state == STATE_PLAYING

    @property
    def is_paused(self) -> bool:
        return self._state == STATE_PAUSED

    @property
    def state(self) -> str:
        return self._state

    @property
    def url(self) -> str | None:
        return self._url

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    # -- state propagation ----------------------------------------------

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.state_changed.emit(state)

    def _on_media_ready(self) -> None:
        """Apply the start offset and unpause once mpv knows the media length.

        mpv 0.41 has no ``file-loaded`` property, so readiness is detected from
        the first positive ``duration`` value, which is delivered reliably.
        """
        if self._shutting_down or self._url is None:
            return
        if self._pending_seek_ms:
            target = self._pending_seek_ms
            self._pending_seek_ms = 0
            try:
                self._player.seek(target / 1000.0, reference="absolute")
            except Exception:
                log.debug("initial seek failed", exc_info=True)
        if not self._want_playing:
            return
        try:
            if self._player.pause:
                self._player.pause = False
        except Exception:
            log.debug("resume after load failed", exc_info=True)

    def _wire_events(self) -> None:
        player = self._player

        @player.event_callback("end_file")  # type: ignore[attr-defined]
        def _on_end_file(event: object) -> None:
            if self._shutting_down:
                return
            reason = event.get("reason") if isinstance(event, dict) else None
            if reason == "error":
                self.error.emit("playback failed: media could not be decoded")
            elif reason == "eof":
                self._set_state(STATE_STOPPED)
                self.finished.emit()

        @player.property_observer("pause")  # type: ignore[attr-defined]
        def _on_pause(_name: str, value: object) -> None:
            if self._shutting_down or value is None:
                return
            if self._url is None:
                return
            if bool(value):
                self._set_state(STATE_PAUSED)
                self._stop_tap()
            else:
                self._set_state(STATE_PLAYING)
                self._start_tap()

        @player.property_observer("duration")  # type: ignore[attr-defined]
        def _on_duration(_name: str, value: object) -> None:
            if self._shutting_down or value is None:
                return
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                return
            if seconds <= 0:
                return
            self.duration_changed.emit(seconds)
            self._on_media_ready()

        @player.property_observer("eof-reached")  # type: ignore[attr-defined]
        def _on_eof(_name: str, value: object) -> None:
            if self._shutting_down or not value:
                return
            if self._want_playing:
                self._want_playing = False
                self._set_state(STATE_STOPPED)
                self.finished.emit()

        @player.event_callback("start-file")  # type: ignore[attr-defined]
        def _on_start_file(_event: object) -> None:
            if self._shutting_down or self._url is None:
                return
            self._set_state(STATE_PLAYING)
            self._start_tap()

        @player.property_observer("demuxer-cache-state")  # type: ignore[attr-defined]
        def _on_cache(_name: str, value: object) -> None:
            if self._shutting_down or not isinstance(value, dict):
                return
            if value.get("eof") and not self._want_playing:
                self._set_state(STATE_STOPPED)

    # -- transport -------------------------------------------------------

    def play(self, url: str | None = None, start_ms: int = 0) -> bool:
        """Load ``url`` (http(s), file:// or path) and start playback."""
        if url is not None:
            self.stop()
            self._url = url
        if self._url is None:
            self.error.emit("nothing to play")
            return False
        target = uri_to_path(self._url)
        self._want_playing = True
        try:
            self._player.play(target)
            if self._player.pause:
                self._player.pause = False
        except Exception as exc:
            log.error("play failed: %s", exc)
            self._want_playing = False
            self.error.emit(str(exc))
            return False
        if start_ms > 0:
            self._pending_seek_ms = int(start_ms)
        if self.get_duration_ms() > 0:
            self._on_media_ready()
        self._set_state(STATE_PLAYING)
        self._start_tap()
        self.playback_started.emit(self._url)
        return True

    def pause(self) -> bool:
        if self._url is None or self._state != STATE_PLAYING:
            return False
        self._want_playing = False
        try:
            self._player.pause = True
        except Exception as exc:
            log.warning("pause failed: %s", exc)
            return False
        self._set_state(STATE_PAUSED)
        self._stop_tap()
        return True

    def toggle_play(self) -> bool:
        """Pause when playing, resume when paused; returns the new state."""
        if self._state == STATE_PLAYING:
            return STATE_PAUSED if self.pause() else self._state
        if self._url is None:
            return STATE_STOPPED
        self._want_playing = True
        try:
            self._player.pause = False
        except Exception as exc:
            log.warning("resume failed: %s", exc)
            return self._state
        self._set_state(STATE_PLAYING)
        self._start_tap()
        return self._state

    def stop(self) -> None:
        self._want_playing = False
        self._stop_tap()
        try:
            self._player.command("stop")
        except Exception:
            log.debug("stop failed", exc_info=True)
        self._url = None
        self._set_state(STATE_STOPPED)

    def seek(self, position_ms: int) -> bool:
        """Absolute seek in milliseconds (clamped to the media duration)."""
        if self._url is None:
            return False
        target = max(0, int(position_ms))
        duration_ms = self.get_duration_ms()
        if duration_ms:
            target = min(target, duration_ms)
        try:
            self._player.seek(target / 1000.0, reference="absolute")
            return True
        except Exception as exc:
            log.warning("seek failed: %s", exc)
            return False

    def seek_relative(self, delta_ms: int) -> bool:
        if self._url is None:
            return False
        return self.seek(self.get_position_ms() + int(delta_ms))

    def set_volume(self, percent: int) -> int:
        """Set output volume in percent, returns the applied value."""
        self._volume = max(0, min(100, int(percent)))
        try:
            self._player.volume = float(self._volume)
        except Exception:
            log.debug("volume set failed", exc_info=True)
        return self._volume

    def set_muted(self, muted: bool) -> bool:
        try:
            self._player.mute = bool(muted)
            return bool(muted)
        except Exception:
            return False

    def get_position(self) -> float:
        """Playback position in seconds."""
        return self.get_position_ms() / 1000.0

    def get_duration(self) -> float:
        """Media duration in seconds (0.0 while unknown)."""
        return self.get_duration_ms() / 1000.0

    def get_position_ms(self) -> int:
        if self._url is None:
            return 0
        try:
            value = self._player.time_pos
        except Exception:
            return 0
        if value is None:
            return 0
        try:
            return max(0, int(float(value) * 1000))
        except (TypeError, ValueError):
            return 0

    def get_duration_ms(self) -> int:
        if self._url is None:
            return 0
        try:
            value = self._player.duration
        except Exception:
            value = None
        if value is None:
            return 0
        try:
            return max(0, int(float(value) * 1000))
        except (TypeError, ValueError):
            return 0

    # -- analyzer -------------------------------------------------------

    def set_band_count(self, n_bands: int) -> int:
        """Switch between 32 and 64 frequency bands."""
        target = normalize_bands(n_bands)
        if target == self._bands:
            return target
        self._bands = target
        self._envelope = Envelope(target)
        self.bands_changed.emit(target)
        return target

    def add_pcm_consumer(self, fn: Callable[[np.ndarray, int], None]) -> None:
        self._consumers.append(fn)

    def inject_pcm(self, block: np.ndarray, sample_rate: int | None = None) -> None:
        """Test hook: push a synthetic block through the analyzer path."""
        self._on_pcm(np.asarray(block, dtype=np.float32), sample_rate or self._sample_rate)

    def _on_pcm(self, block: np.ndarray, sample_rate: int) -> None:
        if sample_rate and sample_rate != self._sample_rate:
            self._sample_rate = int(sample_rate)
        with self._lock:
            self._latest = block
        if not self._pcm_seen:
            self._pcm_seen = True
            self.pcm_state_changed.emit(True)
        for consumer in tuple(self._consumers):
            try:
                consumer(block, sample_rate)
            except Exception:
                log.exception("pcm consumer failed")

    def _start_tap(self) -> None:
        if self._tap is not None:
            self._tap.start(self._url)

    def _stop_tap(self) -> None:
        if self._tap is not None:
            self._tap.stop()

    def _emit_frame(self) -> None:
        if self._shutting_down:
            return
        with self._lock:
            block = self._latest
        if block is None:
            return
        try:
            bands = self._envelope.update(
                spectrum_bands(block, self._sample_rate, self._bands)
            )
            wave = oscilloscope(block)
        except Exception:
            log.exception("analyzer failed")
            return
        self.spectrum_ready.emit(bands)
        self.waveform_ready.emit(wave)

    # -- teardown -------------------------------------------------------

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._timer.stop()
        self._stop_tap()
        try:
            self._player.terminate()
        except Exception:
            log.debug("terminate failed", exc_info=True)
        with self._lock:
            self._latest = None
        self._consumers.clear()
        self._envelope.reset()
