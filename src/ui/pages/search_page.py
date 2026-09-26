"""«Поиск»: one query, four sections, double click to play.

The request goes through :meth:`core.yandex_service.YandexService.search`, so
typing never blocks the GUI; the page renders whatever ``search_ready`` reports
and shows ``search_failed`` in the status line.

The query is a single full-width field with a magnifier inside it: there is no
«Найти» button, because a search box that fires on Enter *and* on a 400ms pause
while typing is one control instead of two, and it gives the results the whole
width of the page.  The tabs are a segmented control, and the results share the
single frame of :class:`ui.widgets.results_panel.ResultsPanel`, which is what
keeps the window free of nested borders and half-open corners.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.playback_controller import PlaybackController
from ui.theme import PAGE_PADDING, SPACE_LG, TEXT_MUTED
from ui.widgets.icons import magnifier
from ui.widgets.results_panel import ResultsPanel, header_row
from ui.widgets.track_list import TrackList, entry_item

SECTION_TITLES = {
    "tracks": "Треки",
    "albums": "Альбомы",
    "artists": "Исполнители",
    "playlists": "Плейлисты",
}
SECTION_ORDER = ("tracks", "albums", "artists", "playlists")
MIN_QUERY_LENGTH = 1
DEBOUNCE_MS = 400
"""How long the field stays quiet before a query is sent on its own."""

SEARCH_ICON_SIZE = 18


class SearchPage(QWidget):
    """One full-width search field plus a segmented switch per result section."""

    track_activated = Signal(object)
    search_requested = Signal(str)

    def __init__(
        self,
        controller: PlaybackController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._last_query: str = ""
        self._build_ui()
        controller.service.search_ready.connect(self._on_ready)
        controller.service.search_failed.connect(self._on_failed)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PAGE_PADDING, PAGE_PADDING, PAGE_PADDING, PAGE_PADDING)
        root.setSpacing(SPACE_LG)

        header, _title = header_row("Поиск")
        self.input = QLineEdit()
        self.input.setObjectName("SearchInput")
        self.input.setPlaceholderText("Название трека, альбома или исполнителя")
        self.input.setClearButtonEnabled(True)
        self.input.setFixedHeight(48)
        self.input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # The magnifier is an action inside the field, so it sits in the padding
        # the style sheet reserves for it and never overlaps the text.
        self.input.addAction(
            magnifier(SEARCH_ICON_SIZE, TEXT_MUTED),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.input.returnPressed.connect(self.search)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._on_debounce)
        self.input.textChanged.connect(self._on_text_changed)
        header.insertWidget(1, self.input, 1)
        root.addLayout(header)

        self.panel = ResultsPanel()
        root.addWidget(self.panel, 1)

        self.tabs = self.panel.tabs
        self._pages: dict[str, QWidget] = {}
        for section in SECTION_ORDER:
            if section == "tracks":
                page: QWidget = TrackList()
                page.track_activated.connect(self._play)  # type: ignore[attr-defined]
                self.panel.add_page(page, SECTION_TITLES[section])
            else:
                page = self.panel.add_result_list(SECTION_TITLES[section])
                page.addItem(QListWidgetItem("Ничего не найдено"))
            self._pages[section] = page

        self.status_label = QLabel("Введите запрос — поиск запустится сам")
        self.status_label.setObjectName("Dim")
        root.addWidget(self.status_label)

    # -- public API ---------------------------------------------------------

    @property
    def sections(self) -> tuple[str, ...]:
        return SECTION_ORDER

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
        if query is not None and text != self.input.text():
            # Set the text without arming the debounce again: this call *is* the
            # search the pause would have triggered.
            blocked = self.input.blockSignals(True)
            self.input.setText(text)
            self.input.blockSignals(blocked)
        self._last_query = text
        self._debounce.stop()
        self.search_requested.emit(text)
        started = self._controller.service.search(text)
        if not started:
            self.status_label.setText("Поиск недоступен")
        return started

    # -- slots --------------------------------------------------------------

    def _on_text_changed(self, _text: str) -> None:
        """Wait for a pause in typing, so every keystroke is not a request."""
        if self.query and self.query != self._last_query:
            self._debounce.start()
        else:
            self._debounce.stop()

    def _on_debounce(self) -> None:
        text = self.query
        if text and text != self._last_query:
            self.search(text)

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
        titles: dict[str, str] = {}
        for section, page in self._pages.items():
            titles[section] = f"{SECTION_TITLES[section]} ({counts[section]})"
            entries = list(getattr(results, section, ()) or ())
            if isinstance(page, TrackList):
                page.set_tracks(entries)
                continue
            page.clear()
            for index, entry in enumerate(entries):
                page.addItem(entry_item(entry, index))
            if not entries:
                page.addItem(QListWidgetItem("Ничего не найдено"))
        self.panel.set_section_titles(titles)
        total = sum(counts.values())
        self.status_label.setText(f"«{query}»: найдено {total}" if total else f"«{query}»: ничего не найдено")

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(message)


__all__ = [
    "DEBOUNCE_MS",
    "MIN_QUERY_LENGTH",
    "SECTION_ORDER",
    "SECTION_TITLES",
    "SearchPage",
]
