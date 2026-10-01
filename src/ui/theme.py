"""Midnight: the design tokens and the single style sheet of the app.

Everything visual is named here first.  Widgets and painters import these
constants instead of spelling hex codes, so the palette can be retuned in one
place and the components cannot drift apart.  Four families:

* colour - a ladder of dark surfaces (``BACKGROUND`` -> ``PANEL`` -> ``SURFACE``
  -> ``SURFACE_HOVER``) on a deep ``#0d0e15`` base, one hairline border, three
  steps of text contrast, and two accents: the warm Yandex gold for anything
  pressable and the violet/pink pair for «Моя волна»;
* geometry - one page padding, one control height, one row height, one cover
  size per context and one spacing scale (4/8/12/16/24);
* radii - panels, controls, rows and covers each get a single radius, and every
  pill is its own half-height rather than an over-large radius, which Qt cannot
  draw;
* state tints - the playing row, the hover row and the segmented control are
  translucencies of the tokens above, never new colours.

Qt style sheets have no variables, so the sheet below is an f-string over these
names: a token that is not interpolated simply is not in the sheet.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from PySide6.QtGui import QColor, QPalette


def _alpha(colour: str, opacity: float) -> str:
    """``#FFDB4D`` + ``0.14`` -> ``rgba(255, 219, 77, 0.14)``.

    Tinted overlays are derived from a token instead of being written out, so
    the sheet never contains a raw colour of its own.
    """
    text = colour.lstrip("#")
    red, green, blue = (int(text[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {opacity:g})"


def parse_rgba(token: str) -> tuple[int, int, int, float]:
    """``rgba(255, 219, 77, 0.08)`` -> ``(255, 219, 77, 0.08)``.

    A widget that paints a token itself needs the very numbers the sheet gets.
    ``QColor`` will not parse these strings - it wants whole components and reads
    ``0.08`` as invalid, which paints black - so the format is parsed in one
    place instead of at every call site.
    """
    inner = token[token.index("(") + 1 : token.rindex(")")]
    parts = [part.strip() for part in inner.split(",")]
    red, green, blue = (int(float(part)) for part in parts[:3])
    return red, green, blue, float(parts[3])


def tint(base: str, overlay: str) -> QColor:
    """Mix an ``rgba(...)`` overlay onto ``base`` and return an opaque colour.

    Painting the two in sequence is not an option: a translucent brush over a
    freshly cleared backing store composites against whatever was there before,
    so a row wash can come out as a black band. Mixing the numbers keeps a
    hand-painted surface the exact colour the style sheet asks for.
    """
    red, green, blue, opacity = parse_rgba(overlay)
    under = QColor(base)
    return QColor(
        round(under.red() * (1.0 - opacity) + red * opacity),
        round(under.green() * (1.0 - opacity) + green * opacity),
        round(under.blue() * (1.0 - opacity) + blue * opacity),
    )


# -- colour: surfaces --------------------------------------------------------

BACKGROUND = "#0D0E15"
"""Window background and the darkest step of every ladder."""

SIDEBAR_BACKGROUND = "#11121A"
"""Sidebar and the player bar, so the two read as one frame around the page."""

PANEL = "#14151F"
"""Page panels, list rows at rest, the visualiser canvas."""

SURFACE = "#1C1E2B"
"""Controls: buttons, inputs, chips, the profile card."""

SURFACE_HOVER = "#262636"
"""Selected row, active navigation, the active segment of a segmented control."""

SEGMENT_BG = "#171824"
"""The single dark backing a segmented control is carved out of."""

BORDER = "#232533"
BORDER_STRONG = SURFACE_HOVER
CHIP_BORDER = "#2A2A38"
"""One hairline; the strong variant is the hover surface, chips carry their own."""

CHIP_BG = "#1A1C26"
CHIP_INK = "#E0E0E0"
"""An unselected chip: a touch darker than the surface it sits on, and light ink.

The chip row is the only place in the app where a label sits on the *quietest*
surface, so ``TEXT_DIM`` left it reading as disabled.  These two tokens make the
row legible without touching the text ladder the rest of the window is built on.
"""

# -- colour: text ------------------------------------------------------------

TEXT = "#FFFFFF"
TEXT_DIM = "#9A9AAE"
TEXT_MUTED = "#6A6C80"
"""Three contrast steps: primary, secondary, and the quietest one."""

# -- colour: accents ---------------------------------------------------------

ACCENT = "#FFDB4D"
ACCENT_END = "#FFA300"
ACCENT_HOVER = "#FFE480"
ACCENT_HOVER_END = "#FFB733"
ACCENT_INK = BACKGROUND
"""Text drawn on the accent, i.e. the background colour again."""

ACCENT_SOFT = "#FF2A74"
WAVE_VIOLET = "#B845ED"
WAVE_PINK = "#FF2A74"
WAVE_INK = "#E6B6FF"
LIKE_ACTIVE = "#FF3366"

DANGER = "#FF6B6B"
SUCCESS = "#5AD19B"
"""Semantic status colours for messages that are neither neutral nor accented."""

# -- colour: derived overlays ------------------------------------------------

ACCENT_BG = _alpha(ACCENT, 0.14)
ACCENT_BORDER = _alpha(ACCENT, 0.30)
ACCENT_FOCUS = _alpha(ACCENT, 0.45)
PLAYING_BG = _alpha(ACCENT, 0.08)
"""The soft gold wash of the row that is currently sounding."""

ROW_HOVER = _alpha(TEXT, 0.04)
"""A pointer over a row lifts it by four percent of white, nothing more."""

LIKE_GLOW = _alpha(LIKE_ACTIVE, 0.22)
DISLIKE_GLOW = _alpha(DANGER, 0.22)
"""The wash behind a disliked track.

