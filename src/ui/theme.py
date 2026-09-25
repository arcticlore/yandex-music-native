"""Deep Obsidian: the palette and the single style sheet of the app.

One palette drives everything: backgrounds are layered from ``#0b0c10`` to
``#1a1c26`` so panels read as depth rather than as boxes, text stays on three
levels of contrast, and only two accents are allowed to be saturated - the warm
Yandex gradient for anything you press, and the violet/pink pair for «Моя волна».
Borders are a single 7% white hairline, which keeps the dark theme calm.

Qt style sheets have no variables, so the colours are spelled out below; the
constants next to the sheet are what the widgets and the tests import.
"""

from __future__ import annotations

# -- palette ----------------------------------------------------------------

BACKGROUND = "#0b0c10"
SIDEBAR_BACKGROUND = "#12131a"
SURFACE = "#1a1c26"
SURFACE_HOVER = "#242736"
BORDER = "rgba(255, 255, 255, 0.07)"
BORDER_STRONG = "rgba(255, 255, 255, 0.12)"

TEXT = "#ffffff"
TEXT_DIM = "#8b8d9e"
TEXT_MUTED = "#5c5e70"

ACCENT = "#ffcc00"
ACCENT_END = "#ff9900"
ACCENT_SOFT = "#ff2a74"
WAVE_VIOLET = "#b845ed"
WAVE_PINK = "#ff2a74"
LIKE_ACTIVE = "#ff3366"

#: Geometry the widgets and the sheet agree on.
SIDEBAR_WIDTH = 228
PLAYER_BAR_HEIGHT = 84
NAV_ITEM_HEIGHT = 40
PLAY_BUTTON_SIZE = 42
COVER_SIZE = 56
TRACK_ROW_HEIGHT = 56

