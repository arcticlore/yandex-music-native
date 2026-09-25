"""libmpv playback engine with a Pulse/PipeWire PCM tap for FFT.

Playback: python-mpv (libmpv) — native FLAC/lossless and HQ 320 streaming.
Visualization: ``parec`` records the default sink monitor while audio plays
and feeds float32 frames (N, 2) to the analyzer. If Pulse/PipeWire is
unavailable the tap stays silent and visualizers idle — playback still works.

Qt signals are only emitted from mpv callbacks / threads via Signal.emit,
which Qt delivers queued to the GUI thread.
"""

from __future__ import annotations

import ctypes.util
import logging
import os
import shutil
import subprocess
import threading
from collections import deque
from typing import Callable
from urllib.parse import unquote, urlparse

import numpy as np
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

_TAP_RATE = 44100
_TAP_CHANNELS = 2


def ensure_mpv() -> None:
    """Verify python-mpv and libmpv are usable (raises RuntimeError otherwise)."""
    try:
        import mpv  # noqa: F401
    except OSError as exc:
        raise RuntimeError(
            "libmpv not found — install mpv-libs / libmpv "
            "(dnf install mpv-libs, apt install libmpv2, pacman -S mpv)"
        ) from exc
    except ImportError as exc:
        raise RuntimeError("python-mpv not installed — pip install python-mpv") from exc
    if ctypes.util.find_library("mpv") is None:
        raise RuntimeError(
            "libmpv.so not found — install mpv-libs / libmpv package"
        )


def _uri_to_path(uri: str) -> str:
    """Convert file:// URI to a filesystem path; pass http(s) and plain paths through."""
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        return unquote(parsed.path)
    return uri


