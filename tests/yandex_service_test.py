"""Tests for the core Yandex API service (session, «Моя волна», streams, covers).

Run: python tests/yandex_service_test.py

The fake client records every call and returns real ``yandex_music`` models, so
attribute names are validated against the installed library.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from yandex_music import (  # noqa: E402
    Album,
    Artist,
    Cover,
    DownloadInfo,
    Id,
    Sequence,
    StationTracksResult,
    Track,
)

from core.yandex_service import (  # noqa: E402
    FEEDBACK_RADIO_STARTED,
    FEEDBACK_SKIP,
    FEEDBACK_TRACK_PLAYED,
    FEEDBACK_TRACK_STARTED,
    PREFETCH_THRESHOLD,
    QUALITY_AUTO,
    QUALITY_HIGH,
    QUALITY_LOSSLESS,
    QUALITY_LOW,
    QUALITY_MEDIUM,
    STREAM_CACHE_TTL_S,
    WAVE_STATION,
    StreamLink,
    WaveTrack,
    YandexService,
    cover_size,
    normalize_diversity,
    normalize_language,
    normalize_mood_energy,
    readable_error,
    select_variant,
    stream_link,
    track_cover_url,
)

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"ok: {name}")


def raises(name: str, exception: type[Exception], call, *args, **kwargs) -> None:
    try:
        call(*args, **kwargs)
    except exception:
        PASSED.append(name)
        print(f"ok: {name}")
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"{name} FAILED unexpected {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"{name} FAILED no exception raised")


# -- model factories --------------------------------------------------------


def make_album(album_id: int = 7, cover_uri: str | None = None) -> Album:
    return Album(id=album_id, title=f"Album {album_id}", cover_uri=cover_uri)


def make_track(
    track_id: int = 42,
    album_id: int = 7,
    title: str = "Song",
    duration_ms: int = 180000,
    cover_uri: str | None = None,
    og_image: str | None = None,
    artist_name: str = "Artist",
    artist_cover: str | None = None,
    available: bool = True,
) -> Track:
    artist = Artist(name=artist_name, og_image=artist_cover)
    return Track(
        id=track_id,
        title=title,
        available=available,
        artists=[artist],
        albums=[make_album(album_id, cover_uri=cover_uri)] if album_id else [],
        duration_ms=duration_ms,
        cover_uri=cover_uri,
        og_image=og_image,
        explicit=False,
    )


def make_sequence(track: Track | None, liked: bool = False, type_: str = "track") -> Sequence:
    return Sequence(type=type_, track=track, liked=liked)


def make_batch(
    tracks: list[Track],
    batch_id: str = "batch-1",
    with_ads: bool = False,
) -> StationTracksResult:
    sequence: list[Sequence] = []
    if with_ads:
        sequence.append(make_sequence(None, type_="ad"))
    sequence.extend(make_sequence(track, liked=index == 0) for index, track in enumerate(tracks))
    return StationTracksResult(
        id=Id(type="user", tag="onyourwave"),
        sequence=sequence,
        batch_id=batch_id,
        pumpkin=False,
    )


def make_download(codec: str, bitrate: int, preview: bool = False) -> DownloadInfo:
    return DownloadInfo(
        codec=codec,
        bitrate_in_kbps=bitrate,
        gain=0,
        preview=preview,
        download_info_url=f"https://storage.mds.yandex.net/get-music/{codec}-{bitrate}",
        direct=False,
    )


@dataclass
class FakeVariant:
    codec: str
    bitrate_in_kbps: int
    preview: bool = False
    url: str = "https://storage.mds.yandex.net/direct/stream"

    def get_direct_link(self, **kwargs: object) -> str:
        return self.url


# -- fake client ------------------------------------------------------------


class FakeClient:
    """Records API calls and serves canned responses."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.tokens: list[str] = []
        self.calls: list[str] = []
        self.me: SimpleNamespace | None = None
        self.init_error: Exception | None = None
        self.omit_profile = False
        self.has_plus = True
        self.uid = 12345
        self.batches: list[StationTracksResult] = []
        self.station_calls: list[dict] = []
        self.feedback_calls: list[dict] = []
        self.settings_calls: list[tuple] = []
        self.like_calls: list[tuple] = []
        self.dislike_calls: list[tuple] = []
        self.download_calls: list[tuple] = []
        self.variants: list = []
        self.station_error: Exception | None = None
        self.feedback_error: Exception | None = None
        self.like_error: Exception | None = None
        self.download_error: Exception | None = None

    def init(self) -> None:
        self.calls.append("init")
        if self.init_error is not None:
            raise self.init_error
        if self.omit_profile:
            self.me = None
            return
        account = SimpleNamespace(
            login="ivan@yandex.ru",
            display_name="Иван Петров",
            full_name="Иван Петров",
            first_name="Иван",
            second_name="Петров",
            uid=self.uid,
        )
        account.to_dict = lambda: {"login": account.login, "uid": account.uid}  # type: ignore[attr-defined]
        self.me = SimpleNamespace(
            account=account,
            plus=SimpleNamespace(has_plus=self.has_plus),
        )

    def rotor_station_tracks(self, station, settings2=True, queue=None, **kwargs):
        self.calls.append("rotor_station_tracks")
        self.station_calls.append(
            {"station": station, "settings2": settings2, "queue": queue, "kwargs": kwargs}
        )
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
        payload = {
            "station": station,
            "type": type_,
            "from": from_,
            "batch_id": batch_id,
            "total_played_seconds": total_played_seconds,
            "track_id": track_id,
        }
        self.feedback_calls.append(payload)
        if self.feedback_error is not None:
            raise self.feedback_error
        return True

    def rotor_station_settings2(self, station, mood_energy, diversity, language="not-russian", **kwargs):
        self.calls.append("rotor_station_settings2")
        self.settings_calls.append((station, mood_energy, diversity, language))
        return SimpleNamespace(diversity=diversity, mood_energy=mood_energy, language=language)

    def users_likes_tracks_add(self, track_ids, user_id=None, **kwargs):
        self.calls.append("likes_add")
        self.like_calls.append((tuple(track_ids), user_id))
        if self.like_error is not None:
            raise self.like_error
        return True

    def users_likes_tracks_remove(self, track_ids, user_id=None, **kwargs):
        self.calls.append("likes_remove")
        self.like_calls.append((tuple(track_ids), user_id))
        return True

    def users_dislikes_tracks_add(self, track_ids, user_id=None, **kwargs):
        self.calls.append("dislikes_add")
        self.dislike_calls.append((tuple(track_ids), user_id))
        if self.like_error is not None:
            raise self.like_error
        return True

    def users_dislikes_tracks_remove(self, track_ids, user_id=None, **kwargs):
        self.calls.append("dislikes_remove")
        self.dislike_calls.append((tuple(track_ids), user_id))
        return True

    def tracks_download_info(self, track_id, get_direct_links=False, **kwargs):
        self.calls.append("tracks_download_info")
        self.download_calls.append((track_id, get_direct_links))
        if self.download_error is not None:
            raise self.download_error
        return list(self.variants)


