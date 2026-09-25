"""High-level API facade used by services and pages.

Every public method accepts ``on_ok``/``on_err`` callbacks which are invoked
in the GUI thread. Tags are unique per call; unmatched results fall back to
the ``failed`` signal so the status bar can surface network errors.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine

import aiohttp
from PySide6.QtCore import QObject, Signal
from yandex_music import ClientAsync, DownloadInfo

from yamusic.api.worker import ApiWorker
from yamusic.config import Credentials
from yamusic.constants import QUALITY_LADDER
from yamusic.models import (
    PlaylistInfo,
    RotorBatch,
    SearchResults,
    StationInfo,
    album_to_dto,
    artist_to_dto,
    playlist_to_dto,
    station_to_dto,
    tracks_to_dtos,
)

log = logging.getLogger(__name__)

OnOk = Callable[[Any], None]
OnErr = Callable[[str], None]

_DL_TIMEOUT = aiohttp.ClientTimeout(total=120)
_GET_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def fetch_bytes(url: str, headers: dict[str, str] | None = None) -> bytes:
    """Plain HTTP GET without the Yandex client (covers, lyrics JSON)."""
    async with aiohttp.ClientSession(headers=headers, timeout=_GET_TIMEOUT) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


async def download_file(url: str, dest: Path) -> Path:
    """Stream ``url`` to ``dest`` atomically (.part then rename)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    async with aiohttp.ClientSession(timeout=_DL_TIMEOUT) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            with part.open("wb") as fh:
                async for chunk in resp.content.iter_chunked(256 * 1024):
                    fh.write(chunk)
    part.replace(dest)
    return dest


def _quality_ladder(quality: str) -> tuple[tuple[str, int], ...]:
    if quality == "lossless":
        return (("flac", 320), ("mp3", 320), ("mp3", 192))
    if quality == "320":
        return (("mp3", 320), ("flac", 320), ("mp3", 192))
    if quality == "192":
        return (("mp3", 192), ("mp3", 320))
    return QUALITY_LADDER


def pick_download_info(infos: list[DownloadInfo], quality: str) -> DownloadInfo | None:
    """Choose the best variant according to the user's quality preference."""
    if not infos:
        return None
    for codec, bitrate in _quality_ladder(quality):
        for info in infos:
            if info.codec == codec and info.bitrate_in_kbps >= bitrate:
                return info
            if codec == "flac" and info.codec == "flac":
                return info
    return max(infos, key=lambda i: (i.codec == "flac", i.bitrate_in_kbps))


