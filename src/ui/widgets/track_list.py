"""A flat track list shared by the collection and search pages.

Double click (or Enter) hands the picked track to the controller, which is the
same call the wave page uses, so every list in the GUI starts playback the same
way.

The rows themselves are painted by :class:`TrackRowDelegate` instead of the
stock item style: a rounded cover, a bold title over a dim artist line, a dim
album column, a monospace duration and a heart.  The widget stays a
``QListWidget`` on purpose - pages and tests keep using ``item()``,
``setCurrentRow()`` and ``itemActivated`` - but the visible row is ours, and
``Qt.ItemDataRole.DisplayRole`` still carries the plain one-line text for
anything that reads the item as text.
"""

from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from core.playback_controller import cover_cache_dir, cover_file_name
from core.yandex_service import WaveTrack, track_cover_url
from ui.theme import (
    ACCENT,
    LIKE_ACTIVE,
    SURFACE,
    SURFACE_HOVER,
    TEXT,
    TEXT_DIM,
    TEXT_MUTED,
    TRACK_ROW_HEIGHT,
)

#: Geometry of one painted row; kept next to the sheet so both agree.
THUMB_SIZE = 40
THUMB_RADIUS = 8
ROW_PADDING = 12
TITLE_FONT_PX = 13
META_FONT_PX = 11
HEART_COLUMN = 26
DURATION_COLUMN = 48
MIN_ALBUM_WIDTH = 560

#: Item data roles the delegate reads.  ``UserRole`` keeps the plain index.
_ROLE_INDEX = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_TITLE = _ROLE_INDEX + 1
_ROLE_SUBTITLE = _ROLE_INDEX + 2
_ROLE_ALBUM = _ROLE_INDEX + 3
_ROLE_DURATION = _ROLE_INDEX + 4
_ROLE_LIKED = _ROLE_INDEX + 5
_ROLE_EXPLICIT = _ROLE_INDEX + 6
_ROLE_PLAYING = _ROLE_INDEX + 7
_ROLE_GLYPH = _ROLE_INDEX + 8
_ROLE_COVER = _ROLE_INDEX + 9


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