def make_service(token: str = "token-1", setup: object = None) -> tuple[YandexService, FakeClient]:
    client = FakeClient(token)
    if setup is not None:
        setup(client)  # type: ignore[operator]

    def factory(value: str) -> FakeClient:
        client.tokens.append(value)
        return client

    return YandexService(token, factory), client


def settle(app: QApplication, service: YandexService, timeout: float = 5.0) -> bool:
    """Process events until the worker queue stays empty for a while."""
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


# -- pure helpers -----------------------------------------------------------


def test_cover_size() -> None:
    check("cover size default", cover_size(None) == "1000x1000")
    check("cover size alias", cover_size("thumbnail") == "100x100")
    check("cover size int", cover_size(300) == "300x300")
    check("cover size passthrough", cover_size("50x50") == "50x50")
    check("cover size original", cover_size("original") == "orig")


def test_cover_urls() -> None:
    track = make_track(cover_uri="avatars.yandex.net/get-music/%%/%%")
    url = track_cover_url(track, "large")
    check(
        "cover from track cover_uri",
        url == "https://avatars.yandex.net/get-music/1000x1000/1000x1000",
        str(url),
    )
    absolute = make_track(cover_uri="https://avatars.yandex.net/get-music/%%/%%")
    check(
        "cover absolute kept",
        track_cover_url(absolute, "small") == "https://avatars.yandex.net/get-music/200x200/200x200",
    )

    wave = WaveTrack(id="42", track_id="42:7", title="T", og_image="avatars.yandex.net/og/%%")
    check(
        "cover wave og_image",
        track_cover_url(wave, "medium") == "https://avatars.yandex.net/og/400x400",
        str(track_cover_url(wave, "medium")),
    )
    check("cover wave method", wave.cover_url("small") == "https://avatars.yandex.net/og/200x200")

    album_cover = make_track(album_id=7, cover_uri=None)
    album_cover.albums[0].cover_uri = "avatars.yandex.net/album/%%"
    check(
        "cover from album",
        track_cover_url(album_cover) == "https://avatars.yandex.net/album/1000x1000",
    )

    artist_cover = make_track(artist_cover="avatars.yandex.net/artist/%%")
    check(
        "cover from artist og_image",
        track_cover_url(artist_cover) == "https://avatars.yandex.net/artist/1000x1000",
    )

    with_cover = make_track()
    with_cover.artists[0].cover = Cover(type="pic", uri="avatars.yandex.net/cover/%%")
    check(
        "cover from artist cover object",
        track_cover_url(with_cover, "thumbnail") == "https://avatars.yandex.net/cover/100x100",
    )

    check("cover dict input", track_cover_url({"cover_uri": "avatars.yandex.net/d/%%"}) is not None)
    check("cover none", track_cover_url(None) is None)
    check("cover empty", track_cover_url(WaveTrack(id="1", track_id="1", title="x")) is None)
    check(
        "cover dict artists fallback",
        track_cover_url({"artists": [{"og_image": "avatars.yandex.net/da/%%"}]}) is not None,
    )