Derived from ``DANGER`` rather than from ``LIKE_ACTIVE``: a dislike and a like
are both «the user has an opinion about this track», and two buttons glowing in
the same pink side by side would say nothing about which is which.  The shape
carries that - a whole heart against a broken one.
"""
WAVE_BG = _alpha(WAVE_VIOLET, 0.16)
WAVE_BORDER = _alpha(WAVE_VIOLET, 0.55)
GROOVE = _alpha(TEXT, 0.10)
HANDLE = _alpha(TEXT, 0.14)
HANDLE_HOVER = _alpha(TEXT, 0.24)

# -- geometry ----------------------------------------------------------------

PAGE_PADDING = 24
"""Every page starts with the same inset."""

CONTROL_HEIGHT = 48
"""Buttons, inputs and the header row."""

ICON_BUTTON = 36
"""Icon-only buttons: shuffle, prev, next, repeat, mute, visualiser mode."""

SEGMENT_HEIGHT = 40
"""One segment of a segmented control."""

ROW_HEIGHT = 56
"""One list row: 40px cover plus 8px of breathing room above and below."""

COVER_SIZE = 40
LIST_COVER_SIZE = 40
PLAYER_COVER_SIZE = 54
"""The cover in the player bar is larger than the one in a list row."""

SIDEBAR_WIDTH = 228
NAV_ITEM_HEIGHT = 40
PLAY_BUTTON_SIZE = 40
"""The play / pause button is a 40px circle with a 20px radius."""

PLAYER_BAR_HEIGHT = 96
"""14px padding + 36px transport + 10px gap + 20px seek row + 16px padding."""

PLAYER_LEFT_WIDTH = 260
PLAYER_RIGHT_WIDTH = 240
"""The two fixed sections of the player bar; the transport takes the rest."""

VOLUME_SLIDER_WIDTH = 80
"""The volume slider is a compact strip next to the mute button."""

TIME_LABEL_WIDTH = 40

BADGE_HEIGHT = 20
"""A one-line micro badge: the Plus badge and the FLAC / HQ flag."""

CHIP_HEIGHT = 32
"""Filter chips: 30px of content plus a 1px border on each side."""

# -- radii -------------------------------------------------------------------

PANEL_RADIUS = 12
CONTROL_RADIUS = 10
ROW_RADIUS = 8
COVER_RADIUS = 8
SEGMENT_RADIUS = 10


def pill_radius(height: int) -> int:
    """The radius that turns a box of ``height`` px into a pill.

    Qt draws a rounded rect with ``addRoundedRect``, and a radius larger than
    half the side makes that path self-intersecting: the corner comes out square
    or torn. So a pill is spelled as its own half-height, never as ``999px``.
    """
    return height // 2


CHIP_RADIUS = pill_radius(CHIP_HEIGHT)
"""16px, i.e. a full pill for a 32px chip."""

RADIUS_LIST_COVER = pill_radius(LIST_COVER_SIZE)
RADIUS_BADGE = pill_radius(BADGE_HEIGHT)
RADIUS_ICON_BUTTON = pill_radius(ICON_BUTTON)
RADIUS_PLAY_BUTTON = pill_radius(PLAY_BUTTON_SIZE)
"""The pill radii the sheet asks for by name, so it can hold ``<<MARKERS>>``.

A style sheet is a plain string and cannot call :func:`pill_radius`, and the
heights it needs are fixed geometry rather than a theme choice - so they are
resolved here once and referenced from the template like any other token.
"""

ICON_BUTTON_BOX = ICON_BUTTON - 2
STATUS_BUTTON_HEIGHT = ICON_BUTTON - 12
CONTROL_BOX = CONTROL_HEIGHT - 2
SEGMENT_CONTENT_HEIGHT = SEGMENT_HEIGHT - 8
CHIP_BOX = CHIP_HEIGHT - 2
"""Inner box heights, i.e. a control less its 1px border on each side.

The sheet writes ``min-height: 46px`` for a 48px control.  Doing the arithmetic
in the template instead would mean calling into it, so each result is a token.
"""

# -- spacing scale -----------------------------------------------------------

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

SPACE_XXL = SPACE_XL * 2

QSS_TEMPLATE = """
/* -- base: colour and metrics only, so a parent background shows through --- */
QWidget {
    color: <<TEXT>>;
    font-size: 14px;
}
QMainWindow, QDialog, QStackedWidget, QScrollArea {
    background: <<BACKGROUND>>;
}
QLabel { background: transparent; }
QToolTip {
    background: <<SURFACE>>;
    color: <<TEXT>>;
    border: 1px solid <<BORDER_STRONG>>;
    border-radius: <<ROW_RADIUS>>px;
    padding: <<SPACE_SM>>px <<SPACE_MD>>px;
    font-size: 12px;
}

/* -- sidebar ---------------------------------------------------------------- */
QFrame#Sidebar {
    background: <<SIDEBAR_BACKGROUND>>;
    border-right: 1px solid <<BORDER>>;
}
QLabel#Brand {
    font-size: 17px;
    font-weight: 700;
    color: <<TEXT>>;
}
QFrame#NavRow {
    background: transparent;
    border-radius: <<CONTROL_RADIUS>>px;
}
QFrame#NavRow:hover { background: <<SURFACE>>; }
QFrame#NavRow[active="true"] { background: <<SURFACE_HOVER>>; }
QLabel#NavAccent { background: transparent; border-radius: 2px; }
QLabel#NavAccent[active="true"] {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 <<ACCENT>>, stop:1 <<ACCENT_END>>);
}
QPushButton#NavButton {
    background: transparent;
    border: none;
    border-radius: <<CONTROL_RADIUS>>px;
    color: <<TEXT_DIM>>;
    font-size: 14px;
    font-weight: 500;
    text-align: left;
    padding-left: <<SPACE_MD>>px;
}
QPushButton#NavButton:hover { color: <<TEXT>>; }
QFrame#NavRow[active="true"] QPushButton#NavButton {
    color: <<TEXT>>;
    font-weight: 600;
}
QFrame#ProfileCard {
    background: <<SURFACE>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<PANEL_RADIUS>>px;
}
/* A settings card is a subject, not a decoration: one surface, one border, and
   a heading that is louder than anything inside it. The left accent is the only
   thing that carries the accent colour, so the page does not turn into a row of
   coloured boxes. */