class YandexApi(QObject):
    """GUI-thread facade over :class:`ApiWorker`."""

    failed = Signal(str)  # unmatched errors for the status bar
    authorized = Signal(int)  # uid
    unauthorized = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.credentials = Credentials()
        self.worker = ApiWorker(self)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self._handlers: dict[str, tuple[OnOk | None, OnErr | None]] = {}
        self.uid: int | None = None
        self.quality: str = "auto"

    # -- lifecycle --------------------------------------------------------

    def start(self, token: str | None = None) -> None:
        self.worker.set_token(token)
        self.worker.start()

    def shutdown(self) -> None:
        self.worker.stop()

    def set_token(self, token: str) -> None:
        self.credentials.save(token)
        self.worker.set_token(token)

    def logout(self) -> None:
        self.credentials.clear()
        self.uid = None
        self.worker.set_token(None)

    # -- dispatcher -------------------------------------------------------

    def submit(
        self,
        factory: Callable[[ClientAsync], Coroutine[Any, Any, Any]],
        on_ok: OnOk | None = None,
        on_err: OnErr | None = None,
    ) -> str:
        tag = uuid.uuid4().hex[:16]
        self._handlers[tag] = (on_ok, on_err)
        if not self.worker.submit(factory, tag):
            self._handlers.pop(tag, None)
            if on_err:
                on_err("API worker is not running")
        return tag

    def _on_success(self, tag: str, result: object) -> None:
        handler = self._handlers.pop(tag, None)
        if handler is None:
            log.debug("unmatched api success: %s", tag)
            return
        on_ok, _ = handler
        if on_ok is not None:
            on_ok(result)

    def _on_failure(self, tag: str, error: str) -> None:
        handler = self._handlers.pop(tag, None)
        message = self._humanize(error)
        if handler is None:
            self.failed.emit(message)
            return
        _, on_err = handler
        if on_err is not None:
            on_err(message)
        else:
            self.failed.emit(message)

    @staticmethod
    def _humanize(error: str) -> str:
        low = error.lower()
        if "unauthorized" in low or "token" in low and "invalid" in low:
            return "Сессия истекла — войдите заново"
        if "network" in low or "connection" in low or "timeout" in low:
            return "Ошибка сети: " + error[:120]
        return error[:200]

    # -- auth -------------------------------------------------------------

    def validate_token(
        self,
        token: str,
        on_ok: OnOk,
        on_err: OnErr,
    ) -> None:
        async def factory(_client: ClientAsync) -> Any:
            probe = self.worker._make_client(token)
            status = await probe.account_status()
            return probe, status

        def _done(result: Any) -> None:
            client, status = result
            uid = getattr(getattr(status, "account", None), "uid", None)
            self.set_token(token)
            if uid is not None:
                self.uid = int(uid)
                self.authorized.emit(self.uid)
            on_ok(status)

        def _fail(error: str) -> None:
            on_err(error)
            if "unauthorized" in error.lower() or "401" in error:
                self.unauthorized.emit()

        self.submit(factory, _done, _fail)

    def device_auth(
        self,
        on_code: Callable[[Any], None],
        on_ok: OnOk,
        on_err: OnErr,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        """OAuth Device Flow: browser confirmation, polling in the worker."""

        async def factory(_client: ClientAsync) -> Any:
            probe = self.worker._make_client(None)
            result = await probe.device_auth(
                on_code=on_code,
                timeout=180,
                should_cancel=should_cancel,
                device_name="Yandex Music Native (Linux)",
            )
            return probe, result

        def _done(result: Any) -> None:
            probe, token_obj = result
            token = getattr(token_obj, "access_token", None)
            if not token:
                on_err("Не удалось получить токен")
                return
            self.set_token(token)
            # validate + emit uid
            async def me(client: ClientAsync) -> Any:
                return await client.account_status()

            def _ok(status: Any) -> None:
                uid = getattr(getattr(status, "account", None), "uid", None)
                if uid is not None:
                    self.uid = int(uid)
                    self.authorized.emit(self.uid)
                on_ok(token)

            self.submit(me, _ok, on_err)

        self.submit(factory, _done, on_err)

    def load_account(self, on_ok: OnOk, on_err: OnErr | None = None) -> None:
        async def factory(client: ClientAsync) -> Any:
            return await client.account_status()

        def _ok(status: Any) -> None:
            uid = getattr(getattr(status, "account", None), "uid", None)
            if uid is not None:
                self.uid = int(uid)
                self.authorized.emit(self.uid)
            on_ok(status)

        self.submit(factory, _ok, on_err or (lambda _e: None))

    # -- rotor ------------------------------------------------------------

    def rotor_stations(self, on_ok: OnOk, on_err: OnErr | None = None) -> None:
        async def factory(client: ClientAsync) -> Any:
            results = await client.rotor_stations_list()
            stations: list[StationInfo] = []
            for item in results:
                dto = station_to_dto(item)
                if dto is not None:
                    stations.append(dto)
            return stations

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def rotor_tracks(
        self,
        station: str,
        on_ok: Callable[[RotorBatch], None],
        on_err: OnErr | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            result = await client.rotor_station_tracks(station, settings2=True)
            if result is None:
                return RotorBatch([], "")
            liked = await self._liked_ids(client)
            tracks = tracks_to_dtos(
                (s.track for s in result.sequence if s.track is not None),
                liked,
            )
            return RotorBatch(tracks=tracks, batch_id=str(result.batch_id))

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def rotor_feedback(
        self,
        station: str,
        type_: str,
        *,
        track_id: str | None = None,
        batch_id: str | None = None,
        total_played_seconds: float | None = None,
        from_: str | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            return await client.rotor_station_feedback(
                station,
                type_,
                timestamp=time.time(),
                track_id=track_id,
                batch_id=batch_id,
                total_played_seconds=total_played_seconds,
                from_=from_,
            )

        self.submit(factory, None, lambda e: log.info("rotor feedback %s: %s", type_, e))

    def rotor_settings(
        self,
        station: str,
        mood_energy: str,
        diversity: str,
        language: str,
        on_ok: OnOk | None = None,
        on_err: OnErr | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            return await client.rotor_station_settings2(
                station, mood_energy=mood_energy, diversity=diversity, language=language
            )

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    # -- likes ------------------------------------------------------------

    def set_like(self, track_id: str, liked: bool, on_ok: OnOk | None = None, on_err: OnErr | None = None) -> None:
        base = track_id.split(":")[0]

        async def factory(client: ClientAsync) -> Any:
            if liked:
                return await client.users_likes_tracks_add([base])
            return await client.users_likes_tracks_remove([base])

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def set_dislike(self, track_id: str, on_ok: OnOk | None = None, on_err: OnErr | None = None) -> None:
        base = track_id.split(":")[0]

        async def factory(client: ClientAsync) -> Any:
            return await client.users_dislikes_tracks_add([base])

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def liked_track_ids(self, on_ok: Callable[[set[str]], None], on_err: OnErr | None = None) -> None:
        async def factory(client: ClientAsync) -> Any:
            return await self._liked_ids(client)

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    async def _liked_ids(self, client: ClientAsync) -> set[str]:
        try:
            listing = await client.users_likes_tracks()
        except Exception:  # favourites may be huge/forbidden — non-fatal
            return set()
        if listing is None:
            return set()
        ids: set[str] = set()
        for short in listing.tracks:
            if short.album_id:
                ids.add(f"{short.id}:{short.album_id}")
            ids.add(str(short.id))
        return ids

    def liked_tracks(
        self,
        on_ok: Callable[[list], None],
        on_err: OnErr | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            listing = await client.users_likes_tracks()
            if listing is None or not listing.tracks:
                return []
            base_ids = [str(s.id) for s in listing.tracks]
            full = await client.tracks(base_ids)
            liked = set()
            for s in listing.tracks:
                liked.add(str(s.id))
                if s.album_id:
                    liked.add(f"{s.id}:{s.album_id}")
            return tracks_to_dtos(full, liked)

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    # -- playlists --------------------------------------------------------

    def playlists(self, on_ok: Callable[[list[PlaylistInfo]], None], on_err: OnErr | None = None) -> None:
        async def factory(client: ClientAsync) -> Any:
            items = await client.users_playlists_list()
            return [playlist_to_dto(p) for p in (items or [])]

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def playlist_tracks(
        self,
        playlist_id: str,
        on_ok: Callable[[list], None],
        on_err: OnErr | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            pl = await client.playlist(playlist_id)
            if pl is None:
                return []
            shorts = pl.tracks or []
            base_ids = [str(s.id) for s in shorts]
            if not base_ids:
                return []
            full = await client.tracks(base_ids)
            liked = await self._liked_ids(client)
            return tracks_to_dtos(full, liked)

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    # -- search / catalog -------------------------------------------------

    def search(self, query: str, on_ok: Callable[[SearchResults], None], on_err: OnErr | None = None) -> None:
        async def factory(client: ClientAsync) -> Any:
            found = await client.search(query, type_="all")
            liked = await self._liked_ids(client)
            results = SearchResults(query=query)
            if found is None:
                return results
            if found.tracks and found.tracks.results:
                results.tracks = tracks_to_dtos(found.tracks.results, liked)
            if found.artists and found.artists.results:
                results.artists = [artist_to_dto(a) for a in found.artists.results]
            if found.albums and found.albums.results:
                results.albums = [album_to_dto(a) for a in found.albums.results]
            if found.playlists and found.playlists.results:
                results.playlists = [playlist_to_dto(p) for p in found.playlists.results]
            return results

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def chart(self, on_ok: Callable[[list], None], on_err: OnErr | None = None) -> None:
        async def factory(client: ClientAsync) -> Any:
            info = await client.chart()
            if info is None or info.chart is None:
                return []
            chart = info.chart
            tracks = getattr(chart, "tracks", None)
            if tracks is None:
                return []
            tracks_list = getattr(tracks, "results", tracks)
            liked = await self._liked_ids(client)
            return tracks_to_dtos(tracks_list, liked)

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def artist_tracks(
        self,
        artist_id: str,
        on_ok: Callable[[list], None],
        on_err: OnErr | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            result = await client.artists_tracks(artist_id, page_size=50)
            if result is None:
                return []
            tracks = getattr(result, "tracks", None) or []
            liked = await self._liked_ids(client)
            return tracks_to_dtos(tracks, liked)

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def album_tracks(
        self,
        album_id: str,
        on_ok: Callable[[list], None],
        on_err: OnErr | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            album = await client.albums_with_tracks(album_id)
            if album is None or not album.volumes:
                return []
            tracks = [t for volume in album.volumes for t in volume]
            liked = await self._liked_ids(client)
            return tracks_to_dtos(tracks, liked)

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    # -- stream resolution ------------------------------------------------

    def stream_url(
        self,
        track_raw: Any,
        quality: str,
        on_ok: Callable[[str], None],
        on_err: OnErr | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            infos = await track_raw.get_download_info_async(get_direct_links=True)
            info = pick_download_info(list(infos or []), quality)
            if info is None:
                raise RuntimeError("нет доступных вариантов загрузки")
            url = await info.get_direct_link_async()
            if not url:
                raise RuntimeError("прямая ссылка не получена")
            codec = info.codec
            return str(url), codec, int(info.bitrate_in_kbps)

        def _ok(payload: Any) -> None:
            on_ok(payload[0])

        self.submit(factory, _ok, on_err or (lambda _e: None))

    def stream_info(
        self,
        track_raw: Any,
        quality: str,
        on_ok: Callable[[tuple[str, str, int]], None],
        on_err: OnErr | None = None,
    ) -> None:
        """Like :meth:`stream_url` but passes ``(url, codec, kbps)``."""

        async def factory(client: ClientAsync) -> Any:
            infos = await track_raw.get_download_info_async(get_direct_links=True)
            info = pick_download_info(list(infos or []), quality)
            if info is None:
                raise RuntimeError("нет доступных вариантов загрузки")
            url = await info.get_direct_link_async()
            if not url:
                raise RuntimeError("прямая ссылка не получена")
            return str(url), str(info.codec), int(info.bitrate_in_kbps)

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    # -- covers / bytes ---------------------------------------------------

    def fetch_cover(self, url: str, on_ok: Callable[[bytes], None], on_err: OnErr | None = None) -> None:
        async def factory(_client: ClientAsync) -> Any:
            return await fetch_bytes(url)

        self.submit(factory, on_ok, on_err or (lambda _e: None))

    def cache_track(
        self,
        track_raw: Any,
        dest: Path,
        quality: str,
        on_ok: OnOk | None = None,
        on_err: OnErr | None = None,
    ) -> None:
        async def factory(client: ClientAsync) -> Any:
            infos = await track_raw.get_download_info_async(get_direct_links=True)
            info = pick_download_info(list(infos or []), quality)
            if info is None:
                raise RuntimeError("нет вариантов для кэша")
            return info, dest

        def _got(payload: Any) -> None:
            info, target = payload
            async def _dl(_c: ClientAsync) -> Any:
                url = await info.get_direct_link_async()
                if not url:
                    raise RuntimeError("нет прямой ссылки для кэширования")
                await download_file(str(url), target)
                return target

            self.submit(_dl, on_ok or (lambda _p: None), on_err or (lambda _e: None))

        self.submit(factory, _got, on_err or (lambda _e: None))

    # -- lyrics ------------------------------------------------------------

    def lyrics(self, track_id: str, on_ok: OnOk, on_err: OnErr | None = None) -> None:
        async def factory(client: ClientAsync) -> Any:
            base = track_id.split(":")[0]
            plain: str | None = None
            sync_payload: Any = None
            sync_text: str | None = None
            try:
                supplement = await client.track_supplement(int(base))
                if supplement is not None and supplement.lyrics is not None:
                    plain = supplement.lyrics.full_lyrics or supplement.lyrics.lyrics
            except Exception as exc:  # lyrics are optional
                log.debug("supplement failed: %s", exc)
            try:
                meta = await client.tracks_lyrics(int(base))
                if meta is not None and meta.download_url:
                    raw = await fetch_bytes(meta.download_url)
                    text = raw.decode("utf-8", errors="replace")
                    try:
                        import json

                        sync_payload = json.loads(text)
                    except ValueError:
                        sync_text = text
            except Exception as exc:
                log.debug("sync lyrics failed: %s", exc)
            return plain, sync_payload, sync_text

        self.submit(factory, on_ok, on_err or (lambda _e: None))
