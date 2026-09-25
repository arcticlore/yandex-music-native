"""Transport-layer DTOs.

UI and services exchange these dataclasses; ``yandex-music`` model objects
are kept only in ``raw`` for follow-up API calls and never touched by widgets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from yamusic.constants import COVER_SIZE


def cover_url(uri: str | None, size: str = COVER_SIZE) -> str | None:
    """Convert Yandex ``cover_uri`` ('avatars.yandex.net/.../%%') to a URL."""
    if not uri:
        return None
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    return f"https://{uri.replace('%%', size)}"


@dataclass(slots=True)
class TrackInfo:
    """Normalized track."""

    id: str
    title: str
    artists: list[str]
    album: str
    duration_ms: int
    cover_url: str | None = None
    available: bool = True
    liked: bool = False
    lyrics_available: bool = False
    album_id: str | None = None
    explicit: bool = False
    raw: Any = field(default=None, repr=False, compare=False)

    @property
    def artist_line(self) -> str:
        return ", ".join(self.artists) if self.artists else "Unknown artist"

    @property
    def duration_text(self) -> str:
        total = max(0, self.duration_ms) // 1000
        return f"{total // 60}:{total % 60:02d}"


@dataclass(slots=True)
class PlaylistInfo:
    id: str
    title: str
    owner: str
    track_count: int
    duration_ms: int
    cover_url: str | None = None
    description: str = ""
    raw: Any = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class ArtistInfo:
    id: str
    name: str
    cover_url: str | None = None
    raw: Any = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class AlbumInfo:
    id: str
    title: str
    artists: list[str]
    year: int | None
    track_count: int
    cover_url: str | None = None
    raw: Any = field(default=None, repr=False, compare=False)

    @property
    def artist_line(self) -> str:
        return ", ".join(self.artists)


@dataclass(slots=True)
class StationInfo:
    id: str
    name: str
    category: str  # id_for_from: personal | genre | mood | activity ...
    description: str = ""
    raw: Any = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class RotorBatch:
    tracks: list[TrackInfo]
    batch_id: str


@dataclass(slots=True)
class SearchResults:
    query: str
    tracks: list[TrackInfo] = field(default_factory=list)
    artists: list[ArtistInfo] = field(default_factory=list)
    albums: list[AlbumInfo] = field(default_factory=list)
    playlists: list[PlaylistInfo] = field(default_factory=list)


@dataclass(slots=True)
class LyricLine:
    start_ms: int
    end_ms: int
    text: str


@dataclass(slots=True)
class LyricDocument:
    track_id: str
    lines: list[LyricLine] = field(default_factory=list)
    plain_text: str = ""

    @property
    def synced(self) -> bool:
        return bool(self.lines) and any(line.start_ms > 0 for line in self.lines)

    def line_at(self, position_ms: int) -> int:
        """Index of the active line for karaoke highlighting, -1 if none."""
        best = -1
        for i, line in enumerate(self.lines):
            if line.start_ms <= position_ms <= line.end_ms:
                return i
            if line.start_ms <= position_ms:
                best = i
        return best


# ---------------------------------------------------------------------------
# Converters: yandex-music models -> DTOs
# ---------------------------------------------------------------------------

def _artist_names(artists: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for artist in artists:
        name = getattr(artist, "name", None)
        if name:
            names.append(str(name))
    return names


def track_to_dto(track: Any, liked_ids: set[str] | None = None) -> TrackInfo:
    tid = str(track.track_id)
    album = ""
    album_id: str | None = None
    if track.albums:
        album = str(track.albums[0].title or "")
        album_id = str(track.albums[0].id) if track.albums[0].id is not None else None
    return TrackInfo(
        id=tid,
        title=str(track.title or ""),
        artists=_artist_names(track.artists),
        album=album,
        duration_ms=int(track.duration_ms or 0),
        cover_url=cover_url(track.cover_uri) or cover_url(getattr(track, "og_image", None)),
        available=bool(track.available),
        liked=tid in liked_ids if liked_ids is not None else False,
        lyrics_available=bool(track.lyrics_available),
        album_id=album_id,
        explicit=bool(track.explicit),
        raw=track,
    )


def tracks_to_dtos(tracks: Iterable[Any], liked_ids: set[str] | None = None) -> list[TrackInfo]:
    return [track_to_dto(t, liked_ids) for t in tracks if t is not None]


def playlist_to_dto(pl: Any) -> PlaylistInfo:
    uid = getattr(pl, "uid", None)
    kind = getattr(pl, "kind", None)
    pid = str(getattr(pl, "playlist_id", None) or f"{uid}:{kind}")
    owner_obj = getattr(pl, "owner", None)
    owner = str(getattr(owner_obj, "login", None) or getattr(owner_obj, "uid", "") or "")
    cover = None
    cover_obj = getattr(pl, "cover", None)
    if cover_obj is not None:
        cover = cover_url(cover_obj.uri)
    if cover is None:
        cover = cover_url(getattr(pl, "og_image", None))
    return PlaylistInfo(
        id=pid,
        title=str(pl.title or "Без названия"),
        owner=owner,
        track_count=int(pl.track_count or 0),
        duration_ms=int(pl.duration_ms or 0),
        cover_url=cover,
        description=str(pl.description_formatted or pl.description or ""),
        raw=pl,
    )


def artist_to_dto(artist: Any) -> ArtistInfo:
    cover = None
    cover_obj = getattr(artist, "cover", None)
    if cover_obj is not None:
        cover = cover_url(cover_obj.uri)
    if cover is None:
        cover = cover_url(getattr(artist, "og_image", None))
    return ArtistInfo(
        id=str(artist.id),
        name=str(artist.name or ""),
        cover_url=cover,
        raw=artist,
    )


def album_to_dto(album: Any) -> AlbumInfo:
    year = getattr(album, "year", None)
    return AlbumInfo(
        id=str(album.id),
        title=str(album.title or ""),
        artists=_artist_names(album.artists),
        year=int(year) if isinstance(year, int) else None,
        track_count=int(album.track_count or 0),
        cover_url=cover_url(album.cover_uri),
        raw=album,
    )


def station_to_dto(result: Any) -> StationInfo | None:
    station = getattr(result, "station", None)
    if station is None:
        return None
    stype = getattr(station.id, "type", None)
    stag = getattr(station.id, "tag", None)
    if stype and stag:
        sid = f"{stype}:{stag}"
    else:
        sid = str(station.id)
    return StationInfo(
        id=sid,
        name=str(station.name or sid),
        category=str(station.id_for_from or ""),
        description=str(getattr(result, "rup_description", "") or ""),
        raw=result,
    )


# ---------------------------------------------------------------------------
# Lyrics parsers (sync JSON / LRC / plain text)
# ---------------------------------------------------------------------------

_LRC_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]\s?(.*)")


def _ms_from_lrc(minute: str, second: str, frac: str | None) -> int:
    frac_ms = 0
    if frac:
        frac_ms = int(frac.ljust(3, "0")[:3])
    return int(minute) * 60_000 + int(second) * 1000 + frac_ms


def parse_lrc(text: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    for raw in text.splitlines():
        match = _LRC_RE.match(raw.strip())
        if not match:
            continue
        start = _ms_from_lrc(match.group(1), match.group(2), match.group(3))
        content = match.group(4).strip()
        if content:
            lines.append(LyricLine(start_ms=start, end_ms=start + 5_000, text=content))
    for i in range(len(lines) - 1):
        lines[i] = LyricLine(lines[i].start_ms, lines[i + 1].start_ms, lines[i].text)
    if lines:
        last = lines[-1]
        lines[-1] = LyricLine(last.start_ms, last.start_ms + 8_000, last.text)
    return lines


def _lines_from_yandex_json(payload: Any) -> list[LyricLine]:
    """Best-effort extraction of timed lines from Yandex lyrics payloads."""
    candidates: list[Any] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(node, dict):
            if "lines" in node and isinstance(node["lines"], list):
                candidates.extend(node["lines"])
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node[:200]:
                walk(item, depth + 1)

    walk(payload)
    result: list[LyricLine] = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        text = entry.get("lyrics") or entry.get("text") or ""
        if not text:
            continue
        start = entry.get("lineStartMs", entry.get("startMs", entry.get("start")))
        end = entry.get("lineEndMs", entry.get("endMs", entry.get("end")))
        if start is None:
            result.append(LyricLine(0, 0, str(text)))
            continue
        start_i = int(start)
        end_i = int(end) if end is not None else start_i + 5_000
        result.append(LyricLine(start_i, end_i, str(text)))
    return result


def build_lyric_document(
    track_id: str,
    plain_text: str | None,
    sync_payload: Any,
    sync_text: str | None,
) -> LyricDocument:
    lines: list[LyricLine] = []
    if sync_payload is not None:
        lines = _lines_from_yandex_json(sync_payload)
    if not lines and sync_text:
        if "[" in sync_text and _LRC_RE.search(sync_text):
            lines = parse_lrc(sync_text)
        else:
            lines = [LyricLine(0, 0, s) for s in sync_text.splitlines() if s.strip()]
    if not lines and plain_text:
        lines = [LyricLine(0, 0, s) for s in plain_text.splitlines() if s.strip()]
    if not lines and sync_payload is not None and isinstance(sync_payload, str):
        lines = [LyricLine(0, 0, s) for s in sync_payload.splitlines() if s.strip()]
    plain = plain_text or "\n".join(line.text for line in lines)
    return LyricDocument(track_id=track_id, lines=lines, plain_text=plain)