QFrame#SettingsCard {
    background: <<SURFACE>>;
    border: 1px solid <<BORDER>>;
    border-left: 2px solid <<ACCENT>>;
    border-radius: <<PANEL_RADIUS>>px;
}
QLabel#SettingsCardTitle {
    color: <<TEXT>>;
    font-size: 15px;
    font-weight: 600;
}
QLabel#ProfileAvatar {
    background: <<SURFACE_HOVER>>;
    border-radius: <<RADIUS_LIST_COVER>>px;
    border: 1px solid <<BORDER>>;
}
QLabel#ProfileName {
    color: <<TEXT>>;
    font-size: 13px;
    font-weight: 600;
}
QLabel#ProfileHint { color: <<TEXT_MUTED>>; font-size: 11px; }
QLabel#PlusBadge {
    background: <<ACCENT_BG>>;
    color: <<ACCENT>>;
    border: 1px solid <<ACCENT_BORDER>>;
    border-radius: <<RADIUS_BADGE>>px;
    padding: 0 <<SPACE_SM>>px;
    font-size: 10px;
    font-weight: 700;
}

/* -- player bar: three balanced sections in one continuous panel ------------ */
QFrame#PlayerBar {
    background: <<SIDEBAR_BACKGROUND>>;
    border-top: 1px solid <<BORDER>>;
}
QWidget#NowPlaying, QWidget#Transport, QWidget#VolumePanel {
    background: transparent;
}
QLabel#TrackTitle {
    color: <<TEXT>>;
    font-size: 13px;
    font-weight: 700;
}
QLabel#TrackArtist { color: <<TEXT_DIM>>; font-size: 11px; }
QLabel#QualityBadge {
    background: <<ACCENT_BG>>;
    color: <<ACCENT>>;
    border: 1px solid <<ACCENT_BORDER>>;
    border-radius: <<RADIUS_BADGE>>px;
    padding: 0 6px;
    font-size: 9px;
    font-weight: 700;
}
QLabel#QualityBadge[lossless="false"] {
    background: <<WAVE_BG>>;
    color: <<WAVE_INK>>;
    border: 1px solid <<WAVE_BORDER>>;
}
/* A switch waiting for the next track: the pill still reports what is playing,
   so it is dimmed and dashed rather than relabelled with a quality that is not
   in the stream yet. */
QLabel#QualityBadge[pending="true"] {
    background: transparent;
    border: 1px dashed <<TEXT_MUTED>>;
    color: <<TEXT_DIM>>;
}
QLabel#TimeLabel {
    color: <<TEXT_DIM>>;
    font-size: 11px;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
}
/* The sheet carries a min-height for text buttons, and a style sheet size wins
   over setFixedSize, so every square icon button spells out its own 36x36. The
   two bordered pills lose 2px, because their 1px frame sits outside the size. */
QPushButton#TransportButton, QPushButton#ShuffleButton, QPushButton#RepeatButton,
QPushButton#ModeButton, QPushButton#LikeButton, QPushButton#DislikeButton {
    min-width: <<ICON_BUTTON>>px;
    max-width: <<ICON_BUTTON>>px;
    min-height: <<ICON_BUTTON>>px;
    max-height: <<ICON_BUTTON>>px;
    padding: 0;
}
QPushButton#ShuffleButton, QPushButton#RepeatButton, QPushButton#ModeButton {
    min-width: <<ICON_BUTTON_BOX>>px;
    max-width: <<ICON_BUTTON_BOX>>px;
    min-height: <<ICON_BUTTON_BOX>>px;
    max-height: <<ICON_BUTTON_BOX>>px;
}
/* A button whose whole face is an icon has no text to fill it, so the style
   falls back to the palette's button brush and paints an opaque box where the
   heart beside it shows the page through.  Naming the transparency here is what
   keeps the two marks looking like two marks. */
QPushButton#DislikeButton {
    background: transparent;
    border: none;
    border-radius: <<RADIUS_ICON_BUTTON>>px;
}
QPushButton#TransportButton {
    background: transparent;
    border: none;
    border-radius: <<RADIUS_ICON_BUTTON>>px;
    color: <<TEXT_DIM>>;
    font-size: 14px;
}
QPushButton#TransportButton:hover { background: <<SURFACE_HOVER>>; color: <<TEXT>>; }
QPushButton#TransportButton:checked { color: <<ACCENT>>; background: transparent; }
QPushButton#TransportButton:disabled { color: <<TEXT_MUTED>>; background: transparent; }
QPushButton#PlayButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 <<ACCENT>>, stop:1 <<ACCENT_END>>);
    border: none;
    /* A style sheet size wins over setFixedSize, so the circle is spelled out:
       40x40 with a 20px radius is exactly round, with no seam. */
    min-width: <<PLAY_BUTTON_SIZE>>px;
    max-width: <<PLAY_BUTTON_SIZE>>px;
    min-height: <<PLAY_BUTTON_SIZE>>px;
    max-height: <<PLAY_BUTTON_SIZE>>px;
    border-radius: <<RADIUS_PLAY_BUTTON>>px;
    padding: 0;
    color: <<ACCENT_INK>>;
    font-size: 14px;
    font-weight: 700;
}
QPushButton#PlayButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 <<ACCENT_HOVER>>, stop:1 <<ACCENT_HOVER_END>>);
}
QPushButton#PlayButton:disabled { background: <<SURFACE_HOVER>>; color: <<TEXT_MUTED>>; }
QPushButton#ShuffleButton, QPushButton#RepeatButton {
    background: <<SURFACE>>;
    border: 1px solid <<CHIP_BORDER>>;
    border-radius: <<RADIUS_ICON_BUTTON>>px;
    color: <<TEXT_DIM>>;
}
QPushButton#ShuffleButton:hover, QPushButton#RepeatButton:hover {
    background: <<SURFACE_HOVER>>;
    color: <<TEXT>>;
}
/* Shuffle and repeat are toggles, so the active state has to be visible at a
   glance: the pill lights up in accent instead of relying on a caption. */