def test_select_variant() -> None:
    mp3_320 = make_download("mp3", 320)
    mp3_192 = make_download("mp3", 192)
    mp3_128 = make_download("mp3", 128)
    flac = make_download("flac", 1411)
    all_variants = [mp3_128, flac, mp3_320, mp3_192]

    check("variant empty", select_variant([]) is None)
    check("variant none filtered", select_variant([None, None]) is None)
    check("variant auto lossless", select_variant(all_variants, QUALITY_AUTO) is flac)
    check("variant lossless alias", select_variant(all_variants, "flac") is flac)
    check("variant max alias", select_variant(all_variants, "max") is flac)
    check("variant lossless denied", select_variant(all_variants, QUALITY_LOSSLESS, False) is mp3_320)
    check("variant auto denied lossless", select_variant(all_variants, QUALITY_AUTO, False) is mp3_320)
    check("variant high", select_variant(all_variants, QUALITY_HIGH) is mp3_320)
    check("variant 320 alias", select_variant(all_variants, "320") is mp3_320)
    check("variant medium", select_variant(all_variants, QUALITY_MEDIUM) is mp3_192)
    check("variant low", select_variant(all_variants, QUALITY_LOW) is mp3_128)
    check("variant numeric bitrate", select_variant(all_variants, "192") is mp3_192)
    check(
        "variant missing bitrate fallback",
        select_variant([mp3_128, mp3_192], QUALITY_HIGH) is mp3_192,
    )
    check("variant garbage quality", select_variant([mp3_128, mp3_192], "nonsense") is mp3_192)

    previews = [make_download("mp3", 192, preview=True), make_download("mp3", 320, preview=True)]
    check("variant skips previews", select_variant(previews + [mp3_128], QUALITY_AUTO) is mp3_128)
    check("variant preview only", select_variant(previews, QUALITY_AUTO).bitrate_in_kbps == 320)
    check(
        "variant preview only low",
        select_variant(previews, QUALITY_LOW).bitrate_in_kbps == 192,
    )
    check(
        "variant prefers common codec",
        select_variant([make_download("aac", 320), make_download("mp3", 320)], QUALITY_HIGH).codec == "mp3",
    )
    check("variant no bitrate field", select_variant([FakeVariant("mp3", 0)]) is not None)


def test_stream_link() -> None:
    variant = FakeVariant("flac", 1411, url="https://direct/flac-track")
    link = stream_link("42:7", variant)
    check("stream link url", link.url == "https://direct/flac-track")
    check("stream link codec", link.codec == "flac")
    check("stream link lossless", link.lossless is True)
    check("stream link bitrate", link.bitrate == 1411)
    check("stream link track", link.track_id == "42:7")
    check("stream link preview", stream_link("1", FakeVariant("mp3", 192, preview=True)).preview is True)
    check("stream link explicit url", stream_link("1", variant, "https://other/link").url == "https://other/link")
    raises("stream link without getter", RuntimeError, stream_link, "1", object())
    check("stream link dict", stream_link("1", variant).to_dict()["codec"] == "flac")


def test_normalizers() -> None:
    check("mood explicit", normalize_mood_energy(mood_energy="fun") == "fun")
    check("mood energy wins", normalize_mood_energy(mood="calm", energy="active") == "active")
    check("mood used alone", normalize_mood_energy(mood="sad") == "sad")
    check("mood 0", normalize_mood_energy(mood=0) == "sad")
    check("mood 1", normalize_mood_energy(mood=1) == "fun")
    check("energy 0", normalize_mood_energy(energy=0) == "calm")
    check("energy 1", normalize_mood_energy(energy=1) == "active")
    check("mood bool", normalize_mood_energy(mood=True) == "fun")
    check("mood case", normalize_mood_energy(mood="FUN") == "fun")
    check("mood none", normalize_mood_energy() is None)
    check("mood empty", normalize_mood_energy(mood="") is None)
    raises("mood invalid", ValueError, normalize_mood_energy, mood_energy="party")
    raises("mood unknown word", ValueError, normalize_mood_energy, mood="party")
    raises("mood out of range", ValueError, normalize_mood_energy, mood=2)
    raises("energy out of range", ValueError, normalize_mood_energy, energy=7)

    check("language ru", normalize_language("ru") == "russian")
    check("language en", normalize_language("en") == "not-russian")
    check("language none", normalize_language("none") == "not-russian")
    check("language all", normalize_language("all") == "any")
    check("language passthrough", normalize_language("russian") == "russian")
    check("language none value", normalize_language(None) is None)
    check("language empty", normalize_language("") is None)
    raises("language invalid", ValueError, normalize_language, "klingon")

    check("diversity value", normalize_diversity("discover") == "discover")
    check("diversity upper", normalize_diversity("POPULAR") == "popular")
    check("diversity none", normalize_diversity(None) is None)
    check("diversity empty", normalize_diversity("") is None)
    raises("diversity invalid", ValueError, normalize_diversity, "chaos")


def test_readable_error() -> None:
    check("error unauthorized", "Токен" in readable_error(RuntimeError("Unauthorized 401")))
    check("error forbidden", "Доступ" in readable_error(RuntimeError("Forbidden 403")))
    check("error notfound", "не найдены" in readable_error(RuntimeError("NotFound")))
    check("error badrequest", "отклонил" in readable_error(RuntimeError("BadRequest 400")))
    check("error timeout", "связи" in readable_error(RuntimeError("Connection timeout")))
    check("error network", "соединиться" in readable_error(RuntimeError("network unreachable")))
    check("error bitrate", "качество" in readable_error(RuntimeError("Unsupported bitrate")))
    check("error restricted", "ограничений" in readable_error(RuntimeError("do not recommend")))
    check("error text", "Ошибка Яндекс Музыки: boom" == readable_error(RuntimeError("boom")))
    check("error empty", "RuntimeError" in readable_error(RuntimeError()))


