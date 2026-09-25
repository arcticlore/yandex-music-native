"""Tests for core.playback_controller: queue modes, rotor feedback, covers.

Run: python tests/test_playback_controller.py

The real ``YandexService`` runs against a fake client, so the station cursor,
feedback order and batch bookkeeping are checked end to end; the libmpv engine
is replaced by a fake that mimics its signals and position reporting.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from yandex_music import (  # noqa: E402
    Album,
    Artist,
    Id,
    Sequence,
    StationTracksResult,
    Track,
)

from core.playback_controller import (  # noqa: E402
    PlaybackController,
    PlaybackState,
    QueueMode,
    TrackMetadata,
    cover_cache_dir,
    cover_file_name,
    link_summary,
)
from core.yandex_service import (  # noqa: E402
    FEEDBACK_RADIO_STARTED,
    FEEDBACK_SKIP,
    FEEDBACK_TRACK_PLAYED,
    FEEDBACK_TRACK_STARTED,
    QUALITY_LOSSLESS,
    StreamLink,
    WaveTrack,
    YandexService,
)

PASSED: list[str] = []
LIVE_RIGS: list["Rig"] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"ok: {name}")


def wait_for(app: QApplication, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def settle(app: QApplication, service: YandexService, timeout: float = 5.0) -> bool:
    """Process events until the service queue stays empty for a while."""
    deadline = time.monotonic() + timeout
    quiet = 0
    while time.monotonic() < deadline:
        app.processEvents()
        if service.is_busy:
            quiet = 0
        else:
            quiet += 1
            if quiet >= 6:
                app.processEvents()
                return True
        time.sleep(0.005)
    return False


# -- fake engine ------------------------------------------------------------


class FakeEngine(QObject):
    """Mimics the libmpv engine surface used by the controller."""

    spectrum_ready = Signal(object)
    waveform_ready = Signal(object)
    bands_changed = Signal(int)
    state_changed = Signal(str)
    duration_changed = Signal(float)
    playback_started = Signal(str)
    finished = Signal()
    error = Signal(str)
    pcm_state_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url: str | None = None
        self._state = "stopped"
        self._volume = 80
        self._position_ms = 0
        self._duration_ms = 0
        self.media_duration_ms = 180000
        self.plays: list[tuple[str, int]] = []
        self.seeks: list[int] = []
        self.stopped = 0
        self.shutdown_calls = 0

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def play(self, url: str | None = None, start_ms: int = 0) -> bool:
        if url is None:
            self.error.emit("nothing to play")
            return False
        self._url = url
        self._position_ms = int(start_ms)
        self._duration_ms = self.media_duration_ms
        self.plays.append((url, int(start_ms)))
        self._set_state("playing")
        self.playback_started.emit(url)
        return True

    def pause(self) -> bool:
        if self._state != "playing":
            return False
        self._set_state("paused")
        return True

    def toggle_play(self) -> str:
        if self._state == "playing":
            self.pause()
            return "paused"
        self._set_state("playing")
        return "playing"

    def stop(self) -> None:
        self.stopped += 1
        self._url = None
        self._position_ms = 0
        self._set_state("stopped")

    def seek(self, position_ms: int) -> bool:
        if self._url is None:
            return False
        self.seeks.append(int(position_ms))
        self._position_ms = int(position_ms)
        return True

    def set_volume(self, percent: int) -> int:
        self._volume = max(0, min(100, int(percent)))
        return self._volume

    def get_position_ms(self) -> int:
        return self._position_ms if self._url is not None else 0

    def get_duration_ms(self) -> int:
        return self._duration_ms if self._url is not None else 0

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
    def is_playing(self) -> bool:
        return self._state == "playing"

    @property
    def is_paused(self) -> bool:
        return self._state == "paused"

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._url = None
        self._set_state("stopped")

    def advance_to_end(self) -> None:
        """Simulate mpv reaching the end of the file."""
        self._position_ms = self._duration_ms or self.media_duration_ms
        self.duration_changed.emit((self._duration_ms or self.media_duration_ms) / 1000.0)
        self._set_state("stopped")
        self.finished.emit()

    def advance_to(self, position_ms: int) -> None:
        self._position_ms = int(position_ms)
        self.duration_changed.emit((self._duration_ms or self.media_duration_ms) / 1000.0)


# -- fake client ------------------------------------------------------------


@dataclass
class FakeVariant:
    codec: str
    bitrate_in_kbps: int
    preview: bool = False
    url: str = "https://storage.mds.yandex.net/direct/stream"

    def get_direct_link(self, **kwargs: object) -> str:
        return f"{self.url}-{self.codec}-{self.bitrate_in_kbps}"


def make_track(
    track_id: int = 42,
    album_id: int = 7,
    title: str = "Song",
    duration_ms: int = 180000,
    cover_uri: str | None = None,
    artist_name: str = "Artist",
    available: bool = True,
) -> Track:
    artist = Artist(name=artist_name)
    albums = [Album(id=album_id, title=f"Album {album_id}", cover_uri=cover_uri)] if album_id else []
    return Track(
        id=track_id,
        title=title,
        available=available,
        artists=[artist],
        albums=albums,
        duration_ms=duration_ms,
        cover_uri=cover_uri,
        explicit=False,
    )


def make_batch(tracks: list[Track], batch_id: str = "batch-1") -> StationTracksResult:
    sequence = [Sequence(type="track", track=track, liked=False) for track in tracks]
    return StationTracksResult(
        id=Id(type="user", tag="onyourwave"),
        sequence=sequence,
        batch_id=batch_id,
        pumpkin=False,
    )


class FakeClient:
    """Records API calls and serves canned station responses."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.calls: list[str] = []
        self.station_calls: list[dict] = []
        self.feedback_calls: list[dict] = []
        self.settings_calls: list[tuple] = []
        self.like_calls: list[tuple] = []
        self.dislike_calls: list[tuple] = []
        self.download_calls: list[tuple] = []
        self.batches: list[StationTracksResult] = []
        self.variants: list = []
        self.station_error: Exception | None = None
        self.download_error: Exception | None = None
        self.search_error: Exception | None = None
        self.search_calls: list[tuple[str, int]] = []
        self.liked_calls: list[tuple[str, object]] = []
        self.search_result: object = SimpleNamespace(tracks=(), albums=(), artists=(), playlists=())
        self.liked_tracks_result: object = SimpleNamespace(tracks=())
        self.liked_albums_result: object = SimpleNamespace(albums=())
        self.uid = 777

    def init(self) -> None:
        self.calls.append("init")
        account = SimpleNamespace(
            login="wave@yandex.ru",
            display_name="Волна",
            full_name="Волна",
            first_name="Во",
            second_name="Лна",
            uid=self.uid,
        )
        account.to_dict = lambda: {"login": account.login, "uid": account.uid}  # type: ignore[attr-defined]
        self.me = SimpleNamespace(account=account, plus=SimpleNamespace(has_plus=True))

    def rotor_station_tracks(self, station, settings2=True, queue=None, **kwargs):
        self.calls.append("rotor_station_tracks")
        self.station_calls.append({"station": station, "queue": queue, "settings2": settings2})
        if self.station_error is not None:
            raise self.station_error
        if not self.batches:
            return make_batch([make_track(900 + len(self.station_calls))])
        return self.batches.pop(0)

    def rotor_station_feedback(
        self,
        station,
        type_,
        timestamp=None,
        from_=None,
        batch_id=None,
        total_played_seconds=None,
        track_id=None,
        **kwargs,
    ):
        self.calls.append("rotor_station_feedback")
        self.feedback_calls.append(
            {
                "type": type_,
                "batch_id": batch_id,
                "total_played_seconds": total_played_seconds,
                "track_id": track_id,
            }
        )
        return True

    def rotor_station_settings2(self, station, mood_energy, diversity, language="any", **kwargs):
        self.calls.append("rotor_station_settings2")
        self.settings_calls.append((station, mood_energy, diversity, language))
        return SimpleNamespace(diversity=diversity, mood_energy=mood_energy, language=language)

    def users_likes_tracks_add(self, track_ids, user_id=None, **kwargs):
        self.like_calls.append(("add", tuple(track_ids)))
        return True

    def users_likes_tracks_remove(self, track_ids, user_id=None, **kwargs):
        self.like_calls.append(("remove", tuple(track_ids)))
        return True

    def users_dislikes_tracks_add(self, track_ids, user_id=None, **kwargs):
        self.dislike_calls.append(("add", tuple(track_ids)))
        return True

    def users_dislikes_tracks_remove(self, track_ids, user_id=None, **kwargs):
        self.dislike_calls.append(("remove", tuple(track_ids)))
        return True

    def tracks_download_info(self, track_id, get_direct_links=False, **kwargs):
        self.calls.append("tracks_download_info")
        self.download_calls.append((track_id, get_direct_links))
        if self.download_error is not None:
            raise self.download_error
        return list(self.variants)

    def search(self, text, page=0, type_="all", **kwargs):
        self.calls.append("search")
        self.search_calls.append((text, page))
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    def users_likes_tracks(self, user_id=None, **kwargs):
        self.calls.append("users_likes_tracks")
        self.liked_calls.append(("tracks", user_id))
        return self.liked_tracks_result

    def users_likes_albums(self, user_id=None, **kwargs):
        self.calls.append("users_likes_albums")
        self.liked_calls.append(("albums", user_id))
        return self.liked_albums_result