QPushButton#ShuffleButton:checked, QPushButton#RepeatButton:checked {
    background: <<ACCENT_BG>>;
    border-color: <<ACCENT_BORDER>>;
    color: <<ACCENT>>;
}
QPushButton#LikeButton {
    background: transparent;
    border: none;
    border-radius: <<RADIUS_ICON_BUTTON>>px;
    color: <<TEXT_DIM>>;
    font-size: 15px;
}
QPushButton#LikeButton:hover { background: <<SURFACE_HOVER>>; }
QPushButton#LikeButton:disabled { color: <<TEXT_MUTED>>; background: transparent; }
QPushButton#ModeButton {
    background: <<SURFACE>>;
    border: 1px solid <<CHIP_BORDER>>;
    border-radius: <<CONTROL_RADIUS>>px;
    color: <<TEXT_DIM>>;
}
QPushButton#ModeButton:hover { background: <<SURFACE_HOVER>>; color: <<TEXT>>; }
QPushButton#ModeButton:checked { color: <<ACCENT>>; border-color: <<ACCENT_BORDER>>; }

/* -- sliders: a 4px track that thickens under the pointer ------------------ */
QSlider::groove:horizontal {
    background: <<GROOVE>>;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 <<ACCENT>>, stop:1 <<ACCENT_END>>);
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: <<TEXT>>;
    border-radius: 6px;
    width: 12px;
    margin: -4px 0;
}
QSlider#SeekSlider::groove:horizontal { height: 4px; }
QSlider#SeekSlider::groove:horizontal:hover { height: 6px; }
QSlider#SeekSlider::sub-page:horizontal { height: 4px; }
QSlider#SeekSlider::sub-page:horizontal:hover { height: 6px; }
QSlider#SeekSlider::handle:horizontal { width: 12px; margin: -4px 0; }
QSlider#SeekSlider::handle:horizontal:hover {
    background: <<ACCENT>>;
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QSlider#VolumeSlider::groove:horizontal { height: 4px; }
QSlider#VolumeSlider::groove:horizontal:hover { height: 6px; }
QSlider#VolumeSlider::sub-page:horizontal { height: 4px; }
QSlider#VolumeSlider::sub-page:horizontal:hover { height: 6px; }
QSlider#VolumeSlider::handle:horizontal {
    width: 10px;
    margin: -3px 0;
    background: <<TEXT_DIM>>;
}
QSlider#VolumeSlider::handle:horizontal:hover { background: <<ACCENT>>; }

/* -- pages ------------------------------------------------------------------ */
QFrame#WaveCard, QFrame#Card, QFrame#ResultsPanel {
    background: <<PANEL>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<PANEL_RADIUS>>px;
}
QFrame#WaveCard, QFrame#Card {
    background: <<PANEL>>;
}
QLabel#PageTitle { color: <<TEXT>>; font-size: 26px; font-weight: 700; }
QLabel#Dim { color: <<TEXT_DIM>>; font-size: 13px; }
QLabel#Hint { color: <<TEXT_MUTED>>; font-size: 12px; }

/* -- segmented control: one backing, one lit segment ----------------------- */
QFrame#SegmentBar {
    background: <<SEGMENT_BG>>;
    border: 1px solid <<CHIP_BORDER>>;
    border-radius: <<PANEL_RADIUS>>px;
}
QPushButton#Segment {
    background: transparent;
    border: none;
    border-radius: <<SEGMENT_RADIUS>>px;
    min-height: <<SEGMENT_CONTENT_HEIGHT>>px;
    padding: 0 <<SPACE_LG>>px;
    color: <<TEXT_DIM>>;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#Segment:hover { color: <<TEXT>>; background: <<ROW_HOVER>>; }
QPushButton#Segment:checked {
    background: <<SURFACE_HOVER>>;
    color: <<TEXT>>;
    font-weight: 700;
}

/* -- controls: one height, one radius --------------------------------------- */
QPushButton {
    background: <<SURFACE>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<CONTROL_RADIUS>>px;
    min-height: <<CONTROL_BOX>>px;
    padding: 0 <<SPACE_LG>>px;
    color: <<TEXT>>;
    font-weight: 500;
}
QPushButton:hover { background: <<SURFACE_HOVER>>; }
QPushButton:disabled { color: <<TEXT_MUTED>>; border-color: <<BORDER>>; }
QPushButton#Accent, QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 <<ACCENT>>, stop:1 <<ACCENT_END>>);
    color: <<ACCENT_INK>>;
    border: none;
    font-weight: 700;
}
QPushButton#Accent:hover, QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 <<ACCENT_HOVER>>, stop:1 <<ACCENT_HOVER_END>>);
}
QPushButton#Accent:disabled, QPushButton#Primary:disabled {
    background: <<SURFACE_HOVER>>;
    color: <<TEXT_MUTED>>;
}
/* 6px of vertical padding around a 20px line of text is the 32px chip; the
   14px sides are what keeps a word like «Спокойное» from touching its border.
   min-width is deliberately absent: a chip is as wide as its label, and the
   FlowLayout above wraps a row that does not fit instead of clipping it. */