# -- service ---------------------------------------------------------------


def test_session(app: QApplication) -> None:
    service, client = make_service()
    profiles: list[dict] = []
    errors: list[str] = []
    service.session_validated.connect(profiles.append)
    service.session_error.connect(errors.append)

    check("session not authenticated initially", service.is_authenticated is False)
    check("validate submits", service.validate_session() is True)
    check("session settled", settle(app, service))
    client = client
    check("session init called", client.calls == ["init"], str(client.calls))
    check("session profile emitted", len(profiles) == 1, str(profiles))
    check("session login", profiles[0]["login"] == "ivan@yandex.ru")
    check("session plus", profiles[0]["has_plus"] is True)
    check("session no client leak", "client" not in profiles[0])
    check("session authenticated", service.is_authenticated is True)
    check("session uid", service.uid == 12345)
    check("session has_plus", service.has_plus is True)
    check("session from field", service.from_field == "mobile-radio-user-12345", service.from_field)
    check("session copy", service.session["display_name"] == "Иван Петров")
    check("session no error", errors == [], str(errors))
    check("worker running", service.is_running is True)

    service.shutdown()
    check("session cleared on shutdown", service.is_authenticated is False)
    check("worker stopped", service.is_running is False)


def test_session_errors(app: QApplication) -> None:
    service, client = make_service()
    service.validate_session()
    settle(app, service)
    check("validate creates client", client.calls == ["init"])
    service.shutdown()

    empty = YandexService("")
    errors: list[str] = []
    empty.session_error.connect(errors.append)
    check("validate empty token", empty.validate_session() is False)
    check("validate empty token error", errors and "Нет токена" in errors[-1])
    check("validate empty token no thread", empty.is_running is False)

    def break_init(client: FakeClient) -> None:
        client.init_error = RuntimeError("Unauthorized 401")

    failing, client2 = make_service("token-3", break_init)
    failures: list[str] = []
    failing.session_error.connect(failures.append)
    check("failing init submitted", failing.validate_session() is True)
    settle(app, failing)
    check("failing init error", failures and "Токен" in failures[-1], str(failures))
    check("failing init not authenticated", failing.is_authenticated is False)
    check("failing init uid empty", failing.uid is None)
    check("failing from field", failing.from_field == "mobile-radio-user-0", failing.from_field)
    failing.shutdown()

    def no_profile(client: FakeClient) -> None:
        client.omit_profile = True

    profile_less, client2 = make_service("token-4", no_profile)
    profile_less_errors: list[str] = []
    profile_less.session_error.connect(profile_less_errors.append)
    profile_less.validate_session()
    settle(app, profile_less)
    check(
        "missing profile error",
        profile_less_errors and "Аккаунт не найден" in profile_less_errors[-1],
        str(profile_less_errors),
    )
    check("missing profile not authenticated", profile_less.is_authenticated is False)
    profile_less.shutdown()


def test_wave_start(app: QApplication) -> None:
    service, client = make_service()
    started: list[list] = []
    batches: list[list] = []
    changed: list = []
    events: list[tuple] = []
    errors: list[str] = []
    service.wave_started.connect(started.append)
    service.wave_batch.connect(batches.append)
    service.track_changed.connect(changed.append)
    service.feedback_sent.connect(lambda track, event: events.append((track, event)))
    service.wave_error.connect(errors.append)

    first = [make_track(1, 10, "First"), make_track(2, 11, "Second"), make_track(3, 12, "Third")]
    client.batches = [make_batch(first, batch_id="b-1", with_ads=True)]
    service.validate_session()
    check("wave session settled", settle(app, service))
    check("wave from real uid", service.from_field == "mobile-radio-user-12345")
    client.feedback_calls.clear()

    check("wave start submitted", service.start_my_wave() is True)
    check("wave settled", settle(app, service))
    client = client
    check("wave station requested", len(client.station_calls) == 1)
    check("wave station name", client.station_calls[0]["station"] == WAVE_STATION)
    check("wave station settings2", client.station_calls[0]["settings2"] is True)
    check("wave first queue cursor", client.station_calls[0]["queue"] is None)
    check("wave no settings call by default", client.settings_calls == [])
    check("wave started emitted", len(started) == 1 and len(started[0]) == 3, str(started))
    check("wave ad filtered", [t.title for t in started[0]] == ["First", "Second", "Third"])
    check("wave current track", service.current_track().title == "First")
    check("wave position", service.position() == 0)
    check("wave remaining", service.remaining() == 2)
    check("wave track_changed emitted", len(changed) == 1 and changed[0].title == "First")
    check("wave liked from sequence", started[0][0].liked is True)
    check("wave track ids", started[0][0].track_id == "1:10", started[0][0].track_id)
    check("wave artists", started[0][0].artists_name == "Artist")
    check("wave album", started[0][0].album == "Album 10")
    check("wave source", started[0][0].source == WAVE_STATION)
    check(
        "wave feedback order",
        [event for _track, event in events] == [FEEDBACK_RADIO_STARTED, FEEDBACK_TRACK_STARTED],
        str(events),
    )
    check("wave feedback started no track", events[0][0] == "")
    check("wave feedback first track id", events[1][0] == "1:10")
    check("wave feedback from field", client.feedback_calls[0]["from"] == "mobile-radio-user-12345")
    check("wave no feedback error", errors == [], str(errors))
    check("wave batch_id stored", client.feedback_calls[1]["batch_id"] == "b-1")
    check("wave no duplicate batch", batches == [])

    service.shutdown()


