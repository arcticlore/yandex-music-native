"""Dark QSS theme with Yandex-yellow accent."""

from __future__ import annotations

ACCENT = "#ffdb4d"
ACCENT_HOVER = "#ffe37a"
BG = "#0f0f13"
BG_PANEL = "#16161d"
BG_ELEVATED = "#1d1d26"
FG = "#f2f2f5"
FG_DIM = "#9a9aa8"
DANGER = "#ff5c7a"
BORDER = "#2a2a36"

STYLE_SHEET = f"""
* {{
    font-family: "Inter", "Noto Sans", "Cantarell", sans-serif;
    font-size: 13px;
    color: {FG};
    outline: none;
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QWidget#Sidebar {{
    background: {BG_PANEL};
    border-right: 1px solid {BORDER};
}}
QListWidget#NavList {{
    background: transparent;
    border: none;
    padding: 8px;
}}
QListWidget#NavList::item {{
    padding: 10px 14px;
    margin: 2px 6px;
    border-radius: 8px;
    color: {FG_DIM};
}}
QListWidget#NavList::item:hover {{
    background: {BG_ELEVATED};
    color: {FG};
}}
QListWidget#NavList::item:selected {{
    background: {BG_ELEVATED};
    color: {ACCENT};
}}
QLabel#AppTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {ACCENT};
    padding: 18px 16px 8px 16px;
}}
QLabel#PageTitle {{
    font-size: 22px;
    font-weight: 700;
}}
QLabel#Dim {{
    color: {FG_DIM};
}}
QLabel#TrackTitle {{
    font-size: 13px;
    font-weight: 600;
}}
QLabel#TrackArtist {{
    font-size: 12px;
    color: {FG_DIM};
}}
QLabel#Time {{
    font-size: 11px;
    color: {FG_DIM};
    font-family: "JetBrains Mono", "Noto Sans Mono", monospace;
}}
QPushButton {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
}}
QPushButton:hover {{
    background: #262633;
    border-color: #3a3a4a;
}}
QPushButton:pressed {{
    background: #12121a;
}}
QPushButton:disabled {{
    color: {FG_DIM};
    background: {BG_PANEL};
}}
QPushButton#Accent {{
    background: {ACCENT};
    color: #141414;
    border: none;
    font-weight: 700;
}}
QPushButton#Accent:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#Transport {{
    background: transparent;
    border: none;
    border-radius: 18px;
    min-width: 36px;
    min-height: 36px;
    font-size: 16px;
}}
QPushButton#Transport:hover {{
    background: {BG_ELEVATED};
}}
QPushButton#TransportBig {{
    background: {ACCENT};
    color: #141414;
    border: none;
    border-radius: 22px;
    min-width: 44px;
    min-height: 44px;
    font-size: 18px;
    font-weight: 700;
}}
QPushButton#TransportBig:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#Like {{
    background: transparent;
    border: none;
    font-size: 16px;
    min-width: 32px;
    min-height: 32px;
    border-radius: 16px;
}}
QPushButton#Like:hover {{
    background: {BG_ELEVATED};
}}
QPushButton#Like[checked="true"] {{
    color: {ACCENT};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {FG};
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT};
}}
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {ACCENT};
    selection-color: #141414;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {FG_DIM};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    selection-background-color: {BG_ELEVATED};
    selection-color: {ACCENT};
}}
QTableView {{
    background: transparent;
    border: none;
    gridline-color: transparent;
    alternate-background-color: {BG_PANEL};
}}
QTableView::item {{
    padding: 6px 8px;
    border-radius: 6px;
}}
QTableView::item:hover {{
    background: {BG_ELEVATED};
}}
QTableView::item:selected {{
    background: #262633;
    color: {FG};
}}
QHeaderView::section {{
    background: transparent;
    color: {FG_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px;
    font-size: 11px;
    text-transform: uppercase;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {FG_DIM};
    padding: 8px 16px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
    background: {BG_ELEVATED};
    color: {ACCENT};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #3a3a4a;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 5px;
    min-width: 30px;
}}
QFrame#PlayerBar {{
    background: {BG_PANEL};
    border-top: 1px solid {BORDER};
}}
QFrame#Divider {{
    background: {BORDER};
    max-width: 1px;
}}
QListWidget#PlaylistGrid {{
    background: transparent;
    border: none;
}}
QListWidget#PlaylistGrid::item {{
    background: {BG_PANEL};
    border-radius: 12px;
    margin: 6px;
    padding: 10px;
}}
QListWidget#PlaylistGrid::item:hover {{
    background: {BG_ELEVATED};
}}
QToolTip {{
    background: {BG_ELEVATED};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}
QStatusBar {{
    background: {BG_PANEL};
    color: {FG_DIM};
    border-top: 1px solid {BORDER};
}}
QDockWidget {{
    color: {FG};
    background: {BG_PANEL};
}}
QDockWidget::close-button {{
    subcontrol-origin: padding;
}}
"""


def apply_theme() -> str:
    """Return the stylesheet (callers assign it to QApplication)."""
    return STYLE_SHEET
