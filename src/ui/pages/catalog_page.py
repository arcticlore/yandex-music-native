from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QPushButton,
)

from core.playback_controller import PlaybackController, WaveTrack
from ui.widgets.flow_layout import FlowLayout
from ui.widgets.track_list import TrackList


class AlbumView(QWidget):
    back_requested = Signal()

    def __init__(self, controller: PlaybackController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._album: dict[str, Any] = {}
        self._tracks: list[WaveTrack] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(12)

        bar = QHBoxLayout()
        self.back_btn = QPushButton("← Назад")
        self.back_btn.clicked.connect(self.back_requested.emit)
        bar.addWidget(self.back_btn, 0, Qt.AlignmentFlag.AlignLeft)
        bar.addStretch(1)
        root.addLayout(bar)

        head = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setFixedSize(160, 160)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        self.title_lbl = QLabel()
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.artist_btn = QPushButton()
        self.artist_btn.setCursor(Qt.PointingHandCursor)
        self.artist_btn.setStyleSheet("border:none; text-align:left; color:#4da3ff;")
        self.meta_lbl = QLabel()
        self.meta_lbl.setWordWrap(True)
        self.meta_lbl.setStyleSheet("color: #9aa4b2;")
        self.play_all = QPushButton("Слушать альбом")
        self.play_all.clicked.connect(self._play_all)

        info.addWidget(self.title_lbl)
        info.addWidget(self.artist_btn)
        info.addWidget(self.meta_lbl)
        info.addWidget(self.play_all, 0, Qt.AlignmentFlag.AlignLeft)
        info.addStretch(1)
        head.addLayout(info, 1)
        root.addLayout(head)

        self.tracks = TrackList(self)
        root.addWidget(self.tracks, 1)

    def set_album(self, data: dict[str, Any]) -> None:
        self._album = data
        title = data.get("title", "")
        self.title_lbl.setText(str(title))
        artists = data.get("artists") or []
        name = "; ".join(a.get("name", "") for a in artists) if artists else ""
        self.artist_btn.setText(name)
        year = data.get("year") or data.get("release_date") or ""
        tracks = data.get("tracks") or data.get("volumes", [])
        count = len(tracks) if isinstance(tracks, list) else data.get("track_count", 0)
        self.meta_lbl.setText(f"{year} • {count} треков")
        cover = data.get("cover_uri") or data.get("og_image") or ""
        if cover:
            pix = QPixmap()
            pix.loadFromData(b"")
        self._tracks = [WaveTrack.from_track(t) for t in self._flat_tracks(tracks)]
        self.tracks.set_tracks(self._tracks)

    @staticmethod
    def _flat_tracks(tracks: Any) -> list[Any]:
        out: list[Any] = []
        if isinstance(tracks, list):
            for t in tracks:
                if isinstance(t, list):
                    out.extend(t)
                else:
                    out.append(t)
        return out

    def _play_all(self) -> None:
        if self._tracks:
            self._controller.play_playlist(self._tracks)


class ArtistView(QWidget):
    back_requested = Signal()
    album_open_requested = Signal(int | str)
    artist_open_requested = Signal(int | str)

    def __init__(self, controller: PlaybackController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(12)

        bar = QHBoxLayout()
        self.back_btn = QPushButton("← Назад")
        self.back_btn.clicked.connect(self.back_requested.emit)
        bar.addWidget(self.back_btn, 0, Qt.AlignmentFlag.AlignLeft)
        bar.addStretch(1)
        root.addLayout(bar)

        head = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setFixedSize(160, 160)
        head.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.genres_lbl = QLabel()
        self.genres_lbl.setWordWrap(True)
        self.genres_lbl.setStyleSheet("color: #9aa4b2;")
        info.addWidget(self.name_lbl)
        info.addWidget(self.genres_lbl)
        info.addStretch(1)
        head.addLayout(info, 1)
        root.addLayout(head)

        self.pop_lbl = QLabel("Популярные треки")
        root.addWidget(self.pop_lbl)
        self.pop_list = TrackList(self)
        root.addWidget(self.pop_list)

        self.alb_lbl = QLabel("Альбомы")
        root.addWidget(self.alb_lbl)
        self.albums_scroll = QScrollArea()
        self.albums_scroll.setWidgetResizable(True)
        self.albums_wrap = QWidget()
        self.albums_flow = FlowLayout(self.albums_wrap)
        self.albums_scroll.setWidget(self.albums_wrap)
        root.addWidget(self.albums_scroll)

    def set_artist(self, data: dict[str, Any]) -> None:
        artist = data.get("artist") or {}
        self.name_lbl.setText(str(artist.get("name", "")))
        genres = artist.get("genres") or []
        self.genres_lbl.setText(", ".join(map(str, genres)) if genres else "")
        pop = data.get("popular_tracks") or []
        self.pop_list.set_tracks([WaveTrack.from_track(t) for t in pop])
        albums = data.get("albums") or []
        for b in self.albums_wrap.findChildren(QPushButton):
            b.deleteLater()
        for a in albums[:20]:
            aid = a.get("id") or a.get("album_id") or ""
            btn = QPushButton(str(a.get("title", "")))
            btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _e, x=aid: self.album_open_requested.emit(x))
            self.albums_flow.addWidget(btn)
