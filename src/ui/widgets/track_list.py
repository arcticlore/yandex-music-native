"""A flat track list shared by the collection and search pages.

Double click (or Enter) hands the picked track to the controller, which is the
same call the wave page uses, so every list in the GUI starts playback the same
way.
"""

from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QWidget

from core.yandex_service import WaveTrack


def format_duration(duration_ms: int) -> str:
    """``215000`` -> ``3:35``; unknown lengths render as an em dash."""
    seconds = int(duration_ms or 0) // 1000
    if seconds <= 0:
        return "—"
    minutes, rest = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{rest:02d}"
    return f"{minutes}:{rest:02d}"


def track_line(track: Any) -> str:
    """One-line label: title, artists and length."""
    if isinstance(track, WaveTrack):
        title = track.title
        subtitle = track.artists_name or track.album
        duration = track.duration_ms
    else:
        title = str(getattr(track, "title", "") or "Без названия")
        subtitle = str(getattr(track, "artists_name", "") or getattr(track, "subtitle", "") or "")
        duration = int(getattr(track, "duration_ms", 0) or 0)
    if subtitle:
        return f"{title} — {subtitle}   {format_duration(duration)}"
    return f"{title}   {format_duration(duration)}"


class TrackList(QListWidget):
    """Displays :class:`~core.yandex_service.WaveTrack` items and plays them."""

    track_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrackList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setUniformItemSizes(True)
        self.setAlternatingRowColors(True)
        self._tracks: list[WaveTrack] = []
        self.itemActivated.connect(self._on_activated)
        self.itemDoubleClicked.connect(self._on_activated)

    @property
    def tracks(self) -> list[WaveTrack]:
        return list(self._tracks)

    @property
    def current_track(self) -> WaveTrack | None:
        row = self.currentRow()
        if 0 <= row < len(self._tracks):
            return self._tracks[row]
        return None

    def set_tracks(self, tracks: Sequence[WaveTrack]) -> None:
        self.clear()
        self._tracks = []
        for track in tracks:
            item = QListWidgetItem(track_line(track))
            item.setData(Qt.ItemDataRole.UserRole, len(self._tracks))
            self.addItem(item)
            self._tracks.append(track)
        self.setEnabled(bool(self._tracks))

    def set_placeholder(self, text: str) -> None:
        self.clear()
        self._tracks = []
        self.setEnabled(False)
        if text:
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addItem(item)

    def _on_activated(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return
        if 0 <= int(index) < len(self._tracks):
            self.track_activated.emit(self._tracks[int(index)])


__all__ = ["TrackList", "format_duration", "track_line"]
