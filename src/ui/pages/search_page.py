"""«Поиск»: one query, four sections, double click to play.

The request goes through :meth:`core.yandex_service.YandexService.search`, so
typing never blocks the GUI; the page renders whatever ``search_ready`` reports
and shows ``search_failed`` in the status line.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.playback_controller import PlaybackController
from ui.widgets.track_list import TrackList, track_line

SECTION_TITLES = {
    "tracks": "Треки",
    "albums": "Альбомы",
    "artists": "Исполнители",
    "playlists": "Плейлисты",
}
SECTION_ORDER = ("tracks", "albums", "artists", "playlists")
MIN_QUERY_LENGTH = 1


class SearchPage(QWidget):
    """Search box plus a tab per result section."""

    track_activated = Signal(object)
    search_requested = Signal(str)

    def __init__(
        self,
        controller: PlaybackController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._build_ui()
        controller.service.search_ready.connect(self._on_ready)
        controller.service.search_failed.connect(self._on_failed)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)
        title = QLabel("Поиск")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setObjectName("SearchInput")
        self.input.setPlaceholderText("Название трека, альбома или исполнителя")
        self.input.returnPressed.connect(self.search)
        row.addWidget(self.input, 1)
        self.search_button = QPushButton("Найти")
        self.search_button.setObjectName("Accent")
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        root.addLayout(row)

        self.tabs = QTabWidget()
        self._pages: dict[str, QWidget] = {}
        for section in SECTION_ORDER:
            if section == "tracks":
                page: QWidget = TrackList()
                page.track_activated.connect(self._play)  # type: ignore[attr-defined]
            else:
                page = QListWidget()
                page.setObjectName("TrackList")
                page.setAlternatingRowColors(True)
                page.addItem(QListWidgetItem("Ничего не найдено"))
            self._pages[section] = page
            self.tabs.addTab(page, SECTION_TITLES[section])
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("Введите запрос и нажмите «Найти»")
        self.status_label.setObjectName("Dim")
        root.addWidget(self.status_label)

    # -- public API ---------------------------------------------------------

    @property
    def query(self) -> str:
        return self.input.text().strip()

    def current_section(self) -> str:
        return SECTION_ORDER[self.tabs.currentIndex()]

    def list_for(self, section: str) -> QWidget:
        return self._pages[section]

    def search(self, query: str | None = None) -> bool:
        """Run a search for ``query`` or the current input text."""
        text = (query if query is not None else self.input.text()).strip()
        if len(text) < MIN_QUERY_LENGTH:
            self.status_label.setText("Введите поисковый запрос")
            return False
        self.input.setText(text)
        self.search_requested.emit(text)
        started = self._controller.service.search(text)
        if not started:
            self.status_label.setText("Поиск недоступен")
        return started

    # -- slots --------------------------------------------------------------

    def _play(self, track: object) -> None:
        self.track_activated.emit(track)
        self._controller.play_track(track)

    def _on_ready(self, results: object) -> None:
        query = str(getattr(results, "query", "") or self.query)
        counts = {
            "tracks": len(getattr(results, "tracks", ()) or ()),
            "albums": len(getattr(results, "albums", ()) or ()),
            "artists": len(getattr(results, "artists", ()) or ()),
            "playlists": len(getattr(results, "playlists", ()) or ()),
        }
        for section, page in self._pages.items():
            self.tabs.setTabText(self.tabs.indexOf(page), f"{SECTION_TITLES[section]} ({counts[section]})")
            entries = list(getattr(results, section, ()) or ())
            if isinstance(page, TrackList):
                page.set_tracks(entries)
                continue
            page.clear()
            for entry in entries:
                page.addItem(QListWidgetItem(track_line(entry)))
            if not entries:
                page.addItem(QListWidgetItem("Ничего не найдено"))
        total = sum(counts.values())
        self.status_label.setText(f"«{query}»: найдено {total}" if total else f"«{query}»: ничего не найдено")

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(message)


__all__ = ["MIN_QUERY_LENGTH", "SECTION_ORDER", "SECTION_TITLES", "SearchPage"]
