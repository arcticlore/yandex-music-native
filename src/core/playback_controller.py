"""Playback orchestration on top of :mod:`core.yandex_service` and :mod:`core.audio_engine`.

The controller owns the user-facing queue. A manual list (a single track, an
album, a playlist) is kept locally, while «Моя волна» keeps its queue inside
:class:`~core.yandex_service.YandexService`, which also owns the station cursor
and the feedback stream. The controller turns transport events into feedback,
resolves direct links, caches covers locally and publishes a small set of Qt
signals for the UI.
"""

from __future__ import annotations

import hashlib
import logging
import os
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from core import station
from core.audio_engine import STATE_PAUSED, STATE_PLAYING, STATE_STOPPED, AudioEngine
from core.yandex_service import (
    DEFAULT_COVER_SIZE,
    DEFAULT_DIVERSITY,
    DEFAULT_LANGUAGE,
    PREFETCH_THRESHOLD,
    QUALITY_AUTO,
    StreamLink,
    WaveTrack,
    YandexService,
    track_cover_url,
)

log = logging.getLogger(__name__)

POSITION_INTERVAL_MS = 200
COVER_TIMEOUT_S = 12.0
COVER_MAX_BYTES = 8 * 1024 * 1024
COVER_CHUNK_BYTES = 64 * 1024
COVER_USER_AGENT = "yandex-music-native/0.1"
COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")

CoverDownloader = Callable[[str, Path], None]


def cover_cache_dir() -> Path:
    """Default cover directory: ``~/.cache/yandex-music-native/covers``."""
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "yandex-music-native" / "covers"


def cover_file_name(url: str) -> str:
    """Stable cache file name derived from the cover URL."""
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix not in COVER_SUFFIXES:
        suffix = ".jpg"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return f"{digest}{suffix}"


def download_cover(url: str, target: Path) -> None:
    """Fetch a cover into ``target`` atomically, refusing oversized answers."""
    request = urllib.request.Request(url, headers={"User-Agent": COVER_USER_AGENT})
    partial = target.with_name(target.name + ".part")
    try:
        with urllib.request.urlopen(request, timeout=COVER_TIMEOUT_S) as response:
            with partial.open("wb") as handle:
                total = 0
                while True:
                    chunk = response.read(COVER_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > COVER_MAX_BYTES:
                        raise RuntimeError("обложка слишком большая")
                    handle.write(chunk)
        if not partial.exists() or partial.stat().st_size == 0:
            raise RuntimeError("сервер вернул пустую обложку")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


class QueueMode(str, Enum):
    """Where the upcoming tracks come from."""

    MANUAL = "manual"
    RADIO = "radio"


class PlaybackState(str, Enum):
    """Transport state published through :attr:`PlaybackController.state_changed`."""

    STOPPED = "stopped"
    BUFFERING = "buffering"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass(frozen=True)
class TrackMetadata:
    """Everything the UI needs to render the current track."""

    id: str
    title: str
    artists: tuple[str, ...] = ()
    album: str = ""
    duration_ms: int = 0
    cover_path: str | None = None
    cover_url: str | None = None
    liked: bool = False
    available: bool = True
    explicit: bool = False
    source: str = ""
    quality: str = ""
    bitrate: int = 0
    lossless: bool = False
    index: int = -1

    @property
    def artists_name(self) -> str:
        return ", ".join(self.artists)

    @property
    def duration(self) -> float:
        return self.duration_ms / 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "artists": list(self.artists),
            "artists_name": self.artists_name,
            "album": self.album,
            "duration_ms": self.duration_ms,
            "duration": self.duration,
            "cover_path": self.cover_path,
            "cover_url": self.cover_url,
            "liked": self.liked,
            "available": self.available,
            "explicit": self.explicit,
            "source": self.source,
            "quality": self.quality,
            "bitrate": self.bitrate,
            "lossless": self.lossless,
            "index": self.index,
        }


class _CoverSignals(QObject):
    finished = Signal(str, str)
    failed = Signal(str, str)


