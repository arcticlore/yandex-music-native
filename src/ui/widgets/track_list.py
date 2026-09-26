"""A flat track list shared by the collection and search pages.

Double click (or Enter) hands the picked track to the controller, which is the
same call the wave page uses, so every list in the GUI starts playback the same
way.

The rows are painted by :class:`TrackRowDelegate` rather than by the stock item
style, because a list has to survive being 400px wide as well as 900px: every
row gets the same base surface, the same height and the same padding, the
secondary columns sit in fixed grid tracks, and a narrow window hides them
instead of squeezing the text.  The widget stays a ``QListWidget`` on purpose -
pages and tests keep using ``item()``, ``setCurrentRow()`` and
``itemActivated`` - and ``Qt.ItemDataRole.DisplayRole`` still carries the plain
one-line text for anything that reads an item as text.

Artwork arrives late and out of order, so a row has three visual states that all
have to look finished on their own: a gradient placeholder while the cover is in
flight, the real cover once :mod:`ui.widgets.cover_loader` has it, and the
pointer-over or the playing-row tint painted on top of either.
"""

from __future__ import annotations

from dataclasses import dataclass
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

from core.yandex_service import WaveTrack, track_cover_url
from ui.theme import (
    ACCENT,
    COVER_SIZE,
    LIKE_ACTIVE,
    PANEL,
    PLAYING_BG,
    ROW_HEIGHT,
    ROW_HOVER,
    ROW_RADIUS,
    SPACE_MD,
    SPACE_SM,
    SURFACE,
    SURFACE_HOVER,
    TEXT,
    TEXT_DIM,
    TEXT_MUTED,
    tint,
)
from ui.widgets.cover_loader import CoverLoader, cached_pixmap
from ui.widgets.icons import draw_equalizer, placeholder_pixmap

# -- row metrics -------------------------------------------------------------

ROW_INSET = SPACE_SM
"""Rows are inset horizontally, so a rounded hover pill has room to show."""

NUMBER_COLUMN = 28
"""The gutter of the row number and of the playing equaliser."""

COLUMN_GAP = SPACE_MD
TIME_COLUMN = 44
ACTIONS_COLUMN = 28
ALBUM_COLUMN = 140
MIN_TEXT_COLUMN = 80
ALBUM_BREAKPOINT = 720
ACTIONS_BREAKPOINT = 520
TITLE_FONT_PX = 13
META_FONT_PX = 11
HEART = "♥"
HEART_OFF = "♡"
EXPLICIT_GLYPH = "E"

PLACEHOLDER_TOP = SURFACE
PLACEHOLDER_BOTTOM = PANEL
"""The placeholder gradient is made of two existing surfaces, not new colours."""

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
_ROLE_NUMBER = _ROLE_INDEX + 10
_ROLE_URL = _ROLE_INDEX + 11


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


def track_line_text(title: str, subtitle: str = "", duration_ms: int = 0) -> str:
    """The same one-line string :func:`track_line` builds, from parts."""
    duration = format_duration(duration_ms)
    if subtitle:
        return f"{title} — {subtitle}   {duration}"
    return f"{title}   {duration}"


@dataclass(frozen=True)
class RowColumns:
    """Where each grid track of one row starts, in pixels from its left edge."""

    number_x: int
    number_width: int
    text_x: int
    text_width: int
    album_x: int
    album_width: int
    time_x: int
    time_width: int
    actions_x: int
    show_album: bool
    show_actions: bool


