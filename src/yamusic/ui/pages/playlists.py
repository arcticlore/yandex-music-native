"""Playlists page: user playlists grid + detail track table."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from yamusic.api.service import YandexApi
from yamusic.models import PlaylistInfo
from yamusic.ui.widgets.track_table import TrackTableView


class PlaylistsPage(QWidget):
    """Two-level page: grid of playlists → tracks of the selected one."""

    play_tracks_requested = Signal(list, int)

    def __init__(self, api: YandexApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._playlists: list[PlaylistInfo] = []
        self._current: PlaylistInfo | None = None
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # -- page 0: grid
        grid_page = QWidget()
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("Плейлисты")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        btn_reload = QPushButton("Обновить")
        btn_reload.clicked.connect(self.reload)
        header.addWidget(btn_reload)
        grid_layout.addLayout(header)
        self.grid = QListWidget()
        self.grid.setObjectName("PlaylistGrid")
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setGridSize(QSize(200, 240))
        self.grid.setIconSize(QSize(160, 160))
        self.grid.setWrapping(True)
        self.grid.itemDoubleClicked.connect(self._open_playlist)
        grid_layout.addWidget(self.grid, 1)
        self.grid_status = QLabel("")
        self.grid_status.setObjectName("Dim")
        grid_layout.addWidget(self.grid_status)
        self.stack.addWidget(grid_page)

        # -- page 1: tracks
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)
        back_row = QHBoxLayout()
        self.btn_back = QPushButton("← Назад")
        self.btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        back_row.addWidget(self.btn_back)
        self.detail_title = QLabel("")
        self.detail_title.setObjectName("PageTitle")
        back_row.addWidget(self.detail_title)
        back_row.addStretch(1)
        self.btn_play_all = QPushButton("▶ Играть всё")
        self.btn_play_all.setObjectName("Accent")
        self.btn_play_all.clicked.connect(self._play_all)
        back_row.addWidget(self.btn_play_all)
        detail_layout.addLayout(back_row)
        self.detail_view = TrackTableView()
        self.detail_view.track_activated.connect(self._track_activated)
        detail_layout.addWidget(self.detail_view, 1)
        self.detail_status = QLabel("")
        self.detail_status.setObjectName("Dim")
        detail_layout.addWidget(self.detail_status)
        self.stack.addWidget(detail)

    # -- data ---------------------------------------------------------------

    def reload(self) -> None:
        self.grid_status.setText("Загрузка…")
        self._api.playlists(self._on_playlists, lambda e: self.grid_status.setText(e))

    def _on_playlists(self, playlists: object) -> None:
        if not isinstance(playlists, list):
            return
        self._playlists = [p for p in playlists if isinstance(p, PlaylistInfo)]
        self.grid.clear()
        for pl in self._playlists:
            item = QListWidgetItem(f"{pl.title}\n{pl.track_count} треков · {pl.owner}")
            item.setData(Qt.ItemDataRole.UserRole, pl.id)
            item.setSizeHint(QSize(190, 220))
            self.grid.addItem(item)
        self.grid_status.setText(f"Плейлистов: {len(self._playlists)}")

    def _open_playlist(self, item: QListWidgetItem) -> None:
        pid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        pl = next((p for p in self._playlists if p.id == pid), None)
        if pl is None:
            return
        self._current = pl
        self.detail_title.setText(pl.title)
        self.detail_status.setText("Загрузка треков…")
        self.detail_view.model().set_tracks([])
        self.stack.setCurrentIndex(1)
        self._api.playlist_tracks(pid, self._on_tracks, lambda e: self.detail_status.setText(e))

    def _on_tracks(self, tracks: object) -> None:
        if not isinstance(tracks, list):
            return
        self.detail_view.model().set_tracks(tracks)
        self.detail_status.setText(f"Треков: {len(tracks)}")

    def _track_activated(self, row: int) -> None:
        tracks = self.detail_view.model().tracks()
        if 0 <= row < len(tracks):
            self.play_tracks_requested.emit(tracks, row)

    def _play_all(self) -> None:
        tracks = self.detail_view.model().tracks()
        if tracks:
            self.play_tracks_requested.emit(tracks, 0)

    def set_current_track(self, track_id: str | None) -> None:
        self.detail_view.select_track_id(track_id)

    def set_liked(self, track_id: str, liked: bool) -> None:
        self.detail_view.model().set_liked(track_id, liked)