QPushButton#Chip {
    background: <<CHIP_BG>>;
    border: 1px solid <<CHIP_BORDER>>;
    border-radius: <<CHIP_RADIUS>>px;
    min-height: <<CHIP_BOX>>px;
    padding: 6px 14px;
    color: <<CHIP_INK>>;
    font-size: 13px;
    font-weight: 500;
}
QPushButton#Chip:hover { background: <<SURFACE_HOVER>>; color: <<TEXT>>; }
QPushButton#Chip:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 <<ACCENT>>, stop:1 <<ACCENT_END>>);
    border: 1px solid <<ACCENT>>;
    color: <<ACCENT_INK>>;
    font-weight: 700;
}
QPushButton#Chip:checked:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 <<ACCENT_HOVER>>, stop:1 <<ACCENT_HOVER_END>>);
}
/* A disabled chip is the one state where the dim ink is the right answer: the
   control cannot be reached, so looking muted is the truth. */
QPushButton#Chip:disabled { color: <<TEXT_MUTED>>; background: <<CHIP_BG>>; }
QLineEdit, QSpinBox {
    background: <<SURFACE>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<CONTROL_RADIUS>>px;
    min-height: <<CONTROL_BOX>>px;
    padding: 0 <<SPACE_LG>>px;
    color: <<TEXT>>;
    selection-background-color: <<ACCENT>>;
    selection-color: <<ACCENT_INK>>;
    outline: none;
}
QLineEdit#SearchInput { font-size: 15px; padding-left: 44px; }
QLineEdit#SearchInput:hover { border-color: <<BORDER_STRONG>>; }
QLineEdit:focus, QSpinBox:focus { border: 1px solid <<ACCENT_FOCUS>>; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; border: none; }

/* -- lists: the rows paint themselves, the widget only frames them ---------- */
QListWidget, QTreeView {
    background: <<PANEL>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<PANEL_RADIUS>>px;
    color: <<TEXT>>;
    outline: none;
}
QListWidget#TrackList, QTreeView#TrackList {
    background: <<PANEL>>;
    border: none;
    border-radius: 0;
}
QListWidget::item, QTreeView::item {
    border: none;
    color: <<TEXT>>;
    padding: 0 <<SPACE_LG>>px;
}
QListWidget::item:selected, QTreeView::item:selected {
    background: <<SURFACE_HOVER>>;
    color: <<TEXT>>;
}

/* -- the tab widget keeps its pages, the segments above it do the switching - */
QTabWidget::pane {
    background: transparent;
    border: none;
    top: 0;
}
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent;
    color: <<TEXT_DIM>>;
    min-height: <<CONTROL_HEIGHT>>px;
    padding: 0 <<SPACE_LG>>px;
    border: none;
}

/* -- switches and scrollbars ------------------------------------------------ */
QCheckBox, QRadioButton { spacing: <<SPACE_MD>>px; color: <<TEXT_DIM>>; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 18px;
    height: 18px;
    border-radius: <<ROW_RADIUS>>px;
    border: 1px solid <<BORDER_STRONG>>;
    background: <<SURFACE>>;
}
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 <<ACCENT>>, stop:1 <<ACCENT_END>>);
    border-color: <<ACCENT_END>>;
}
QRadioButton::indicator { border-radius: 9px; }
QRadioButton::indicator:checked {
    background: <<ACCENT>>;
    border: 4px solid <<SIDEBAR_BACKGROUND>>;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0 2px 0 0;
    border: none;
}
QScrollBar::handle:vertical {
    background: <<HANDLE>>;
    border-radius: 5px;
    min-height: <<SPACE_XXL>>px;
}
QScrollBar::handle:vertical:hover { background: <<HANDLE_HOVER>>; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px 0 0 0;
    border: none;
}
QScrollBar::handle:horizontal {
    background: <<HANDLE>>;
    border-radius: 5px;
    min-width: <<SPACE_XXL>>px;
}
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }

/* -- footer: the message strip belongs to the frame, not to the page ------- */
QStatusBar {
    background: <<SIDEBAR_BACKGROUND>>;
    color: <<TEXT_DIM>>;
    border-top: none;
    font-size: 12px;
}
QStatusBar::item { border: none; }
QStatusBar QPushButton {
    min-height: <<STATUS_BUTTON_HEIGHT>>px;
    padding: 0 <<SPACE_MD>>px;
    border-radius: <<ROW_RADIUS>>px;
    font-size: 12px;
}

/* -- now-playing drawer: a slide-over, not a page --------------------------- */
QFrame#NowPlayingDrawer {
    background: <<SIDEBAR_BACKGROUND>>;
    border-left: 1px solid <<BORDER>>;
}
QLabel#DrawerHeading {
    color: <<TEXT_MUTED>>;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#DrawerTitle {
    color: <<TEXT>>;
    font-size: 16px;
    font-weight: 700;
}
QLabel#DrawerArtist { color: <<TEXT_DIM>>; font-size: 12px; }
QLabel#DrawerAlbum { color: <<TEXT_MUTED>>; font-size: 12px; }
QLabel#DrawerYear {
    color: <<TEXT_DIM>>;
    background: <<SURFACE>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<RADIUS_BADGE>>px;
    padding: 0 8px;
    font-size: 10px;
    font-weight: 700;
}
/* The stream facts read as a spec sheet: boxed, monospaced on the right. */
QFrame#DrawerTech {
    background: <<PANEL>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<PANEL_RADIUS>>px;
}
QLabel#DrawerTechCaption { color: <<TEXT_MUTED>>; font-size: 11px; }
QLabel#DrawerTechValue {
    color: <<TEXT_DIM>>;
    font-size: 11px;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
}
QPushButton#DrawerButton {
    background: <<SURFACE>>;
    color: <<TEXT_DIM>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<ROW_RADIUS>>px;
    padding: 0 <<SPACE_MD>>px;
    min-height: 32px;
    font-size: 12px;
}
QPushButton#DrawerButton:hover { background: <<SURFACE_HOVER>>; color: <<TEXT>>; }
QPushButton#DrawerButton:disabled { color: <<TEXT_MUTED>>; background: <<SEGMENT_BG>>; }
QLabel#DrawerLyrics {
    color: <<TEXT_DIM>>;
    background: <<PANEL>>;
    border: 1px solid <<BORDER>>;
    border-radius: <<ROW_RADIUS>>px;
    padding: <<SPACE_SM>>px;
    font-size: 11px;
}
"""
# -- themes -------------------------------------------------------------------

MARKER = re.compile(r"<<([A-Z_][A-Z0-9_]*)>>")
"""``<<NAME>>`` - a token slot in the sheet.