def row_columns(width: int) -> RowColumns:
    """Lay the row out as a grid for a list ``width`` pixels wide.

    The text track absorbs whatever is left, so album, duration and the heart
    never move when a title is longer.  Below the breakpoints the secondary
    columns are dropped instead of squeezed, which keeps every row the same
    height and the same padding.
    """
    left = ROW_INSET + NUMBER_COLUMN + COVER_SIZE + COLUMN_GAP
    right = width - ROW_INSET
    show_actions = width >= ACTIONS_BREAKPOINT
    show_album = width >= ALBUM_BREAKPOINT

    actions_x = right - ACTIONS_COLUMN if show_actions else right
    cursor = actions_x - COLUMN_GAP if show_actions else right
    time_x = cursor - TIME_COLUMN
    cursor = time_x - COLUMN_GAP
    if show_album:
        album_x = cursor - ALBUM_COLUMN
        cursor = album_x - COLUMN_GAP
        album_width = ALBUM_COLUMN
    else:
        album_x = 0
        album_width = 0
    text_width = max(MIN_TEXT_COLUMN, cursor - left)
    return RowColumns(
        number_x=ROW_INSET,
        number_width=NUMBER_COLUMN,
        text_x=left,
        text_width=text_width,
        album_x=album_x,
        album_width=album_width,
        time_x=time_x,
        time_width=TIME_COLUMN,
        actions_x=actions_x,
        show_album=show_album,
        show_actions=show_actions,
    )


