"""System tray icon with playback controls."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QObject, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from yamusic.constants import APP_NAME
from yamusic.services.playback import PlaybackController

log = logging.getLogger(__name__)


def _make_icon(playing: bool) -> QIcon:
    """Draw a simple vector-like mark so we depend on no external assets."""
    pm = QPixmap(64, 64)
    pm.fill(0x00000000)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(0xFFDB4D)  # Yandex yellow
    painter.setBrush(0xFFDB4D)
    painter.drawEllipse(2, 2, 60, 60)
    painter.setBrush(0x121216)
    if playing:
        painter.drawRect(20, 18, 8, 28)
        painter.drawRect(36, 18, 8, 28)
    else:
        painter.drawPolygon([QPoint(24, 16), QPoint(48, 32), QPoint(24, 48)])
    painter.end()
    return QIcon(pm)


class TrayIcon(QObject):
    """Tray icon + menu; double-click toggles the main window."""

    show_hide_requested = Signal()
    quit_requested = Signal()

    def __init__(self, controller: PlaybackController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._tray: QSystemTrayIcon | None = None
        self._playing_icon = _make_icon(True)
        self._paused_icon = _make_icon(False)

    @property
    def available(self) -> bool:
        return self._tray is not None

    def create(self) -> None:
        from PySide6.QtWidgets import QApplication

        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("system tray not available")
            return
        tray = QSystemTrayIcon(self._paused_icon, parent=None)  # type: ignore[arg-type]
        tray.setToolTip(APP_NAME)
        menu = QMenu()

        act_play = QAction("Воспроизведение/пауза", menu)
        act_play.triggered.connect(self._controller.play_pause)
        act_next = QAction("Следующий", menu)
        act_next.triggered.connect(lambda: self._controller.next(manual=True))
        act_prev = QAction("Предыдущий", menu)
        act_prev.triggered.connect(self._controller.previous)
        act_like = QAction("♥ Нравится", menu)
        act_like.triggered.connect(self._controller.toggle_like)
        act_show = QAction("Показать/скрыть", menu)
        act_show.triggered.connect(self.show_hide_requested.emit)
        act_quit = QAction("Выход", menu)
        act_quit.triggered.connect(self.quit_requested.emit)

        for action in (act_play, act_next, act_prev, act_like):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(act_show)
        menu.addAction(act_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_activated)
        tray.show()
        self._tray = tray

        self._controller.state_changed.connect(self._on_state)
        self._controller.track_changed.connect(self._on_track)
        QApplication.instance().aboutToQuit.connect(self.destroy)  # type: ignore[union-attr]

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_hide_requested.emit()

    def _on_state(self, playing: bool) -> None:
        if self._tray is None:
            return
        self._tray.setIcon(self._playing_icon if playing else self._paused_icon)

    def _on_track(self, track: object) -> None:
        if self._tray is None:
            return
        if track is None:
            self._tray.setToolTip(APP_NAME)
            return
        title = getattr(track, "title", "")
        artists = getattr(track, "artist_line", "")
        self._tray.setToolTip(f"{title} — {artists}")

    def show_message(self, title: str, body: str) -> None:
        if self._tray is not None:
            self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 3000)

    def destroy(self) -> None:
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None
