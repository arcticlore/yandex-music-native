"""«Моя волна» (Rotor) service: station streaming, feedback and tuning.

Feedback types sent to Yandex (per official client behaviour):
    radioStarted, trackStarted, trackFinished, skip
Likes/dislikes additionally go through the favourites API.
"""

from __future__ import annotations

import logging
import re
from collections import deque

from PySide6.QtCore import QObject, Signal

from yamusic.api.service import YandexApi
from yamusic.config import Settings
from yamusic.constants import ROTOR_FEEDBACK_FROM_PREFIX, WAVE_STATION
from yamusic.models import RotorBatch, StationInfo, TrackInfo

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^\w.~-]+")

_FETCH_THRESHOLD = 4  # start refilling when this few tracks remain


class RotorService(QObject):
    """Buffered infinite stream of tracks from a rotor station."""

    batch_loaded = Signal(int)  # tracks appended to the internal buffer
    stations_loaded = Signal(list)  # list[StationInfo]
    station_changed = Signal(str)
    error = Signal(str)

    def __init__(self, api: YandexApi, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._settings = settings
        self.station: str = settings.options().station or WAVE_STATION
        self.batch_id: str | None = None
        self.buffer: deque[TrackInfo] = deque()
        self.history: list[TrackInfo] = []
        self.stations: list[StationInfo] = []
        self._loading = False
        self._started = False

    # -- from attribute (proven format in the API docs) -------------------

    @property
    def from_field(self) -> str:
        uid = self._api.uid or 0
        return f"{ROTOR_FEEDBACK_FROM_PREFIX}-{uid}"

    # -- stations ---------------------------------------------------------

    def load_stations(self) -> None:
        def _ok(stations: object) -> None:
            if isinstance(stations, list):
                self.stations = list(stations)
                # ensure wave is present first
                if not any(s.id == WAVE_STATION for s in self.stations):
                    self.stations.insert(
                        0, StationInfo(id=WAVE_STATION, name="Моя волна", category="personal")
                    )
                else:
                    wave = next(s for s in self.stations if s.id == WAVE_STATION)
                    self.stations.remove(wave)
                    wave.name = "Моя волна"
                    self.stations.insert(0, wave)
                self.stations_loaded.emit(self.stations)

        self._api.rotor_stations(_ok, lambda e: self.error.emit(e))

    # -- stream -----------------------------------------------------------

    def set_station(self, station_id: str) -> None:
        if station_id == self.station:
            return
        self.station = station_id
        self._settings.set_station(station_id)
        self.batch_id = None
        self.buffer.clear()
        self._started = False
        self.station_changed.emit(station_id)

    def start(self) -> None:
        """Begin (or restart) the radio: radioStarted + first batch."""
        self.batch_id = None
        self.buffer.clear()
        self._started = True
        self._api.rotor_feedback(
            self.station, "radioStarted", from_=self.from_field
        )
        self.fetch_more(force=True)

    @property
    def started(self) -> bool:
        return self._started

    def fetch_more(self, force: bool = False) -> None:
        if self._loading and not force:
            return
        if self._loading:
            # allow parallel only when forced restart; keep single-flight otherwise
            return
        self._loading = True

        def _ok(batch: object) -> None:
            self._loading = False
            if isinstance(batch, RotorBatch):
                self.batch_id = batch.batch_id or self.batch_id
                fresh = [t for t in batch.tracks if t.available]
                # de-duplicate against buffer + last history
                recent = {t.id for t in list(self.buffer)[:8]}
                recent.update(t.id for t in self.history[-8:])
                added = 0
                for track in fresh:
                    if track.id in recent:
                        continue
                    self.buffer.append(track)
                    added += 1
                if added or fresh:
                    self.batch_loaded.emit(len(self.buffer))

        def _err(message: str) -> None:
            self._loading = False
            log.info("rotor fetch failed: %s", message)
            self.error.emit(message)

        self._api.rotor_tracks(self.station, _ok, _err)

    def pop(self) -> TrackInfo | None:
        """Next track from the buffer (auto-refills)."""
        if not self.buffer:
            self.fetch_more(force=True)
            return None
        track = self.buffer.popleft()
        self.history.append(track)
        if len(self.history) > 100:
            del self.history[:-100]
        if len(self.buffer) <= _FETCH_THRESHOLD:
            self.fetch_more()
        return track

    def can_go_previous(self) -> bool:
        return len(self.history) > 1  # current + previous

    def previous(self) -> TrackInfo | None:
        """Drop the current track from history and return the previous one."""
        if len(self.history) < 2:
            return None
        self.history.pop()  # current
        return self.history[-1]  # new current stays at the tail

    # -- feedback ---------------------------------------------------------

    def track_started(self, track: TrackInfo) -> None:
        if not self._started:
            return
        self._api.rotor_feedback(
            self.station,
            "trackStarted",
            track_id=track.id,
            batch_id=self.batch_id,
            from_=self.from_field,
        )

    def track_finished(self, track: TrackInfo, played_seconds: float) -> None:
        if not self._started:
            return
        self._api.rotor_feedback(
            self.station,
            "trackFinished",
            track_id=track.id,
            batch_id=self.batch_id,
            total_played_seconds=round(played_seconds, 2),
            from_=self.from_field,
        )

    def track_skipped(self, track: TrackInfo, played_seconds: float) -> None:
        if not self._started:
            return
        self._api.rotor_feedback(
            self.station,
            "skip",
            track_id=track.id,
            batch_id=self.batch_id,
            total_played_seconds=round(played_seconds, 2),
            from_=self.from_field,
        )

    # -- tuning -----------------------------------------------------------

    def set_settings(self, mood_energy: str, diversity: str, language: str, restart: bool = True) -> None:
        def _ok(_result: object) -> None:
            if restart:
                self.start()

        self._api.rotor_settings(
            self.station, mood_energy, diversity, language, _ok,
            lambda e: self.error.emit(e),
        )