def test_wave_settings(app: QApplication) -> None:
    service, client = make_service()
    applied: list[tuple] = []
    errors: list[str] = []
    service.settings_applied.connect(lambda *args: applied.append(args))
    service.wave_error.connect(errors.append)
    client.batches = [make_batch([make_track(1, 10), make_track(2, 11)])]

    check("wave with settings submitted", service.start_my_wave(mood="fun", language="ru", diversity="discover") is True)
    check("settings wave settled", settle(app, service))
    client = client
    check("settings api call", client.settings_calls == [(WAVE_STATION, "fun", "discover", "russian")], str(client.settings_calls))
    check("settings applied signal", applied == [("fun", "discover", "russian")], str(applied))
    check("settings stored", service.settings() == ("fun", "discover", "russian"), str(service.settings()))
    check("settings before station request", client.calls.index("rotor_station_settings2") < client.calls.index("rotor_station_tracks"), str(client.calls))
    check("settings wave has queue", len(service.queue_snapshot()) == 2)
    check("settings no error", errors == [], str(errors))
    service.shutdown()

    bad, client2 = make_service()
    bad_errors: list[str] = []
    bad.wave_error.connect(bad_errors.append)
    check("wave invalid mood rejected", bad.start_my_wave(mood="party") is False)
    check("wave invalid mood error", bad_errors and "настроение" in bad_errors[-1])
    check("wave invalid mood no thread", bad.is_running is False)
    bad.shutdown()

    no_token = YandexService(None)
    no_token_errors: list[str] = []
    no_token.wave_error.connect(no_token_errors.append)
    check("wave without token rejected", no_token.start_my_wave() is False)
    check("wave without token error", "авторизации" in no_token_errors[-1])


def test_queue_navigation(app: QApplication) -> None:
    service, client = make_service()
    changed: list = []
    queue_states: list[int] = []
    events: list[tuple] = []
    service.track_changed.connect(changed.append)
    service.queue_changed.connect(queue_states.append)
    service.feedback_sent.connect(lambda track, event: events.append((track, event)))

    tracks = [make_track(number, 100 + number, f"Track {number}") for number in range(1, 7)]
    client.batches = [make_batch(tracks, batch_id="q-1")]
    service.start_my_wave()
    settle(app, service)
    events.clear()

    nxt = service.next_track(played_seconds=1.0)
    check("next track settled", settle(app, service))
    check("next track returns track", nxt is not None and nxt.title == "Track 2")
    check("next track position", service.position() == 1)
    check("next track remaining", service.remaining() == 4)
    check("next track changed signal", changed[-1].title == "Track 2")
    check("next track history", [t.title for t in service.history()] == ["Track 1", "Track 2"])
    check("next track queue state", queue_states[-1] == 4)
    check("skip feedback on low play", events[-2] == ("1:101", FEEDBACK_SKIP), str(events))
    check("start feedback for new track", events[-1] == ("2:102", FEEDBACK_TRACK_STARTED), str(events))
    check("prefetch deferred", len(client.station_calls) == 1, str(len(client.station_calls)))

    events.clear()
    played = service.next_track(played_seconds=170.0)
    check("completed track settled", settle(app, service))
    check("completed feedback", events[-2] == ("2:102", FEEDBACK_TRACK_PLAYED), str(events))
    check("completed advances", played.title == "Track 3")
    check("played ratio event", FEEDBACK_TRACK_PLAYED in [event for _t, event in events])
    check("prefetch at threshold", len(client.station_calls) == 2, str(len(client.station_calls)))
    check(
        "prefetch queue cursor",
        client.station_calls[-1]["queue"] == "2:102",
        str(client.station_calls[-1]),
    )

    before = len(client.feedback_calls)
    service.skip(played_seconds=4.0)
    settle(app, service)
    new_events = client.feedback_calls[before:]
    check("skip event", new_events[0]["type"] == FEEDBACK_SKIP, str(new_events))
    check("skip seconds", new_events[0]["total_played_seconds"] == 4.0)
    check("skip track id", new_events[0]["track_id"] == "3:103")
    check("skip starts next", any(item["type"] == FEEDBACK_TRACK_STARTED for item in new_events[1:]))
    check("skip advanced", service.current_track().title == "Track 4")

    check("track_played reports finished", service.track_played(played_seconds=170.0) is True)
    settle(app, service)
    check("track_played event", client.feedback_calls[-1]["type"] == FEEDBACK_TRACK_PLAYED, str(client.feedback_calls[-1]))
    check("track_played track id", client.feedback_calls[-1]["track_id"] == "4:104")
    check("track_played once", service.track_played(played_seconds=170.0) is False)
    settle(app, service)
    check("track_played no duplicate", len(client.feedback_calls) == before + 3, str(len(client.feedback_calls)))

    events.clear()
    back = service.previous_track()
    check("previous settled", settle(app, service))
    check("previous track", back is not None and back.title == "Track 3", str(back))
    check("previous position", service.position() == 2)
    check("previous no feedback", events == [], str(events))
    check("previous at start", service.previous_track() is not None)

    service.position_changed(4)
    check("position settled", settle(app, service))
    check("position changed track", service.current_track().title == "Track 5")
    service.position_changed(99)
    check("position out of range ignored", service.current_track().title == "Track 5")

    before = len(client.feedback_calls)
    check("played after position change", service.track_played(played_seconds=170.0) is True)
    settle(app, service)
    check(
        "played after seek sent",
        client.feedback_calls[-1]["type"] == FEEDBACK_TRACK_PLAYED,
        str(client.feedback_calls[-1]),
    )
    check("skip after finish ignored", service.skip(played_seconds=1.0) is not None)
    settle(app, service)
    check(
        "no skip after finished",
        [item for item in client.feedback_calls[before:] if item["type"] == FEEDBACK_SKIP] == [],
        str(client.feedback_calls[before:]),
    )
    service.stop_my_wave()
    check("wave stopped signal track kept", service.current_track() is not None)
    service.shutdown()