class Rig:
    """Controller under test with a recording engine and a fake client."""

    def __init__(self, app: QApplication, cover_dir: Path | None = None) -> None:
        self.app = app
        self.client = FakeClient("token-1")
        self.client.variants = [
            FakeVariant("mp3", 320),
            FakeVariant("flac", 1411),
        ]
        self.cover_dir = cover_dir or Path(tempfile.mkdtemp(prefix="yml-covers-"))
        self.cover_downloads: list[tuple[str, Path]] = []
        self.events: list[tuple] = []
        self.service = YandexService("token-1", self._factory)
        self.engine = FakeEngine()
        self.controller = PlaybackController(
            self.service,
            self.engine,
            cover_dir=self.cover_dir,
            cover_downloader=self._download_cover,
            position_interval_ms=50,
        )
        self._connect_signals()
        LIVE_RIGS.append(self)

    def _factory(self, token: str) -> FakeClient:
        return self.client

    def _download_cover(self, url: str, target: Path) -> None:
        self.cover_downloads.append((url, target))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG\r\n\x1a\nfake-cover")

    def _connect_signals(self) -> None:
        controller = self.controller
        controller.track_changed.connect(lambda meta: self.events.append(("track", meta.id)))
        controller.state_changed.connect(lambda state: self.events.append(("state", state)))
        controller.position_changed.connect(lambda pos, total: self.events.append(("position", pos, total)))
        controller.cover_ready.connect(lambda track_id, path: self.events.append(("cover", track_id, path)))
        controller.stream_changed.connect(
            lambda track_id, text: self.events.append(("stream", track_id, text))
        )
        controller.queue_changed.connect(lambda value: self.events.append(("queue", value)))
        controller.buffering_changed.connect(lambda value: self.events.append(("buffering", value)))
        controller.wave_settings_changed.connect(lambda payload: self.events.append(("settings", payload)))
        controller.like_status_changed.connect(
            lambda track_id, liked: self.events.append(("like", track_id, liked))
        )
        controller.fft_data_ready.connect(lambda block: self.events.append(("fft", block)))
        controller.waveform_data_ready.connect(lambda block: self.events.append(("wave", block)))
        controller.playback_error.connect(lambda text: self.events.append(("error", text)))
        controller.wave_error.connect(lambda text: self.events.append(("wave_error", text)))
        controller.mark_error.connect(lambda key, text: self.events.append(("mark_error", key, text)))

    def settle(self, timeout: float = 5.0) -> bool:
        return settle(self.app, self.service, timeout)

    def advance(self, expected: str | None = None, timeout: float = 5.0) -> bool:
        """Finish the current media and wait for ``expected`` to start playing."""
        self.engine.advance_to_end()
        if expected is None:
            return self.settle(timeout)
        ok = wait_for(
            self.app,
            lambda: (
                self.controller.current is not None
                and self.controller.current.id == expected
                and self.controller.state == PlaybackState.PLAYING
            ),
            timeout,
        )
        self.settle()
        return ok

    def wait_cover(self, timeout: float = 5.0) -> bool:
        ok = wait_for(self.app, lambda: bool(self.events_of("cover")), timeout)
        self.settle()
        return ok

    def events_of(self, name: str) -> list[tuple]:
        return [item for item in self.events if item and item[0] == name]

    def states(self) -> list[str]:
        return [item[1] for item in self.events_of("state")]

    def track_ids(self) -> list[str]:
        return [item[1] for item in self.events_of("track")]

    def feedback(self, event: str) -> list[dict]:
        return [item for item in self.client.feedback_calls if item["type"] == event]

    def close(self) -> None:
        self.controller.shutdown()
        self.service.shutdown()
        if self in LIVE_RIGS:
            LIVE_RIGS.remove(self)

    def login(self) -> bool:
        self.service.set_token("token-1")
        ok = self.settle()
        return ok and self.service.is_authenticated