Deliberately not ``{NAME}``: a style sheet is full of literal braces, and an
f-string would need every one of them escaped, which makes the sheet unreadable
and easy to break.  A marker is inert in a plain string, greppable, and
:func:`render_qss` refuses to emit a sheet that still has one.
"""

_DERIVED = frozenset(
    {
        "ACCENT_BG",
        "ACCENT_BORDER",
        "ACCENT_FOCUS",
        "PLAYING_BG",
        "ROW_HOVER",
        "LIKE_GLOW",
        "DISLIKE_GLOW",
        "WAVE_BG",
        "WAVE_BORDER",
        "GROOVE",
        "HANDLE",
        "HANDLE_HOVER",
    }
)
"""Overlays that are translucencies of the colours above, not colours of their own."""


class ThemeTokens(Mapping):
    """The colours one theme paints with.

    A theme chooses colours and nothing else: sizes, radii and the spacing scale
    are the app's geometry and do not change between Obsidian, Cyberpunk and
    OLED, so a theme cannot quietly ship a different layout.  Anything the sheet
    asks for that is not a raw colour is derived here, which keeps the rule that
    the sheet never contains a colour of its own.
    """

    REQUIRED = frozenset(
        {
            "BACKGROUND",
            "SIDEBAR_BACKGROUND",
            "PANEL",
            "SURFACE",
            "SURFACE_HOVER",
            "SEGMENT_BG",
            "BORDER",
            "CHIP_BORDER",
            "CHIP_BG",
            "CHIP_INK",
            "TEXT",
            "TEXT_DIM",
            "TEXT_MUTED",
            "ACCENT",
            "ACCENT_END",
            "ACCENT_HOVER",
            "ACCENT_HOVER_END",
            "ACCENT_INK",
            "ACCENT_SOFT",
            "WAVE_VIOLET",
            "WAVE_PINK",
            "WAVE_INK",
            "LIKE_ACTIVE",
            "DANGER",
            "SUCCESS",
        }
    )
    """The colours a theme must state for itself to be complete."""

    def __init__(self, name: str, label: str, colours: dict[str, str]) -> None:
        missing = sorted(self.REQUIRED - set(colours))
        if missing:
            raise ValueError(f"theme {name!r} is missing colours: {', '.join(missing)}")
        self.name = name
        self.label = label
        self._colours = dict(colours)
        self._cache: dict[str, str] = {}

    def _derive(self, key: str) -> str:
        colours = self._colours
        if key == "BORDER_STRONG":
            return colours["SURFACE_HOVER"]
        if key == "ACCENT_BG":
            return _alpha(colours["ACCENT"], 0.14)
        if key == "ACCENT_BORDER":
            return _alpha(colours["ACCENT"], 0.30)
        if key == "ACCENT_FOCUS":
            return _alpha(colours["ACCENT"], 0.45)
        if key == "PLAYING_BG":
            return _alpha(colours["ACCENT"], 0.08)
        if key == "ROW_HOVER":
            return _alpha(colours["TEXT"], 0.04)
        if key == "LIKE_GLOW":
            return _alpha(colours["LIKE_ACTIVE"], 0.22)
        if key == "DISLIKE_GLOW":
            return _alpha(colours["DANGER"], 0.22)
        if key == "WAVE_BG":
            return _alpha(colours["WAVE_VIOLET"], 0.16)
        if key == "WAVE_BORDER":
            return _alpha(colours["WAVE_VIOLET"], 0.55)
        if key == "GROOVE":
            return _alpha(colours["TEXT"], 0.10)
        if key == "HANDLE":
            return _alpha(colours["TEXT"], 0.14)
        if key == "HANDLE_HOVER":
            return _alpha(colours["TEXT"], 0.24)
        raise KeyError(key)

    def __getitem__(self, key: str) -> str:
        if key in self._colours:
            return self._colours[key]
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        value = self._derive(key)
        self._cache[key] = value
        return value

    def __iter__(self):
        return iter(self._colours)

    def __len__(self) -> int:
        return len(self._colours)

    def has(self, key: str) -> bool:
        """Whether ``key`` is a colour of this theme or one it can derive."""
        return key in self._colours or key in _DERIVED

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ThemeTokens {self.name!r}>"


_OBSIDIAN = {
    "BACKGROUND": "#0D0E15",
    "SIDEBAR_BACKGROUND": "#11121A",
    "PANEL": "#14151F",
    "SURFACE": "#1C1E2B",
    "SURFACE_HOVER": "#262636",
    "SEGMENT_BG": "#171824",
    "BORDER": "#232533",
    "CHIP_BORDER": "#2A2A38",
    "CHIP_BG": "#1A1C26",
    "CHIP_INK": "#E0E0E0",
    "TEXT": "#FFFFFF",
    "TEXT_DIM": "#9A9AAE",
    "TEXT_MUTED": "#6A6C80",
    "ACCENT": "#FFDB4D",
    "ACCENT_END": "#FFA300",
    "ACCENT_HOVER": "#FFE480",
    "ACCENT_HOVER_END": "#FFB733",
    "ACCENT_INK": "#0D0E15",
    "ACCENT_SOFT": "#FF2A74",
    "WAVE_VIOLET": "#B845ED",
    "WAVE_PINK": "#FF2A74",
    "WAVE_INK": "#E6B6FF",
    "LIKE_ACTIVE": "#FF3366",
    "DANGER": "#FF6B6B",
    "SUCCESS": "#5AD19B",
}

_CYBERPUNK = {
    "BACKGROUND": "#07040F",
    "SIDEBAR_BACKGROUND": "#0C0719",
    "PANEL": "#120B26",
    "SURFACE": "#1B1038",
    "SURFACE_HOVER": "#2A1656",
    "SEGMENT_BG": "#150D2E",
    "BORDER": "#2E1C5C",
    "CHIP_BORDER": "#3B2280",
    "CHIP_BG": "#1E1140",
    "CHIP_INK": "#E8DCFF",
    "TEXT": "#F4EEFF",
    "TEXT_DIM": "#A48CD6",
    "TEXT_MUTED": "#6E5A9C",
    "ACCENT": "#00F0FF",
    "ACCENT_END": "#7A5CFF",
    "ACCENT_HOVER": "#7CFAFF",
    "ACCENT_HOVER_END": "#9B85FF",
    "ACCENT_INK": "#07040F",
    "ACCENT_SOFT": "#FF2E97",
    "WAVE_VIOLET": "#7A5CFF",
    "WAVE_PINK": "#FF2E97",
    "WAVE_INK": "#C9F9FF",
    "LIKE_ACTIVE": "#FF2E97",
    "DANGER": "#FF4D6D",
    "SUCCESS": "#3BF5C0",
}

_OLED = {
    "BACKGROUND": "#000000",
    "SIDEBAR_BACKGROUND": "#000000",
    "PANEL": "#000000",
    "SURFACE": "#0C0C0F",
    "SURFACE_HOVER": "#16161C",
    "SEGMENT_BG": "#08080A",
    "BORDER": "#1C1C22",
    "CHIP_BORDER": "#26262E",
    "CHIP_BG": "#101014",
    "CHIP_INK": "#D8D8DE",
    "TEXT": "#F2F2F5",
    "TEXT_DIM": "#8C8C99",
    "TEXT_MUTED": "#5A5A66",
    "ACCENT": "#00E5A0",
    "ACCENT_END": "#00B27A",
    "ACCENT_HOVER": "#4CFFC4",
    "ACCENT_HOVER_END": "#2FD69C",
    "ACCENT_INK": "#000000",
    "ACCENT_SOFT": "#E23D8C",
    "WAVE_VIOLET": "#8A6CFF",
    "WAVE_PINK": "#E23D8C",
    "WAVE_INK": "#B9F5DC",
    "LIKE_ACTIVE": "#E23D8C",
    "DANGER": "#FF5C5C",
    "SUCCESS": "#00E5A0",
}

THEMES: dict[str, ThemeTokens] = {
    "obsidian": ThemeTokens("obsidian", "Obsidian Dark", _OBSIDIAN),
    "cyberpunk": ThemeTokens("cyberpunk", "Neon Cyberpunk", _CYBERPUNK),
    "oled": ThemeTokens("oled", "OLED Pure Black", _OLED),
}
"""The three themes, keyed by the name the config stores.