def test_queue_exhaustion(app: QApplication) -> None:
    service, client = make_service()
    errors: list[str] = []
    service.wave_error.connect(errors.append)
    client.batches = [make_batch([make_track(1, 1), make_track(2, 2)], batch_id="tiny")]
    service.start_my_wave()
    settle(app, service)

    service._wave_started = False
    check("queue exhausted", service.next_track(played_seconds=100.0) is not None)
    service._wave_started = True
    while service.next_track(played_seconds=100.0) is not None:
        pass
    settle(app, service)
    check("exhaustion current none", service.current_track() is None)
    check("exhaustion forced prefetch", len(client.station_calls) >= 2)
    check("exhaustion remaining", service.remaining() == 0)
    check("exhaustion no error", errors == [], str(errors))
    service.shutdown()


def test_prefetch_dedup(app: QApplication) -> None:
    service, client = make_service()
    batches: list[list] = []
    service.wave_batch.connect(batches.append)
    first = [make_track(number, number, f"Track {number}") for number in range(1, 6)]
    second = [make_track(number, number, f"Track {number}") for number in (3, 5, 6, 7)]
    client.batches = [make_batch(first, batch_id="p-1"), make_batch(second, batch_id="p-2")]
    service.start_my_wave()
    settle(app, service)
    check("dedup initial", len(service.queue_snapshot()) == 5)

    check("prefetch not needed", service.maybe_prefetch() is False)
    while service.remaining() > PREFETCH_THRESHOLD:
        service.next_track(played_seconds=100.0)
    settle(app, service)
    check("prefetch after threshold", len(batches) == 1, str(len(batches)))
    check("prefetch batch size", [t.title for t in batches[0]] == ["Track 6", "Track 7"], str(batches))
    check("prefetch dedup total", len(service.queue_snapshot()) == 7, str(len(service.queue_snapshot())))
    check("prefetch twice blocked", service.maybe_prefetch(force=True) is True)
    settle(app, service)
    check("prefetch stop", service.stop_my_wave() is None)
    check("prefetch disabled", service.maybe_prefetch(force=True) is False)
    service.shutdown()


def test_batch_failures(app: QApplication) -> None:
    service, client = make_service()
    errors: list[str] = []
    batches: list[list] = []
    service.wave_error.connect(errors.append)
    service.wave_started.connect(batches.append)

    client.station_error = RuntimeError("Unauthorized 401")
    check("wave start with error submitted", service.start_my_wave() is True)
    check("wave error settled", settle(app, service))
    check("wave error emitted", errors and "Токен" in errors[-1], str(errors))
    check("wave error no start", batches == [])
    client.station_error = None

    client.batches = [make_batch([make_track(1, 1)], batch_id="only")]
    service.start_my_wave()
    settle(app, service)
    check("wave recovers after error", len(service.queue_snapshot()) == 1)

    client.batches = [make_batch([make_track(2, 2, available=False)], batch_id="bad")]
    service._prefetching = False
    service.maybe_prefetch(force=True)
    settle(app, service)
    check("unavailable filtered", errors and "доступных треков" in errors[-1], str(errors))

    service.shutdown()


def test_feedback_failures(app: QApplication) -> None:
    service, client = make_service()
    events: list[tuple] = []
    service.feedback_sent.connect(lambda track, event: events.append((track, event)))
    client.batches = [make_batch([make_track(1, 1), make_track(2, 2)], batch_id="fb")]
    service.start_my_wave()
    settle(app, service)
    client.feedback_error = RuntimeError("boom")
    events.clear()
    service.next_track(played_seconds=1.0)
    settle(app, service)
    check("feedback failure silent", events == [], str(events))
    check("wave survives feedback error", service.current_track() is not None)
    service.shutdown()

    stopped, client2 = make_service()
    client2.batches = [make_batch([make_track(1, 1)], batch_id="s")]
    stopped.start_my_wave()
    settle(app, stopped)
    before = len(client2.feedback_calls)
    stopped.stop_my_wave()
    stopped.track_played(played_seconds=100.0)
    settle(app, stopped)
    check("feedback after stop skipped", len(client2.feedback_calls) == before)
    check("stopped wave still current", stopped.current_track() is not None)
    stopped.shutdown()


