"""Lyrics dock: plain text or karaoke-synced lines."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from yamusic.models import LyricDocument


class LyricsView(QScrollArea):
    """Auto-scrolling lyrics with line highlighting for synced documents."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self.setWidget(self._container)
        self._labels: list[QLabel] = []
        self._doc: LyricDocument | None = None
        self._active = -1
        self._empty = QLabel("Текст песни появится здесь")
        self._empty.setObjectName("Dim")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.insertWidget(0, self._empty)

    def clear(self) -> None:
        self._doc = None
        self._active = -1
        self._clear_labels()
        self._empty.show()

    def set_document(self, doc: LyricDocument) -> None:
        self._doc = doc
        self._clear_labels()
        self._empty.hide()
        if doc.synced:
            base_font = QFont()
            base_font.setPointSize(12)
            for line in doc.lines:
                label = QLabel(line.text)
                label.setWordWrap(True)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setFont(base_font)
                label.setStyleSheet("color: #9a9aa8; padding: 2px 6px;")
                self._labels.append(label)
                self._layout.insertWidget(len(self._labels) - 1, label)
        else:
            label = QLabel(doc.plain_text or "\n".join(l.text for l in doc.lines))
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #d0d0da; font-size: 14px; line-height: 150%;")
            self._labels.append(label)
            self._layout.insertWidget(0, label)
        self._active = -1

    def _clear_labels(self) -> None:
        for label in self._labels:
            self._layout.removeWidget(label)
            label.deleteLater()
        self._labels.clear()

    def update_position(self, position_ms: int) -> None:
        """Highlight the current karaoke line and scroll to it."""
        if self._doc is None or not self._doc.synced or not self._labels:
            return
        index = self._doc.line_at(position_ms)
        if index == self._active or index < 0 or index >= len(self._labels):
            return
        if 0 <= self._active < len(self._labels):
            prev = self._labels[self._active]
            prev.setStyleSheet("color: #9a9aa8; padding: 2px 6px;")
            prev.setFont(QFont(prev.font().family(), 11))
        active = self._labels[index]
        active.setStyleSheet("color: #ffdb4d; padding: 2px 6px; font-weight: 700;")
        font = QFont(active.font().family(), 13)
        font.setBold(True)
        active.setFont(font)
        self._active = index
        # ensure visible
        viewport_h = self.viewport().height()
        target_y = max(0, active.y() - viewport_h // 2 + active.height() // 2)
        self.verticalScrollBar().setValue(target_y)

    def is_synced(self) -> bool:
        return bool(self._doc and self._doc.synced)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._active >= 0 and self._active < len(self._labels):
            # keep active line centred on resize
            active = self._labels[self._active]
            viewport_h = self.viewport().height()
            target_y = max(0, active.y() - viewport_h // 2 + active.height() // 2)
            self.verticalScrollBar().setValue(target_y)