Obsidian is the default and its colours are the ones this module still exports
as plain constants, so every ``from ui.theme import ACCENT`` keeps working.
"""

THEME_NAMES: tuple[str, ...] = tuple(THEMES)
DEFAULT_THEME = "obsidian"
"""Obsidian, i.e. the tokens above: an unconfigured install looks like Midnight."""


def theme_tokens(name: str = DEFAULT_THEME) -> ThemeTokens:
    """The tokens of ``name``, falling back to the default rather than raising."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def render_qss(name: str = DEFAULT_THEME) -> str:
    """Fill the sheet with the colours of ``name``.

    An unresolved ``<<MARKER>>`` is an error rather than a silent gap: a token
    that is not interpolated would simply be missing from the sheet and the
    widget would fall back to the Fusion default instead of a themed colour.
    """
    tokens = theme_tokens(name)

    def substitute(match: re.Match[str]) -> str:
        token_name = match.group(1)
        if tokens.has(token_name):
            return str(tokens[token_name])
        value = globals().get(token_name)
        if isinstance(value, (str, int, float)):
            return str(value)
        raise KeyError(f"theme {name!r} cannot fill <<{token_name}>>")

    return MARKER.sub(substitute, QSS_TEMPLATE)


DARK_QSS = render_qss(DEFAULT_THEME)
"""The Midnight sheet under its old name: it is still the default theme."""