def test_likes(app: QApplication) -> None:
    service, client = make_service()
    likes: list[tuple] = []
    dislikes: list[tuple] = []
    service.like_changed.connect(lambda *args: likes.append(args))
    service.dislike_changed.connect(lambda *args: dislikes.append(args))
    client.batches = [make_batch([make_track(42, 7, "Liked")], batch_id="like")]
    service.start_my_wave()
    settle(app, service)
    client = client
    current = service.current_track()
    check("like target is current", current is not None)

    check("like submitted", service.like() is True)
    settle(app, service)
    check("like api call", client.like_calls == [(("42:7",), None)], str(client.like_calls))
    check("like signal", likes == [("42:7", True)], str(likes))
    check("like state", service.is_liked(current) is True)
    check("like clears dislike", service.is_disliked(current) is False)

    check("dislike submitted", service.dislike() is True)
    settle(app, service)
    check("dislike api call", client.dislike_calls == [(("42:7",), None)], str(client.dislike_calls))
    check("dislike signal", dislikes == [("42:7", True)], str(dislikes))
    check("dislike state", service.is_disliked(current) is True)
    check("dislike clears like", service.is_liked(current) is False)

    check("unlike submitted", service.remove_like() is True)
    settle(app, service)
    check("unlike api call", client.like_calls[-1] == (("42:7",), None))
    check("unlike signal", likes[-1] == ("42:7", False))
    check("unlike state", service.is_liked(current) is False)

    check("undislike submitted", service.remove_dislike() is True)
    settle(app, service)
    check("undislike api call", client.dislike_calls[-1] == (("42:7",), None))
    check("undislike state", service.is_disliked(current) is False)

    client.like_error = RuntimeError("Forbidden 403")
    errors: list[tuple] = []
    service.like_error.connect(lambda *args: errors.append(args))
    check("like error submitted", service.like() is True)
    settle(app, service)
    check("like error reported", errors and "Доступ" in errors[-1][1], str(errors))
    check("like error state", service.is_liked(current) is False)
    dislike_errors: list[tuple] = []
    service.dislike_error.connect(lambda *args: dislike_errors.append(args))
    check("dislike error submitted", service.dislike() is True)
    settle(app, service)
    check("dislike error reported", dislike_errors and "Доступ" in dislike_errors[-1][1])
    check("dislike error state", service.is_disliked(current) is False)
    service.shutdown()

    empty, client2 = make_service()
    check("like without current", empty.like(make_track(1, 1)) is True)
    settle(app, empty)
    check("like explicit track", empty.is_liked("1:1") is True)
    empty.shutdown()

    no_token = YandexService(None)
    no_token_errors: list[tuple] = []
    no_token.like_error.connect(lambda *args: no_token_errors.append(args))
    no_token.dislike_error.connect(lambda *args: no_token_errors.append(args))
    no_token.stream_error.connect(lambda *args: no_token_errors.append(args))
    check("like without token", no_token.like(make_track(1, 1)) is False)
    check("like no track", no_token.like() is False)
    check("dislike without token", no_token.dislike(make_track(1, 1)) is False)
    check("unlike without token", no_token.remove_like(make_track(1, 1)) is False)
    check("undislike without token", no_token.remove_dislike(make_track(1, 1)) is False)
    check("stream without token error", no_token.stream_url(make_track(1, 1)) is False)
    check("no token errors", len(no_token_errors) == 6, str(no_token_errors))
    check(
        "no token messages",
        all("авторизации" in message or "не выбран" in message for _key, message in no_token_errors),
        str(no_token_errors),
    )


def test_streams(app: QApplication) -> None:
    service, client = make_service()
    links: list[StreamLink] = []
    errors: list[tuple] = []
    service.stream_ready.connect(links.append)
    service.stream_error.connect(lambda *args: errors.append(args))
    client.batches = [make_batch([make_track(42, 7, "Stream")], batch_id="s1")]
    service.start_my_wave()
    settle(app, service)
    service.validate_session()
    settle(app, service)
    check("stream has plus", service.has_plus is True)
    client = client
    current = service.current_track()

    client.variants = [FakeVariant("mp3", 192), FakeVariant("mp3", 320), FakeVariant("flac", 1411)]
    check("stream submitted", service.stream_url(quality=QUALITY_HIGH) is True)
    check("stream settled", settle(app, service))
    check("stream api call", client.download_calls == [("42:7", False)], str(client.download_calls))
    check("stream ready emitted", len(links) == 1 and links[0].bitrate == 320, str(links))
    check("stream track", links[0].track_id == "42:7")
    check("stream codec", links[0].codec == "mp3")

    check("stream cached submit", service.stream_url(quality=QUALITY_HIGH) is True)
    settle(app, service)
    check("stream cache hit", len(client.download_calls) == 1, str(client.download_calls))
    check("stream cache signal", len(links) == 2 and links[1] == links[0])
    check("cached_stream lookup", service.cached_stream(current, QUALITY_HIGH) == links[0])
    check("cached_stream other quality", service.cached_stream(current).url == links[0].url)
    check("cached_stream unknown", service.cached_stream("nope") is None)

    check("stream lossless submitted", service.stream_url(quality=QUALITY_LOSSLESS) is True)
    settle(app, service)
    check("stream lossless codec", links[-1].codec == "flac", str(links[-1]))
    check("stream lossless flag", links[-1].lossless is True)

    check("stream denied lossless", service.stream_url(quality=QUALITY_LOSSLESS, allow_lossless=False) is True)
    settle(app, service)
    check("stream denied codec", links[-1].codec == "mp3" and links[-1].bitrate == 320)

    for key in list(service._stream_cache):
        service._stream_cache[key] = (time.monotonic() - STREAM_CACHE_TTL_S - 1, service._stream_cache[key][1])
    check("expired cache miss", service.cached_stream(current, QUALITY_HIGH) is None)
    check("expired stream refetch", service.stream_url(quality=QUALITY_HIGH) is True)
    settle(app, service)
    check("expired stream refetched", len(client.download_calls) == 4, str(client.download_calls))

    client.variants = []
    check("stream no variants submitted", service.stream_url(current) is True)
    settle(app, service)
    check("stream no variants error", errors and "вариантов" in errors[-1][1], str(errors))
    before = len(client.download_calls)
    check("stream error cached", service.stream_url(current) is False)
    settle(app, service)
    check("stream error no refetch", len(client.download_calls) == before, str(client.download_calls))

    client.download_error = RuntimeError("NotFound 404")
    client.variants = [FakeVariant("mp3", 320)]
    check("stream request error", service.stream_url(make_track(77, 8)) is True)
    settle(app, service)
    check("stream request error reported", errors and "не найдены" in errors[-1][1], str(errors))
    service.shutdown()

    none_service, client2 = make_service()
    none_errors: list[tuple] = []
    none_service.stream_error.connect(lambda *args: none_errors.append(args))
    check("stream no track", none_service.stream_url() is False)
    check("stream no track error", none_errors and "не выбран" in none_errors[-1][1])
    none_service.shutdown()


