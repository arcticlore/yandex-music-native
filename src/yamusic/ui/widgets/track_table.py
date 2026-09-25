"""Track list model + view (QAbstractTableModel — never blocks the GUI)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from yamusic.models import TrackInfo

COLUMNS = ("#", "Название", "Альбом", "Время")


class TrackTableModel(QAbstractTableModel):
    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)
        self._tracks: list[TrackInfo] = []

    # -- data management ---------------------------------------------------

    def set_tracks(self, tracks: list[TrackInfo]) -> None:
        self.beginResetModel()
        self._tracks = list(tracks)
        self.endResetModel()

    def tracks(self) -> list[TrackInfo]:
        return list(self._tracks)

    def track_at(self, row: int) -> TrackInfo | None:
        if 0 <= row < len(self._tracks):
            return self._tracks[row]
        return None

    def row_of(self, track_id: str) -> int:
        for i, t in enumerate(self._tracks):
            if t.id == track_id:
                return i
        return -1

    def set_liked(self, track_id: str, liked: bool) -> None:
        row = self.row_of(track_id)
        if row < 0:
            return
        self._tracks[row].liked = liked
        top = self.index(row, 0)
        bottom = self.index(row, len(COLUMNS) - 1)
        self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.DisplayRole])

    def mark_current(self, track_id: str | None) -> None:
        # full repaint for the bold/accent current row
        if self._tracks:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._tracks) - 1, len(COLUMNS) - 1),
                [Qt.ItemDataRole.FontRole, Qt.ItemDataRole.ForegroundRole],
            )

    # -- QAbstractTableModel ----------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        track = self._tracks[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return f"{index.row() + 1}. {'♥' if track.liked else ''}"
            if col == 1:
                return track.title
            if col == 2:
                return track.artist_line
            if col == 3:
                return track.duration_text
        elif role == Qt.ItemDataRole.TextAlignmentRole and col in (0, 3):
            return int(Qt.AlignmentFlag.AlignCenter)
        elif role == Qt.ItemDataRole.ForegroundRole:
            if not track.available:
                return QColor("#5a5a6e")
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class TrackTableView(QTableView):
    """Read-only table; double-click → ``track_activated(row)``."""

    track_activated = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = TrackTableModel(self)
        self.setModel(self._model)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(36)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.setShowGrid(False)
        self.setStyleSheet("QTableView { border: none; }")

    def model(self) -> TrackTableModel:  # type: ignore[override]
        return self._model

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.pos())
        if index.isValid():
            self.track_activated.emit(index.row())
        super().mouseDoubleClickEvent(event)

    def select_track_id(self, track_id: str | None) -> None:
        if not track_id:
            self.clearSelection()
            return
        row = self._model.row_of(track_id)
        if row >= 0:
            self.selectRow(row)
