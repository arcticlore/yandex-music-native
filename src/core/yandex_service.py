"""Yandex Music API service: session, «Моя волна», stream links and covers.

All network access is performed by :class:`_YandexWorker`, a single ``QThread``
that owns exactly one :class:`yandex_music.Client`. Jobs are queued and executed
sequentially, which keeps the (not thread-safe) client confined to one thread and
makes request order deterministic: feedback for a track is always sent before
the follow-up station request that continues the chain. Results are reported
through Qt signals, so the GUI thread never blocks on HTTP.

The service is the only door to the Yandex API for the whole application: the
UI layer never touches ``yandex_music`` directly, and the ``core`` layer
depends on nothing but ``yandex_music`` itself.

API mapping notes (verified against yandex-music 3.0.0):

* The personal station is loaded with ``rotor_station_tracks("user:onyourwave")``
  and returns ``StationTracksResult.sequence`` - a list of ``Sequence`` items
  whose ``track`` field may be ``None`` (ad slots) and must be filtered.
* The station chain is continued by passing the id of the track that was just
  left (finished or skipped) as the ``queue`` argument; there is no cursor or
  page number.
* Feedback event names accepted by the server are ``radioStarted``,
  ``trackStarted``, ``trackFinished`` and ``skip``. There is no ``trackPlayed``
  event: this module exposes :meth:`YandexService.track_played` and sends it as
  ``trackFinished`` (see :data:`FEEDBACK_TRACK_PLAYED`).
* ``mood``/``energy`` are not arguments of the station request. They are applied
  through ``rotor_station_settings2`` as a single combined ``moodEnergy`` value.
* ``Track.dislike()`` in the library actually *removes* a like. A real
  "do not recommend" mark requires ``users_dislikes_tracks_add``, which is what
  :meth:`YandexService.dislike` calls.
* Direct links are signed and valid for about a minute, so they are resolved
  lazily and cached only briefly (:data:`STREAM_CACHE_TTL_S`).
* The library models do not enumerate FLAC, but the server may return a
  ``flac`` variant for Plus subscribers. :func:`select_variant` uses it when
  present and falls back to the highest available bitrate otherwise.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from PySide6.QtCore import QObject, QThread, Signal

from core import station
from core.auth import AuthService
from core.station import WaveSettings

log = logging.getLogger(__name__)

WAVE_STATION = "user:onyourwave"
FEEDBACK_FROM_PREFIX = "mobile-radio-user"

FEEDBACK_RADIO_STARTED = "radioStarted"
FEEDBACK_TRACK_STARTED = "trackStarted"
FEEDBACK_TRACK_PLAYED = "trackFinished"
FEEDBACK_SKIP = "skip"

MOOD_ENERGY_VALUES = ("all", "fun", "active", "calm", "sad")
DIVERSITY_VALUES = ("default", "discover", "popular", "favorite")
LANGUAGE_VALUES = ("any", "russian", "not-russian")
LANGUAGE_ALIASES = {
    "ru": "russian",
    "russian": "russian",
    "all": "any",
    "any": "any",
    "world": "any",
    "en": "not-russian",
    "none": "not-russian",
    "not-russian": "not-russian",
    "not_russian": "not-russian",
    "foreign": "not-russian",
}
MOOD_ALIASES = {0: "sad", 1: "fun"}
ENERGY_ALIASES = {0: "calm", 1: "active"}
DEFAULT_DIVERSITY = "default"
DEFAULT_LANGUAGE = "any"

MOOD_ENERGY_LABELS = {
    "all": "Любое настроение",
    "fun": "Весёлое",
    "active": "Активное",
    "calm": "Спокойное",
    "sad": "Грустное",
}
DIVERSITY_LABELS = {
    "default": "Стандарт",
    "discover": "Новое и интересное",
    "popular": "Популярное",
    "favorite": "Похожее на моё",
}
LANGUAGE_LABELS = {
    "any": "Любой язык",
    "russian": "Только русский",
    "not-russian": "Без русского",
}

PREFETCH_THRESHOLD = 3
PLAYED_RATIO = 0.9
STREAM_CACHE_TTL_S = 45.0
ERROR_CACHE_TTL_S = 10.0
MAX_HISTORY = 100

QUALITY_AUTO = "auto"
QUALITY_LOSSLESS = "lossless"
QUALITY_FLAC = "flac"
QUALITY_HIGH = "high"
QUALITY_MEDIUM = "medium"
QUALITY_LOW = "low"
QUALITY_ALIASES = {
    "flac": QUALITY_LOSSLESS,
    "lossless": QUALITY_LOSSLESS,
    "max": QUALITY_LOSSLESS,
    "best": QUALITY_LOSSLESS,
    "320": QUALITY_HIGH,
    "192": QUALITY_MEDIUM,
    "128": QUALITY_LOW,
    "64": QUALITY_LOW,
}
QUALITY_BITRATES = {
    QUALITY_HIGH: 320,
    QUALITY_MEDIUM: 192,
    QUALITY_LOW: 128,
}
LOSSLESS_CODECS = ("flac", "alac", "wav", "aiff", "ape", "mqa")
CODEC_RANK = {"mp3": 0, "aac": 1, "ogg": 2, "opus": 3}

COVER_SIZES = {
    "thumbnail": "100x100",
    "small": "200x200",
    "medium": "400x400",
    "large": "1000x1000",
    "original": "orig",
}
DEFAULT_COVER_SIZE = "large"

DEVICE_STRING = (
    "os=Linux; os_version=; manufacturer=PC; model=Yandex Music Native; "
    "clid=; device_id=yandex-music-native; uuid=yandex-music-native"
)

ClientFactory = Callable[[str], Any]


def default_client_factory(token: str) -> Any:
    """Create a ``yandex_music.Client`` bound to ``token``."""
    from yandex_music import Client

    client = Client(token)
    client.device = DEVICE_STRING
    return client


def _text(value: Any) -> str:
    return str(value).strip() if value else ""


def cover_size(size: str | int = DEFAULT_COVER_SIZE) -> str:
    """Resolve a size alias (``large``) to a Yandex size string (``1000x1000``)."""
    if isinstance(size, int):
        return f"{size}x{size}"
    name = _text(size).lower()
    return COVER_SIZES.get(name, name or COVER_SIZES[DEFAULT_COVER_SIZE])


def _uri_to_url(uri: str | None, size: str) -> str | None:
    if not uri:
        return None
    text = str(uri).strip()
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        return text.replace("%%", size)
    return f"https://{text.replace('%%', size)}"


def _artist_cover(artist: Any, size: str) -> str | None:
    artist = _as_view(artist)
    cover = _as_view(getattr(artist, "cover", None))
    if cover is not None:
        getter = getattr(cover, "get_url", None)
        if callable(getter):
            try:
                url = getter(size=size)
            except Exception:
                url = None
            if url:
                return str(url)
        url = _uri_to_url(getattr(cover, "uri", None), size)
        if url:
            return url
    for name in ("op_image", "og_image"):
        url = _uri_to_url(getattr(artist, name, None), size)
        if url:
            return url
    return None


def track_cover_url(track: Any, size: str | int = DEFAULT_COVER_SIZE) -> str | None:
    """Best available cover URL for a ``Track``, ``WaveTrack`` or ``dict``.

    Resolution order: the track's own ``cover_uri``, then the first album cover,
    then ``og_image``, then the first artist image. Returns ``None`` when the
    track carries no image at all.
    """
    resolved = cover_size(size)
    if track is None:
        return None
    track = _as_view(track)
    for name in ("cover_uri", "cover_url", "og_image"):
        value = getattr(track, name, None)
        if callable(value):
            continue
        url = _uri_to_url(value, resolved)
        if url:
            return url
    for album in getattr(track, "albums", None) or ():
        album = _as_view(album)
        for name in ("cover_uri", "og_image"):
            url = _uri_to_url(getattr(album, name, None), resolved)
            if url:
                return url
    for artist in getattr(track, "artists", None) or ():
        url = _artist_cover(artist, resolved)
        if url:
            return url
    return None


def _as_view(value: Any) -> Any:
    """Wrap plain dicts so they can be inspected like library models."""
    return _DictLike(value) if isinstance(value, dict) else value


class _DictLike:
    """Attribute view over a plain dict, used by :func:`track_cover_url`."""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name) from None


@dataclass(frozen=True)
class WaveTrack:
    """Immutable UI-facing view of a track from a rotor station chain."""

    id: str
    track_id: str
    title: str
    artists: tuple[str, ...] = ()
    album: str = ""
    duration_ms: int = 0
    cover_uri: str | None = None
    og_image: str | None = None
    available: bool = True
    explicit: bool = False
    liked: bool = False
    source: str = ""

    @property
    def artists_name(self) -> str:
        return ", ".join(self.artists)

    def cover_url(self, size: str | int = DEFAULT_COVER_SIZE) -> str | None:
        return track_cover_url(self, size)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "track_id": self.track_id,
            "title": self.title,
            "artists": list(self.artists),
            "artists_name": self.artists_name,
            "album": self.album,
            "duration_ms": self.duration_ms,
            "cover_uri": self.cover_uri,
            "cover_url": self.cover_url(),
            "available": self.available,
            "explicit": self.explicit,
            "liked": self.liked,
            "source": self.source,
        }

    @classmethod
    def from_track(cls, track: Any, liked: bool = False, source: str = "") -> "WaveTrack | None":
        """Build from a :class:`yandex_music.Track` (or any object alike)."""
        if track is None:
            return None
        raw_id = getattr(track, "id", None)
        if raw_id is None:
            return None
        track_id = _text(getattr(track, "track_id", None)) or str(raw_id)
        albums = list(getattr(track, "albums", None) or ())
        album = albums[0] if albums else None
        artists = tuple(
            name
            for name in (
                _text(getattr(item, "name", None)) for item in (getattr(track, "artists", None) or ())
            )
            if name
        )
        available = getattr(track, "available", None)
        duration = getattr(track, "duration_ms", None)
        explicit = getattr(track, "explicit", None)
        return cls(
            id=str(raw_id),
            track_id=track_id,
            title=_text(getattr(track, "title", None)) or "Без названия",
            artists=artists,
            album=_text(getattr(album, "title", None)),
            duration_ms=int(duration or 0),
            cover_uri=_text(getattr(track, "cover_uri", None)) or None,
            og_image=_text(getattr(track, "og_image", None)) or None,
            available=True if available is None else bool(available),
            explicit=bool(explicit),
            liked=bool(liked),
            source=source,
        )

    @classmethod
    def from_sequence(cls, item: Any, source: str = "") -> "WaveTrack | None":
        """Build from a ``Sequence`` item, skipping ads and unusable entries."""
        track = getattr(item, "track", None)
        if track is None and getattr(item, "id", None) is not None:
            track = item
        if track is None:
            return None
        return cls.from_track(track, liked=bool(getattr(item, "liked", False)), source=source)


@dataclass(frozen=True)
class CatalogItem:
    """A non-track entity (album, artist, playlist) shown in the GUI."""

    id: str
    title: str
    subtitle: str = ""
    kind: str = ""
    cover_url: str | None = None
    year: int = 0
    track_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "kind": self.kind,
            "cover_url": self.cover_url,
            "year": self.year,
            "track_count": self.track_count,
        }

    @classmethod
    def from_album(cls, album: Any) -> "CatalogItem | None":
        raw_id = _text(getattr(album, "id", None))
        if not raw_id:
            return None
        artists = ", ".join(
            name
            for name in (
                _text(getattr(item, "name", None)) for item in (getattr(album, "artists", None) or ())
            )
            if name
        )
        year = 0
        track_count = 0
        if getattr(album, "release_date", None) is not None:
            year = _text(getattr(album.release_date, "year", "")) or "0"
            try:
                year = int(year)
            except ValueError:
                year = 0
        try:
            track_count = int(getattr(album, "track_count", 0) or 0)
        except (TypeError, ValueError):
            track_count = 0
        return cls(
            id=raw_id,
            title=_text(getattr(album, "title", None)) or "Без названия",
            subtitle=artists,
            kind="album",
            cover_url=track_cover_url(album),
            year=year,
            track_count=track_count,
        )

    @classmethod
    def from_artist(cls, artist: Any) -> "CatalogItem | None":
        raw_id = _text(getattr(artist, "id", None))
        if not raw_id:
            return None
        return cls(
            id=raw_id,
            title=_text(getattr(artist, "name", None)) or "Без имени",
            kind="artist",
            cover_url=track_cover_url(artist),
        )

    @classmethod
    def from_playlist(cls, playlist: Any) -> "CatalogItem | None":
        raw_id = _text(getattr(playlist, "id", None)) or _text(getattr(playlist, "kind", None))
        if not raw_id:
            return None
        try:
            track_count = int(getattr(playlist, "track_count", 0) or 0)
        except (TypeError, ValueError):
            track_count = 0
        owner = _text(getattr(playlist, "owner", None) and getattr(playlist.owner, "name", None))
        return cls(
            id=raw_id,
            title=_text(getattr(playlist, "title", None)) or "Без названия",
            subtitle=owner,
            kind="playlist",
            cover_url=track_cover_url(playlist),
            track_count=track_count,
        )


@dataclass(frozen=True)
class SearchResults:
    """One page of ``client.search`` reduced to what the GUI can render."""

    query: str
    tracks: tuple[WaveTrack, ...] = ()
    albums: tuple[CatalogItem, ...] = ()
    artists: tuple[CatalogItem, ...] = ()
    playlists: tuple[CatalogItem, ...] = ()

    @property
    def total(self) -> int:
        return len(self.tracks) + len(self.albums) + len(self.artists) + len(self.playlists)

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "tracks": [item.to_dict() for item in self.tracks],
            "albums": [item.to_dict() for item in self.albums],
            "artists": [item.to_dict() for item in self.artists],
            "playlists": [item.to_dict() for item in self.playlists],
        }


LIKED_SECTIONS = ("tracks", "albums", "artists", "playlists")


def _iter_section(raw: Any, name: str) -> list[Any]:
    section = getattr(raw, name, None)
    if section is None and isinstance(raw, dict):
        section = raw.get(name)
    return list(section or ())


def search_results(query: str, raw: Any) -> SearchResults:
    """Convert a ``Search`` response into :class:`SearchResults`."""
    tracks = tuple(
        item
        for item in (WaveTrack.from_track(track, source="search") for track in _iter_section(raw, "tracks"))
        if item is not None
    )
    albums = tuple(
        item
        for item in (CatalogItem.from_album(album) for album in _iter_section(raw, "albums"))
        if item is not None
    )
    artists = tuple(
        item
        for item in (CatalogItem.from_artist(artist) for artist in _iter_section(raw, "artists"))
        if item is not None
    )
    playlists = tuple(
        item
        for item in (CatalogItem.from_playlist(playlist) for playlist in _iter_section(raw, "playlists"))
        if item is not None
    )
    return SearchResults(
        query=query,
        tracks=tracks,
        albums=albums,
        artists=artists,
        playlists=playlists,
    )


def liked_items(section: str, raw: Any) -> tuple[WaveTrack, ...] | tuple[CatalogItem, ...]:
    """Convert a ``users_likes_*`` response into renderable items."""
    if _text(section) == "tracks":
        return tuple(
            item
            for item in (
                WaveTrack.from_track(track, liked=True, source="likes")
                for track in _iter_section(raw, "tracks")
            )
            if item is not None
        )
    builders = {
        "albums": CatalogItem.from_album,
        "artists": CatalogItem.from_artist,
        "playlists": CatalogItem.from_playlist,
    }
    builder = builders.get(_text(section), CatalogItem.from_album)
    return tuple(
        item for item in (builder(entry) for entry in _iter_section(raw, _text(section))) if item is not None
    )


@dataclass(frozen=True)
class StreamLink:
    """A resolved direct audio link."""

    track_id: str
    url: str
    codec: str = "mp3"
    bitrate: int = 0
    lossless: bool = False
    preview: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "url": self.url,
            "codec": self.codec,
            "bitrate": self.bitrate,
            "lossless": self.lossless,
            "preview": self.preview,
        }


def _variant_codec(variant: Any) -> str:
    return _text(getattr(variant, "codec", None)).lower() or "mp3"


def _variant_bitrate(variant: Any) -> int:
    try:
        return int(getattr(variant, "bitrate_in_kbps", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _variant_rank(variant: Any) -> tuple[int, int, int]:
    """Sort key for :func:`max`: lossless first, then bitrate, then codec."""
    codec = _variant_codec(variant)
    lossless = codec in LOSSLESS_CODECS
    return (0 if lossless else 1, _variant_bitrate(variant), -CODEC_RANK.get(codec, 9))


def select_variant(
    variants: Iterable[Any],
    quality: str = QUALITY_AUTO,
    allow_lossless: bool = True,
) -> Any | None:
    """Pick the best download variant for ``quality``.

    Lossless variants win when they are allowed and present. For an explicit
    lossy bitrate the matching variant is used, with a graceful fallback to the
    best remaining one when the server does not offer that exact bitrate. Preview
    variants are only selected when nothing else is available.
    """
    items = [item for item in (variants or ()) if item is not None]
    if not items:
        return None
    full = [item for item in items if not bool(getattr(item, "preview", False))]
    pool = full or items
    name = _text(quality).lower()
    wanted = QUALITY_ALIASES.get(name, name or QUALITY_AUTO)
    if not allow_lossless:
        lossy = [item for item in pool if _variant_codec(item) not in LOSSLESS_CODECS]
        pool = lossy or pool

    def best(candidates: list[Any]) -> Any | None:
        return max(candidates, key=_variant_rank) if candidates else None

    def by_bitrate(target: int) -> Any | None:
        exact = [item for item in pool if _variant_bitrate(item) == target]
        if exact:
            return min(exact, key=lambda item: CODEC_RANK.get(_variant_codec(item), 9))
        below = [item for item in pool if 0 < _variant_bitrate(item) <= target]
        if below:
            return best(below)
        above = [item for item in pool if _variant_bitrate(item) > target]
        if above:
            return min(above, key=_variant_bitrate)
        return best(pool)

    if wanted == QUALITY_LOSSLESS and allow_lossless:
        lossless = [item for item in pool if _variant_codec(item) in LOSSLESS_CODECS]
        found = best(lossless)
        if found is not None:
            return found
    elif wanted in QUALITY_BITRATES:
        return by_bitrate(QUALITY_BITRATES[wanted])
    else:
        try:
            target = int(wanted)
        except (TypeError, ValueError):
            target = 0
        if target > 0:
            return by_bitrate(target)

    if allow_lossless:
        lossless = [item for item in pool if _variant_codec(item) in LOSSLESS_CODECS]
        found = best(lossless)
        if found is not None:
            return found
    return best(pool)


def stream_link(track_id: str, variant: Any, url: str | None = None) -> StreamLink:
    """Build a :class:`StreamLink` from a selected variant."""
    if url is None:
        getter = getattr(variant, "get_direct_link", None)
        if not callable(getter):
            raise RuntimeError("вариант загрузки не умеет отдавать прямую ссылку")
        url = getter()
    codec = _variant_codec(variant)
    return StreamLink(
        track_id=track_id,
        url=str(url),
        codec=codec,
        bitrate=_variant_bitrate(variant),
        lossless=codec in LOSSLESS_CODECS,
        preview=bool(getattr(variant, "preview", False)),
    )


def normalize_mood_energy(
    mood: str | int | None = None,
    energy: str | int | None = None,
    mood_energy: str | None = None,
) -> str | None:
    """Reduce mood/energy to the single ``moodEnergy`` value the API accepts.

    The station has one combined mood slider, so when both values are supplied
    ``energy`` wins; integers are mapped through the classic 0/1 scale.
    """
    explicit = _text(mood_energy).lower()
    if explicit:
        if explicit not in MOOD_ENERGY_VALUES:
            raise ValueError("неизвестное настроение: " + ", ".join(MOOD_ENERGY_VALUES))
        return explicit
    for value, aliases in ((energy, ENERGY_ALIASES), (mood, MOOD_ALIASES)):
        if value is None:
            continue
        if isinstance(value, bool):
            text = "1" if value else "0"
        else:
            text = str(value).strip().lower()
        if not text:
            continue
        if text.isdigit():
            mapped = aliases.get(int(text))
            if mapped is None:
                raise ValueError("настроение/энергия принимают 0 или 1")
            return mapped
        if text not in MOOD_ENERGY_VALUES:
            raise ValueError("неизвестное настроение: " + ", ".join(MOOD_ENERGY_VALUES))
        return text
    return None


def normalize_language(language: str | None) -> str | None:
    """Map a language alias to the station setting value."""
    if language is None:
        return None
    name = _text(language).lower()
    if not name:
        return None
    resolved = LANGUAGE_ALIASES.get(name, name)
    if resolved not in LANGUAGE_VALUES:
        raise ValueError("неизвестный язык: " + ", ".join(LANGUAGE_VALUES))
    return resolved


def normalize_diversity(diversity: str | None) -> str | None:
    """Validate a diversity setting value."""
    if diversity is None:
        return None
    name = _text(diversity).lower()
    if not name:
        return None
    if name not in DIVERSITY_VALUES:
        raise ValueError("неизвестный режим подбора: " + ", ".join(DIVERSITY_VALUES))
    return name


@dataclass
class _Job:
    tag: str
    call: Callable[[Any], Any]
    on_ok: Callable[[Any], None] = field(default=lambda result: None)
    on_err: Callable[[Exception], None] = field(default=lambda exc: None)


class _YandexWorker(QThread):
    """Runs queued API jobs on a single thread with one shared client."""

    def __init__(
        self,
        token: str | None = None,
        client_factory: ClientFactory | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._factory = client_factory or default_client_factory
        self._token = token
        self._client: Any = None
        self._jobs: deque[_Job] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._jobs)

    def set_token(self, token: str | None) -> None:
        """Install a new token; the client is rebuilt on the worker thread."""
        with self._lock:
            self._token = token
            self._client = None
        self._wake.set()

    @property
    def token(self) -> str | None:
        with self._lock:
            return self._token

    def submit(self, job: _Job) -> bool:
        """Queue a job; returns ``False`` when the worker is stopping."""
        with self._lock:
            if self._stopping:
                return False
            self._jobs.append(job)
        self._wake.set()
        return True

    def _client_for_job(self) -> Any:
        with self._lock:
            token = self._token
            if self._client is None and token:
                self._client = self._factory(token)
            return self._client

    def run(self) -> None:
        while True:
            job: _Job | None = None
            with self._lock:
                if self._jobs:
                    job = self._jobs.popleft()
                elif self._stopping:
                    break
            if job is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            try:
                client = self._client_for_job()
                if client is None:
                    raise RuntimeError("нет авторизации: сначала войдите в аккаунт")
                result = job.call(client)
            except Exception as exc:  # noqa: BLE001 - reported through on_err
                log.debug("job %s failed: %s", job.tag, exc)
                try:
                    job.on_err(exc)
                except Exception:
                    log.exception("error handler for %s crashed", job.tag)
            else:
                try:
                    job.on_ok(result)
                except Exception:
                    log.exception("result handler for %s crashed", job.tag)

    def stop(self, timeout_ms: int = 5000) -> bool:
        """Stop the thread and wait for it; queued jobs are discarded."""
        with self._lock:
            self._stopping = True
            self._jobs.clear()
        self._wake.set()
        if not self.isRunning():
            return True
        return self.wait(timeout_ms)


class YandexService(QObject):
    """Session, «Моя волна» queue, stream links and cover URLs."""

    session_validated = Signal(dict)
    session_error = Signal(str)
    session_cleared = Signal()

    wave_started = Signal(list)
    wave_batch = Signal(list)
    wave_stopped = Signal()
    wave_error = Signal(str)

    track_changed = Signal(object)
    queue_changed = Signal(int)
    feedback_sent = Signal(str, str)
    like_changed = Signal(str, bool)
    dislike_changed = Signal(str, bool)
    like_error = Signal(str, str)
    dislike_error = Signal(str, str)

    settings_applied = Signal(str, str, str)
    preferences_changed = Signal(object)
    search_ready = Signal(object)
    search_failed = Signal(str)
    collection_ready = Signal(str, object)
    collection_failed = Signal(str)
    stream_ready = Signal(object)
    stream_error = Signal(str, str)
    busy_changed = Signal(bool)

    def __init__(
        self,
        token: str | None = None,
        client_factory: ClientFactory | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = _YandexWorker(token, client_factory, self)
        self._lock = threading.RLock()
        self._session: dict[str, Any] = {}
        self._queue: deque[WaveTrack] = deque()
        self._history: list[WaveTrack] = []
        self._current: WaveTrack | None = None
        self._index = -1
        self._batch_id: str | None = None
        self._track_batches: dict[str, str] = {}
        self._station = WAVE_STATION
        self._wave_started = False
        self._prefetching = False
        self._current_event: str | None = None
        self._current_started = False
        self._current_since = 0.0
        self._last_dispatched: str | None = None
        self._settings: tuple[str, str, str] = ("", "", "")
        self._preferences = WaveSettings()
        self._stream_cache: dict[tuple[str, str, bool], tuple[float, StreamLink]] = {}
        self._stream_errors: dict[tuple[str, str, bool], float] = {}
        self._likes: set[str] = set()
        self._dislikes: set[str] = set()
        self._token = token

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread (idempotent)."""
        if not self._worker.isRunning():
            self._worker.start()

    def shutdown(self) -> None:
        """Stop the worker and clear the session state."""
        self._worker.stop()
        with self._lock:
            self._session.clear()
            self._queue.clear()
            self._history.clear()
            self._current = None
            self._index = -1
            self._current_started = False
            self._wave_started = False
            self._batch_id = None
            self._track_batches.clear()
            self._stream_cache.clear()
            self._stream_errors.clear()
        self.wave_stopped.emit()
        self.session_cleared.emit()

    @property
    def is_running(self) -> bool:
        return self._worker.isRunning()

    @property
    def is_authenticated(self) -> bool:
        with self._lock:
            return bool(self._session)

    @property
    def session(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._session)

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def has_plus(self) -> bool:
        with self._lock:
            return bool(self._session.get("has_plus"))

    @property
    def uid(self) -> Any:
        with self._lock:
            return self._session.get("uid")

    @property
    def is_busy(self) -> bool:
        return self._worker.busy

    @property
    def from_field(self) -> str:
        uid = self.uid
        try:
            suffix = int(uid)
        except (TypeError, ValueError):
            suffix = 0
        return f"{FEEDBACK_FROM_PREFIX}-{suffix}"

    # -- session -----------------------------------------------------------

    def set_token(self, token: str | None) -> bool:
        """Install a token and validate it in the background."""
        self._token = token
        self._worker.set_token(token)
        if not token:
            self.clear_session()
            return False
        return self.validate_session()

    def validate_session(self) -> bool:
        """Run ``Client.init()`` and publish the profile."""
        if not self._token:
            self.session_error.emit("Нет токена: войдите в аккаунт")
            return False
        self.start()

        def call(client: Any) -> Any:
            client.init()
            return client

        def ok(client: Any) -> None:
            try:
                profile = AuthService.extract_profile(client)
            except Exception as exc:
                self.session_error.emit(readable_error(exc))
                return
            profile.pop("client", None)
            with self._lock:
                self._session = profile
            log.info("session validated for %s", profile.get("login"))
            self.session_validated.emit(dict(profile))

        def err(exc: Exception) -> None:
            self.session_error.emit(readable_error(exc))

        return self._submit(_Job("validate_session", call, ok, err))

    def clear_session(self) -> None:
        """Forget the session and stop the wave."""
        with self._lock:
            self._session.clear()
            self._queue.clear()
            self._history.clear()
            self._current = None
            self._index = -1
            self._current_started = False
            self._wave_started = False
            self._batch_id = None
            self._track_batches.clear()
        self.session_cleared.emit()
        self.wave_stopped.emit()

    # -- «Моя волна» -------------------------------------------------------

    def start_my_wave(
        self,
        mood: str | int | None = None,
        energy: str | int | None = None,
        language: str | None = None,
        diversity: str | None = None,
        mood_energy: str | None = None,
        activity: str | None = None,
    ) -> bool:
        """Load the personal station queue, applying settings when they change."""
        try:
            preferences = station.resolve_settings(
                mood=mood,
                activity=activity,
                language=language,
                diversity=diversity,
                energy=energy,
                mood_energy=mood_energy,
            )
        except ValueError as exc:
            self.wave_error.emit(str(exc))
            return False
        explicit = any(
            value is not None for value in (mood, energy, activity, mood_energy, language, diversity)
        )
        if not self._token:
            self.wave_error.emit("Нет авторизации: войдите в аккаунт")
            return False
        self.start()
        with self._lock:
            self._station = WAVE_STATION
            self._queue.clear()
            self._history.clear()
            self._current = None
            self._index = -1
            self._batch_id = None
            self._last_dispatched = None
            self._prefetching = False
            self._wave_started = True
            self._preferences = preferences
        self.preferences_changed.emit(preferences)
        if explicit:
            self._apply_station_settings(preferences.to_wire(), restart=False)
        self._send_feedback(FEEDBACK_RADIO_STARTED)
        return self._load_batch(first=True)

    def stop_my_wave(self) -> None:
        """Stop feeding the queue; already downloaded tracks stay playable."""
        with self._lock:
            self._wave_started = False
            self._prefetching = False
        self.wave_stopped.emit()

    def apply_settings(
        self,
        mood_energy: str | None = None,
        diversity: str | None = None,
        language: str | None = None,
        restart: bool = True,
        mood: str | None = None,
        activity: str | None = None,
        energy: str | int | None = None,
    ) -> bool:
        """Change station settings through ``rotor_station_settings2``.

        Only the axes that are passed are touched; ``"all"`` resets an axis back
        to the server default.
        """
        try:
            requested = station.resolve_settings(
                mood=mood,
                activity=activity,
                energy=energy,
                mood_energy=mood_energy,
                language=language,
                diversity=diversity,
            )
        except ValueError as exc:
            self.wave_error.emit(str(exc))
            return False
        if not self._token:
            self.wave_error.emit("Нет авторизации: войдите в аккаунт")
            return False
        self.start()
        with self._lock:
            current = self._preferences
        preferences = WaveSettings(
            mood=requested.mood if (mood is not None or mood_energy is not None) else current.mood,
            activity=requested.activity if (activity is not None or energy is not None) else current.activity,
            language=requested.language if language is not None else current.language,
            diversity=requested.diversity if diversity is not None else current.diversity,
        )
        with self._lock:
            self._preferences = preferences
        self.preferences_changed.emit(preferences)
        self._apply_station_settings(preferences.to_wire(), restart=restart)
        return True

    def _apply_station_settings(self, target: tuple[str, str, str], restart: bool) -> bool:
        mood_value, diversity_value, language_value = target
        mood_value = mood_value or "all"
        language_value = language_value or DEFAULT_LANGUAGE
        diversity_value = diversity_value or DEFAULT_DIVERSITY
        station_id = self._station

        def call(client: Any) -> Any:
            return client.rotor_station_settings2(
                station_id,
                mood_value,
                diversity_value,
                language=language_value,
            )

        def ok(_result: Any) -> None:
            with self._lock:
                self._settings = (mood_value, diversity_value, language_value)
            self.settings_applied.emit(mood_value, diversity_value, language_value)
            if restart:
                self._load_batch(first=True)

        def err(exc: Exception) -> None:
            self.wave_error.emit(readable_error(exc))

        return self._submit(_Job("station_settings", call, ok, err))

    def _load_batch(self, first: bool = False, force: bool = False) -> bool:
        with self._lock:
            if not self._wave_started:
                return False
            if self._prefetching and not force:
                return False
            self._prefetching = True
            station = self._station
            queue_id = self._last_dispatched
        self._emit_busy(True)

        def call(client: Any) -> Any:
            return client.rotor_station_tracks(station, settings2=True, queue=queue_id)

        def ok(result: Any) -> None:
            self._emit_busy(False)
            with self._lock:
                self._prefetching = False
                if result is None:
                    self.wave_error.emit("Станция вернула пустой ответ")
                    return
                batch = _text(getattr(result, "batch_id", None)) or None
                if batch:
                    self._batch_id = batch
                items = list(getattr(result, "sequence", None) or ())
            tracks = self._append_tracks(items, source=station, batch_id=batch or "")
            if not tracks:
                self.wave_error.emit("Станция не вернула доступных треков")
                return
            if first:
                current = self._dispatch_first()
                self.wave_started.emit(self.queue_snapshot())
                if current is not None:
                    self.track_changed.emit(current)
                    self._send_feedback(FEEDBACK_TRACK_STARTED, current)
            else:
                self.wave_batch.emit(list(tracks))
            self._emit_queue_state()

        def err(exc: Exception) -> None:
            self._emit_busy(False)
            with self._lock:
                self._prefetching = False
            self.wave_error.emit(readable_error(exc))

        return self._submit(_Job("station_tracks", call, ok, err))

    def _append_tracks(self, items: Iterable[Any], source: str, batch_id: str = "") -> list[WaveTrack]:
        added: list[WaveTrack] = []
        with self._lock:
            known = {item.track_id for item in self._queue}
            known.update(item.track_id for item in self._history[-MAX_HISTORY:])
            for item in items:
                track = WaveTrack.from_sequence(item, source=source)
                if track is None or not track.available:
                    continue
                if track.track_id in known:
                    continue
                known.add(track.track_id)
                self._queue.append(track)
                if batch_id:
                    self._track_batches[track.track_id] = batch_id
                added.append(track)
            if len(self._track_batches) > MAX_HISTORY * 4:
                for stale in list(self._track_batches)[: MAX_HISTORY * 2]:
                    self._track_batches.pop(stale, None)
        return added

    def _dispatch_first(self) -> WaveTrack | None:
        """Make the first queued track current and start its feedback window."""
        with self._lock:
            if not self._queue:
                return None
            self._index = 0
            self._current = self._queue[0]
            self._remember_history(self._current)
            self._current_event = None
            self._current_started = False
            self._current_since = time.monotonic()
            return self._current

    def _remember_history(self, track: WaveTrack) -> None:
        self._history.append(track)
        if len(self._history) > MAX_HISTORY:
            del self._history[:-MAX_HISTORY]

    def _mark_current(self, track: WaveTrack | None) -> None:
        """Reset per-track feedback state after the current track changed."""
        with self._lock:
            self._current_event = None
            self._current_started = False
            self._current_since = time.monotonic() if track is not None else 0.0

    def maybe_prefetch(self, force: bool = False) -> bool:
        """Load the next batch when the queue is running low."""
        with self._lock:
            if not self._wave_started:
                return False
            if not force and self.remaining() > PREFETCH_THRESHOLD:
                return False
        return self._load_batch(force=force)

    # -- search and collection ----------------------------------------------

    def search(self, query: str, page: int = 0) -> bool:
        """Search tracks, albums, artists and playlists; results arrive async."""
        text = _text(query)
        if not text:
            self.search_failed.emit("Введите поисковый запрос")
            return False
        if not self._token:
            self.search_failed.emit("Нет авторизации: войдите в аккаунт")
            return False
        self.start()
        number = max(0, int(page))

        def call(client: Any) -> Any:
            return client.search(text, page=number)

        def ok(result: Any) -> None:
            self.search_ready.emit(search_results(text, result))

        def err(exc: Exception) -> None:
            self.search_failed.emit(readable_error(exc))

        return self._submit(_Job("search", call, ok, err))

    def load_liked(self, section: str = "tracks") -> bool:
        """Load a liked section of the current account."""
        key = _text(section) or "tracks"
        if key not in LIKED_SECTIONS:
            self.collection_failed.emit("Неизвестный раздел коллекции: " + key)
            return False
        if not self._token:
            self.collection_failed.emit("Нет авторизации: войдите в аккаунт")
            return False
        with self._lock:
            uid = self._session.get("uid")
        if not uid:
            self.collection_failed.emit("Нет авторизации: войдите в аккаунт")
            return False
        self.start()
        method = f"users_likes_{key}"

        def call(client: Any) -> Any:
            return getattr(client, method)(uid)

        def ok(result: Any) -> None:
            self.collection_ready.emit(key, liked_items(key, result))

        def err(exc: Exception) -> None:
            self.collection_failed.emit(readable_error(exc))

        return self._submit(_Job("load_liked", call, ok, err))

    # -- queue -------------------------------------------------------------

    def current_track(self) -> WaveTrack | None:
        with self._lock:
            return self._current

    def queue_snapshot(self) -> list[WaveTrack]:
        with self._lock:
            return list(self._queue)

    def history(self) -> list[WaveTrack]:
        with self._lock:
            return list(self._history)

    def remaining(self) -> int:
        with self._lock:
            return max(0, len(self._queue) - self._index - 1)

    def position(self) -> int:
        with self._lock:
            return self._index

    def is_liked(self, track: Any) -> bool:
        key = _track_key(track)
        with self._lock:
            return key in self._likes

    def is_disliked(self, track: Any) -> bool:
        key = _track_key(track)
        with self._lock:
            return key in self._dislikes

    def next_track(
        self,
        played_seconds: float | None = None,
        completed: bool | None = None,
    ) -> WaveTrack | None:
        """Advance the queue, reporting feedback for the track being left."""
        current = self.current_track()
        if current is not None and self._wave_started:
            if completed is None:
                completed = self._looks_completed(current, played_seconds)
            self._report_event(current, played_seconds, completed)
        with self._lock:
            self._index += 1
            if self._index < 0:
                self._index = 0
            self._current = self._queue[self._index] if self._index < len(self._queue) else None
            if self._current is not None:
                self._remember_history(self._current)
            nxt = self._current
        self._mark_current(nxt)
        self._emit_queue_state()
        if nxt is None:
            self.maybe_prefetch(force=True)
            return None
        if self._wave_started:
            self._send_feedback(FEEDBACK_TRACK_STARTED, nxt)
        self.track_changed.emit(nxt)
        self.maybe_prefetch()
        return nxt

    def skip(self, played_seconds: float | None = None) -> WaveTrack | None:
        """Report a skip for the current track and move on."""
        current = self.current_track()
        if current is not None:
            self._report_event(current, played_seconds, completed=False)
        return self.next_track(played_seconds=played_seconds, completed=False)

    def previous_track(self) -> WaveTrack | None:
        """Return to the previously dispatched track without new feedback."""
        with self._lock:
            if len(self._history) < 2:
                return self._current
            self._history.pop()
            self._index = max(0, self._index - 1)
            self._current = self._queue[self._index] if self._index < len(self._queue) else None
            current = self._current
        self._mark_current(current)
        self._emit_queue_state()
        if current is not None:
            self.track_changed.emit(current)
        return current

    def resume_queue(self) -> WaveTrack | None:
        """Dispatch the first track appended after the queue was exhausted.

        :meth:`next_track` walks the index forward and leaves it one past the
        last item when it runs dry, so a later batch lands exactly at the
        cursor. This picks that item up and reports it as started.
        """
        with self._lock:
            if self._current is not None or self._index < 0 or self._index >= len(self._queue):
                return self._current
            self._current = self._queue[self._index]
            current = self._current
            self._remember_history(current)
        self._mark_current(current)
        self._emit_queue_state()
        if self._wave_started:
            self._send_feedback(FEEDBACK_TRACK_STARTED, current)
        self.track_changed.emit(current)
        self.maybe_prefetch()
        return current

    def track_played(self, played_seconds: float | None = None) -> bool:
        """Report that the current track played to the end (``trackFinished``)."""
        current = self.current_track()
        if current is None:
            return False
        return self._report_event(current, played_seconds, completed=True)

    def track_started(self, track: Any = None) -> bool:
        """Report ``trackStarted`` for ``track`` (or the current one).

        Sending it twice for the same track is pointless and the station
        dislikes duplicates, so a repeat request for the current track is a
        no-op until the queue moves on. Returns whether a request was queued.
        """
        target = self._resolve_track(track)
        if target is None:
            return False
        with self._lock:
            if (
                self._current is not None
                and self._current.track_id == target.track_id
                and self._current_started
            ):
                return False
        return self._send_feedback(FEEDBACK_TRACK_STARTED, target)

    def track_skipped(self, track: Any = None, played_seconds: float | None = None) -> bool:
        """Report that ``track`` was left before the end (``skip``)."""
        target = self._resolve_track(track)
        if target is None:
            return False
        return self._report_event(target, played_seconds, completed=False)

    def position_changed(self, index: int) -> None:
        """Sync the queue position with the player and prefetch when needed."""
        with self._lock:
            if 0 <= index < len(self._queue):
                self._index = index
                self._current = self._queue[index]
                moved = self._current
            else:
                moved = None
        if moved is not None:
            self._mark_current(moved)
        self._emit_queue_state()
        self.maybe_prefetch()

    def _looks_completed(self, track: WaveTrack, played_seconds: float | None) -> bool:
        seconds = played_seconds
        if seconds is None:
            seconds = max(0.0, time.monotonic() - self._current_since) if self._current_since else 0.0
        if not track.duration_ms:
            return False
        return seconds >= (track.duration_ms / 1000.0) * PLAYED_RATIO

    def _emit_queue_state(self) -> None:
        self.queue_changed.emit(self.remaining())

    def _emit_busy(self, value: bool) -> None:
        self.busy_changed.emit(value)

    # -- feedback ----------------------------------------------------------

    def _report_event(
        self,
        track: WaveTrack,
        played_seconds: float | None,
        completed: bool,
    ) -> bool:
        with self._lock:
            if self._current is not None and self._current.track_id == track.track_id:
                if self._current_event is not None:
                    return False
                self._current_event = FEEDBACK_TRACK_PLAYED if completed else FEEDBACK_SKIP
        event = FEEDBACK_TRACK_PLAYED if completed else FEEDBACK_SKIP
        self._send_feedback(event, track, played_seconds)
        return True

    def _send_feedback(
        self,
        event: str,
        track: WaveTrack | None = None,
        played_seconds: float | None = None,
    ) -> bool:
        """Send one rotor feedback event with a payload the API accepts.

        The API expects an ISO 8601 ``timestamp`` and, for track events, both the
        station ``batch_id`` and a ``track_id``; a message without them is
        rejected as "condition is not met". ``station`` travels in the documented
        ``type:tag`` wire form (``user:onyourwave`` is the personal station),
        because the endpoint carries it in the request path.
        """
        if not self._token:
            return False
        if not self._wave_started and event != FEEDBACK_RADIO_STARTED:
            return False
        self.start()
        station = self._station
        origin = self.from_field
        with self._lock:
            batch_id = self._batch_id
            if track is not None:
                batch_id = self._track_batches.get(track.track_id) or batch_id
        track_id = _track_key(track) if track is not None else None
        if event != FEEDBACK_RADIO_STARTED:
            if not batch_id or not track_id:
                log.debug("feedback %s skipped: batch_id=%r track_id=%r", event, batch_id, track_id)
                return False
        seconds = played_seconds
        if seconds is None and track is not None and self._current_since:
            seconds = max(0.0, time.monotonic() - self._current_since)
        total = None if seconds is None else round(float(seconds), 2)
        if track is not None and event in (FEEDBACK_TRACK_PLAYED, FEEDBACK_SKIP):
            with self._lock:
                self._last_dispatched = track.track_id
        if track is not None and event == FEEDBACK_TRACK_STARTED:
            with self._lock:
                if self._current is not None and self._current.track_id == track.track_id:
                    self._current_started = True

        def call(client: Any) -> Any:
            return client.rotor_station_feedback(
                station,
                event,
                timestamp=_feedback_timestamp(),
                from_=origin,
                batch_id=batch_id,
                total_played_seconds=total,
                track_id=track_id,
            )

        def ok(_result: Any) -> None:
            self.feedback_sent.emit(track_id or "", event)

        def err(exc: Exception) -> None:
            log.debug("feedback %s failed: %s", event, exc)

        return self._submit(_Job(f"feedback:{event}", call, ok, err))

    def like(self, track: Any = None) -> bool:
        """Add a like; the server drops a previous dislike for the same track."""
        return self._set_mark(track, add=True, dislike=False)

    def dislike(self, track: Any = None) -> bool:
        """Add a real "do not recommend" mark (not a like removal)."""
        return self._set_mark(track, add=True, dislike=True)

    def remove_like(self, track: Any = None) -> bool:
        """Remove a like."""
        return self._set_mark(track, add=False, dislike=False)

    def remove_dislike(self, track: Any = None) -> bool:
        """Remove a "do not recommend" mark."""
        return self._set_mark(track, add=False, dislike=True)

    def _set_mark(self, track: Any, add: bool, dislike: bool) -> bool:
        """Queue a like/dislike toggle for ``track`` (or the current one)."""
        target = self._resolve_track(track)
        key = target.track_id if target is not None else _track_key(track)
        if target is None:
            self._mark_error(dislike, key, "Трек не выбран")
            return False
        if not self._token:
            self._mark_error(dislike, key, "Нет авторизации: войдите в аккаунт")
            return False
        self.start()
        ids: list[str] = [target.track_id]
        action = "add" if add else "remove"
        name = f"users_{'dislikes' if dislike else 'likes'}_tracks_{action}"
        tag = f"{'dislike' if dislike else 'like'}:{action}"

        def call(client: Any) -> Any:
            return getattr(client, name)(ids)

        def ok(_result: Any) -> None:
            with self._lock:
                mine = self._dislikes if dislike else self._likes
                other = self._likes if dislike else self._dislikes
                if add:
                    mine.add(key)
                    other.discard(key)
                else:
                    mine.discard(key)
            (self.dislike_changed if dislike else self.like_changed).emit(key, add)

        def err(exc: Exception) -> None:
            self._mark_error(dislike, key, readable_error(exc))

        return self._submit(_Job(tag, call, ok, err))

    def _mark_error(self, dislike: bool, key: str, message: str) -> None:
        (self.dislike_error if dislike else self.like_error).emit(key, message)

    def _resolve_track(self, track: Any) -> WaveTrack | None:
        if track is None:
            return self.current_track()
        if isinstance(track, WaveTrack):
            return track
        return WaveTrack.from_track(track)

    # -- streams -----------------------------------------------------------

    def stream_url(
        self,
        track: Any = None,
        quality: str = QUALITY_AUTO,
        allow_lossless: bool | None = None,
    ) -> bool:
        """Resolve a direct audio link for ``track`` in the background."""
        target = self._resolve_track(track)
        if target is None:
            self.stream_error.emit("", "Трек не выбран")
            return False
        if not self._token:
            self.stream_error.emit(target.track_id, "Нет авторизации: войдите в аккаунт")
            return False
        if allow_lossless is None:
            allow_lossless = self.has_plus
        key = (target.track_id, _text(quality).lower() or QUALITY_AUTO, bool(allow_lossless))
        now = time.monotonic()
        with self._lock:
            cached = self._stream_cache.get(key)
            if cached and now - cached[0] < STREAM_CACHE_TTL_S:
                link = cached[1]
                self.stream_ready.emit(link)
                return True
            failed_at = self._stream_errors.get(key)
            if failed_at is not None and now - failed_at < ERROR_CACHE_TTL_S:
                self.stream_error.emit(target.track_id, "Повторная попытка будет позже")
                return False
            self._stream_errors.pop(key, None)
        self.start()

        def call(client: Any) -> Any:
            return client.tracks_download_info(target.track_id, get_direct_links=False)

        def ok(variants: Any) -> None:
            try:
                variant = select_variant(list(variants or ()), quality, allow_lossless)
                if variant is None:
                    raise RuntimeError("сервер не вернул доступных вариантов потока")
                link = stream_link(target.track_id, variant)
            except Exception as exc:
                self._remember_error(key, target.track_id, readable_error(exc))
                return
            with self._lock:
                self._stream_cache[key] = (time.monotonic(), link)
            log.info(
                "stream for %s: %s %s kbps%s",
                target.track_id,
                link.codec,
                link.bitrate,
                " (lossless)" if link.lossless else "",
            )
            self.stream_ready.emit(link)

        def err(exc: Exception) -> None:
            self._remember_error(key, target.track_id, readable_error(exc))

        return self._submit(_Job("stream_url", call, ok, err))

    def _remember_error(self, key: tuple[str, str, bool], track_id: str, message: str) -> None:
        with self._lock:
            self._stream_errors[key] = time.monotonic()
        self.stream_error.emit(track_id, message)

    def cached_stream(self, track: Any, quality: str = QUALITY_AUTO) -> StreamLink | None:
        """Return a still-valid cached link without touching the network.

        An exact ``(quality, allow_lossless)`` entry wins; otherwise any fresh
        entry for the same track is accepted, the most recent one first.
        """
        target = self._resolve_track(track)
        if target is None:
            return None
        wanted = _text(quality).lower() or QUALITY_AUTO
        now = time.monotonic()
        with self._lock:
            fresh = [
                (stamp, link)
                for (track_id, _quality, _lossless), (stamp, link) in self._stream_cache.items()
                if track_id == target.track_id and now - stamp < STREAM_CACHE_TTL_S
            ]
            if not fresh:
                return None
            exact = [
                item
                for item in self._stream_cache.items()
                if item[0][0] == target.track_id
                and item[0][1] == wanted
                and now - item[1][0] < STREAM_CACHE_TTL_S
            ]
            if exact:
                return exact[0][1][1]
            return max(fresh, key=lambda item: item[0])[1]

    # -- helpers -----------------------------------------------------------

    def _submit(self, job: _Job) -> bool:
        if not self._worker.submit(job):
            self.job_failed(job.tag, RuntimeError("сервис остановлен"))
            return False
        return True

    def job_failed(self, tag: str, exc: Exception) -> None:
        """Hook for subclasses; the default implementation only logs."""
        log.debug("job %s failed: %s", tag, exc)

    def settings(self) -> tuple[str, str, str]:
        """Current ``(mood_energy, diversity, language)`` applied to the station."""
        with self._lock:
            return self._settings

    def preferences(self) -> WaveSettings:
        """The validated mood/activity/language/diversity selection."""
        with self._lock:
            return self._preferences


