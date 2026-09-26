"""«Коллекция»: liked tracks, albums, artists and playlists of the account.

Every section is fetched through :meth:`core.yandex_service.YandexService.load_liked`,
which runs the request on the service worker thread; the page only renders the
``collection_ready`` payload, so scrolling never blocks the GUI.

The page draws one frame around the whole result area - see
:class:`ui.widgets.results_panel.ResultsPanel` - so the tabs, the list and the
status line read as three bands of one block instead of four nested boxes.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.playback_controller import PlaybackController
from ui.theme import PAGE_PADDING, SPACE_LG
from ui.widgets.results_panel import ResultsPanel, header_row
from ui.widgets.track_list import TrackList, entry_item

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
            page = self._pages[section]
            if isinstance(page, TrackList):
                page.set_placeholder("Нажмите «Обновить»")
            else:
                page.setEnabled(False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PAGE_PADDING, PAGE_PADDING, PAGE_PADDING, PAGE_PADDING)
        root.setSpacing(SPACE_LG)

        header, _title = header_row("Коллекция")
        self.panel = ResultsPanel()
        header.addStretch(1)
        self.refresh_button = ResultsPanel.page_button("Обновить")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self.tabs = self.panel.tabs
        self._pages: dict[str, QWidget] = {}
        for section in SECTION_ORDER:
            if section == "tracks":
                page: QWidget = TrackList()
                page.track_activated.connect(self._play)  # type: ignore[attr-defined]
                self.panel.add_page(page, SECTION_TITLES[section])
            else:
                page = self.panel.add_result_list(SECTION_TITLES[section])
            self._pages[section] = page
        root.addWidget(self.panel, 1)

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
