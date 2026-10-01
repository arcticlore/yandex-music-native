from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QApplication, QMenu

from core.playback_controller import PlaybackController, WaveTrack


class TrackContextMenu(QMenu):
    def __init__(self, controller: PlaybackController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._track: WaveTrack | None = None
        self._meta: dict[str, Any] = {}

        self._play_now = self.addAction("Воспроизвести")
        self._play_next = self.addAction("Воспроизвести следующим")
        self._add_end = self.addAction("В конец очереди")
        self.addSeparator()
        self._go_album = self.addAction("Перейти к альбому")
        self._go_artist = self.addAction("Перейти к исполнителю")
        self.addSeparator()
        self._like = self.addAction("Нравится")
        self._dislike = self.addAction("Не рекомендовать")
        self.addSeparator()
        self._copy_link = self.addAction("Скопировать ссылку")

        self._play_now.triggered.connect(self._on_play_now)
        self._play_next.triggered.connect(self._on_play_next)
        self._add_end.triggered.connect(self._on_add_end)
        self._go_album.triggered.connect(self._on_go_album)
        self._go_artist.triggered.connect(self._on_go_artist)
        self._like.triggered.connect(self._on_like)
        self._dislike.triggered.connect(self._on_dislike)
        self._copy_link.triggered.connect(self._on_copy_link)

    def set_track(self, trk: WaveTrack | None, meta: dict[str, Any] | None = None) -> None:
        self._track = trk
        self._meta = meta or {}
        has = trk is not None
        for a in (self._play_now, self._play_next, self._add_end, self._like, self._dislike, self._copy_link):
            a.setEnabled(has)
        album_ok = bool(self._album_id())
        art_ok = bool(self._artist_id())
        self._go_album.setEnabled(album_ok)
        self._go_artist.setEnabled(art_ok)

    def _album_id(self) -> int | str | None:
        if self._track and getattr(self._track, "album", None):
            pass
        m = self._meta
        aid = (
            m.get("album_id") or m.get("id") or (m.get("albums") or [{}])[0].get("id")
            if m.get("albums")
            else None
        )
        return aid

    def _artist_id(self) -> int | str | None:
        m = self._meta
        arts = m.get("artists") or []
        if arts:
            return arts[0].get("id")
        return None

    def _on_play_now(self) -> None:
        if self._track:
            self._controller.play_playlist([self._track])

    def _on_play_next(self) -> None:
        if self._track:
            self._controller.enqueue_next(self._track)

    def _on_add_end(self) -> None:
        if self._track:
            self._controller.enqueue(self._track)

    def _on_go_album(self) -> None:
        aid = self._album_id()
        if aid:
            self._controller.service.load_album(aid)

    def _on_go_artist(self) -> None:
        aid = self._artist_id()
        if aid:
            self._controller.service.load_artist(aid)

    def _on_like(self) -> None:
        self._controller.like(self._track)

    def _on_dislike(self) -> None:
        self._controller.dislike(self._track)

    def _on_copy_link(self) -> None:
        tid = self._track.track_id if self._track else ""
        aid = self._album_id() or ""
        if tid and aid:
            url = f"https://music.yandex.ru/album/{aid}/track/{tid.split(':')[0] if ':' in tid else tid}"
        else:
            url = "https://music.yandex.ru/"
        QApplication.clipboard().setText(url)