def album_column_width(width: int) -> int:
    """Width of the album column, or ``0`` when the row is too narrow for it.

    Kept as a pure function so the decision can be tested without painting.
    """
    if width < MIN_ALBUM_WIDTH:
        return 0
    return max(90, min(190, width // 5))


def _cached_cover(url: str | None) -> QPixmap | None:
    """A cover that the player already downloaded, or ``None``.

    Rows never hit the network: an album or artist shows its real artwork once
    the controller happened to cache that exact URL, otherwise the row falls
    back to its index number.
    """
    if not url:
        return None
    path = cover_cache_dir() / cover_file_name(url)
    try:
        if not path.is_file():
            return None
        pixmap = QPixmap(str(path))
    except Exception:  # pragma: no cover - unreadable cache entry
        return None
    return pixmap if not pixmap.isNull() else None


class TrackRowDelegate(QStyledItemDelegate):
    """Paints a row as cover + title/artist + album + duration + heart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._covers: dict[int, QPixmap] = {}

    # -- geometry ------------------------------------------------------------

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt naming
        width = option.rect.width() if option is not None else 0
        return QSize(width, TRACK_ROW_HEIGHT)

    # -- helpers -------------------------------------------------------------

    def _font(self, pixel_size: int, bold: bool = False, mono: bool = False) -> QFont:
        font = QFont(QApplication.font())
        font.setPixelSize(pixel_size)
        font.setBold(bold)
        if mono:
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setFamily("monospace")
        return font

    def set_cover(self, row: int, pixmap: QPixmap | None) -> None:
        """Attach artwork to one row and repaint it."""
        if pixmap is None or pixmap.isNull():
            self._covers.pop(row, None)
        else:
            self._covers[row] = pixmap
        self._refresh(row)

    def clear_covers(self) -> None:
        self._covers.clear()

    def _refresh(self, row: int) -> None:
        parent = self.parent()
        if isinstance(parent, QListWidget) and 0 <= row < parent.count():
            parent.viewport().update(parent.visualItemRect(parent.item(row)))

    # -- painting ------------------------------------------------------------

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802 - Qt naming
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRect(option.rect)

        if index.data(_ROLE_INDEX) is None:
            # A plain text row (placeholders, «Пока пусто»): keep the stock look.
            painter.restore()
            super().paint(painter, option, index)
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        playing = bool(index.data(_ROLE_PLAYING))

        if selected or playing:
            self._fill(painter, rect.adjusted(2, 3, -2, -3), QColor(SURFACE_HOVER), 10)
        elif hovered:
            self._fill(painter, rect.adjusted(2, 3, -2, -3), QColor(SURFACE), 10)
        if playing:
            self._fill(
                painter, QRect(rect.left() + 2, rect.top() + 10, 3, rect.height() - 20), QColor(ACCENT), 2
            )

        top = rect.top() + (rect.height() - THUMB_SIZE) // 2
        self._paint_thumb(painter, QRect(rect.left() + ROW_PADDING, top, THUMB_SIZE, THUMB_SIZE), index)

        right = rect.right() - ROW_PADDING
        liked = bool(index.data(_ROLE_LIKED))
        if liked:
            right = self._paint_heart(painter, right, top) - 6
        explicit = bool(index.data(_ROLE_EXPLICIT))
        if explicit:
            right = self._paint_explicit(painter, right, top) - 6
        duration = str(index.data(_ROLE_DURATION) or "—")
        painter.setFont(self._font(META_FONT_PX, mono=True))
        painter.setPen(QColor(TEXT_DIM if not playing else TEXT))
        painter.drawText(
            QRect(right - DURATION_COLUMN, rect.top(), DURATION_COLUMN, rect.height()),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            duration,
        )
        right -= DURATION_COLUMN + 10

        album = str(index.data(_ROLE_ALBUM) or "")
        album_width = album_column_width(rect.width())
        if album and album_width:
            painter.setFont(self._font(META_FONT_PX))
            painter.setPen(QColor(TEXT_MUTED))
            metrics = QFontMetrics(painter.font())
            painter.drawText(
                QRect(right - album_width, rect.top(), album_width, rect.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(album, Qt.TextElideMode.ElideRight, album_width),
            )
            right -= album_width + 14

        text_left = rect.left() + ROW_PADDING + THUMB_SIZE + 12
        text_width = max(40, right - text_left)
        self._paint_texts(painter, rect, text_left, text_width, index, playing)

        painter.restore()

    def _paint_texts(
        self,
        painter: QPainter,
        rect: QRect,
        left: int,
        width: int,
        index,
        playing: bool,
    ) -> None:
        title = str(index.data(_ROLE_TITLE) or "")
        subtitle = str(index.data(_ROLE_SUBTITLE) or "")
        column = QRect(left, rect.top(), width, rect.height())

        title_font = self._font(TITLE_FONT_PX, bold=True)
        painter.setFont(title_font)
        painter.setPen(QColor(ACCENT if playing else TEXT))
        title_rect = QRect(column.left(), column.top() + 6, column.width(), 20)
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )

        if not subtitle:
            return
        meta_font = self._font(META_FONT_PX)
        painter.setFont(meta_font)
        painter.setPen(QColor(TEXT_DIM if not playing else TEXT_DIM))
        subtitle_rect = QRect(column.left(), column.top() + 25, column.width(), 18)
        painter.drawText(
            subtitle_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(meta_font).elidedText(subtitle, Qt.TextElideMode.ElideRight, subtitle_rect.width()),
        )

    def _paint_thumb(self, painter: QPainter, rect: QRect, index) -> None:
        pixmap: QPixmap | None = self._covers.get(self._row_of(index))
        if pixmap is None:
            glyph = index.data(_ROLE_COVER)
            if isinstance(glyph, QPixmap) and not glyph.isNull():
                pixmap = glyph
        if pixmap is not None:
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect), THUMB_RADIUS, THUMB_RADIUS)
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(
                rect,
                pixmap.scaled(
                    rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
            painter.restore()
            return

        self._fill(painter, rect, QColor(SURFACE_HOVER), THUMB_RADIUS)
        label = str(index.data(_ROLE_GLYPH) or "")
        if label:
            font = self._font(META_FONT_PX, bold=True)
            painter.setFont(font)
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)

    def _paint_heart(self, painter: QPainter, right: int, top: int) -> int:
        box = QRect(right - HEART_COLUMN, top, HEART_COLUMN, THUMB_SIZE)
        painter.setFont(self._font(14, bold=True))
        painter.setPen(QColor(LIKE_ACTIVE))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), "♥")
        return box.left()

    def _paint_explicit(self, painter: QPainter, right: int, top: int) -> int:
        box = QRect(right - 18, top + (THUMB_SIZE - 16) // 2, 16, 16)
        self._fill(painter, box, QColor(TEXT_MUTED), 4)
        font = self._font(9, bold=True)
        painter.setFont(font)
        painter.setPen(QColor(SURFACE))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), "E")
        return box.left()

    @staticmethod
    def _fill(painter: QPainter, rect: QRect, colour: QColor, radius: int) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        painter.fillPath(path, colour)

    @staticmethod
    def _row_of(index) -> int:
        try:
            return int(index.row())
        except Exception:  # pragma: no cover - defensive
            return -1


def _store_row(
    item: QListWidgetItem,
    *,
    index: int,
    title: str,
    subtitle: str = "",
    album: str = "",
    duration_ms: int = 0,
    liked: bool = False,
    explicit: bool = False,
    cover: QPixmap | None = None,
    glyph: str = "",
) -> QListWidgetItem:
    """Fill the roles the delegate paints from, keeping a readable text."""
    text = track_line_text(title, subtitle, duration_ms)
    item.setText(text)
    item.setData(Qt.ItemDataRole.UserRole, index)
    item.setData(_ROLE_INDEX, index)
    item.setData(_ROLE_TITLE, title)
    item.setData(_ROLE_SUBTITLE, subtitle)
    item.setData(_ROLE_ALBUM, album)
    item.setData(_ROLE_DURATION, format_duration(duration_ms))
    item.setData(_ROLE_LIKED, bool(liked))
    item.setData(_ROLE_EXPLICIT, bool(explicit))
    item.setData(_ROLE_PLAYING, False)
    item.setData(_ROLE_GLYPH, glyph)
    item.setData(_ROLE_COVER, cover)
    item.setToolTip(f"{title}\n{subtitle}" if subtitle else title)
    return item


def track_line_text(title: str, subtitle: str = "", duration_ms: int = 0) -> str:
    """The same one-line string :func:`track_line` builds, from parts."""
    duration = format_duration(duration_ms)
    if subtitle:
        return f"{title} — {subtitle}   {duration}"
    return f"{title}   {duration}"


def apply_row_delegate(list_widget: QListWidget) -> QListWidget:
    """Give any ``QListWidget`` the Deep Obsidian row look."""
    list_widget.setItemDelegate(TrackRowDelegate(list_widget))
    list_widget.setUniformItemSizes(True)
    list_widget.setAlternatingRowColors(False)
    list_widget.setMouseTracking(True)
    return list_widget


def entry_cover_url(entry: Any) -> str | None:
    """Cover URL of a non-track entry, in the size the controller caches.

    A full URL is taken as it is; a library object is asked for its own art.
    """
    given = getattr(entry, "cover_url", None)
    if isinstance(given, str) and given.strip():
        return given.strip()
    if isinstance(entry, WaveTrack):
        return entry.cover_url()
    return track_cover_url(entry)


def entry_item(entry: Any, index: int) -> QListWidgetItem:
    """A styled row for an album, artist or playlist - artwork when cached."""
    title = str(getattr(entry, "title", "") or getattr(entry, "name", "") or "Без названия")
    kind = str(getattr(entry, "kind", "") or "")
    subtitle = str(getattr(entry, "subtitle", "") or getattr(entry, "artists_name", "") or "")
    if kind and subtitle:
        subtitle = f"{kind} · {subtitle}"
    elif kind:
        subtitle = kind
    duration_ms = int(getattr(entry, "duration_ms", 0) or 0)
    cover_url = entry_cover_url(entry)
    item = QListWidgetItem()
    _store_row(
        item,
        index=index,
        title=title,
        subtitle=subtitle,
        duration_ms=duration_ms,
        cover=_cached_cover(cover_url),
        glyph="" if cover_url else "♪",
    )
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


class TrackList(QListWidget):
    """Displays :class:`~core.yandex_service.WaveTrack` items and plays them."""

    track_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrackList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        apply_row_delegate(self)
        self._tracks: list[WaveTrack] = []
        self._playing_id: str = ""
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
            item = QListWidgetItem()
            _store_row(
                item,
                index=len(self._tracks),
                title=str(getattr(track, "title", "") or "Без названия"),
                subtitle=str(getattr(track, "artists_name", "") or getattr(track, "album", "") or ""),
                album=str(getattr(track, "album", "") or ""),
                duration_ms=int(getattr(track, "duration_ms", 0) or 0),
                liked=bool(getattr(track, "liked", False)),
                explicit=bool(getattr(track, "explicit", False)),
                cover=_cached_cover(entry_cover_url(track)),
                glyph="",
            )
            self.addItem(item)
            self._tracks.append(track)
        self.setEnabled(bool(self._tracks))
        self._apply_playing_rows()

    def set_tracks_playing(self, track_id: str) -> None:
        """Mark the row of ``track_id`` as the one currently sounding."""
        track_id = str(track_id or "")
        if track_id == self._playing_id:
            return
        self._playing_id = track_id
        self._apply_playing_rows()

    def _apply_playing_rows(self) -> None:
        for row, track in enumerate(self._tracks):
            item = self.item(row)
            if item is None:
                continue
            item.setData(_ROLE_PLAYING, bool(self._playing_id) and track.track_id == self._playing_id)
            self.viewport().update(self.visualItemRect(item))

    def set_cover_pixmap(self, row: int, pixmap: QPixmap | None) -> None:
        """Show downloaded artwork on one track row."""
        delegate = self.itemDelegate()
        if isinstance(delegate, TrackRowDelegate):
            delegate.set_cover(row, pixmap)

    def set_placeholder(self, text: str) -> None:
        self.clear()
        self._tracks = []
        self._playing_id = ""
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


__all__ = [
    "DURATION_COLUMN",
    "HEART_COLUMN",
    "THUMB_SIZE",
    "TrackList",
    "TrackRowDelegate",
    "album_column_width",
    "apply_row_delegate",
    "entry_cover_url",
    "entry_item",
    "format_duration",
    "track_line",
    "track_line_text",
]