class _CoverTask(QRunnable):
    """Download one cover off the GUI thread."""

    def __init__(
        self,
        track_id: str,
        url: str,
        target: Path,
        downloader: CoverDownloader,
        signals: _CoverSignals,
    ) -> None:
        super().__init__()
        self._track_id = track_id
        self._url = url
        self._target = target
        self._downloader = downloader
        self._signals = signals

    def run(self) -> None:
        try:
            self._downloader(self._url, self._target)
        except Exception as exc:  # noqa: BLE001
            log.info("cover download failed for %s: %s", self._track_id, exc)
            self._signals.failed.emit(self._track_id, str(exc))
            return
        self._signals.finished.emit(self._track_id, str(self._target))


def link_summary(link: StreamLink) -> str:
    """Short human readable quality description, e.g. ``flac 1411 kbps``."""
    parts = [link.codec or "?"]
    if link.bitrate:
        parts.append(f"{link.bitrate} kbps")
    if link.preview:
        parts.append("preview")
    return " ".join(parts)


class PlaybackController(QObject):
    """Queue owner, feedback reporter and transport facade for the UI."""

    track_changed = Signal(object)
    state_changed = Signal(str)
    position_changed = Signal(int, int)
    seeked = Signal(int)
    volume_changed = Signal(int)
    wave_settings_changed = Signal(dict)
    like_status_changed = Signal(str, bool)
    fft_data_ready = Signal(object)
    waveform_data_ready = Signal(object)

    queue_changed = Signal(int)
    buffering_changed = Signal(bool)
    cover_ready = Signal(str, str)
    stream_changed = Signal(str, str)
    wave_started = Signal(list)
    wave_error = Signal(str)
    mark_error = Signal(str, str)
    playback_error = Signal(str)

    def __init__(
        self,
        service: YandexService,
        engine: AudioEngine | None = None,
        parent: QObject | None = None,
        *,
        quality: str = QUALITY_AUTO,
        allow_lossless: bool | None = None,
        volume: int = 80,
        cover_dir: str | os.PathLike[str] | None = None,
        cover_size: str = DEFAULT_COVER_SIZE,
        cover_downloader: CoverDownloader | None = None,
        position_interval_ms: int = POSITION_INTERVAL_MS,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._owns_engine = engine is None
        self._engine = engine if engine is not None else AudioEngine(parent=self, volume=volume)
        self._quality = str(quality or QUALITY_AUTO)
        self._allow_lossless = allow_lossless
        self._volume = int(volume)
        self._cover_dir = Path(cover_dir).expanduser() if cover_dir else cover_cache_dir()
        self._cover_size = str(cover_size or DEFAULT_COVER_SIZE)
        self._cover_downloader: CoverDownloader = cover_downloader or download_cover
        self._cover_paths: dict[str, str] = {}
        self._cover_inflight: set[str] = set()
        self._links: dict[str, StreamLink] = {}
        self._mode = QueueMode.MANUAL
        self._manual_queue: list[WaveTrack] = []
        self._manual_index = -1
        self._pending: WaveTrack | None = None
        self._current: TrackMetadata | None = None
        self._current_wave: WaveTrack | None = None
        self._duration_ms = 0
        self._last_position = 0
        self._start_ms = 0
        self._auto_play = True
        self._buffering = False
        self._state = PlaybackState.STOPPED
        self._shutting_down = False
        self._connections: list[tuple[Any, Any]] = []

        self._cover_pool = QThreadPool(self)
        self._cover_pool.setMaxThreadCount(2)
        self._cover_signals = _CoverSignals(self)
        self._connect(self._cover_signals.finished, self._on_cover_done)
        self._connect(self._cover_signals.failed, self._on_cover_failed)

        self._connect(self._engine.spectrum_ready, self.fft_data_ready.emit)
        self._connect(self._engine.waveform_ready, self.waveform_data_ready.emit)
        self._connect(self._engine.state_changed, self._on_engine_state)
        self._connect(self._engine.duration_changed, self._on_engine_duration)
        self._connect(self._engine.finished, self._on_engine_finished)
        self._connect(self._engine.error, self._on_engine_error)
        self._connect(service.track_changed, self._on_service_track)
        self._connect(service.wave_started, self._on_wave_started)
        self._connect(service.wave_batch, self._on_wave_batch)
        self._connect(service.wave_stopped, self._on_wave_stopped)
        self._connect(service.wave_error, self._on_wave_error)
        self._connect(service.stream_ready, self._on_stream_ready)
        self._connect(service.stream_error, self._on_stream_error)
        self._connect(service.like_changed, self._on_like_changed)
        self._connect(service.like_error, self.mark_error.emit)
        self._connect(service.dislike_error, self.mark_error.emit)
        self._connect(service.settings_applied, self._on_settings_applied)
        self._connect(service.queue_changed, self.queue_changed.emit)
        self._connect(service.session_cleared, self._on_session_cleared)

        self._position_timer = QTimer(self)
        self._position_timer.setInterval(max(50, int(position_interval_ms)))
        self._connect(self._position_timer.timeout, self._poll_position)
        self._position_timer.start()

    def _connect(self, signal: Any, slot: Any) -> None:
        signal.connect(slot)
        self._connections.append((signal, slot))

    # -- properties ---------------------------------------------------------

    @property
    def service(self) -> YandexService:
        return self._service

    @property
    def engine(self) -> AudioEngine:
        return self._engine

    @property
    def mode(self) -> QueueMode:
        return self._mode

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def current(self) -> TrackMetadata | None:
        return self._current

    @property
    def current_wave(self) -> WaveTrack | None:
        return self._current_wave

    @property
    def queue(self) -> list[WaveTrack]:
        if self._mode == QueueMode.RADIO:
            return self._service.queue_snapshot()
        return list(self._manual_queue)

    @property
    def position(self) -> int:
        if self._mode == QueueMode.RADIO:
            return self._service.position()
        return self._manual_index

    @property
    def position_ms(self) -> int:
        """Playback position in milliseconds, as MPRIS and the UI need it."""
        return int(self._engine.get_position_ms())

    @property
    def remaining(self) -> int:
        if self._mode == QueueMode.RADIO:
            return self._service.remaining()
        return max(0, len(self._manual_queue) - self._manual_index - 1)

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def quality(self) -> str:
        return self._quality

    @property
    def allow_lossless(self) -> bool | None:
        return self._allow_lossless

    @property
    def volume(self) -> int:
        return int(self._engine.volume)

    @property
    def is_playing(self) -> bool:
        return self._state == PlaybackState.PLAYING

    @property
    def is_paused(self) -> bool:
        return self._state == PlaybackState.PAUSED

    @property
    def is_buffering(self) -> bool:
        return self._state == PlaybackState.BUFFERING

    @property
    def settings(self) -> dict[str, str]:
        mood_energy, diversity, language = self._service.settings()
        return {
            "mood_energy": mood_energy or "all",
            "diversity": diversity or DEFAULT_DIVERSITY,
            "language": language or DEFAULT_LANGUAGE,
        }

    def cover_path(self, track_id: str) -> str | None:
        """Local cover path for ``track_id`` when it is already downloaded."""
        return self._cover_paths.get(str(track_id))

    def queue_snapshot(self) -> list[WaveTrack]:
        return self.queue

    # -- queue control ------------------------------------------------------

    def play_track(self, track: Any) -> bool:
        """Play a single track given as a model, a wave track or a known id."""
        target = self._coerce_track(track)
        if target is None:
            self.playback_error.emit("Трек не найден в очереди")
            return False
        return self._start_manual([target], 0)

    def play_playlist(self, tracks: Iterable[Any], start_index: int = 0) -> bool:
        """Replace the queue with ``tracks`` and start at ``start_index``."""
        items = [item for item in (self._coerce_track(one) for one in tracks) if item is not None]
        if not items:
            self.playback_error.emit("Нечего играть: очередь пуста")
            return False
        index = max(0, min(int(start_index), len(items) - 1))
        return self._start_manual(items, index)

    def start_wave(
        self,
        mode: QueueMode = QueueMode.RADIO,
        mood: str | int | None = None,
        activity: str | int | None = None,
        language: str | None = None,
        diversity: str | None = None,
    ) -> bool:
        """Start «Моя волна» with validated ``mood``/``activity``/``language``.

        ``mode`` is accepted for symmetry with :meth:`play_playlist`; the wave
        always runs in :attr:`QueueMode.RADIO`.
        """
        if QueueMode(mode) != QueueMode.RADIO:
            log.info("start_wave called with mode %s, using radio", mode)
        try:
            station.resolve_settings(
                mood=mood, activity=activity, language=language, diversity=diversity
            )
        except ValueError as exc:
            self.wave_error.emit(str(exc))
            return False
        self._mode = QueueMode.RADIO
        self._manual_queue = []
        self._manual_index = -1
        self._report_leave(completed=False)
        self._engine.stop()
        self._set_buffering(True)
        self._emit_queue()
        self._emit_settings(
            mood=mood, activity=activity, language=language, diversity=diversity
        )
        return self._service.start_my_wave(
            mood=mood, activity=activity, language=language, diversity=diversity
        )

    def apply_wave_settings(
        self,
        mood_energy: str | None = None,
        diversity: str | None = None,
        language: str | None = None,
        restart: bool = True,
        mood: str | None = None,
        activity: str | None = None,
    ) -> bool:
        """Change the station settings through ``rotor_station_settings2``."""
        return self._service.apply_settings(
            mood_energy=mood_energy,
            diversity=diversity,
            language=language,
            restart=restart,
            mood=mood,
            activity=activity,
        )

    def _start_manual(self, items: list[WaveTrack], index: int) -> bool:
        self._report_leave(completed=False)
        self._service.stop_my_wave()
        self._mode = QueueMode.MANUAL
        self._manual_queue = items
        self._manual_index = index
        self._emit_queue()
        return self._load(items[index], auto_play=True)

    def next(self) -> bool:
        """Skip to the next track, reporting a skip for the current one."""
        seconds = self._played_seconds()
        self._report_leave(completed=False, seconds=seconds)
        if self._mode == QueueMode.RADIO:
            if self._service.skip(played_seconds=seconds) is None:
                self._wait_for_more()
                return False
            return True
        target = self._next_manual()
        if target is None:
            self._stop_playback(report=False)
            return False
        self._manual_index += 1
        self._emit_queue()
        return self._load(target, auto_play=True)

    def prev(self) -> bool:
        """Go back one track; restart the current one when already at the start."""
        if self._mode == QueueMode.RADIO:
            self._service.previous_track()
            return self._current is not None
        if self._manual_index <= 0:
            if self._current is None:
                return False
            if not self._engine.seek(0):
                return False
            self._last_position = 0
            self.position_changed.emit(0, self._duration_ms)
            self.seeked.emit(0)
            return True
        self._manual_index -= 1
        self._emit_queue()
        return self._load(self._manual_queue[self._manual_index], auto_play=True)

    def toggle_play(self) -> str:
        """Pause, resume or restart and return the resulting state value."""
        if self._state == PlaybackState.PLAYING:
            self._engine.pause()
            return self._refresh_state().value
        if self._state == PlaybackState.PAUSED:
            self._engine.toggle_play()
            return self._refresh_state().value
        if self._current_wave is not None:
            self._load(self._current_wave, auto_play=True)
            return self._state.value
        if self._mode == QueueMode.RADIO:
            self.start_wave()
            return self._state.value
        if self._manual_queue:
            self._manual_index = max(0, min(self._manual_index, len(self._manual_queue) - 1))
            self._emit_queue()
            self._load(self._manual_queue[self._manual_index], auto_play=True)
            return self._state.value
        return PlaybackState.STOPPED.value

    def stop(self) -> None:
        """Stop playback and report the current track as skipped."""
        self._stop_playback(report=True)

    def seek(self, position_ms: int) -> bool:
        """Absolute seek in milliseconds."""
        if not self._engine.seek(int(position_ms)):
            return False
        self._last_position = self._engine.get_position_ms()
        self.position_changed.emit(self._last_position, self._duration_ms)
        self.seeked.emit(self._last_position)
        return True

    def set_volume(self, percent: int) -> int:
        """Set the output volume in percent and return the applied value."""
        self._volume = self._engine.set_volume(percent)
        self.volume_changed.emit(self._volume)
        return self._volume

    def set_quality(self, quality: str, allow_lossless: bool | None = None) -> None:
        """Change the quality used for the next resolved links."""
        self._quality = str(quality or QUALITY_AUTO)
        if allow_lossless is not None:
            self._allow_lossless = bool(allow_lossless)

    # -- marks --------------------------------------------------------------

    def like(self) -> bool:
        return self._mark_current(add=True, dislike=False)

    def remove_like(self) -> bool:
        return self._mark_current(add=False, dislike=False)

    def dislike(self) -> bool:
        return self._mark_current(add=True, dislike=True)

    def remove_dislike(self) -> bool:
        return self._mark_current(add=False, dislike=True)

    def _mark_current(self, add: bool, dislike: bool) -> bool:
        if self._current_wave is None:
            self.mark_error.emit("", "Трек не выбран")
            return False
        if dislike:
            if add:
                return self._service.dislike(self._current_wave)
            return self._service.remove_dislike(self._current_wave)
        if add:
            return self._service.like(self._current_wave)
        return self._service.remove_like(self._current_wave)

    def _on_like_changed(self, track_id: str, liked: bool) -> None:
        if self._current is not None and self._current.id == track_id:
            self._current = replace(self._current, liked=bool(liked))
        self.like_status_changed.emit(track_id, bool(liked))

    # -- loading ------------------------------------------------------------

    def _load(self, track: WaveTrack, *, auto_play: bool = True, start_ms: int = 0) -> bool:
        if track is None or not track.track_id:
            return False
        self._pending = track
        self._auto_play = auto_play
        self._start_ms = max(0, int(start_ms))
        self._set_buffering(True)
        self._engine.stop()
        self._duration_ms = track.duration_ms
        self._last_position = 0
        self._prefetch_ahead()
        cached = self._service.cached_stream(track, self._quality)
        if cached is not None:
            self._on_stream_ready(cached)
            return True
        return self._service.stream_url(track, self._quality, self._allow_lossless)

    def _on_stream_ready(self, link: StreamLink) -> None:
        target = self._pending
        if target is None or link.track_id != target.track_id:
            log.debug("dropping stale stream link for %s", link.track_id)
            return
        self._links[link.track_id] = link
        meta = self._metadata(target, link=link)
        self._current = meta
        self._current_wave = target
        self._pending = None
        self._duration_ms = meta.duration_ms
        self._service.track_started(target)
        self.track_changed.emit(meta)
        self.stream_changed.emit(meta.id, link_summary(link))
        self.position_changed.emit(0, self._duration_ms)
        self._ensure_cover(meta)
        if self._auto_play:
            self._engine.play(link.url, self._start_ms)
        self._set_buffering(False)

    def _on_stream_error(self, track_id: str, message: str) -> None:
        if self._pending is None or (track_id and track_id != self._pending.track_id):
            return
        self._pending = None
        self._set_buffering(False)
        self.playback_error.emit(message)
        if self._engine.state == STATE_STOPPED:
            self._stop_playback(report=False)

    def _metadata(self, track: WaveTrack, link: StreamLink | None = None) -> TrackMetadata:
        liked = bool(track.liked) or self._service.is_liked(track)
        index = self._manual_index if self._mode == QueueMode.MANUAL else -1
        return TrackMetadata(
            id=track.track_id,
            title=track.title,
            artists=tuple(track.artists),
            album=track.album,
            duration_ms=track.duration_ms,
            cover_path=self._cover_paths.get(track.track_id),
            cover_url=track_cover_url(track, self._cover_size),
            liked=liked,
            available=bool(track.available),
            explicit=bool(track.explicit),
            source=track.source,
            quality=link.codec if link is not None else "",
            bitrate=link.bitrate if link is not None else 0,
            lossless=bool(link.lossless) if link is not None else False,
            index=index,
        )

    def _prefetch_ahead(self) -> None:
        if self._mode == QueueMode.RADIO:
            self._service.maybe_prefetch()
        nxt = self._next_track()
        if nxt is None or self.remaining > PREFETCH_THRESHOLD:
            return
        self._service.stream_url(nxt, self._quality, self._allow_lossless)

    def _next_track(self) -> WaveTrack | None:
        if self._mode == QueueMode.RADIO:
            snapshot = self._service.queue_snapshot()
            index = self._service.position() + 1
            return snapshot[index] if 0 <= index < len(snapshot) else None
        return self._next_manual()

    def _next_manual(self) -> WaveTrack | None:
        index = self._manual_index + 1
        if 0 <= index < len(self._manual_queue):
            return self._manual_queue[index]
        return None

    def _coerce_track(self, value: Any) -> WaveTrack | None:
        if value is None:
            return None
        if isinstance(value, WaveTrack):
            return value
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            value = str(value)
        if isinstance(value, str):
            key = value.strip()
            if not key:
                return None
            sources: tuple[list[WaveTrack], ...] = (
                self._manual_queue,
                self._service.queue_snapshot(),
                self._service.history(),
            )
            for source in sources:
                for item in source:
                    if key in (item.track_id, item.id):
                        return item
            return None
        return WaveTrack.from_track(value)

    # -- transport events ---------------------------------------------------

    def _on_engine_state(self, state: str) -> None:
        if state == STATE_PLAYING:
            self._set_buffering(False)
        self._refresh_state()

    def _on_engine_duration(self, seconds: float) -> None:
        try:
            value = int(float(seconds) * 1000)
        except (TypeError, ValueError):
            return
        if value > 0:
            self._duration_ms = value
        self.position_changed.emit(self._engine.get_position_ms(), self._duration_ms)

    def _on_engine_finished(self) -> None:
        if self._current is None or self._buffering or self._pending is not None:
            log.debug("ignoring end-of-file while the next track is loading")
            return
        meta = self._current
        total = self._engine.get_position_ms() or (meta.duration_ms if meta is not None else 0)
        seconds = max(0.0, total / 1000.0)
        self._set_buffering(False)
        if self._mode == QueueMode.RADIO:
            self._service.track_played(seconds)
            if self._service.next_track(played_seconds=seconds, completed=True) is None:
                self._wait_for_more()
            return
        self._manual_index += 1
        target = self._next_manual()
        if target is None:
            self._stop_playback(report=False)
            return
        self._emit_queue()
        self._load(target, auto_play=True)

    def _on_engine_error(self, message: str) -> None:
        self._pending = None
        self._set_buffering(False)
        self.playback_error.emit(message)
        if self._engine.state == STATE_STOPPED:
            self._stop_playback(report=False)

    def _poll_position(self) -> None:
        position = self._engine.get_position_ms()
        if position == self._last_position:
            return
        self._last_position = position
        self.position_changed.emit(position, self._duration_ms)

    def _played_seconds(self) -> float:
        return max(0.0, self._engine.get_position_ms() / 1000.0)

    # -- service events -----------------------------------------------------

    def _on_service_track(self, track: WaveTrack) -> None:
        if self._mode != QueueMode.RADIO or track is None:
            return
        self._load(track, auto_play=self._auto_play)

    def _on_wave_started(self, tracks: list[WaveTrack]) -> None:
        self._emit_queue()
        self.wave_started.emit(list(tracks))
        if self._current is None and self._pending is None:
            self._set_buffering(False)
            self._refresh_state()

    def _on_wave_batch(self, tracks: list[WaveTrack]) -> None:
        self._emit_queue()
        if self._mode != QueueMode.RADIO or self._current is not None:
            return
        if self._service.resume_queue() is None:
            self._set_buffering(False)
            self._refresh_state()

    def _on_wave_stopped(self) -> None:
        self._refresh_state()

    def _on_wave_error(self, message: str) -> None:
        if self._pending is None and self._engine.state != STATE_PLAYING:
            self._current = None
            self._current_wave = None
            self._set_buffering(False)
        self.wave_error.emit(message)
        self._refresh_state()

    def _on_session_cleared(self) -> None:
        self._stop_playback(report=False)

    def _on_settings_applied(self, mood_energy: str, diversity: str, language: str) -> None:
        preferences = self._service.preferences()
        self.wave_settings_changed.emit(
            {
                "mood": preferences.mood or station.DEFAULT_MOOD,
                "activity": preferences.activity or station.DEFAULT_ACTIVITY,
                "mood_energy": mood_energy or "all",
                "diversity": diversity or station.DEFAULT_DIVERSITY,
                "language": preferences.language or station.DEFAULT_LANGUAGE,
            }
        )

    def _emit_settings(
        self,
        mood: str | int | None = None,
        activity: str | int | None = None,
        language: str | None = None,
        diversity: str | None = None,
    ) -> None:
        current = self._service.preferences()
        try:
            requested = station.resolve_settings(
                mood=mood,
                activity=activity,
                language=language,
                diversity=diversity,
            )
        except ValueError:
            return
        preferences = station.WaveSettings(
            mood=requested.mood if mood is not None else current.mood,
            activity=requested.activity if activity is not None else current.activity,
            language=requested.language if language is not None else current.language,
            diversity=requested.diversity if diversity is not None else current.diversity,
        )
        mood_energy, diversity_value, language_value = preferences.to_wire()
        self.wave_settings_changed.emit(
            {
                "mood": preferences.mood or station.DEFAULT_MOOD,
                "activity": preferences.activity or station.DEFAULT_ACTIVITY,
                "mood_energy": mood_energy,
                "diversity": diversity_value,
                "language": language_value,
            }
        )

    # -- covers -------------------------------------------------------------

    def _ensure_cover(self, meta: TrackMetadata) -> None:
        if not meta.cover_url or meta.id in self._cover_inflight:
            return
        target = self._cover_dir / cover_file_name(meta.cover_url)
        cached = self._cover_paths.get(meta.id)
        if cached and Path(cached).exists():
            return
        if target.exists() and target.stat().st_size > 0:
            self._cover_paths[meta.id] = str(target)
            return
        self._cover_dir.mkdir(parents=True, exist_ok=True)
        self._cover_inflight.add(meta.id)
        self._cover_pool.start(
            _CoverTask(meta.id, meta.cover_url, target, self._cover_downloader, self._cover_signals)
        )

    def _on_cover_done(self, track_id: str, path: str) -> None:
        self._cover_inflight.discard(track_id)
        self._cover_paths[track_id] = path
        if self._current is not None and self._current.id == track_id:
            self._current = replace(self._current, cover_path=path)
        self.cover_ready.emit(track_id, path)

    def _on_cover_failed(self, track_id: str, message: str) -> None:
        self._cover_inflight.discard(track_id)
        log.debug("cover for %s failed: %s", track_id, message)

    # -- internals ----------------------------------------------------------

    def _set_buffering(self, value: bool) -> None:
        value = bool(value)
        if value == self._buffering:
            self._refresh_state()
            return
        self._buffering = value
        self.buffering_changed.emit(value)
        self._refresh_state()

    def _refresh_state(self) -> PlaybackState:
        if self._buffering:
            state = PlaybackState.BUFFERING
        elif self._engine.state == STATE_PLAYING:
            state = PlaybackState.PLAYING
        elif self._engine.state == STATE_PAUSED:
            state = PlaybackState.PAUSED
        else:
            state = PlaybackState.STOPPED
        return self._publish(state)

    def _publish(self, state: PlaybackState) -> PlaybackState:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state.value)
        return state

    def _wait_for_more(self) -> None:
        self._pending = None
        self._auto_play = True
        self._engine.stop()
        self._set_buffering(True)

    def _stop_playback(self, report: bool) -> None:
        if report:
            self._report_leave(completed=False)
        self._engine.stop()
        self._pending = None
        self._current = None
        self._current_wave = None
        self._duration_ms = 0
        self._last_position = 0
        self._set_buffering(False)
        self.position_changed.emit(0, 0)
        self._refresh_state()
        self._emit_queue()

    def _report_leave(self, completed: bool, seconds: float | None = None) -> None:
        if self._mode != QueueMode.RADIO or self._current_wave is None:
            return
        value = self._played_seconds() if seconds is None else max(0.0, float(seconds))
        if completed:
            self._service.track_played(value)
        else:
            self._service.track_skipped(self._current_wave, value)

    def _emit_queue(self) -> None:
        self.queue_changed.emit(self.remaining)

    # -- teardown -----------------------------------------------------------

    def shutdown(self) -> None:
        """Stop transport, cancel cover downloads and detach from the service."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._report_leave(completed=False)
        self._position_timer.stop()
        self._cover_pool.clear()
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections.clear()
        self._engine.stop()
        if self._owns_engine:
            try:
                self._engine.shutdown()
            except Exception:  # noqa: BLE001
                log.debug("engine shutdown failed", exc_info=True)
        self._pending = None
        self._current = None
        self._current_wave = None
        self._set_buffering(False)
        self._publish(PlaybackState.STOPPED)


__all__ = [
    "COVER_MAX_BYTES",
    "COVER_TIMEOUT_S",
    "POSITION_INTERVAL_MS",
    "PlaybackController",
    "PlaybackState",
    "QueueMode",
    "TrackMetadata",
    "cover_cache_dir",
    "cover_file_name",
    "download_cover",
    "link_summary",
]