@pytest.fixture(autouse=True)
def _close_rigs_after_test(app: QApplication) -> Iterator[None]:
    """Stop every controller this module started: no stray QTimer may outlive it.

    A running position timer whose owner is garbage-collected leaves a queued
    QTimerEvent behind, and the next ``processEvents()`` of another module then
    dereferences the freed QObject.
    """
    yield
    for rig in list(LIVE_RIGS):
        rig.close()
    LIVE_RIGS.clear()
    app.processEvents()


# -- helpers ----------------------------------------------------------------


def wave_of(track_id: int, title: str = "Song", duration_ms: int = 180000) -> WaveTrack:
    model = WaveTrack.from_track(make_track(track_id, title=title, duration_ms=duration_ms))
    assert model is not None
    return model


def stream_for(track_id: str) -> StreamLink:
    return StreamLink(
        track_id=track_id, url=f"https://direct/{track_id}", codec="flac", bitrate=1411, lossless=True
    )


# -- pure helpers -----------------------------------------------------------


def test_helpers() -> None:
    check("cover cache dir name", cover_cache_dir().name == "covers")
    check(
        "cover file name stable", cover_file_name("https://a/b/c.jpg") == cover_file_name("https://a/b/c.jpg")
    )
    check("cover file name suffix", cover_file_name("https://a/b/c").endswith(".jpg"))
    check("cover file name webp", cover_file_name("https://a/b/c.webp").endswith(".webp"))
    check("cover file name digest", len(cover_file_name("https://a/b/c.jpg")) == 24)

    link = StreamLink(track_id="1", url="u", codec="flac", bitrate=1411, lossless=True)
    check("link summary lossless", link_summary(link) == "flac 1411 kbps", link_summary(link))
    preview = StreamLink(track_id="1", url="u", codec="mp3", bitrate=128, preview=True)
    check("link summary preview", link_summary(preview) == "mp3 128 kbps preview", link_summary(preview))
    check("link summary bare", link_summary(StreamLink(track_id="1", url="u")) == "mp3")

    meta = TrackMetadata(id="42:7", title="Song", artists=("A", "B"), duration_ms=1000)
    check("metadata artists", meta.artists_name == "A, B")
    check("metadata duration", meta.duration == 1.0)
    payload = meta.to_dict()
    check("metadata to_dict", payload["id"] == "42:7" and payload["artists"] == ["A", "B"])
    check("metadata defaults", payload["cover_path"] is None and payload["liked"] is False)
    check("queue mode values", QueueMode.MANUAL == "manual" and QueueMode.RADIO == "radio")
    check("playback state values", PlaybackState.BUFFERING == "buffering")


