"""The dark Qt style sheet used by the new shell."""

from __future__ import annotations

BACKGROUND = "#131318"
SIDEBAR_BACKGROUND = "#0d0d11"
SURFACE = "#1d1d25"
SURFACE_HOVER = "#262633"
BORDER = "#2b2b38"
TEXT = "#ececf3"
TEXT_DIM = "#8b8b9c"
ACCENT = "#ffdb4d"
ACCENT_SOFT = "#ff4d82"

DARK_QSS = """
QWidget {
    background: #131318;
    color: #ececf3;
    font-size: 14px;
}
QMainWindow, QStackedWidget, QTabWidget::pane {
    background: #131318;
}
QFrame#Sidebar {
    background: #0d0d11;
    border-right: 1px solid #24242f;
}
QFrame#PlayerBar {
    background: #0f0f14;
    border-top: 1px solid #24242f;
}
QLabel#Brand { color: #ffdb4d; font-size: 18px; font-weight: 600; }
QLabel#PageTitle { color: #ffffff; font-size: 26px; font-weight: 600; }
QLabel#Dim { color: #8b8b9c; }
QLabel#TrackTitle { color: #ffffff; font-size: 15px; font-weight: 600; }
QLabel#TrackArtist { color: #9a9aad; font-size: 13px; }
QPushButton {
    background: #1d1d25;
    border: 1px solid #2b2b38;
    border-radius: 10px;
    padding: 7px 14px;
    color: #ececf3;
}
QPushButton:hover { background: #262633; }
QPushButton:disabled { color: #5c5c6b; }
QPushButton#Accent {
    background: #ffdb4d;
    color: #1b1b1f;
    border: none;
    font-weight: 600;
}
QPushButton#Accent:hover { background: #ffe681; }
QPushButton#NavButton {
    background: transparent;
    border: none;
    border-radius: 10px;
    padding: 9px 14px;
    text-align: left;
    color: #b9b9ca;
}
QPushButton#NavButton:hover { background: #1b1b23; }
QPushButton#NavButton:checked {
    background: #22222d;
    color: #ffdb4d;
    font-weight: 600;
}
QPushButton#Chip {
    background: #1b1b23;
    border: 1px solid #2b2b38;
    border-radius: 14px;
    padding: 5px 12px;
    color: #b9b9ca;
}
QPushButton#Chip:checked {
    background: #ffdb4d;
    color: #1b1b1f;
    border-color: #ffdb4d;
    font-weight: 600;
}
QPushButton#LikeButton:checked { color: #ff4d82; border-color: #ff4d82; }
QSlider::groove:horizontal { height: 4px; background: #2b2b38; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #ffdb4d; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #ffdb4d;
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
}
QListWidget, QListView, QLineEdit, QTabBar {
    background: #17171d;
    border: 1px solid #24242f;
    border-radius: 10px;
    color: #ececf3;
}
QListWidget::item { padding: 7px 8px; border-radius: 6px; }
QListWidget::item:selected { background: #262633; color: #ffdb4d; }
QListWidget::item:alternate { background: #191920; }
QLineEdit { padding: 8px 10px; }
QLineEdit#SearchInput { font-size: 15px; }
QTabBar::tab {
    background: transparent;
    color: #8b8b9c;
    padding: 8px 14px;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: #ffdb4d; border-bottom-color: #ffdb4d; }
QCheckBox { spacing: 10px; color: #b9b9ca; }
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 6px;
    border: 1px solid #2b2b38;
    background: #1b1b23;
}
QCheckBox::indicator:checked { background: #ffdb4d; border-color: #ffdb4d; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #2b2b38; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


def apply_theme(app, qss: str = DARK_QSS) -> None:
    """Apply the Fusion style plus the dark sheet to a ``QApplication``."""
    app.setStyle("Fusion")
    app.setStyleSheet(qss)


__all__ = ["ACCENT", "ACCENT_SOFT", "BACKGROUND", "DARK_QSS", "apply_theme"]
