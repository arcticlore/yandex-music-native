"""Search page with debounced queries."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from yamusic.api.service import YandexApi
from yamusic.models import SearchResults
from yamusic.ui.widgets.track_table import TrackTableView


class SearchPage(QWidget):
    """Type-ahead search across tracks/artists/albums/playlists."""

    play_tracks_requested = Signal(list, int)  # tracks, index
    open_artist_requested = Signal(str)
    open_album_requested = Signal(str)
    open_playlist_requested = Signal(str)

    def __init__(self, api: YandexApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._results: SearchResults | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._do_search)
        self._request_id: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Поиск")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        self.query = QLineEdit()
        self.query.setPlaceholderText("Треки, артисты, альбомы, плейлисты…")
        self.query.setClearButtonEnabled(True)
        root.addWidget(self.query)

        self.tabs = QTabWidget()
        self.track_view = TrackTableView()
        self.artist_list = QListWidget()
        self.album_list = QListWidget()
        self.playlist_list = QListWidget()
        self.artist_list.itemDoubleClicked.connect(self._artist_activated)
        self.album_list.itemDoubleClicked.connect(self._album_activated)
        self.playlist_list.itemDoubleClicked.connect(self._playlist_activated)
        self.tabs.addTab(self.track_view, "Треки")
        self.tabs.addTab(self.artist_list, "Артисты")
        self.tabs.addTab(self.album_list, "Альбомы")
        self.tabs.addTab(self.playlist_list, "Плейлисты")
        root.addWidget(self.tabs, 1)

        self.status = QLabel("")
        self.status.setObjectName("Dim")
        root.addWidget(self.status)

        self.track_view.track_activated.connect(self._track_activated)
        self.query.textChanged.connect(self._on_text)

    # -- search -------------------------------------------------------------

    def focus_input(self) -> None:
        self.query.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _on_text(self, text: str) -> None:
        self._debounce.start()
        if not text.strip():
            self._clear_results()
            self.status.setText("")

    def _do_search(self) -> None:
        text = self.query.text().strip()
        if not text:
            return
        self.status.setText("Поиск…")
        tag = self._api.search(text, self._on_results, self._on_error)
        self._request_id = tag

    def _on_results(self, results: object) -> None:
        if not isinstance(results, SearchResults):
            return
        self._results = results
        self.track_view.model().set_tracks(results.tracks)
        self.artist_list.clear()
        for artist in results.artists:
            item = QListWidgetItem(artist.name)
            item.setData(Qt.ItemDataRole.UserRole, artist.id)
            self.artist_list.addItem(item)
        self.album_list.clear()
        for album in results.albums:
            label = f"{album.title} — {album.artist_line}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, album.id)
            self.album_list.addItem(item)
        self.playlist_list.clear()
        for pl in results.playlists:
            item = QListWidgetItem(f"{pl.title} · {pl.track_count}")
            item.setData(Qt.ItemDataRole.UserRole, pl.id)
            self.playlist_list.addItem(item)
        total = len(results.tracks) + len(results.artists) + len(results.albums) + len(results.playlists)
        self.status.setText("Ничего не найдено" if total == 0 else f"Найдено: {total}")
        self.tabs.setTabText(0, f"Треки ({len(results.tracks)})")
        self.tabs.setTabText(1, f"Артисты ({len(results.artists)})")
        self.tabs.setTabText(2, f"Альбомы ({len(results.albums)})")
        self.tabs.setTabText(3, f"Плейлисты ({len(results.playlists)})")

    def _on_error(self, message: str) -> None:
        self.status.setText(message)

    def _clear_results(self) -> None:
        self.track_view.model().set_tracks([])
        self.artist_list.clear()
        self.album_list.clear()
        self.playlist_list.clear()

    # -- activation ---------------------------------------------------------

    def _track_activated(self, row: int) -> None:
        tracks = self.track_view.model().tracks()
        if 0 <= row < len(tracks):
            self.play_tracks_requested.emit(tracks, row)

    def _artist_activated(self, item: QListWidgetItem) -> None:
        artist_id = item.data(Qt.ItemDataRole.UserRole)
        if artist_id:
            self.open_artist_requested.emit(str(artist_id))

    def _album_activated(self, item: QListWidgetItem) -> None:
        album_id = item.data(Qt.ItemDataRole.UserRole)
        if album_id:
            self.open_album_requested.emit(str(album_id))

    def _playlist_activated(self, item: QListWidgetItem) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid:
            self.open_playlist_requested.emit(str(pid))

    def set_current_track(self, track_id: str | None) -> None:
        self.track_view.select_track_id(track_id)

    def set_liked(self, track_id: str, liked: bool) -> None:
        self.track_view.model().set_liked(track_id, liked)