# -- manual queue -----------------------------------------------------------


def test_manual_playlist(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("playlist login", rig.login())
        tracks = [make_track(11, title="One"), make_track(22, title="Two"), make_track(33, title="Three")]
        rig.client.variants = [FakeVariant("mp3", 320)]
        check("play playlist", rig.controller.play_playlist(tracks, start_index=1))
        check("manual playlist settles", rig.settle())
        check("playlist mode", rig.controller.mode == QueueMode.MANUAL)
        check("playlist started index", rig.controller.position == 1)
        check("playlist first track", rig.track_ids() == ["22:7"], rig.track_ids())
        check(
            "playlist engine url", rig.engine.plays and rig.engine.plays[-1][0].startswith("https://storage")
        )
        check(
            "playlist metadata title",
            rig.controller.current is not None and rig.controller.current.title == "Two",
        )
        check(
            "playlist metadata artist",
            rig.controller.current is not None and rig.controller.current.artists == ("Artist",),
        )
        check(
            "playlist metadata album",
            rig.controller.current is not None and rig.controller.current.album == "Album 7",
        )
        check(
            "playlist metadata index",
            rig.controller.current is not None and rig.controller.current.index == 1,
        )
        check(
            "playlist metadata duration",
            rig.controller.current is not None and rig.controller.current.duration_ms == 180000,
        )
        check("playlist state playing", rig.controller.state == PlaybackState.PLAYING)
        check(
            "playlist buffering before playing", "buffering" in rig.states() and rig.states()[-1] == "playing"
        )
        check("playlist remaining", rig.controller.remaining == 1)
        check("playlist queue signal", ("queue", 1) in rig.events, rig.events_of("queue"))
        check("no feedback in manual", rig.client.feedback_calls == [], rig.client.feedback_calls)

        check("playlist next", rig.controller.next())
        check("playlist next settles", rig.settle())
        check("playlist next track", rig.track_ids() == ["22:7", "33:7"], rig.track_ids())
        check("playlist next index", rig.controller.position == 2)
        check("playlist no feedback on next", rig.client.feedback_calls == [])

        check("playlist next exhausted", rig.controller.next() is False)
        check("playlist exhausted state", rig.controller.state == PlaybackState.STOPPED)
        check("playlist exhausted cleared", rig.controller.current is None)
        check(
            "playlist position zero", any(item[1] == 0 and item[2] == 0 for item in rig.events_of("position"))
        )
        check("playlist queue drained", ("queue", 0) in rig.events)

        check(
            "playlist restart via prev", rig.controller.play_playlist(tracks, start_index=0) and rig.settle()
        )
        check("playlist prev clamps", rig.controller.prev() is True)
        check("playlist prev restarts", rig.engine.seeks == [0], rig.engine.seeks)
        check(
            "playlist prev keeps track",
            rig.controller.current is not None and rig.controller.current.id == "11:7",
        )
        check("playlist prev at start", rig.controller.prev() is True)
        check("playlist prev at start seeks", rig.engine.seeks[-1] == 0, rig.engine.seeks)
        check("playlist forward again", rig.controller.next() and rig.settle())
        check(
            "playlist forward track",
            rig.controller.current is not None and rig.controller.current.id == "22:7",
        )
        check("playlist prev back", rig.controller.prev() is True)
        check(
            "playlist prev back track",
            rig.controller.current is not None and rig.controller.current.id == "11:7",
        )
    finally:
        rig.close()


def test_single_track_and_lookup(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("single login", rig.login())
        track = make_track(55, title="Solo")
        check("play track model", rig.controller.play_track(track) and rig.settle())
        check("play track id", rig.controller.current is not None and rig.controller.current.id == "55:7")
        check("play track mode", rig.controller.mode == QueueMode.MANUAL)
        check("play track unknown", rig.controller.play_track("999:1") is False)
        check("play track unknown error", ("error", "Трек не найден в очереди") in rig.events)
        check("play track by known id", rig.controller.play_track("55:7") and rig.settle())
        check(
            "play track same id", rig.controller.current is not None and rig.controller.current.id == "55:7"
        )
        check("play empty playlist", rig.controller.play_playlist([]) is False)
        check("play empty playlist error", ("error", "Нечего играть: очередь пуста") in rig.events)
    finally:
        rig.close()


def test_transport(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("transport login", rig.login())
        rig.controller.play_playlist([make_track(70), make_track(71)], start_index=0)
        check("transport settles", rig.settle())
        check("volume applied", rig.controller.set_volume(150) == 100)
        check("volume clamp low", rig.controller.set_volume(-5) == 0)
        check("volume property", rig.controller.volume == 0)
        rig.controller.set_volume(55)
        check("volume restored", rig.engine.volume == 55)

        rig.engine.advance_to(30000)
        check("seek forwards", rig.controller.seek(90000))
        check("seek passed to engine", rig.engine.seeks[-1] == 90000)
        check("seek emits position", ("position", 90000, 180000) in rig.events)

        check("toggle pauses", rig.controller.toggle_play() == "paused")
        check("toggle state paused", rig.controller.state == PlaybackState.PAUSED)
        check("toggle resumes", rig.controller.toggle_play() == "playing")
        check("toggle state playing", rig.controller.state == PlaybackState.PLAYING)
        check("stop reports", rig.controller.stop() is None)
        check("stop state", rig.controller.state == PlaybackState.STOPPED)
        check("stop clears current", rig.controller.current is None)
        check("seek without media", rig.controller.seek(1000) is False)
        check("toggle after stop", rig.controller.toggle_play() in ("playing", "buffering"))
        check("toggle reload settles", rig.settle())
        check("toggle reloads track", rig.controller.current is not None)
        check("toggle reload state", rig.controller.state == PlaybackState.PLAYING)
        check("toggle reload position", rig.controller.position == 0)
    finally:
        rig.close()


def test_fft_passthrough(app: QApplication) -> None:
    import numpy as np

    rig = Rig(app)
    try:
        block = np.zeros(8, dtype=np.float32)
        wave = np.ones(8, dtype=np.float32)
        rig.engine.spectrum_ready.emit(block)
        rig.engine.waveform_ready.emit(wave)
        check("fft passthrough", rig.events_of("fft") and rig.events_of("fft")[0][1] is block)
        check("waveform passthrough", rig.events_of("wave") and rig.events_of("wave")[0][1] is wave)
    finally:
        rig.close()


# -- radio ------------------------------------------------------------------


def test_radio_feedback_and_cursor(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("radio login", rig.login())
        rig.client.batches = [
            make_batch([make_track(101, title="A"), make_track(102, title="B")], "batch-a"),
            make_batch([make_track(103, title="C")], "batch-b"),
        ]
        check("start wave", rig.controller.start_wave(mood=1, activity=0, language="ru"))
        check("wave settles", rig.settle())
        check("wave mode", rig.controller.mode == QueueMode.RADIO)
        check(
            "wave settings applied",
            rig.client.settings_calls[0][1:] == ("calm", "default", "russian"),
            rig.client.settings_calls,
        )
        check(
            "wave settings signal",
            (
                "settings",
                {
                    "mood": "fun",
                    "activity": "rest",
                    "mood_energy": "calm",
                    "diversity": "default",
                    "language": "russian",
                },
            )
            in rig.events,
            [item for item in rig.events if item[0] == "settings"],
        )
        check("wave first station call", rig.client.station_calls[0]["queue"] is None)
        check("wave radio feedback", len(rig.feedback(FEEDBACK_RADIO_STARTED)) == 1)
        check(
            "wave started feedback",
            [item["track_id"] for item in rig.feedback(FEEDBACK_TRACK_STARTED)] == ["101:7"],
            rig.client.feedback_calls,
        )
        check("wave started batch", rig.feedback(FEEDBACK_TRACK_STARTED)[0]["batch_id"] == "batch-a")
        check(
            "wave playing first", rig.controller.current is not None and rig.controller.current.id == "101:7"
        )
        check(
            "wave source is station",
            rig.controller.current is not None and rig.controller.current.source == "user:onyourwave",
        )
        check(
            "wave stream resolved",
            rig.engine.plays and rig.engine.plays[-1][0].endswith("flac-1411"),
            rig.engine.plays,
        )
        check("wave lossless flag", rig.controller.current is not None and rig.controller.current.lossless)
        check("wave quality summary", ("stream", "101:7", "flac 1411 kbps") in rig.events)
        check(
            "wave no duplicate start",
            len(rig.feedback(FEEDBACK_TRACK_STARTED)) == 1,
            rig.client.feedback_calls,
        )

        check("track finished advances", rig.advance("102:7"))
        finished = rig.feedback(FEEDBACK_TRACK_PLAYED)
        check("finished feedback once", len(finished) == 1, rig.client.feedback_calls)
        check("finished feedback track", finished and finished[0]["track_id"] == "101:7")
        check(
            "finished feedback seconds", finished and finished[0]["total_played_seconds"] == 180.0, finished
        )
        check("finished batch id", finished and finished[0]["batch_id"] == "batch-a")
        check("next track started", len(rig.feedback(FEEDBACK_TRACK_STARTED)) == 2, rig.client.feedback_calls)
        check("next track id", rig.controller.current is not None and rig.controller.current.id == "102:7")
        check(
            "cursor points to finished track",
            rig.client.station_calls[-1]["queue"] == "101:7",
            rig.client.station_calls,
        )
        check("cursor batch id carried", rig.client.station_calls[-1]["settings2"] is True)

        rig.engine.advance_to(5000)
        check("radio next", rig.controller.next())
        check("radio next settles", rig.settle())
        skipped = rig.feedback(FEEDBACK_SKIP)
        check("skip feedback once", len(skipped) == 1, rig.client.feedback_calls)
        check("skip feedback track", skipped and skipped[0]["track_id"] == "102:7")
        check("skip feedback seconds", skipped and skipped[0]["total_played_seconds"] == 5.0, skipped)
        check(
            "skip advanced track", rig.controller.current is not None and rig.controller.current.id == "103:7"
        )
        check("skip cursor", rig.client.station_calls[-1]["queue"] == "102:7", rig.client.station_calls)
        check("skip no finished event", len(rig.feedback(FEEDBACK_TRACK_PLAYED)) == 1)
        check(
            "no repeated feedback per track",
            len(rig.feedback(FEEDBACK_TRACK_STARTED)) == 3,
            rig.client.feedback_calls,
        )
    finally:
        rig.close()


def test_radio_prefetch_and_resume(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("prefetch login", rig.login())
        rig.client.batches = [
            make_batch([make_track(201), make_track(202), make_track(203), make_track(204)], "b1"),
            make_batch([make_track(205), make_track(206), make_track(207), make_track(208)], "b2"),
            make_batch([make_track(209), make_track(210), make_track(211), make_track(212)], "b3"),
        ]
        rig.controller.start_wave()
        check("prefetch settle", rig.settle())
        check("prefetch top up", len(rig.client.station_calls) == 2, rig.client.station_calls)
        check("prefetch first cursor empty", rig.client.station_calls[1]["queue"] is None)
        check(
            "prefetch first track",
            rig.controller.current is not None and rig.controller.current.id == "201:7",
        )
        check("prefetch remaining", rig.controller.remaining == 7, rig.controller.remaining)

        check("prefetch advances", rig.advance("202:7"))
        check(
            "prefetch second track",
            rig.controller.current is not None and rig.controller.current.id == "202:7",
        )
        check("prefetch no extra call", len(rig.client.station_calls) == 2, rig.client.station_calls)

        played = ["201:7", "202:7"]
        for index in range(3, 9):
            rig.advance(f"{200 + index}:7")
            played.append(rig.controller.current.id if rig.controller.current else "")
        check("prefetch sequence", played[:4] == ["201:7", "202:7", "203:7", "204:7"], played)
        check("prefetch no repeats", len(set(played)) == len(played) and all(played), played)
        check("prefetch cursor advanced", any(item["queue"] for item in rig.client.station_calls[1:]))
        check("prefetch bounded calls", len(rig.client.station_calls) <= 4, rig.client.station_calls)
    finally:
        rig.close()


def test_radio_queue_exhaustion(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("exhaust login", rig.login())
        rig.client.batches = [
            make_batch([make_track(401)], "b1"),
            make_batch([make_track(402), make_track(403)], "b2"),
        ]
        rig.controller.start_wave()
        check("exhaust settle", rig.settle())
        check(
            "exhaust first track", rig.controller.current is not None and rig.controller.current.id == "401:7"
        )
        rig.client.station_error = RuntimeError("no more batches")

        check("exhaust second track", rig.advance("402:7"))
        check("exhaust error surfaced", any(item[0] == "wave_error" for item in rig.events))

        check("exhaust third track", rig.advance("403:7"))
        check("exhaust drained settles", rig.advance())
        check("exhaust drained state", rig.controller.state == PlaybackState.STOPPED)
        check("exhaust drained current", rig.controller.current is None)
        check("exhaust drained remaining", rig.controller.remaining == 0)

        rig.client.station_error = None
        rig.client.batches = [make_batch([make_track(404)], "b3")]
        rig.service.maybe_prefetch(force=True)
        check("exhaust resume settles", rig.settle())
        check(
            "exhaust resumed track",
            rig.controller.current is not None and rig.controller.current.id == "404:7",
        )
        check("exhaust resumed playing", rig.controller.state == PlaybackState.PLAYING)
        check(
            "exhaust resume cursor",
            rig.client.station_calls[-1]["queue"] == "403:7",
            rig.client.station_calls,
        )
    finally:
        rig.close()


def test_radio_prev_and_restart(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("prev login", rig.login())
        rig.client.batches = [make_batch([make_track(301), make_track(302)], "b1")]
        rig.controller.start_wave()
        check("prev settle", rig.settle())
        check("prev next settles", rig.advance("302:7"))
        check(
            "prev moved forward", rig.controller.current is not None and rig.controller.current.id == "302:7"
        )
        started_before = len(rig.feedback(FEEDBACK_TRACK_STARTED))
        check("prev returns track", rig.controller.prev())
        check("prev back settles", rig.settle())
        check("prev back", rig.controller.current is not None and rig.controller.current.id == "301:7")
        check(
            "prev re-reports start",
            len(rig.feedback(FEEDBACK_TRACK_STARTED)) == started_before + 1,
            rig.client.feedback_calls,
        )
        check("prev no extra finished", len(rig.feedback(FEEDBACK_TRACK_PLAYED)) == 1)
        check("prev plays again", rig.settle() and rig.engine.plays[-1][0].endswith("flac-1411"))
    finally:
        rig.close()


def test_radio_errors(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("errors login", rig.login())
        rig.client.download_error = RuntimeError("no variants")
        rig.client.batches = [make_batch([make_track(401)], "b1")]
        check("error wave start", rig.controller.start_wave())
        check("error settles", rig.settle())
        check("stream error raised", any(item[0] == "error" for item in rig.events), rig.events[-4:])
        check("stream error state", rig.controller.state == PlaybackState.STOPPED)
        check("stream error not buffering", not rig.controller.is_buffering)
        check("stream error no play", rig.engine.plays == [])

        rig.client.download_error = None
        rig.client.batches = [make_batch([make_track(402)], "b2")]
        check("error retry", rig.controller.start_wave())
        check("error retry settles", rig.settle())
        check(
            "error retry plays", rig.controller.current is not None and rig.controller.current.id == "402:7"
        )

        rig.client.station_error = RuntimeError("station down")
        check("error wave restart", rig.controller.start_wave())
        check("error wave settles", rig.settle())
        check("wave error raised", any(item[0] == "wave_error" for item in rig.events), rig.events[-3:])
    finally:
        rig.close()


def test_manual_after_radio(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("switch login", rig.login())
        rig.client.batches = [make_batch([make_track(501), make_track(502)], "b1")]
        rig.controller.start_wave()
        check("switch settles wave", rig.settle())
        check("switch mode radio", rig.controller.mode == QueueMode.RADIO)
        check("switch to playlist", rig.controller.play_playlist([make_track(601), make_track(602)]))
        check("switch settles manual", rig.settle())
        check("switch mode manual", rig.controller.mode == QueueMode.MANUAL)
        check("switch track", rig.controller.current is not None and rig.controller.current.id == "601:7")
        check("switch reported skip", len(rig.feedback(FEEDBACK_SKIP)) == 1, rig.client.feedback_calls)
        check("switch skip seconds zero", rig.feedback(FEEDBACK_SKIP)[0]["total_played_seconds"] == 0.0)

        rig.client.batches = [make_batch([make_track(701), make_track(702)], "b2")]
        check("switch back to wave", rig.controller.start_wave())
        check("switch back settles", rig.settle())
        check(
            "switch no manual feedback",
            all(item["track_id"] != "601:7" for item in rig.client.feedback_calls),
            rig.client.feedback_calls,
        )
        check(
            "switch back radio", rig.controller.mode == QueueMode.RADIO and rig.controller.current is not None
        )
    finally:
        rig.close()


def test_marks_and_settings(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("marks login", rig.login())
        rig.client.batches = [make_batch([make_track(801)], "b1")]
        rig.controller.start_wave()
        check("marks settle", rig.settle())
        check("mark like", rig.controller.like())
        check("mark like settles", rig.settle())
        check("mark like sent", rig.client.like_calls == [("add", ("801:7",))], rig.client.like_calls)
        check("mark like signal", ("like", "801:7", True) in rig.events)
        check("mark liked metadata", rig.controller.current is not None and rig.controller.current.liked)
        check("mark remove like", rig.controller.remove_like())
        check("mark remove settles", rig.settle())
        check("mark remove sent", ("remove", ("801:7",)) in rig.client.like_calls)
        check("mark dislike", rig.controller.dislike())
        check("mark dislike settles", rig.settle())
        check("mark dislike sent", rig.client.dislike_calls == [("add", ("801:7",))])
        check("mark remove dislike", rig.controller.remove_dislike())
        check("mark remove dislike settles", rig.settle())
        check("mark remove dislike sent", ("remove", ("801:7",)) in rig.client.dislike_calls)
        check("mark no error", not rig.events_of("mark_error"), rig.events_of("mark_error"))

        check("apply settings", rig.controller.apply_wave_settings(mood_energy="calm", diversity="discover"))
        check("apply settings settles", rig.settle())
        check(
            "apply settings call",
            rig.client.settings_calls[-1][1:] == ("calm", "discover", "any"),
            rig.client.settings_calls,
        )
        check(
            "apply settings signal",
            (
                "settings",
                {
                    "mood": "calm",
                    "activity": "all",
                    "mood_energy": "calm",
                    "diversity": "discover",
                    "language": "all",
                },
            )
            in rig.events,
            [item for item in rig.events if item[0] == "settings"],
        )
        check("apply settings property", rig.controller.settings["mood_energy"] == "calm")
        check("bad mood energy", rig.controller.apply_wave_settings(mood_energy="wrong") is False)
        check("bad mood energy error", any(item[0] == "wave_error" for item in rig.events))
    finally:
        rig.close()


def test_session_and_shutdown(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("session login", rig.login())
        rig.client.batches = [make_batch([make_track(901)], "b1")]
        rig.controller.start_wave()
        check("session settles", rig.settle())
        rig.service.clear_session()
        check("session cleared stops", rig.controller.state == PlaybackState.STOPPED)
        check("session cleared current", rig.controller.current is None)
        check("session cleared no feedback", rig.feedback(FEEDBACK_SKIP) == [], rig.client.feedback_calls)

        rig.service.set_token("token-1")
        rig.settle()
        rig.client.batches = [make_batch([make_track(902)], "b2")]
        rig.controller.start_wave()
        check("shutdown settle", rig.settle())
        rig.controller.shutdown()
        check("shutdown stops engine", rig.controller.state == PlaybackState.STOPPED)
        check("shutdown engine stopped", rig.engine.url is None)
        plays_before = len(rig.engine.plays)
        rig.controller.next()
        rig.settle()
        check("shutdown detaches", len(rig.engine.plays) == plays_before, rig.engine.plays)
        check("shutdown stays stopped", rig.controller.state == PlaybackState.STOPPED)
        check("shutdown idempotent", rig.controller.shutdown() is None)
        rig.service.shutdown()
    finally:
        rig.controller.deleteLater()


def test_cover_cache(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("cover login", rig.login())
        cover_uri = "avatars.yandex.net/get-music/cover.jpg"
        track = make_track(1001, title="Cover", cover_uri=cover_uri)
        rig.controller.play_track(track)
        check("cover settles", rig.settle())
        check(
            "cover url resolved",
            rig.controller.current is not None and rig.controller.current.cover_url is not None,
        )
        check("cover downloaded", rig.wait_cover())
        path = rig.controller.cover_path("1001:7")
        check("cover path set", path is not None)
        check("cover file exists", path is not None and Path(path).exists())
        check("cover in cache dir", path is not None and Path(path).parent == rig.cover_dir)
        check("cover signal", any(item[0] == "cover" and item[1] == "1001:7" for item in rig.events))
        check(
            "cover metadata updated",
            rig.controller.current is not None and rig.controller.current.cover_path == path,
        )
        check(
            "cover file name used",
            path is not None
            and Path(path).name
            == cover_file_name(rig.controller.current.cover_url if rig.controller.current else ""),
        )

        downloads_before = len(rig.cover_downloads)
        rig.controller.play_track(track)
        check("cover reuse settles", rig.settle())
        check("cover not downloaded twice", len(rig.cover_downloads) == downloads_before, rig.cover_downloads)
        check(
            "cover path prefilled",
            rig.controller.current is not None and rig.controller.current.cover_path == path,
        )

        def failing(url: str, target: Path) -> None:
            raise RuntimeError("download failed")

        other = Rig(app)
        try:
            other.controller = PlaybackController(
                other.service,
                other.engine,
                cover_dir=other.cover_dir,
                cover_downloader=failing,
            )
            other.controller.play_track(make_track(1002, cover_uri=cover_uri))
            check("cover failure settles", other.settle())
            check("cover failure handled", wait_for(app, lambda: not other.controller._cover_inflight, 3.0))
            check("cover failure no path", other.controller.cover_path("1002:7") is None)
        finally:
            other.controller.shutdown()
            other.service.shutdown()
    finally:
        rig.close()


def test_stale_stream_and_link_cache(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("stale login", rig.login())
        rig.controller.play_playlist([make_track(1101), make_track(1102)], start_index=0)
        check("stale settles", rig.settle())
        check("stale first play", len(rig.engine.plays) == 1, rig.engine.plays)
        first_url = rig.engine.plays[-1][0]
        rig.controller.next()
        check("stale next settles", rig.settle())
        check("stale second play", len(rig.engine.plays) == 2, rig.engine.plays)
        check("stale reuses same variant", rig.engine.plays[-1][0] == first_url)
        plays_before = len(rig.engine.plays)
        rig.controller._on_stream_ready(stream_for("1101:7"))
        check("stale link ignored", len(rig.engine.plays) == plays_before)
        check(
            "stale link keeps current",
            rig.controller.current is not None and rig.controller.current.id == "1102:7",
        )

        rig.controller.play_playlist([make_track(1101)], start_index=0)
        check("link cache settles", rig.settle())
        downloads_before = len(rig.client.download_calls)
        rig.controller.seek(0)
        rig.controller.stop()
        rig.controller.toggle_play()
        check("link cache settles again", rig.settle())
        check(
            "link cache reused", len(rig.client.download_calls) == downloads_before, rig.client.download_calls
        )
    finally:
        rig.close()


def test_quality_selection(app: QApplication) -> None:
    rig = Rig(app)
    try:
        check("quality login", rig.login())
        rig.controller.set_quality(QUALITY_LOSSLESS, allow_lossless=True)
        check("quality stored", rig.controller.quality == QUALITY_LOSSLESS)
        check("lossless stored", rig.controller.allow_lossless is True)
        rig.controller.play_track(make_track(1201))
        check("quality settles", rig.settle())
        check("lossless selected", rig.engine.plays[-1][0].endswith("flac-1411"), rig.engine.plays)
        check("lossless flag", rig.controller.current is not None and rig.controller.current.lossless)

        rig.client.variants = [FakeVariant("mp3", 192)]
        rig.controller.set_quality("high", allow_lossless=False)
        rig.controller.play_track(make_track(1202))
        check("hq settles", rig.settle())
        check("hq bitrate", rig.engine.plays[-1][0].endswith("mp3-192"), rig.engine.plays)
        check("hq not lossless", rig.controller.current is not None and not rig.controller.current.lossless)
    finally:
        rig.close()


def test_requires_authentication(app: QApplication) -> None:
    rig = Rig(app)
    try:
        rig.service.set_token(None)
        rig.client.batches = [make_batch([make_track(1301)], "b1")]
        check("wave without token", rig.controller.start_wave() is False)
        check("wave error no token", any(item[0] == "wave_error" for item in rig.events), rig.events[-2:])
        check("no play without token", rig.engine.plays == [])
        check("no feedback without token", rig.client.feedback_calls == [])
    finally:
        rig.close()


def main() -> int:
    os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="yml-ctrl-config-")
    app = QApplication.instance() or QApplication(sys.argv)

    test_helpers()
    test_manual_playlist(app)
    test_single_track_and_lookup(app)
    test_transport(app)
    test_fft_passthrough(app)
    test_radio_feedback_and_cursor(app)
    test_radio_prefetch_and_resume(app)
    test_radio_queue_exhaustion(app)
    test_radio_prev_and_restart(app)
    test_radio_errors(app)
    test_manual_after_radio(app)
    test_marks_and_settings(app)
    test_session_and_shutdown(app)
    test_cover_cache(app)
    test_stale_stream_and_link_cache(app)
    test_quality_selection(app)
    test_requires_authentication(app)

    print(f"\nAll {len(PASSED)} playback controller checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