class _PcmTap:
    """Background ``parec`` reader → float32 stereo chunks."""

    def __init__(self, on_chunk: Callable[[np.ndarray, int], None]) -> None:
        self._on_chunk = on_chunk
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        if shutil.which("parec") is None:
            return False
        try:
            out = subprocess.run(
                ["pactl", "info"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            return out.returncode == 0 and "Default Sink" in out.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def default_sink() -> str | None:
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

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            sink = self.default_sink()
            if sink is None or shutil.which("parec") is None:
                log.debug("pcm tap unavailable (no parec/pulse)")
                return
            try:
                self._proc = subprocess.Popen(
                    [
                        "parec",
                        "-d",
                        f"{sink}.monitor",
                        "--format=s16le",
                        f"--rate={_TAP_RATE}",
                        f"--channels={_TAP_CHANNELS}",
                        "--raw",
                        "--latency=50ms",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=_TAP_RATE * 4 // 10,
                )
            except OSError as exc:
                log.warning("parec start failed: %s", exc)
                self._proc = None
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._loop, name="pcm-tap", daemon=True
            )
            self._thread.start()
            log.debug("pcm tap started on %s.monitor", sink)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        bytes_per_frame = _TAP_CHANNELS * 2  # s16le
        while self._running:
            try:
                data = proc.stdout.read(bytes_per_frame * 1024)
            except (OSError, ValueError):
                break
            if not data:
                break
            usable = len(data) - (len(data) % bytes_per_frame)
            if usable <= 0:
                continue
            samples = np.frombuffer(data[:usable], dtype=np.int16)
            frames = samples.reshape(-1, _TAP_CHANNELS).astype(np.float32)
            frames /= 32768.0
            try:
                self._on_chunk(frames, _TAP_RATE)
            except Exception:  # analyzer must never break the tap
                log.exception("pcm consumer failed")


class AudioEngine(QObject):
    """Play/pause/seek/volume over libmpv with PCM capture for FFT."""

    eos = Signal()
    error = Signal(str)
    playback_state_changed = Signal(bool)
    buffering_changed = Signal(int)
    duration_changed = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        ensure_mpv()
        import mpv

        self._pcm_lock = threading.Lock()
        self._pcm_chunks: deque[np.ndarray] = deque(maxlen=128)
        self._sample_rate = _TAP_RATE
        self._pcm_consumers: list[Callable[[np.ndarray, int], None]] = []

        self._current_uri: str | None = None
        self._target_volume = 80
        self._want_playing = False
        self._last_duration = 0
        self._shutting_down = False
        # consumers/tap first — mpv may fire pause events during construction
        self._tap = _PcmTap(self._dispatch_pcm)

        ao = os.environ.get("YAMUSIC_AO")  # tests may force ao=null
        opts: dict[str, object] = {
            "vo": "null",
            "ytdl": False,
            "osc": False,
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "keep_open": "yes",
            "cache": True,
            "demuxer_max_bytes": "128MiB",
            "volume": float(self._target_volume),
            "mute": False,
        }
        if ao:
            opts["ao"] = ao
        try:
            self._player = mpv.MPV(**opts)  # type: ignore[arg-type]
        except Exception as first:
            log.debug("mpv init with full opts failed (%s), retrying minimal", first)
            minimal: dict[str, object] = {
                "vo": "null",
                "ytdl": False,
                "volume": float(self._target_volume),
            }
            if ao:
                minimal["ao"] = ao
            try:
                self._player = mpv.MPV(**minimal)  # type: ignore[arg-type]
            except Exception as exc:
                raise RuntimeError(f"cannot create libmpv instance: {exc}") from exc

        self._wire_mpv_events()

    # -- mpv event wiring (mpv thread → Qt signals) -----------------------

    def _wire_mpv_events(self) -> None:
        player = self._player

        @player.event_callback("end_file")
        def _on_end_file(event: object) -> None:
            if self._shutting_down:
                return
            reason = None
            if isinstance(event, dict):
                reason = event.get("reason")
            elif hasattr(event, "reason"):
                reason = getattr(event, "reason")
            if reason in (None, "eof", "end", 0, "stop"):
                # 'stop' also fires on our own stop() — only treat natural EOF as eos
                if reason == "eof" or reason is None or reason == "end":
                    self.eos.emit()
            elif reason == "error":
                self.error.emit("playback error (end_file)")
            # reason 'stop' / 'quit' / 'redirect' → ignore

        @player.property_observer("duration")
        def _on_duration(_name: str, value: object) -> None:
            if self._shutting_down:
                return
            try:
                seconds = float(value) if value is not None else 0.0
            except (TypeError, ValueError):
                return
            ms = int(seconds * 1000)
            if ms > 0 and ms != self._last_duration:
                self._last_duration = ms
                self.duration_changed.emit(ms)

        @player.property_observer("pause")
        def _on_pause(_name: str, value: object) -> None:
            if self._shutting_down:
                return
            # pause=None (unloaded) → ignore
            if value is None:
                return
            playing = not bool(value) and self._current_uri is not None
            self.playback_state_changed.emit(playing)
            tap = getattr(self, "_tap", None)
            if tap is None:
                return
            if playing:
                tap.start()
            else:
                tap.stop()

        @player.property_observer("buffering")
        def _on_buffering(_name: str, value: object) -> None:
            if self._shutting_down:
                return
            busy = bool(value)
            self.buffering_changed.emit(0 if busy else 100)
            if not busy and self._want_playing:
                try:
                    self._player.pause = False
                except Exception:
                    log.debug("resume after buffering failed", exc_info=True)

        @player.property_observer("file-loaded")
        def _on_loaded(_name: str, _value: object) -> None:
            if self._shutting_down or not self._want_playing:
                return
            try:
                self._player.pause = False
            except Exception:
                pass

    # -- PCM --------------------------------------------------------------

    def _dispatch_pcm(self, chunk: np.ndarray, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        with self._pcm_lock:
            self._pcm_chunks.append(chunk)
        for consumer in tuple(self._pcm_consumers):
            try:
                consumer(chunk, sample_rate)
            except Exception:
                log.exception("pcm consumer failed")

    def inject_pcm(self, chunk: np.ndarray, sample_rate: int) -> None:
        """Test hook: push synthetic PCM into the analyzer path."""
        self._dispatch_pcm(np.asarray(chunk, dtype=np.float32), sample_rate)

    def add_pcm_consumer(self, fn: Callable[[np.ndarray, int], None]) -> None:
        self._pcm_consumers.append(fn)

    def drain_pcm(self) -> list[np.ndarray]:
        with self._pcm_lock:
            if not self._pcm_chunks:
                return []
            chunks = list(self._pcm_chunks)
            self._pcm_chunks.clear()
        return chunks

    # -- transport --------------------------------------------------------

    def load(self, uri: str, start_ms: int = 0) -> None:
        """Set media URI. ``uri`` may be http(s):// or file://."""
        self.stop()
        with self._pcm_lock:
            self._pcm_chunks.clear()
        self._current_uri = uri
        target = _uri_to_path(uri)
        try:
            self._player.pause = True
            self._player.play(target)
        except Exception as exc:
            log.error("mpv play failed: %s", exc)
            self.error.emit(str(exc))
            return
        if start_ms > 0:
            from PySide6.QtCore import QTimer

            def _try_seek() -> None:
                if self._current_uri == uri:
                    self.seek(start_ms)

            QTimer.singleShot(400, _try_seek)
        self.play()

    def play(self) -> None:
        if self._current_uri is None:
            return
        self._want_playing = True
        try:
            self._player.pause = False
        except Exception as exc:
            log.error("mpv resume failed: %s", exc)
            self.error.emit(str(exc))
            return
        self._tap.start()
        self.playback_state_changed.emit(True)

    def pause(self) -> None:
        self._want_playing = False
        try:
            self._player.pause = True
        except Exception:
            log.debug("mpv pause failed", exc_info=True)
        self._tap.stop()
        self.playback_state_changed.emit(False)

    def stop(self) -> None:
        self._want_playing = False
        self._tap.stop()
        try:
            self._player.command("stop")
        except Exception:
            log.debug("mpv stop failed", exc_info=True)
        self._current_uri = None
        self.playback_state_changed.emit(False)

    @property
    def is_playing(self) -> bool:
        if self._current_uri is None:
            return False
        try:
            return not bool(self._player.pause)
        except Exception:
            return False

    @property
    def uri(self) -> str | None:
        return self._current_uri

    def seek(self, position_ms: int) -> bool:
        if self._current_uri is None:
            return False
        try:
            self._player.seek(
                max(0, int(position_ms)) / 1000.0, reference="absolute"
            )
            return True
        except Exception as exc:
            log.warning("seek failed: %s", exc)
            return False

    def position_ms(self) -> int:
        if self._current_uri is None:
            return 0
        try:
            pos = self._player.time_pos
        except Exception:
            return 0
        if pos is None:
            return 0
        return int(float(pos) * 1000)

    def duration_ms(self) -> int:
        if self._current_uri is None:
            return 0
        try:
            dur = self._player.duration
        except Exception:
            return self._last_duration
        if dur is not None and float(dur) > 0:
            value = int(float(dur) * 1000)
            if value != self._last_duration:
                self._last_duration = value
                self.duration_changed.emit(value)
            return value
        return self._last_duration

    def set_volume(self, percent: int) -> None:
        self._target_volume = max(0, min(100, int(percent)))
        # mpv soft-volume already approximates a comfortable curve
        try:
            self._player.volume = float(self._target_volume)
        except Exception:
            log.debug("volume set failed", exc_info=True)

    @property
    def volume(self) -> int:
        return self._target_volume

    def shutdown(self) -> None:
        self._shutting_down = True
        self._want_playing = False
        self._tap.stop()
        try:
            self._player.terminate()
        except Exception:
            log.debug("mpv terminate failed", exc_info=True)
        with self._pcm_lock:
            self._pcm_chunks.clear()
        self._pcm_consumers.clear()


# Back-compat alias (old GStreamer name used in early wiring / docs)
def ensure_gst() -> None:
    ensure_mpv()
