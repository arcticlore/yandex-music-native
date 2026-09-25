"""Main window: sidebar + stacked pages + player bar + lyrics dock."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from yamusic.api.service import YandexApi
from yamusic.audio.analyzer import SpectrumAnalyzer
from yamusic.audio.engine import AudioEngine
from yamusic.cache.store import CacheStore
from yamusic.config import Settings
from yamusic.models import LyricDocument, TrackInfo
from yamusic.services.lyrics import LyricsService
from yamusic.services.playback import PlaybackController
from yamusic.services.rotor import RotorService
from yamusic.ui.pages import (
    FavoritesPage,
    LyricsView,
    PlaylistsPage,
    SearchPage,
    SettingsPage,
    WavePage,
)
from yamusic.ui.widgets.player_bar import PlayerBar
from yamusic.ui.widgets.sidebar import Sidebar

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Shell window wiring pages to services."""

    def __init__(
        self,
        api: YandexApi,
        engine: AudioEngine,
        analyzer: SpectrumAnalyzer,
        rotor: RotorService,
        controller: PlaybackController,
        lyrics: LyricsService,
        cache: CacheStore,
        settings: Settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.engine = engine
        self.analyzer = analyzer
        self.rotor = rotor
        self.controller = controller
        self.lyrics_service = lyrics
        self.cache = cache
        self.settings = settings

        self.setWindowTitle("Яндекс Музыка — нативный клиент")
        self.setMinimumSize(1024, 680)
        self.resize(1280, 820)

        # -- central widget: pages + player bar
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.pages = QStackedWidget()
        body_layout.addWidget(self.sidebar)
        body_layout.addWidget(self.pages, 1)
        central_layout.addWidget(body, 1)

        self.player_bar = PlayerBar(controller)
        central_layout.addWidget(self.player_bar)
        self.setCentralWidget(central)

        # -- pages
        self.wave_page = WavePage(controller, rotor)
        self.search_page = SearchPage(api)
        self.playlists_page = PlaylistsPage(api)
        self.favorites_page = FavoritesPage(api)
        self.settings_page = SettingsPage(settings, cache, controller)
        for page in (
            self.wave_page,
            self.search_page,
            self.playlists_page,
            self.favorites_page,
            self.settings_page,
        ):
            self.pages.addWidget(page)

        # -- lyrics dock
        self.lyrics_view = LyricsView()
        dock = QDockWidget("Текст песни", self)
        dock.setObjectName("LyricsDock")
        dock.setWidget(self.lyrics_view)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.hide()
        self._lyrics_dock = dock

        # -- status bar
        self.statusBar().showMessage("Готово")

        self._wire()
        self._shortcuts()
        self.sidebar.select_page("wave")

    # ------------------------------------------------------------------ wire

    def _wire(self) -> None:
        c = self.controller

        # navigation
        self.sidebar.navigated.connect(self._navigate)
        self.pages.setCurrentIndex(0)

        # playback → player bar / pages
        c.track_changed.connect(self._on_track_changed)
        c.cover_ready.connect(self._on_cover_ready)
        c.error.connect(lambda msg: self.statusBar().showMessage(msg, 5000))
        self.api.failed.connect(lambda msg: self.statusBar().showMessage(msg, 4000))
        c.state_changed.connect(
            lambda playing: self.statusBar().showMessage(
                "Воспроизведение" if playing else "Пауза", 1500
            )
        )

        # analyzer → visualizers (60 FPS)
        self.analyzer.spectrum.connect(self.wave_page.feed_spectrum)
        self.analyzer.wave.connect(self.wave_page.feed_wave)

        # player bar → visualizer mode / lyrics
        self.player_bar.visualizer_mode_changed.connect(self._set_visualizer_mode)
        self.player_bar.lyrics_toggled.connect(self._toggle_lyrics)
        self.player_bar.set_visualizer_index(self.settings.options().visualizer_mode)
        self._set_visualizer_mode(self.settings.options().visualizer_mode, persist=False)

        # wave settings apply
        self.wave_page.apply_settings_requested.connect(
            lambda m, d, l: (
                self.rotor.set_settings(m, d, l, restart=True),
                self.statusBar().showMessage("Настройки волны применены", 3000),
            )
        )

        # play requests from pages
        for page in (self.search_page, self.playlists_page, self.favorites_page):
            page.play_tracks_requested.connect(c.play_queue)

        # artist/album drill-down from search
        self.search_page.open_artist_requested.connect(self._open_artist)
        self.search_page.open_album_requested.connect(self._open_album)
        self.search_page.open_playlist_requested.connect(self._open_playlist)

        # likes → update all tables
        c.liked_changed.connect(self._on_liked_changed)

        # lyrics
        c.track_changed.connect(lambda t: self.lyrics_service.request(t))
        self.lyrics_service.lyrics_ready.connect(self._on_lyrics)
        self.lyrics_service.lyrics_cleared.connect(self.lyrics_view.clear)
        c.position_changed.connect(self.lyrics_view.update_position)

        # logout
        self.settings_page.logout_requested.connect(self._logout)

        # rotor errors
        self.rotor.error.connect(lambda m: self.statusBar().showMessage(m, 4000))

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.controller.play_pause)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self.controller.next(True))
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=self.controller.previous)
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, activated=self._vol_up)
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, activated=self._vol_down)
        QShortcut(QKeySequence("Ctrl+F"), self, self._focus_search)
        QShortcut(QKeySequence("Ctrl+L"), self, self._toggle_lyrics)

    def _vol_up(self) -> None:
        self.controller.set_volume(min(100, self.controller.engine.volume + 5))

    def _vol_down(self) -> None:
        self.controller.set_volume(max(0, self.controller.engine.volume - 5))

    def _focus_search(self) -> None:
        self._navigate("search")
        self.search_page.focus_input()

    # ---------------------------------------------------------------- pages

    def _navigate(self, page_id: str) -> None:
        mapping = {
            "wave": self.wave_page,
            "search": self.search_page,
            "playlists": self.playlists_page,
            "favorites": self.favorites_page,
            "settings": self.settings_page,
        }
        page = mapping.get(page_id)
        if page is not None:
            self.pages.setCurrentWidget(page)
            self.sidebar.select_page(page_id)
            if page_id == "settings":
                self.settings_page.refresh_stats()
            if page_id == "playlists" and not self.playlists_page._playlists:
                self.playlists_page.reload()

    def _set_visualizer_mode(self, mode: int, persist: bool = True) -> None:
        self.wave_page.set_visualizer_mode(mode)
        if persist:
            self.settings.set_visualizer_mode(mode)
            self.player_bar.set_visualizer_index(mode)

    def _toggle_lyrics(self) -> None:
        show = not self._lyrics_dock.isVisible()
        self._lyrics_dock.setVisible(show)
        self.player_bar.set_lyrics_checked(show)
        if show:
            track = self.controller.current
            if track is not None:
                self.lyrics_service.request(track)

    # ----------------------------------------------------------- track events

    def _on_track_changed(self, track: TrackInfo | None) -> None:
        if track is None:
            self.lyrics_view.clear()
            self.search_page.set_current_track(None)
            self.playlists_page.set_current_track(None)
            self.favorites_page.set_current_track(None)
            self.wave_page.set_cover_pixmap(None)
            self.player_bar.cover.clear()
            return
        self.statusBar().showMessage(f"{track.artist_line} — {track.title}", 3000)
        self.search_page.set_current_track(track.id)
        self.playlists_page.set_current_track(track.id)
        self.favorites_page.set_current_track(track.id)
        # auto-show lyrics if synced available
        if track.lyrics_available and not self._lyrics_dock.isVisible():
            self._lyrics_dock.setVisible(True)
            self.player_bar.set_lyrics_checked(True)

    def _on_cover_ready(self, track_id: str, path: str) -> None:
        track = self.controller.current
        if track is None or track.id != track_id:
            return
        self.player_bar.set_cover_path(path)
        from PySide6.QtGui import QPixmap

        pm = QPixmap(path)
        if not pm.isNull():
            self.wave_page.set_cover_pixmap(pm)

    def _on_liked_changed(self, track_id: str, liked: bool) -> None:
        for view in (self.search_page, self.playlists_page, self.favorites_page):
            view.set_liked(track_id, liked)

    def _on_lyrics(self, track_id: str, doc: object) -> None:
        track = self.controller.current
        if track is None or track.id != track_id:
            return
        if isinstance(doc, LyricDocument):
            self.lyrics_view.set_document(doc)
            if doc.synced:
                self.lyrics_view.update_position(self.controller.position_ms)

    # -------------------------------------------------------- drill-down ops

    def _open_artist(self, artist_id: str) -> None:
        self.statusBar().showMessage("Загрузка треков артиста…")
        self.api.artist_tracks(artist_id, self._play_resolved, lambda e: self.statusBar().showMessage(e, 4000))

    def _open_album(self, album_id: str) -> None:
        self.statusBar().showMessage("Загрузка треков альбома…")
        self.api.album_tracks(album_id, self._play_resolved, lambda e: self.statusBar().showMessage(e, 4000))

    def _open_playlist(self, playlist_id: str) -> None:
        self._navigate("playlists")

    def _play_resolved(self, tracks: object) -> None:
        if isinstance(tracks, list) and tracks:
            self.controller.play_queue(tracks, 0)
        else:
            self.statusBar().showMessage("Нет доступных треков", 3000)

    def _logout(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            "Выход",
            "Удалить сохранённый токен и выйти из аккаунта?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.api.logout()
            self.controller.stop()
            self.statusBar().showMessage("Вы вышли из аккаунта", 3000)

    # ----------------------------------------------------------------- close

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.settings.sync()
        super().closeEvent(event)
