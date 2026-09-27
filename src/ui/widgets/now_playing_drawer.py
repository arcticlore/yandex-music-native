"""The now-playing drawer: everything about the current track, in one panel.

Clicking the cover or the title in the player bar slides this out over the page.
It exists because the player bar can only hold 260px of metadata, and the
questions it cannot answer - which album, which year, what bitrate is this
actually streaming, how loud is it set - all belong together next to the art
rather than scattered across the settings page.

The drawer reads the track from the controller and holds no state of its own, so
it is always correct for whatever is sounding: the controller emits
``track_changed`` and the drawer re-reads.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.lyrics import Lyrics, parse_lrc
from core.playback_controller import PlaybackController, TrackMetadata
from ui.theme import (
    BADGE_HEIGHT,
    PAGE_PADDING,
    SPACE_MD,
    SPACE_SM,
    SPACE_XS,
)
from ui.widgets.cover_frame import CoverFrame
from ui.widgets.karaoke import GlowLabel, KaraokeView

DRAWER_WIDTH = 320
"""The panel is fixed-width so opening it never reflows the page underneath."""

DRAWER_COVER = 250
"""A 250px cover: the one place in the app where the art is the interface."""

LYRICS_MIN_HEIGHT = 150
LYRICS_MAX_HEIGHT = 300
"""The lyric column takes the room the cover leaves, but never the whole panel.

