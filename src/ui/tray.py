"""System tray icon wired to :class:`~core.playback_controller.PlaybackController`.

Left click toggles the main window, the wheel over the icon changes the volume
and the context menu carries the transport plus «Моя волна» and «Выход». The
icon widget is injectable, so the whole class is testable on a headless box
where no system tray exists.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QPoint, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from core.playback_controller import PlaybackController, PlaybackState, TrackMetadata

log = logging.getLogger(__name__)

APP_NAME = "Яндекс Музыка"
DEFAULT_VOLUME_STEP = 5

STATE_LABELS = {
    PlaybackState.PLAYING: "Играет",
    PlaybackState.PAUSED: "Пауза",
    PlaybackState.BUFFERING: "Буферизация…",
    PlaybackState.STOPPED: "Остановлено",
}


def make_icon(playing: bool) -> QIcon:
    """Draw a small mark so the tray needs no external assets."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(0x00000000)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(0xFFDB4D)
    painter.setBrush(0xFFDB4D)
    painter.drawEllipse(2, 2, 60, 60)
    painter.setBrush(0x121216)
    if playing:
        painter.drawRect(20, 18, 8, 28)
        painter.drawRect(36, 18, 8, 28)
    else:
        painter.drawPolygon([QPoint(24, 16), QPoint(48, 32), QPoint(24, 48)])
    painter.end()
    return QIcon(pixmap)


def tooltip_text(
    track: TrackMetadata | None,
    state: PlaybackState,
    app_name: str = APP_NAME,
) -> str:
    """«Title — artists» plus a status line, or just the app name."""
    if track is None:
        return app_name
    artists = track.artists_name or track.album
    head = f"{track.title} — {artists}" if artists else track.title
    return f"{head}\n{STATE_LABELS.get(state, STATE_LABELS[PlaybackState.STOPPED])}"


def step_volume(current: int, steps: int, step: int = DEFAULT_VOLUME_STEP) -> int:
    """Apply ``steps`` wheel notches to ``current`` percent, clamped to 0..100."""
    return max(0, min(100, int(current) + int(steps) * int(step)))


class TrayIconWidget(QSystemTrayIcon):
    """``QSystemTrayIcon`` that forwards wheel events as volume steps.

    ``QSystemTrayIcon`` is a ``QObject``, so wheel notifications arrive through
    ``event()`` instead of ``wheelEvent()``.
    """

    volume_stepped = Signal(int)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            if delta:
                self.volume_stepped.emit(1 if delta > 0 else -1)
                return True
        return super().event(event)


class TrayIcon(QObject):
    """Tray icon, tooltip and context menu for the desktop shell."""

    show_hide_requested = Signal()
    quit_requested = Signal()
    volume_requested = Signal(int)

    def __init__(
        self,
        controller: PlaybackController,
        parent: QObject | None = None,
        *,
        app_name: str = APP_NAME,
        icon_factory: Callable[[bool], QIcon] | None = None,
        widget_factory: Callable[..., Any] | None = None,
        volume_step: int = DEFAULT_VOLUME_STEP,
        available: bool | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._app_name = app_name
        self._volume_step = int(volume_step)
        self._icon_factory = icon_factory or make_icon
        self._widget_factory = widget_factory or TrayIconWidget
        self._tray: Any | None = None
        self._menu: QMenu | None = None
        self._available = available

        self.action_play: QAction | None = None
        self.action_next: QAction | None = None
        self.action_prev: QAction | None = None
        self.action_wave: QAction | None = None
        self.action_quit: QAction | None = None

    @property
    def available(self) -> bool:
        """Whether a tray icon exists (no system tray on bare CI boxes)."""
        return self._tray is not None

    @property
    def tray(self) -> Any | None:
        return self._tray

    def create(self) -> bool:
        """Build the icon and menu; returns whether the tray became visible."""
        if self._tray is not None:
            return True
        if self._available is None:
            self._available = QSystemTrayIcon.isSystemTrayAvailable()
        if not self._available:
            log.info("system tray is not available")
            return False

        tray = self._widget_factory(self._icon_factory(self._controller.is_playing))
        tray.setToolTip(self._app_name)
        menu = QMenu()
        self._menu = menu

        self.action_play = QAction("Пауза", menu)
        self.action_play.triggered.connect(self._on_play_triggered)
        self.action_next = QAction("Следующий", menu)
        self.action_next.triggered.connect(self._controller.next)
        self.action_prev = QAction("Предыдущий", menu)
        self.action_prev.triggered.connect(self._controller.prev)
        self.action_wave = QAction("Моя волна", menu)
        self.action_wave.triggered.connect(self._on_wave_triggered)
        self.action_quit = QAction("Выход", menu)
        self.action_quit.triggered.connect(self.quit_requested.emit)

        for action in (self.action_play, self.action_next, self.action_prev, self.action_wave):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self.action_quit)

        tray.setContextMenu(menu)
        if hasattr(tray, "activated"):
            tray.activated.connect(self._on_activated)
        if hasattr(tray, "volume_stepped"):
            tray.volume_stepped.connect(self._on_volume_step)
        tray.show()

        self._tray = tray
        self._refresh()
        self._controller.state_changed.connect(self._on_state)
        self._controller.track_changed.connect(self._on_track)
        self._controller.cover_ready.connect(self._on_cover)
        return True

    def destroy(self) -> None:
        """Hide and drop the icon; safe to call twice."""
        if self._tray is None:
            return
        try:
            self._tray.hide()
            self._tray.setContextMenu(None)
        except RuntimeError:
            pass
        delete_later = getattr(self._tray, "deleteLater", None)
        if callable(delete_later):
            delete_later()
        self._tray = None
        self._menu = None

    def show_message(self, title: str, body: str, msecs: int = 3000) -> bool:
        if self._tray is None:
            return False
        self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, int(msecs))
        return True

    # -- slots --------------------------------------------------------------

    def _on_play_triggered(self) -> None:
        self._controller.toggle_play()

    def _on_wave_triggered(self) -> None:
        self._controller.start_wave()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_hide_requested.emit()

    def _on_volume_step(self, steps: int) -> None:
        value = step_volume(self._controller.volume, steps, self._volume_step)
        self.volume_requested.emit(value)
        self._controller.set_volume(value)

    def _on_state(self, state: str) -> None:
        self._refresh()

    def _on_track(self, _track: object) -> None:
        self._refresh()

    def _on_cover(self, track_id: str, path: str) -> None:
        current = self._controller.current
        if current is not None and current.id == track_id:
            self._set_cover_icon(path)

    def _refresh(self) -> None:
        if self._tray is None:
            return
        state = self._controller.state
        self._tray.setToolTip(tooltip_text(self._controller.current, state, self._app_name))
        if self.action_play is not None:
            self.action_play.setText("Пауза" if state == PlaybackState.PLAYING else "Играть")
        if self.action_play is not None:
            self.action_play.setEnabled(self._controller.current is not None)
        if self.action_next is not None:
            self.action_next.setEnabled(state != PlaybackState.STOPPED)

    def _set_cover_icon(self, path: str) -> None:
        if self._tray is None:
            return
        try:
            self._tray.setIcon(QIcon(path))
        except (RuntimeError, TypeError):
            log.debug("cannot use %s as tray icon", path)


__all__ = [
    "APP_NAME",
    "DEFAULT_VOLUME_STEP",
    "STATE_LABELS",
    "TrayIcon",
    "TrayIconWidget",
    "make_icon",
    "step_volume",
    "tooltip_text",
]