def dark_palette(name: str = DEFAULT_THEME) -> QPalette:
    """A dark palette to go with :func:`render_qss`.

    The style sheet covers the widgets it knows about, but Fusion paints a few
    things straight from the palette: tab edges, menu items, disabled text. In a
    light palette those come out as pale hairlines, so the palette is darkened
    here and no rule anywhere asks for a white line.
    """
    tokens = theme_tokens(name)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(tokens["BACKGROUND"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens["TEXT"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(tokens["PANEL"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(tokens["SURFACE"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tokens["SURFACE"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(tokens["TEXT"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(tokens["TEXT"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(tokens["SURFACE"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens["TEXT"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(tokens["LIKE_ACTIVE"]))
    palette.setColor(QPalette.ColorRole.Light, QColor(tokens["SURFACE_HOVER"]))
    palette.setColor(QPalette.ColorRole.Midlight, QColor(tokens["SURFACE"]))
    palette.setColor(QPalette.ColorRole.Dark, QColor(tokens["BORDER"]))
    palette.setColor(QPalette.ColorRole.Mid, QColor(tokens["BORDER_STRONG"]))
    palette.setColor(QPalette.ColorRole.Shadow, QColor(tokens["BACKGROUND"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens["ACCENT"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(tokens["ACCENT_INK"]))
    palette.setColor(QPalette.ColorRole.Link, QColor(tokens["ACCENT"]))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(tokens["ACCENT"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens["TEXT_MUTED"]))
    disabled = QPalette.ColorGroup.Disabled
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(disabled, role, QColor(tokens["TEXT_MUTED"]))
    palette.setColor(disabled, QPalette.ColorRole.Highlight, QColor(tokens["SURFACE_HOVER"]))
    palette.setColor(disabled, QPalette.ColorRole.HighlightedText, QColor(tokens["TEXT_MUTED"]))
    return palette


_active_theme: str = DEFAULT_THEME
"""The theme in force, changed by :func:`apply_theme`."""

_theme_listeners: list[Callable[[str], None]] = []


def on_theme_changed(callback: Callable[[str], None]) -> Callable[[], None]:
    """Run ``callback`` whenever the theme changes; returns an unsubscribe.

    A hand-painted widget keeps its colours in module-level names, because they
    are read in a dozen paint methods and threading a palette through every one
    of them would bury the drawing code.  Those names are snapshots, so a widget
    that paints registers here and re-reads them when the theme is switched.
    """
    _theme_listeners.append(callback)
    callback(_active_theme)

    def unsubscribe() -> None:
        try:
            _theme_listeners.remove(callback)
        except ValueError:  # pragma: no cover - unsubscribing twice is harmless
            pass

    return unsubscribe


def active_theme() -> str:
    """The name of the theme the painters are currently reading."""
    return _active_theme


def token(name: str) -> str:
    """Read one token of the active theme.

    Hand-painted widgets cannot use a style sheet, so they ask for a colour here
    rather than importing a constant.  An imported constant is a snapshot taken
    at import time and would keep the old colour after a theme change; this
    follows instead.
    """
    tokens = theme_tokens(_active_theme)
    if tokens.has(name):
        return str(tokens[name])
    value = globals().get(name)
    if isinstance(value, (str, int, float)):
        return str(value)
    raise KeyError(name)


def apply_theme(app, name: str = DEFAULT_THEME) -> None:
    """Apply the Fusion style, the palette and the sheet of ``name``."""
    global _active_theme
    _active_theme = name if name in THEMES else DEFAULT_THEME
    app.setStyle("Fusion")
    app.setPalette(dark_palette(_active_theme))
    app.setStyleSheet(render_qss(_active_theme))
    for callback in list(_theme_listeners):
        try:
            callback(_active_theme)
        except RuntimeError:
            # A listener whose widget is already gone raises here.  Dropping it
            # is the right answer: a dead C++ object can never be repainted, and
            # a test suite builds and drops hundreds of windows.
            _theme_listeners.remove(callback)


__all__ = [
    "ACCENT",
    "ACCENT_BG",
    "ACCENT_BORDER",
    "ACCENT_END",
    "ACCENT_FOCUS",
    "ACCENT_HOVER",
    "ACCENT_HOVER_END",
    "ACCENT_INK",
    "ACCENT_SOFT",
    "BACKGROUND",
    "BADGE_HEIGHT",
    "BORDER",
    "BORDER_STRONG",
    "CHIP_BG",
    "CHIP_BORDER",
    "CHIP_HEIGHT",
    "CHIP_INK",
    "CHIP_RADIUS",
    "CONTROL_HEIGHT",
    "CONTROL_RADIUS",
    "COVER_RADIUS",
    "COVER_SIZE",
    "DANGER",
    "DARK_QSS",
    "DEFAULT_THEME",
    "GROOVE",
    "HANDLE",
    "HANDLE_HOVER",
    "ICON_BUTTON",
    "DISLIKE_GLOW",
    "LIKE_ACTIVE",
    "LIKE_GLOW",
    "LIST_COVER_SIZE",
    "MARKER",
    "NAV_ITEM_HEIGHT",
    "PAGE_PADDING",
    "PANEL",
    "PANEL_RADIUS",
    "PLAYER_BAR_HEIGHT",
    "PLAYER_COVER_SIZE",
    "PLAYER_LEFT_WIDTH",
    "PLAYER_RIGHT_WIDTH",
    "PLAYING_BG",
    "PLAY_BUTTON_SIZE",
    "QSS_TEMPLATE",
    "ROW_HEIGHT",
    "ROW_HOVER",
    "ROW_RADIUS",
    "SEGMENT_BG",
    "SEGMENT_HEIGHT",
    "SEGMENT_RADIUS",
    "SIDEBAR_BACKGROUND",
    "SIDEBAR_WIDTH",
    "SPACE_LG",
    "SPACE_MD",
    "SPACE_SM",
    "SPACE_XL",
    "SPACE_XS",
    "SUCCESS",
    "SURFACE",
    "SURFACE_HOVER",
    "TEXT",
    "TEXT_DIM",
    "TEXT_MUTED",
    "THEMES",
    "THEME_NAMES",
    "TIME_LABEL_WIDTH",
    "ThemeTokens",
    "VOLUME_SLIDER_WIDTH",
    "WAVE_BG",
    "WAVE_BORDER",
    "WAVE_INK",
    "WAVE_PINK",
    "WAVE_VIOLET",
    "active_theme",
    "apply_theme",
    "dark_palette",
    "on_theme_changed",
    "pill_radius",
    "render_qss",
    "theme_tokens",
    "token",
]
