"""Left navigation sidebar."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget


class Sidebar(QFrame):
    """App title + navigation entries; emits ``navigated(page_id)``."""

    navigated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)

        title = QLabel("♫ Яндекс Музыка")
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        self.list = QListWidget()
        self.list.setObjectName("NavList")
        self.list.setSpacing(2)
        entries = (
            ("wave", "◉  Моя волна"),
            ("search", "⌕  Поиск"),
            ("playlists", "☰  Плейлисты"),
            ("favorites", "♥  Любимые"),
            ("settings", "⚙  Настройки"),
        )
        for page_id, label in entries:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, page_id)  # type: ignore[name-defined]
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._on_row)
        layout.addWidget(self.list, 1)

        self._row_map = {i: page_id for i, (page_id, _) in enumerate(entries)}
        self.list.setCurrentRow(0)

    def _on_row(self, row: int) -> None:
        page_id = self._row_map.get(row)
        if page_id:
            self.navigated.emit(page_id)

    def select_page(self, page_id: str) -> None:
        for row, pid in self._row_map.items():
            if pid == page_id:
                self.list.blockSignals(True)
                self.list.setCurrentRow(row)
                self.list.blockSignals(False)
                return
