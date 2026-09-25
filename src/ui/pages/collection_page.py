"""«Коллекция»: liked tracks, albums, artists and playlists of the account.

Every section is fetched through :meth:`core.yandex_service.YandexService.load_liked`,
which runs the request on the service worker thread; the page only renders the
``collection_ready`` payload, so scrolling never blocks the GUI.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.playback_controller import PlaybackController
from ui.widgets.track_list import TrackList, apply_row_delegate, entry_item

SECTION_TITLES = {
    "tracks": "Любимые треки",
    "albums": "Любимые альбомы",
    "artists": "Любимые исполнители",
    "playlists": "Любимые плейлисты",
}
SECTION_ORDER = ("tracks", "albums", "artists", "playlists")


class CollectionPage(QWidget):
    """Four tabs, one per liked section, all fed by the same signal."""

    track_activated = Signal(object)

    def __init__(
        self,
        controller: PlaybackController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._build_ui()
        controller.service.collection_ready.connect(self._on_ready)
        controller.service.collection_failed.connect(self._on_failed)
        for section in SECTION_ORDER:
            self.tabs.setTabText(self.tabs.indexOf(self._pages[section]), SECTION_TITLES[section])
            page = self._pages[section]
            if isinstance(page, TrackList):
                page.set_placeholder("Нажмите «Обновить»")
            else:
                page.setEnabled(False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("Коллекция")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self._pages: dict[str, QWidget] = {}
        for section in SECTION_ORDER:
            if section == "tracks":
                page: QWidget = TrackList()
                page.track_activated.connect(self._play)  # type: ignore[attr-defined]
            else:
                page = apply_row_delegate(QListWidget())
                page.setObjectName("TrackList")
            self._pages[section] = page
            self.tabs.addTab(page, section)
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Dim")
        root.addWidget(self.status_label)

    # -- public API ---------------------------------------------------------

    @property
    def sections(self) -> tuple[str, ...]:
        return SECTION_ORDER

    def current_section(self) -> str:
        return SECTION_ORDER[self.tabs.currentIndex()]

    def list_for(self, section: str) -> QWidget:
        return self._pages[section]

    def tracks(self) -> list:
        page = self._pages["tracks"]
        return page.tracks if isinstance(page, TrackList) else []

    def refresh(self, section: str | None = None) -> bool:
        """Request one section (the visible one by default)."""
        target = section or self.current_section()
        if target not in SECTION_TITLES:
            self.status_label.setText(f"Неизвестный раздел: {target}")
            return False
        started = self._controller.service.load_liked(target)
        if not started:
            self.status_label.setText("Не удалось загрузить коллекцию")
        return started

    def reload(self) -> None:
        self.refresh()

    # -- slots --------------------------------------------------------------

    def _play(self, track: object) -> None:
        self.track_activated.emit(track)
        self._controller.play_track(track)

    def _on_ready(self, section: str, items: object) -> None:
        page = self._pages.get(section)
        if page is None:
            return
        if isinstance(page, TrackList):
            tracks = list(items or [])
            page.set_tracks(tracks)
            self.status_label.setText(f"{SECTION_TITLES[section]}: {len(tracks)}")
            return
        entries = list(items or [])
        page.clear()
        for index, entry in enumerate(entries):
            page.addItem(entry_item(entry, index))
        if not entries:
            page.addItem(QListWidgetItem("Пока пусто"))
        self.status_label.setText(f"{SECTION_TITLES[section]}: {len(entries)}")

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(message)


__all__ = ["SECTION_ORDER", "SECTION_TITLES", "CollectionPage"]
