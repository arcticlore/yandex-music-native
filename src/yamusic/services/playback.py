"""PlaybackController — the central MVVM view-model for the player.

Coordinates: queue, stream resolution, cache, GStreamer engine, rotor
feedback, cover prefetch and playback-related signals consumed by the UI,
MPRIS and the system tray.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from yamusic.api.service import YandexApi
from yamusic.audio.engine import AudioEngine
from yamusic.cache.store import CacheStore
from yamusic.config import Settings
from yamusic.models import TrackInfo
from yamusic.services.rotor import RotorService

log = logging.getLogger(__name__)

_URL_TTL = 30 * 60  # direct links live ~1 h; refresh earlier


class PlaybackController(QObject):
    """Queue-based player with rotor support."""

    track_changed = Signal(object)  # TrackInfo | None
    cover_ready = Signal(str, str)  # track_id, local path
    state_changed = Signal(bool)  # playing
    position_changed = Signal(int)  # ms
    duration_changed = Signal(int)  # ms
    queue_changed = Signal()
    buffering_changed = Signal(int)  # percent, 100 = done
    volume_changed = Signal(int)  # percent
    liked_changed = Signal(str, bool)  # track_id, liked
    seeked = Signal(int)  # ms — for MPRIS Seeked
    stream_info_ready = Signal(str, str, int)  # track_id, codec, kbps
    error = Signal(str)

    def __init__(
        self,
        api: YandexApi,
        engine: AudioEngine,
        rotor: RotorService,
        cache: CacheStore,
        settings: Settings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.engine = engine
        self.rotor = rotor
        self.cache = cache
        self.settings = settings

        opts = settings.options()
        self.quality = opts.quality
        self._queue: list[TrackInfo] = []
        self._index = -1
        self._station_mode = False
        self._playing = False
        self._started_wall = 0.0
        self._played_seconds = 0.0
        self._fail_streak = 0
        self._url_cache: dict[str, tuple[str, float]] = {}
        self._prefetching: set[str] = set()
        self._pending_start_wave = False
        self._pending_next = False

        engine.eos.connect(self._on_eos)
        engine.error.connect(self._on_engine_error)
        engine.playback_state_changed.connect(self._on_engine_state)
        engine.buffering_changed.connect(self.buffering_changed.emit)
        engine.duration_changed.connect(self.duration_changed.emit)
        rotor.batch_loaded.connect(self._on_rotor_batch_internal)

        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(250)
        self._pos_timer.timeout.connect(self._tick_position)

        self.engine.set_volume(opts.volume)

    # ------------------------------------------------------------------ state

    @property
    def current(self) -> TrackInfo | None:
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def position_ms(self) -> int:
        return self.engine.position_ms()

    @property
    def duration_ms(self) -> int:
        return self.engine.duration_ms()

    @property
    def station_mode(self) -> bool:
        return self._station_mode

    @property
    def queue(self) -> list[TrackInfo]:
        return list(self._queue)

    def can_next(self) -> bool:
        if self._station_mode:
            return True
        return self._index + 1 < len(self._queue)

    def can_previous(self) -> bool:
        if self._station_mode:
            return self._played_seconds > 3 or self.rotor.can_go_previous()
        return self._index > 0 or self._played_seconds > 3

    # -------------------------------------------------------------- queue ops

    def play_queue(self, tracks: list[TrackInfo], index: int = 0) -> None:
        """Play an explicit list (playlist/search/album/chart)."""
        if not tracks:
            return
        self._station_mode = False
        self._queue = list(tracks)
        self._index = max(0, min(index, len(self._queue) - 1))
        self.queue_changed.emit()
        self._play_current()

    def play_from_wave(self) -> None:
        """Start / resume «Моя волна»."""
        self._station_mode = True
        self._queue.clear()
        self._index = -1
        if not self.rotor.started:
            self.rotor.start()
        track = self.rotor.pop()
        if track is None:
            self._pending_start_wave = True
            self.queue_changed.emit()
            self.rotor.fetch_more()
            return
        self._queue = [track]
        self._index = 0
        self.queue_changed.emit()
        self._play_current()

    def _append_wave_tracks(self, count: int = 1) -> None:
        for _ in range(count):
            track = self.rotor.pop()
            if track is None:
                break
            self._queue.append(track)
        self.queue_changed.emit()

    def next(self, manual: bool = True) -> None:
        if self._station_mode:
            current = self.current
            if current is not None and manual:
                self.rotor.track_skipped(current, self._played_seconds)
            track = self.rotor.pop()
            if track is None:
                self._pending_next = True
                self.rotor.fetch_more(force=True)
                return
            self._queue.append(track)
            self._index = len(self._queue) - 1
            self.queue_changed.emit()
            self._play_current()
            return

        if self._index + 1 < len(self._queue):
            self._index += 1
            self.queue_changed.emit()
            self._play_current()
        else:
            self._stop_playback()

    def _on_rotor_batch_internal(self, _count: int) -> None:
        """Consume pending start/next actions once a rotor batch arrives."""
        if self._pending_start_wave:
            self._pending_start_wave = False
            track = self.rotor.pop()
            if track is not None:
                self._queue = [track]
                self._index = 0
                self.queue_changed.emit()
                self._play_current()
            return
        if self._pending_next:
            self._pending_next = False
            track = self.rotor.pop()
            if track is not None:
                self._queue.append(track)
                self._index = len(self._queue) - 1
                self.queue_changed.emit()
                self._play_current()
            return
        # keep a short lookahead queue during wave playback
        if self._station_mode and self._index >= 0:
            upcoming = len(self._queue) - (self._index + 1)
            if upcoming < 2:
                self._append_wave_tracks(2 - upcoming)

    def previous(self) -> None:
        if self._played_seconds > 3 or not self.can_previous():
            self.seek(0)
            return
        if self._station_mode:
            track = self.rotor.previous()
            if track is None:
                self.seek(0)
                return
            if self._index > 0 and self._queue[self._index - 1].id == track.id:
                self._index -= 1
                self.queue_changed.emit()
                self._play_current()
            elif self.current is not None and self.current.id == track.id:
                self.seek(0)
            else:
                self._queue.insert(max(self._index, 0), track)
                self._index = max(self._index, 0)
                self.queue_changed.emit()
                self._play_current()
            return
        if self._index > 0:
            self._index -= 1
            self.queue_changed.emit()
            self._play_current()

    # ------------------------------------------------------------- transport

    def play_pause(self) -> None:
        if self.current is None:
            if self._station_mode or self.settings.options().station:
                self.play_from_wave()
            return
        if self._playing:
            self._pause()
        else:
            self._resume()

    def _pause(self) -> None:
        if not self._playing:
            return
        self._accumulate()
        self.engine.pause()
        self._playing = False
        self._pos_timer.stop()
        self.state_changed.emit(False)

    def _resume(self) -> None:
        if self._playing:
            return
        self.engine.play()
        self._playing = True
        self._started_wall = time.monotonic()
        self._pos_timer.start()
        self.state_changed.emit(True)

    def stop(self) -> None:
        self._stop_playback()

    def _stop_playback(self) -> None:
        if self._playing:
            self._accumulate()
        self.engine.stop()
        self._playing = False
        self._pos_timer.stop()
        self._index = -1
        self.state_changed.emit(False)
        self.position_changed.emit(0)
        self.track_changed.emit(None)

    def seek(self, position_ms: int) -> None:
        if self.current is None:
            return
        ok = self.engine.seek(position_ms)
        if ok:
            # restart played accounting from the new position
            self._played_seconds = max(0.0, position_ms / 1000.0)
            self._started_wall = time.monotonic()
            self.position_changed.emit(position_ms)
            self.seeked.emit(position_ms)

    def set_volume(self, percent: int) -> None:
        self.engine.set_volume(percent)
        self.settings.set_volume(percent)
        self.volume_changed.emit(percent)

    # ----------------------------------------------------------------- likes

    def toggle_like(self) -> None:
        track = self.current
        if track is None:
            return
        new_state = not track.liked
        track.liked = new_state
        self.api.set_like(track.id, new_state)
        self.liked_changed.emit(track.id, new_state)

    def dislike(self) -> None:
        track = self.current
        if track is None:
            return
        track.liked = False
        self.api.set_dislike(track.id)
        self.liked_changed.emit(track.id, False)
        if self._station_mode:
            self.rotor.track_skipped(track, self._played_seconds)
            self.next(manual=False)
        else:
            self.next(manual=True)

    # ----------------------------------------------------------- play current

    def _play_current(self) -> None:
        track = self.current
        if track is None:
            return
        if not track.available or track.raw is None:
            self.error.emit(f"Трек недоступен: {track.title}")
            self.next(manual=False)
            return

        self._fail_streak = 0
        self._accumulate(reset=True)
        self.track_changed.emit(track)
        self._request_cover(track)

        cached = self.cache.track_path(track.id)
        if cached is not None:
            self._load_uri(cached.as_uri())
            if self._station_mode:
                self.rotor.track_started(track)
            self._prefetch_next()
            return

        self._resolve_and_play(track, fresh=True)

    def _resolve_and_play(self, track: TrackInfo, fresh: bool) -> None:
        tag_track_id = track.id
        cached_url, ts = self._url_cache.get(tag_track_id, ("", 0.0))
        if cached_url and time.time() - ts < _URL_TTL and not fresh:
            self._start_with_url(track, cached_url)
            return

        quality = self.quality

        def _ok(url: str) -> None:
            if self.current is None or self.current.id != tag_track_id:
                return
            self._url_cache[tag_track_id] = (url, time.time())
            self._start_with_url(track, url)
            self._prefetch_next()
            if self.settings.options().cache_tracks:
                self._background_cache(track)

        def _err(message: str) -> None:
            if self.current is None or self.current.id != tag_track_id:
                return
            self.error.emit(f"Не удалось получить поток: {message}")
            self._fail_streak += 1
            if self._fail_streak < 3:
                QTimer.singleShot(500, lambda: self.next(manual=False))
            else:
                self._stop_playback()

        self.api.stream_url(track.raw, quality, _ok, _err)

    def _start_with_url(self, track: TrackInfo, url: str) -> None:
        self.engine.load(url)
        if self._station_mode:
            self.rotor.track_started(track)
        self.state_changed.emit(True)
        self._playing = True
        self._started_wall = time.monotonic()
        if not self._pos_timer.isActive():
            self._pos_timer.start()

    def _load_uri(self, uri: str) -> None:
        self.engine.load(uri)
        self.state_changed.emit(True)
        self._playing = True
        self._started_wall = time.monotonic()
        if not self._pos_timer.isActive():
            self._pos_timer.start()

    # ---------------------------------------------------------------- events

    def _on_eos(self) -> None:
        track = self.current
        played = self._current_played()
        if track is not None and self._station_mode:
            self.rotor.track_finished(track, played)
        self._playing = False
        self._pos_timer.stop()
        self.next(manual=False)

    def _on_engine_error(self, message: str) -> None:
        self.error.emit(f"Аудио: {message}")
        self._fail_streak += 1
        if self._fail_streak >= 3:
            self._stop_playback()
            return
        QTimer.singleShot(300, lambda: self.next(manual=False))

    def _on_engine_state(self, playing: bool) -> None:
        # mirror GStreamer state (buffering pauses, etc.)
        if playing and not self._playing and self.current is not None:
            self._playing = True
            self._started_wall = time.monotonic()
            if not self._pos_timer.isActive():
                self._pos_timer.start()
            self.state_changed.emit(True)
        elif not playing and self._playing and self.engine.uri is not None:
            # could be our pause() or a buffering dip — only report user pause
            pass

    # -------------------------------------------------------------- position

    def _accumulate(self, reset: bool = False) -> None:
        if reset:
            self._started_wall = time.monotonic()
            self._played_seconds = 0.0
            return
        if self._playing and self._started_wall:
            self._played_seconds += time.monotonic() - self._started_wall
            self._started_wall = time.monotonic()

    def _current_played(self) -> float:
        played = self._played_seconds
        if self._playing and self._started_wall:
            played += time.monotonic() - self._started_wall
        return played

    def _tick_position(self) -> None:
        if self.current is None:
            return
        pos = self.engine.position_ms()
        dur = self.engine.duration_ms()
        self.position_changed.emit(pos)
        if dur:
            self.duration_changed.emit(dur)

    # ----------------------------------------------------------- side effects

    def _request_cover(self, track: TrackInfo) -> None:
        if not track.cover_url:
            return
        url = track.cover_url
        local = self.cache.cover_path(url)
        if local is not None:
            self.cover_ready.emit(track.id, str(local))
            return
        dest = self.cache.cover_file(url)

        def _ok(data: object) -> None:
            if not isinstance(data, (bytes, bytearray)) or not data:
                return
            dest.write_bytes(data)
            self.cache.put_cover(url, dest)
            self.cover_ready.emit(track.id, str(dest))

        def _err(_message: str) -> None:
            pass

        self.api.fetch_cover(url, _ok, _err)

    def _background_cache(self, track: TrackInfo) -> None:
        base = self.cache.base_id(track.id)
        if self.cache.track_path(track.id) is not None:
            return
        ext_guess = "flac" if self.quality in ("auto", "lossless") else "mp3"
        dest = self.cache.tracks_dir / f"{base}.{ext_guess}"

        def _ok(_path: object) -> None:
            real = Path(_path) if isinstance(_path, Path) else dest
            self.cache.put_track(
                track.id,
                real,
                title=track.title,
                artists=track.artist_line,
                album=track.album,
                duration_ms=track.duration_ms,
            )
            self.cache.evict()

        def _err(_message: str) -> None:
            if dest.exists():
                dest.unlink(missing_ok=True)

        self.api.cache_track(track.raw, dest, self.quality, _ok, _err)

    def _prefetch_next(self) -> None:
        """Warm the direct URL of the upcoming track."""
        nxt = self._peek_next()
        if nxt is None or nxt.raw is None or nxt.id in self._prefetching:
            return
        cached_url, ts = self._url_cache.get(nxt.id, ("", 0.0))
        if cached_url and time.time() - ts < _URL_TTL:
            return
        self._prefetching.add(nxt.id)

        def _ok(url: str) -> None:
            self._prefetching.discard(nxt.id)
            self._url_cache[nxt.id] = (url, time.time())

        def _err(_message: str) -> None:
            self._prefetching.discard(nxt.id)

        self.api.stream_url(nxt.raw, self.quality, _ok, _err)

    def _peek_next(self) -> TrackInfo | None:
        if self._station_mode:
            return self.rotor.buffer[0] if self.rotor.buffer else None
        if self._index + 1 < len(self._queue):
            return self._queue[self._index + 1]
        return None

    def on_rotor_batch(self, _count: int) -> None:
        """Compatibility alias — real handling is _on_rotor_batch_internal."""
        return

    def set_quality(self, quality: str) -> None:
        self.quality = quality
        self.settings.set_quality(quality)