def _feedback_timestamp() -> str:
    """Current UTC time in the ISO 8601 form expected by the rotor feedback API."""
    return datetime.now(timezone.utc).isoformat()


def _track_key(track: Any) -> str:
    if track is None:
        return ""
    if isinstance(track, WaveTrack):
        return track.track_id
    if isinstance(track, str):
        return track.strip()
    if isinstance(track, int):
        return str(track)
    if isinstance(track, dict):
        return _text(track.get("track_id") or track.get("id"))
    return _text(getattr(track, "track_id", None)) or _text(getattr(track, "id", None))


def readable_error(exc: Exception) -> str:
    """Translate a library exception into a Russian user-facing message."""
    name = type(exc).__name__
    text = _text(str(exc))
    lowered = f"{name} {text}".lower()
    if "unauthorized" in lowered or "401" in lowered:
        return "Токен отклонён сервером. Пройдите авторизацию заново."
    if "forbidden" in lowered or "403" in lowered:
        return "Доступ запрещён: у токена нет прав на Яндекс Музыку."
    if "notfound" in lowered or "404" in lowered:
        return "Трек или станция не найдены."
    if "badrequest" in lowered or "400" in lowered:
        return "Сервер отклонил запрос. Попробуйте изменить настройки станции."
    if "timeout" in lowered or "timed out" in lowered:
        return "Нет связи с сервером Яндекс Музыки. Проверьте подключение."
    if "connection" in lowered or "network" in lowered:
        return "Не удалось соединиться с Яндекс Музыкой. Проверьте подключение."
    if "bitrate" in lowered:
        return "Запрошенное качество недоступно для этого трека."
    if "do not recommend" in lowered or "not available" in lowered:
        return "Трек недоступен из-за ограничений Яндекс Музыки."
    if text:
        return f"Ошибка Яндекс Музыки: {text}"
    return f"Ошибка Яндекс Музыки ({name})"


__all__ = [
    "COVER_SIZES",
    "CatalogItem",
    "DIVERSITY_LABELS",
    "DIVERSITY_VALUES",
    "FEEDBACK_RADIO_STARTED",
    "FEEDBACK_SKIP",
    "FEEDBACK_TRACK_PLAYED",
    "FEEDBACK_TRACK_STARTED",
    "LANGUAGE_LABELS",
    "LANGUAGE_VALUES",
    "LIKED_SECTIONS",
    "MOOD_ENERGY_LABELS",
    "MOOD_ENERGY_VALUES",
    "PREFETCH_THRESHOLD",
    "QUALITY_AUTO",
    "QUALITY_FLAC",
    "QUALITY_LOSSLESS",
    "SearchResults",
    "StreamLink",
    "WAVE_STATION",
    "WaveSettings",
    "WaveTrack",
    "YandexService",
    "cover_size",
    "default_client_factory",
    "liked_items",
    "normalize_diversity",
    "normalize_language",
    "normalize_mood_energy",
    "readable_error",
    "search_results",
    "select_variant",
    "stream_link",
    "track_cover_url",
]
