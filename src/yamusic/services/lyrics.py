"""Lyrics loading with karaoke-style synchronization."""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import QObject, Signal

from yamusic.api.service import YandexApi
from yamusic.models import LyricDocument, TrackInfo, build_lyric_document

log = logging.getLogger(__name__)


class LyricsService(QObject):
    """Fetches plain and synchronized lyrics for the current track."""

    lyrics_ready = Signal(str, object)  # track_id, LyricDocument
    lyrics_cleared = Signal()

    def __init__(self, api: YandexApi, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._cache: dict[str, LyricDocument] = {}
        self._current_id: str | None = None
        self._pending: set[str] = set()

    def clear(self) -> None:
        self._current_id = None
        self.lyrics_cleared.emit()

    def request(self, track: TrackInfo | None) -> None:
        if track is None:
            self.clear()
            return
        self._current_id = track.id
        if track.id in self._cache:
            self.lyrics_ready.emit(track.id, self._cache[track.id])
            return
        if track.id in self._pending:
            return
        self._pending.add(track.id)
        wanted = track.id

        def _ok(payload: object) -> None:
            self._pending.discard(wanted)
            if not isinstance(payload, tuple) or len(payload) != 3:
                return
            plain, sync_payload, sync_text = payload
            # sync_text may be a raw JSON string if json.loads failed elsewhere
            if sync_payload is None and isinstance(sync_text, str) and sync_text.lstrip().startswith("{"):
                try:
                    sync_payload = json.loads(sync_text)
                    sync_text = None
                except ValueError:
                    pass
            doc = build_lyric_document(wanted, plain, sync_payload, sync_text)
            if not doc.lines and not doc.plain_text:
                return
            self._cache[wanted] = doc
            if self._current_id == wanted:
                self.lyrics_ready.emit(wanted, doc)

        def _err(_message: str) -> None:
            self._pending.discard(wanted)

        self._api.lyrics(track.id, _ok, _err)