A karaoke view needs to be tall enough to show the current line with the one
before and after it - three lines of context - and no taller, because the cover
and the technical box above it are the parts of this panel people came for.
"""

__all__ = ["DRAWER_COVER", "DRAWER_WIDTH", "NowPlayingDrawer"]


def _format_bitrate(meta: TrackMetadata) -> str:
    """«1411 kbps» for a lossless link, «320 kbps» for a lossy one, «—» unknown."""
    if meta.bitrate:
        return f"{meta.bitrate} kbps"
    if meta.lossless:
        return "lossless"
    return "—"


def _format_codec(meta: TrackMetadata) -> str:
    """The codec, upper-cased, with FLAC called out as the lossless one."""
    codec = (meta.quality or "").strip().lower()
    if not codec:
        return "—"
    if codec in {"flac", "alac", "wav"}:
        return codec.upper()
    return codec.upper()


class _LyricsTextProxy:
    """Presents a :class:`KaraokeView` through the old ``QLabel`` surface.

    The drawer used to hold one label with the whole text in it.  Tests and the
    window still ask for ``drawer.lyrics_label.text()``, so rather than
    breaking that contract the karaoke view answers it: the words of the
    karaoke view, joined by newlines.
    """

    def __init__(self, view: KaraokeView, owner: QWidget) -> None:
        self._view = view
        self._owner = owner

    def text(self) -> str:
        return "\n".join(label.text() for label in self._view.findChildren(GlowLabel) if label.text())

    def setText(self, text: str) -> None:  # noqa: N802 - QLabel naming
        self._view.set_lyrics(parse_lrc(text))

    def isVisible(self) -> bool:  # noqa: N802 - QLabel naming
        return self._view.isVisible()

    def __getattr__(self, name: str):
        return getattr(self._view, name)


class NowPlayingDrawer(QFrame):
    """A slide-over panel describing the track that is sounding right now.

    ``album_requested`` and ``lyrics_requested`` are signals rather than actions:
    the drawer has no business deciding what «open the album» means, so the window
    that owns the navigation stack decides and this panel just reports the click.
    """

    album_requested = Signal(str, str)
    """(album title, artist name) - the window decides where that goes."""

    lyrics_requested = Signal(str)
    """(track id) - the window asks the service and fills the panel in."""

    lyrics_loaded = Signal(str, str)
    """(track id, text) - what came back, so the panel can show it."""

    closed = Signal()

    def __init__(self, controller: PlaybackController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._meta: TrackMetadata | None = None
        self._lyrics_track_id = ""
        self._lyrics = Lyrics()
        self._position_ms = 0
        self._animations_enabled: bool | None = None
        self.setObjectName("NowPlayingDrawer")
        self.setFixedWidth(DRAWER_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setVisible(False)
        self._build()
        self._connect(controller)

    # -- construction --------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(PAGE_PADDING, PAGE_PADDING, PAGE_PADDING, PAGE_PADDING)
        outer.setSpacing(SPACE_MD)

        self.heading = QLabel("Сейчас играет")
        self.heading.setObjectName("DrawerHeading")
        outer.addWidget(self.heading)

        self.cover = CoverFrame(DRAWER_COVER)
        self.cover.setObjectName("DrawerCover")
        # Centred: a 250px cover in a 320px panel has to be, or it reads as
        # accidentally left-aligned.
        outer.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignHCenter)

        self.title_label = QLabel("Ничего не играет")
        self.title_label.setObjectName("DrawerTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.title_label)

        self.artist_label = QLabel("—")
        self.artist_label.setObjectName("DrawerArtist")
        self.artist_label.setWordWrap(True)
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.artist_label)

        self.album_label = QLabel("—")
        self.album_label.setObjectName("DrawerAlbum")
        self.album_label.setWordWrap(True)
        self.album_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.album_label)

        self.year_label = QLabel("")
        self.year_label.setObjectName("DrawerYear")
        self.year_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.year_label.setFixedHeight(BADGE_HEIGHT)
        self.year_label.setVisible(False)
        outer.addWidget(self.year_label, 0, Qt.AlignmentFlag.AlignHCenter)

        outer.addSpacing(SPACE_SM)
        outer.addWidget(self._build_technical())
        outer.addStretch(1)
        outer.addWidget(self._build_actions())
        outer.addWidget(self._build_lyrics())

    def _build_technical(self) -> QWidget:
        """The stream facts: codec, bitrate and the current volume.

        They live in a hairline box rather than in the text ladder because they
        are diagnostics, not metadata - a listener does not want «mp3» competing
        with the artist name, but does want it when they go looking for it.
        """
        box = QFrame()
        box.setObjectName("DrawerTech")
        box.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_MD, SPACE_SM)
        layout.setSpacing(SPACE_XS)

        self.codec_label = self._tech_row(layout, "Формат", "—")
        self.bitrate_label = self._tech_row(layout, "Битрейт", "—")
        self.volume_label = self._tech_row(layout, "Громкость", "—")
        return box

    def _tech_row(self, layout: QVBoxLayout, name: str, value: str) -> QLabel:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        caption = QLabel(name)
        caption.setObjectName("DrawerTechCaption")
        field = QLabel(value)
        field.setObjectName("DrawerTechValue")
        field.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(caption, 1)
        row.addWidget(field, 0)
        layout.addLayout(row)
        return field

    def _build_actions(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_SM)

        self.album_button = QPushButton("К альбому")
        self.album_button.setObjectName("DrawerButton")
        self.album_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.album_button.setEnabled(False)
        self.album_button.clicked.connect(self._on_album)
        layout.addWidget(self.album_button, 1)

        self.lyrics_button = QPushButton("Текст")
        self.lyrics_button.setObjectName("DrawerButton")
        self.lyrics_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lyrics_button.setEnabled(False)
        self.lyrics_button.clicked.connect(self._on_lyrics)
        layout.addWidget(self.lyrics_button, 1)
        return row

    def _build_lyrics(self) -> QWidget:
        """The lyric panel: a karaoke column when the text has timings."""
        self.lyrics_view = KaraokeView()
        self.lyrics_view.setObjectName("DrawerLyrics")
        self.lyrics_view.setVisible(False)
        self.lyrics_view.setMinimumHeight(LYRICS_MIN_HEIGHT)
        self.lyrics_view.setMaximumHeight(LYRICS_MAX_HEIGHT)
        # The name the old plain label had is kept as a property so anything
        # outside this file that asked for the label still finds the text.
        self.lyrics_label = _LyricsTextProxy(self.lyrics_view, self)
        return self.lyrics_view

    def _connect(self, controller: PlaybackController) -> None:
        self._on_track(controller.current)
        controller.track_changed.connect(self._on_track)
        controller.cover_ready.connect(self._on_cover)
        controller.volume_changed.connect(self._on_volume)
        controller.position_changed.connect(self._on_position)
        self.lyrics_loaded.connect(self._on_lyrics_loaded)

    # -- content -------------------------------------------------------------

    def set_track(self, meta: TrackMetadata | None) -> None:
        """Show ``meta``; ``None`` empties the panel and disables the actions."""
        self._meta = meta
        self._position_ms = 0
        if meta is None:
            self.title_label.setText("Ничего не играет")
            self.artist_label.setText("—")
            self.album_label.setText("—")
            self.year_label.setVisible(False)
            self.codec_label.setText("—")
            self.bitrate_label.setText("—")
            self.album_button.setEnabled(False)
            self.lyrics_button.setEnabled(False)
            self.cover.set_pixmap(None)
            self._hide_lyrics()
            return

        self.title_label.setText(meta.title or "Без названия")
        self.artist_label.setText(meta.artists_name or "—")
        self.album_label.setText(meta.album or "—")
        year = meta.year_label
        self.year_label.setText(year)
        self.year_label.setVisible(bool(year))
        self.codec_label.setText(_format_codec(meta))
        self.bitrate_label.setText(_format_bitrate(meta))
        # The volume does not change when the track does, so it has to be read
        # here too: opening the drawer on a track that is already playing would
        # otherwise show a dash until the user nudged the slider.
        self.volume_label.setText(f"{self._controller.volume}%")
        self.album_button.setEnabled(bool(meta.album))
        self.lyrics_button.setEnabled(bool(meta.has_lyrics))
        # A new track invalidates any text still on screen from the last one.
        if self._lyrics_track_id and self._lyrics_track_id != meta.id:
            self._hide_lyrics()
        if meta.cover_path:
            self.set_cover_pixmap(QPixmap(meta.cover_path))

    def set_cover_pixmap(self, pixmap: QPixmap | None) -> None:
        """Put an already-loaded cover in the panel."""
        if pixmap is not None and not pixmap.isNull():
            self.cover.set_pixmap(pixmap)

    def set_lyrics(self, track_id: str, text: str) -> None:
        """Show the lyrics of ``track_id`` in the panel, hiding them when empty.

        The payload is parsed here rather than in the service, because whether
        the text can be followed in time is a property of the words, and the
        drawer is what has to follow them.  Untimed text is not a failure: it is
        shown as a static block.
        """
        if track_id != self._current_id:
            return
        lyrics = parse_lrc(text)
        if lyrics.empty:
            self._hide_lyrics()
            return
        self._lyrics_track_id = track_id
        self._lyrics = lyrics
        self.lyrics_view.set_lyrics(lyrics)
        self.lyrics_view.setVisible(True)
        # A track that is already part-way through needs its line lit at once;
        # waiting for the next position tick would leave the first line dark.
        if lyrics.synchronized:
            self.lyrics_view.set_position(int(self._controller.position_ms))
        if self._animations_enabled is not None:
            self.lyrics_view.set_animations_enabled(self._animations_enabled)

    def set_position(self, position_ms: int) -> None:
        """Follow the track: called on every position tick."""
        self._position_ms = int(position_ms)
        if self._lyrics_track_id and self.lyrics_view.isVisible():
            self.lyrics_view.set_position(int(position_ms))

    def set_animations_enabled(self, enabled: bool) -> None:
        """Follow the global switch while a lyric is on screen."""
        self._animations_enabled = bool(enabled)
        self.lyrics_view.set_animations_enabled(enabled)

    @property
    def lyrics(self) -> Lyrics:
        """The parsed lyric, so tests and the window can see what is shown."""
        return self._lyrics

    @property
    def synchronized(self) -> bool:
        return self._lyrics.synchronized

    @property
    def current_lyric_index(self) -> int:
        return self.lyrics_view.current_index

    def _hide_lyrics(self) -> None:
        self._lyrics_track_id = ""
        self._lyrics = Lyrics()
        self.lyrics_view.clear()
        self.lyrics_view.setVisible(False)

    @property
    def _current_id(self) -> str:
        """The id of the track on screen.

        This is the metadata the drawer was handed, not a re-read of the
        controller: what the panel shows and what it will accept lyrics for have
        to be the same track, even if the controller has already moved on.
        """
        return self._meta.id if self._meta is not None else ""

    # -- visibility ----------------------------------------------------------

    def open(self) -> None:
        """Show the panel, if there is anything to show."""
        if self._meta is None:
            return
        self.setVisible(True)
        self.raise_()

    def close_panel(self) -> None:
        # isVisible() would be False whenever an ancestor is hidden, which would
        # make this a no-op in exactly the case it matters: the window closing
        # over the drawer.  isHidden() asks the question that matters instead -
        # did the panel hide itself?
        if not self.isHidden():
            self.setVisible(False)
            self.closed.emit()

    def toggle(self) -> None:
        if self.isHidden():
            self.open()
        else:
            self.close_panel()

    # -- slots ---------------------------------------------------------------

    def _on_track(self, meta: object) -> None:
        self.set_track(meta if isinstance(meta, TrackMetadata) else None)

    def _on_cover(self, track_id: str, path: str) -> None:
        if not track_id or track_id != self._current_id:
            return
        self.set_cover_pixmap(QPixmap(path))

    def _on_volume(self, value: int) -> None:
        self.volume_label.setText(f"{int(value)}%")

    def _on_position(self, position_ms: int, _duration_ms: int = 0) -> None:
        self.set_position(position_ms)

    def _on_album(self) -> None:
        if self._meta is not None and self._meta.album:
            self.album_requested.emit(self._meta.album, self._meta.artists_name)

    def _on_lyrics(self) -> None:
        if self._meta is not None and self._meta.has_lyrics:
            self.lyrics_requested.emit(self._meta.id)

    def _on_lyrics_loaded(self, track_id: str, text: str) -> None:
        self.set_lyrics(track_id, text)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(DRAWER_WIDTH, 520)