def test_clear_session(app: QApplication) -> None:
    service, client = make_service()
    cleared: list[int] = []
    stopped: list[int] = []
    service.session_cleared.connect(lambda: cleared.append(1))
    service.wave_stopped.connect(lambda: stopped.append(1))
    service.start_my_wave()
    settle(app, service)
    check("clear has queue", len(service.queue_snapshot()) == 1)

    service.clear_session()
    check("clear signal", cleared == [1])
    check("clear stops wave", stopped == [1])
    check("clear queue", service.queue_snapshot() == [])
    check("clear current", service.current_track() is None)
    check("clear position", service.position() == -1)
    check("clear authenticated", service.is_authenticated is False)
    check("clear keeps worker alive", service.is_running is True)

    client.batches = [make_batch([make_track(1, 1)])]
    check("restart after clear", service.start_my_wave() is True)
    settle(app, service)
    check("restart queue", len(service.queue_snapshot()) == 1)
    service.shutdown()
    check("shutdown signal", cleared == [1, 1])
    check("shutdown stops wave signal", stopped == [1, 1])

    switching, client4 = make_service("token-8")
    switch_cleared: list[int] = []
    switching.session_cleared.connect(lambda: switch_cleared.append(1))
    check("set_token empty", switching.set_token(None) is False)
    check("set_token empty not authenticated", switching.is_authenticated is False)
    check("set_token empty property", switching.token is None)
    check("set_token clear signal", switch_cleared == [1], str(switch_cleared))
    switching.set_token("token-9")
    check("set_token submitted", settle(app, switching))
    check("set_token new client", client4.tokens == ["token-9"], str(client4.tokens))
    check("set_token property", switching.token == "token-9")
    check("set_token validated", switching.is_authenticated is True)
    switching.shutdown()


def test_defaults_and_job_failure() -> None:
    check("station constant", WAVE_STATION == "user:onyourwave")
    check("feedback played event", FEEDBACK_TRACK_PLAYED == "trackFinished")
    check("prefetch threshold", PREFETCH_THRESHOLD == 3)

    class Dead(YandexService):
        def job_failed(self, tag, exc):
            self.failed = tag

    dead = Dead("t", lambda value: FakeClient(value))
    dead._worker.stop()
    dead.failed = ""
    check("dead job rejected", dead.validate_session() is False)
    check("dead job hook", dead.failed == "validate_session", str(dead.failed))
    check("dead like rejected", dead.like(make_track(1, 1)) is False)
    check("dead feedback rejected", dead._send_feedback(FEEDBACK_RADIO_STARTED) is False)
    dead.shutdown()
    check("dead no thread", dead.is_running is False)

    def bad_factory(value: str) -> FakeClient:
        raise RuntimeError("factory failed")

    broken = YandexService("t2", bad_factory)
    broken._worker.start()
    settle(QApplication.instance() or QApplication(sys.argv), broken)
    check("factory failure reported", broken.is_authenticated is False)
    broken.shutdown()


def main() -> int:
    os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="yml-test-config-")
    app = QApplication.instance() or QApplication(sys.argv)

    test_cover_size()
    test_cover_urls()
    test_select_variant()
    test_stream_link()
    test_normalizers()
    test_readable_error()
    test_defaults_and_job_failure()
    test_session(app)
    test_session_errors(app)
    test_wave_start(app)
    test_wave_settings(app)
    test_queue_navigation(app)
    test_queue_exhaustion(app)
    test_prefetch_dedup(app)
    test_batch_failures(app)
    test_feedback_failures(app)
    test_likes(app)
    test_streams(app)
    test_clear_session(app)

    print(f"\nAll {len(PASSED)} yandex service checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