DARK_QSS = f"""
/* -- base ---------------------------------------------------------------- */
QWidget {{
    background: {BACKGROUND};
    color: {TEXT};
    font-size: 14px;
}}
QMainWindow, QStackedWidget, QTabWidget::pane, QScrollArea, QWidget#Page {{
    background: {BACKGROUND};
}}
QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 6px 9px;
}}

/* -- sidebar ------------------------------------------------------------- */
QFrame#Sidebar {{
    background: {SIDEBAR_BACKGROUND};
    border-right: 1px solid {BORDER};
}}
QLabel#Brand {{
    font-size: 17px;
    font-weight: 700;
    color: {TEXT};
    padding: 2px 6px 6px 6px;
}}
QFrame#NavRow {{
    background: transparent;
    border-radius: 10px;
}}
QFrame#NavRow:hover {{
    background: #16181f;
}}
QFrame#NavRow[active="true"] {{
    background: #1e202d;
}}
QLabel#NavAccent {{
    background: transparent;
    border-radius: 2px;
}}
QLabel#NavAccent[active="true"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT_END});
}}
QPushButton#NavButton {{
    background: transparent;
    border: none;
    border-radius: 10px;
    color: {TEXT_DIM};
    font-size: 14px;
    font-weight: 500;
    text-align: left;
    padding-left: 12px;
}}
QPushButton#NavButton:hover {{
    color: {TEXT};
}}
QFrame#NavRow[active="true"] QPushButton#NavButton {{
    color: {TEXT};
    font-weight: 600;
}}
QFrame#ProfileCard {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QLabel#ProfileAvatar {{
    background: {SURFACE_HOVER};
    border-radius: 20px;
    border: 1px solid {BORDER};
}}
QLabel#ProfileName {{
    color: {TEXT};
    font-size: 13px;
    font-weight: 600;
}}
QLabel#ProfileHint {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QLabel#PlusBadge {{
    background: rgba(255, 204, 0, 0.14);
    color: {ACCENT};
    border: 1px solid rgba(255, 204, 0, 0.30);
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 10px;
    font-weight: 700;
}}

/* -- player bar ---------------------------------------------------------- */
QFrame#PlayerBar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #15171f, stop:1 #0d0e13);
    border-top: 1px solid rgba(255, 255, 255, 0.08);
}}
QLabel#Cover {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel#TrackTitle {{
    color: {TEXT};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#TrackArtist {{
    color: {TEXT_DIM};
    font-size: 12px;
}}
QLabel#QualityBadge {{
    background: rgba(255, 204, 0, 0.13);
    color: {ACCENT};
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#TimeLabel {{
    color: {TEXT_DIM};
    font-size: 11px;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
}}
QPushButton#TransportButton {{
    background: transparent;
    border: none;
    border-radius: 18px;
    color: {TEXT_DIM};
    font-size: 14px;
}}
QPushButton#TransportButton:hover {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}
QPushButton#TransportButton:disabled {{
    color: {TEXT_MUTED};
    background: transparent;
}}
QPushButton#PlayButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_END});
    border: none;
    border-radius: 21px;
    color: {BACKGROUND};
    font-size: 15px;
    font-weight: 700;
}}
QPushButton#PlayButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffd633, stop:1 #ffab26);
}}
QPushButton#PlayButton:disabled {{
    background: #2a2c38;
    color: {TEXT_MUTED};
}}
QPushButton#LikeButton {{
    background: transparent;
    border: none;
    border-radius: 16px;
    color: {TEXT_DIM};
    font-size: 15px;
}}
QPushButton#LikeButton:hover {{
    background: {SURFACE_HOVER};
}}
QPushButton#LikeButton:disabled {{
    color: {TEXT_MUTED};
    background: transparent;
}}
QPushButton#ModeButton {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 600;
    padding: 4px 10px;
}}
QPushButton#ModeButton:hover {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}

/* -- sliders ------------------------------------------------------------- */
QSlider::groove:horizontal {{
    background: rgba(255, 255, 255, 0.10);
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_END});
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    border-radius: 6px;
    width: 12px;
    margin: -4px 0;
}}
QSlider#SeekSlider::groove:horizontal {{
    height: 4px;
}}
QSlider#SeekSlider::groove:horizontal:hover {{
    height: 6px;
}}
QSlider#SeekSlider::sub-page:horizontal {{
    height: 4px;
}}
QSlider#SeekSlider::sub-page:horizontal:hover {{
    height: 6px;
}}
QSlider#SeekSlider::handle:horizontal {{
    width: 12px;
    margin: -4px 0;
}}
QSlider#SeekSlider::handle:horizontal:hover {{
    background: {ACCENT};
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}}
QSlider#VolumeSlider::groove:horizontal {{
    height: 4px;
}}
QSlider#VolumeSlider::handle:horizontal {{
    width: 10px;
    margin: -3px 0;
    background: {TEXT_DIM};
}}
QSlider#VolumeSlider::handle:horizontal:hover {{
    background: {ACCENT};
}}

/* -- pages --------------------------------------------------------------- */
QFrame#WaveCard, QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QLabel#PageTitle {{
    color: {TEXT};
    font-size: 26px;
    font-weight: 700;
}}
QLabel#Dim {{ color: {TEXT_DIM}; font-size: 13px; }}
QLabel#Hint {{ color: {TEXT_MUTED}; font-size: 12px; }}
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 7px 14px;
    color: {TEXT};
    font-weight: 500;
}}
QPushButton:hover {{ background: {SURFACE_HOVER}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; border-color: {BORDER}; }}
QPushButton#Accent {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_END});
    color: {BACKGROUND};
    border: none;
    font-weight: 700;
    padding: 8px 18px;
}}
QPushButton#Accent:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffd633, stop:1 #ffab26);
}}
QPushButton#Accent:disabled {{
    background: #2a2c38;
    color: {TEXT_MUTED};
}}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_END});
    color: {BACKGROUND};
    border: none;
    font-weight: 700;
}}
QPushButton#Chip {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 20px;
    padding: 6px 14px;
    color: {TEXT_DIM};
    font-size: 13px;
}}
QPushButton#Chip:hover {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}
QPushButton#Chip:checked {{
    background: rgba(184, 69, 237, 0.16);
    border: 1px solid rgba(184, 69, 237, 0.55);
    color: #e6b6ff;
    font-weight: 600;
}}

/* -- lists, inputs, tabs ------------------------------------------------- */
QListWidget, QListView, QTreeView, QLineEdit, QSpinBox, QTabBar {{
    background: {SIDEBAR_BACKGROUND};
    border: 1px solid {BORDER};
    border-radius: 12px;
    color: {TEXT};
    outline: none;
}}
QLineEdit {{ padding: 9px 12px; }}
QLineEdit#SearchInput {{ font-size: 15px; }}
QLineEdit:focus {{ border: 1px solid rgba(255, 204, 0, 0.45); }}
QListWidget#TrackList, QTreeView#TrackList {{
    background: transparent;
    border: none;
    border-radius: 0;
}}
QListWidget::item, QTreeView::item {{
    border: none;
    color: {TEXT};
}}
QListWidget::item:hover, QTreeView::item:hover {{ background: #181a24; }}
QListWidget::item:selected, QTreeView::item:selected {{
    background: #1e202d;
    color: {TEXT};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 14px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 9px 16px;
    margin-right: 4px;
    border: none;
    border-radius: 10px;
    font-weight: 500;
}}
QTabBar::tab:hover {{ background: {SURFACE}; color: {TEXT}; }}
QTabBar::tab:selected {{
    background: {SURFACE};
    color: {ACCENT};
    font-weight: 600;
}}

/* -- switches and scrollbars --------------------------------------------- */
QCheckBox, QRadioButton {{ spacing: 10px; color: {TEXT_DIM}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 6px;
    border: 1px solid {BORDER_STRONG};
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_END});
    border-color: {ACCENT_END};
}}
QRadioButton::indicator {{ border-radius: 9px; }}
QRadioButton::indicator:checked {{
    background: {ACCENT};
    border: 4px solid {SIDEBAR_BACKGROUND};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: rgba(255, 255, 255, 0.14);
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255, 255, 255, 0.24); }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0 2px 2px 2px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: rgba(255, 255, 255, 0.14);
    border-radius: 5px;
    min-width: 32px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
"""


def apply_theme(app, qss: str = DARK_QSS) -> None:
    """Apply the Fusion style plus the dark sheet to a ``QApplication``."""
    app.setStyle("Fusion")
    app.setStyleSheet(qss)


__all__ = [
    "ACCENT",
    "ACCENT_END",
    "ACCENT_SOFT",
    "BACKGROUND",
    "BORDER",
    "BORDER_STRONG",
    "COVER_SIZE",
    "DARK_QSS",
    "LIKE_ACTIVE",
    "NAV_ITEM_HEIGHT",
    "PLAYER_BAR_HEIGHT",
    "PLAY_BUTTON_SIZE",
    "SIDEBAR_BACKGROUND",
    "SIDEBAR_WIDTH",
    "SURFACE",
    "SURFACE_HOVER",
    "TEXT",
    "TEXT_DIM",
    "TEXT_MUTED",
    "TRACK_ROW_HEIGHT",
    "WAVE_PINK",
    "WAVE_VIOLET",
    "apply_theme",
]