class TrackRowDelegate(QStyledItemDelegate):
    """Paints a row as number/equaliser + cover + title/artist + album + time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._covers: dict[int, QPixmap] = {}
        self._placeholder = placeholder_pixmap(COVER_SIZE, ROW_RADIUS, PLACEHOLDER_TOP, PLACEHOLDER_BOTTOM)

    # -- geometry ------------------------------------------------------------

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt naming
        width = option.rect.width() if option is not None else 0
        return QSize(width, ROW_HEIGHT)

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

    def _refresh_all(self) -> None:
        parent = self.parent()
        if isinstance(parent, QListWidget):
            parent.viewport().update()

    @staticmethod
    def _fill(painter: QPainter, rect: QRect, colour: QColor, radius: int) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        painter.fillPath(path, colour)

    # -- painting ------------------------------------------------------------

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802 - Qt naming
        if index.data(_ROLE_INDEX) is None:
            # A plain text row (placeholders, «Пока пусто»): keep the stock look.
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        row = QRect(option.rect)
        row.setHeight(ROW_HEIGHT)
        painted = row.adjusted(ROW_INSET, 0, -ROW_INSET, 0)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        playing = bool(index.data(_ROLE_PLAYING))

        # Every row gets the same base surface: no transparent rows, no gaps
        # between the resting and the highlighted state.
        self._fill(painter, painted, QColor(PANEL), ROW_RADIUS)
        if hovered and not selected and not playing:
            self._fill(painter, painted, tint(PANEL, ROW_HOVER), ROW_RADIUS)
        if playing:
            # A soft gold wash instead of a hard bar: the current track is
            # obvious, yet the row keeps the same geometry as every other one.
            self._fill(painter, painted, tint(PANEL, PLAYING_BG), ROW_RADIUS)
        elif selected:
            self._fill(painter, painted, QColor(SURFACE_HOVER), ROW_RADIUS)

        columns = row_columns(painted.width())
        top = painted.top() + (ROW_HEIGHT - COVER_SIZE) // 2
        self._paint_number(painter, painted, columns, index, playing)
        self._paint_thumb(
            painter,
            QRect(painted.left() + NUMBER_COLUMN, top, COVER_SIZE, COVER_SIZE),
            index,
        )
        self._paint_texts(painter, painted, columns, index, playing)
        if columns.show_album:
            self._paint_album(painter, painted, columns, index)
        self._paint_duration(painter, painted, columns, index, playing)
        if columns.show_actions:
            self._paint_actions(painter, painted, columns, index)
        painter.restore()

    def _paint_number(
        self,
        painter: QPainter,
        row: QRect,
        columns: RowColumns,
        index,
        playing: bool,
    ) -> None:
        """The row number, or a mini equaliser for the track that is sounding."""
        rect = QRect(columns.number_x, row.top(), columns.number_width, row.height())
        if playing:
            draw_equalizer(
                painter,
                QRectF(
                    rect.center().x() - 7,
                    rect.center().y() - 8,
                    14,
                    16,
                ),
                ACCENT,
            )
            return
        font = self._font(META_FONT_PX)
        painter.setFont(font)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), str(index.data(_ROLE_NUMBER) or ""))

    def _paint_texts(
        self,
        painter: QPainter,
        row: QRect,
        columns: RowColumns,
        index,
        playing: bool,
    ) -> None:
        title = str(index.data(_ROLE_TITLE) or "")
        subtitle = str(index.data(_ROLE_SUBTITLE) or "")
        title_rect = QRect(columns.text_x, row.top() + SPACE_MD, columns.text_width, 20)
        title_font = self._font(TITLE_FONT_PX, bold=True)
        painter.setFont(title_font)
        painter.setPen(QColor(ACCENT if playing else TEXT))
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )
        if not subtitle:
            return
        subtitle_rect = QRect(columns.text_x, title_rect.bottom() - 2, columns.text_width, 18)
        meta_font = self._font(META_FONT_PX)
        painter.setFont(meta_font)
        painter.setPen(QColor(TEXT_DIM))
        painter.drawText(
            subtitle_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(meta_font).elidedText(subtitle, Qt.TextElideMode.ElideRight, subtitle_rect.width()),
        )

    def _paint_album(self, painter: QPainter, row: QRect, columns: RowColumns, index) -> None:
        album = str(index.data(_ROLE_ALBUM) or "")
        if not album:
            return
        rect = QRect(columns.album_x, row.top(), columns.album_width, row.height())
        font = self._font(META_FONT_PX)
        painter.setFont(font)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(font).elidedText(album, Qt.TextElideMode.ElideRight, rect.width()),
        )

    def _paint_duration(
        self,
        painter: QPainter,
        row: QRect,
        columns: RowColumns,
        index,
        playing: bool,
    ) -> None:
        rect = QRect(columns.time_x, row.top(), columns.time_width, row.height())
        font = self._font(META_FONT_PX, mono=True)
        painter.setFont(font)
        painter.setPen(QColor(TEXT if playing else TEXT_DIM))
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            str(index.data(_ROLE_DURATION) or "—"),
        )

    def _paint_actions(self, painter: QPainter, row: QRect, columns: RowColumns, index) -> None:
        rect = QRect(columns.actions_x, row.top(), ACTIONS_COLUMN, row.height())
        liked = bool(index.data(_ROLE_LIKED))
        font = self._font(14, bold=True)
        painter.setFont(font)
        painter.setPen(QColor(LIKE_ACTIVE if liked else TEXT_MUTED))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), HEART if liked else HEART_OFF)
        if not index.data(_ROLE_EXPLICIT):
            return
        badge = QRect(rect.left() + 1, row.center().y() - 7, 14, 14)
        self._fill(painter, badge, QColor(TEXT_MUTED), 4)
        badge_font = self._font(9, bold=True)
        painter.setFont(badge_font)
        painter.setPen(QColor(PANEL))
        painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), EXPLICIT_GLYPH)

    def _paint_thumb(self, painter: QPainter, rect: QRect, index) -> None:
        """The row cover, or the gradient placeholder with a note on it."""
        pixmap: QPixmap | None = self._covers.get(_row_of(index))
        if pixmap is None:
            glyph = index.data(_ROLE_COVER)
            if isinstance(glyph, QPixmap) and not glyph.isNull():
                pixmap = glyph
        if pixmap is None or pixmap.isNull():
            painter.drawPixmap(rect, self._placeholder)
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), ROW_RADIUS, ROW_RADIUS)
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
    cover_url: str | None = None,
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
    item.setData(_ROLE_NUMBER, index + 1)
    item.setData(_ROLE_URL, cover_url or "")
    item.setToolTip(f"{title}\n{subtitle}" if subtitle else title)
    return item


def apply_row_delegate(list_widget: QListWidget) -> QListWidget:
    """Give any ``QListWidget`` the Midnight row look."""
    list_widget.setItemDelegate(TrackRowDelegate(list_widget))
    list_widget.setUniformItemSizes(True)
    list_widget.setAlternatingRowColors(False)
    list_widget.setMouseTracking(True)
    list_widget.setWordWrap(False)
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
        cover=cached_pixmap(cover_url),
        cover_url=cover_url,
    )
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


class TrackList(QListWidget):
    """Displays :class:`~core.yandex_service.WaveTrack` items and plays them."""

    track_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None, loader: CoverLoader | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrackList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        apply_row_delegate(self)
        self._tracks: list[WaveTrack] = []
        self._playing_id: str = ""
        self._loader = loader if loader is not None else CoverLoader(self)
        self._loader.loaded.connect(self._on_cover_loaded)
        self._cover_urls: list[str] = []
        self.itemActivated.connect(self._on_activated)
        self.itemDoubleClicked.connect(self._on_activated)

    @property
    def tracks(self) -> list[WaveTrack]:
        return list(self._tracks)

    @property
    def loader(self) -> CoverLoader:
        """The shared cover loader; the window stops it on shutdown."""
        return self._loader

    @property
    def current_track(self) -> WaveTrack | None:
        row = self.currentRow()
        if 0 <= row < len(self._tracks):
            return self._tracks[row]
        return None

    def set_tracks(self, tracks: Sequence[WaveTrack]) -> None:
        self.clear()
        self._tracks = []
        self._cover_urls = []
        for track in tracks:
            item = QListWidgetItem()
            url = entry_cover_url(track)
            _store_row(
                item,
                index=len(self._tracks),
                title=str(getattr(track, "title", "") or "Без названия"),
                subtitle=str(getattr(track, "artists_name", "") or getattr(track, "album", "") or ""),
                album=str(getattr(track, "album", "") or ""),
                duration_ms=int(getattr(track, "duration_ms", 0) or 0),
                liked=bool(getattr(track, "liked", False)),
                explicit=bool(getattr(track, "explicit", False)),
                cover=cached_pixmap(url),
                cover_url=url,
            )
            self.addItem(item)
            self._tracks.append(track)
            if url and url not in self._cover_urls:
                self._cover_urls.append(url)
        self.setEnabled(bool(self._tracks))
        self._apply_playing_rows()
        # Artwork is fetched after the rows exist, so a list of thirty tracks
        # paints its placeholders immediately instead of waiting for the net.
        for url in self._cover_urls:
            self._loader.request(url)

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

    def _on_cover_loaded(self, url: str, pixmap) -> None:
        """Apply one downloaded cover to every row that asked for it."""
        if not pixmap:
            return
        delegate = self.itemDelegate()
        if not isinstance(delegate, TrackRowDelegate):
            return
        for row in range(self.count()):
            item = self.item(row)
            if item is not None and item.data(_ROLE_URL) == url:
                delegate.set_cover(row, pixmap)

    def set_cover_pixmap(self, row: int, pixmap: QPixmap | None) -> None:
        """Show downloaded artwork on one track row."""
        delegate = self.itemDelegate()
        if isinstance(delegate, TrackRowDelegate):
            delegate.set_cover(row, pixmap)

    def set_placeholder(self, text: str) -> None:
        self.clear()
        self._tracks = []
        self._cover_urls = []
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
    "ALBUM_BREAKPOINT",
    "ACTIONS_BREAKPOINT",
    "ACTIONS_COLUMN",
    "ALBUM_COLUMN",
    "NUMBER_COLUMN",
    "TIME_COLUMN",
    "TrackList",
    "TrackRowDelegate",
    "apply_row_delegate",
    "entry_cover_url",
    "entry_item",
    "format_duration",
    "row_columns",
    "track_line",
    "track_line_text",
]
