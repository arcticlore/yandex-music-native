"""Liked tracks page."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from yamusic.api.service import YandexApi
from yamusic.ui.widgets.track_table import TrackTableView


class FavoritesPage(QWidget):
    """User's liked tracks."""

    play_tracks_requested = Signal(list, int)

    def __init__(self, api: YandexApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Любимые треки")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        btn = QPushButton("Обновить")
        btn.clicked.connect(self.reload)
        header.addWidget(btn)
        self.btn_play = QPushButton("▶ Играть всё")
        self.btn_play.setObjectName("Accent")
        self.btn_play.clicked.connect(self._play_all)
        header.addWidget(self.btn_play)
        root.addLayout(header)

        self.view = TrackTableView()
        self.view.track_activated.connect(self._activated)
        root.addWidget(self.view, 1)

        self.status = QLabel("")
        self.status.setObjectName("Dim")
        root.addWidget(self.status)

    def reload(self) -> None:
        self.status.setText("Загрузка…")
        self._api.liked_tracks(self._on_tracks, lambda e: self.status.setText(e))

    def _on_tracks(self, tracks: object) -> None:
        if not isinstance(tracks, list):
            return
        self.view.model().set_tracks(tracks)
        self.status.setText(f"Треков: {len(tracks)}")

    def _activated(self, row: int) -> None:
        tracks = self.view.model().tracks()
        if 0 <= row < len(tracks):
            self.play_tracks_requested.emit(tracks, row)

    def _play_all(self) -> None:
        tracks = self.view.model().tracks()
        if tracks:
            self.play_tracks_requested.emit(tracks, 0)

    def set_current_track(self, track_id: str | None) -> None:
        self.view.select_track_id(track_id)

    def set_liked(self, track_id: str, liked: bool) -> None:
        self.view.model().set_liked(track_id, liked)
