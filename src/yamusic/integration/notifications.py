"""Desktop notifications over ``org.freedesktop.Notifications`` (QtDBus)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtDBus import QDBusConnection, QDBusMessage

from yamusic.config import Settings
from yamusic.constants import (
    APP_NAME,
    NOTIFICATIONS_IFACE,
    NOTIFICATIONS_PATH,
    NOTIFICATIONS_SERVICE,
)
from yamusic.models import TrackInfo

log = logging.getLogger(__name__)


class NotificationService(QObject):
    """Sends "now playing" notifications (art via file:// hint)."""

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._bus = QDBusConnection.sessionBus()
        self._available = self._bus.isConnected()
        self._last_id = 0

    def notify_track(self, track: TrackInfo, cover_path: str | None = None) -> None:
        if not self._settings.options().notifications:
            return
        if not self._available:
            return
        title = track.title
        body = track.artist_line
        hints: dict[str, object] = {"category": "music", "resident": True, "urgency": 1}
        if cover_path and Path(cover_path).exists():
            hints["image-path"] = Path(cover_path).as_uri()

        msg = QDBusMessage.createMethodCall(
            NOTIFICATIONS_SERVICE,
            NOTIFICATIONS_PATH,
            NOTIFICATIONS_IFACE,
            "Notify",
        )
        msg << APP_NAME << 0 << "yandex-music-native" << title << body
        msg << []  # actions
        msg << hints
        msg << 5000  # timeout ms
        reply = self._bus.call(msg)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            log.debug("notify failed: %s", reply.errorMessage())
        else:
            args = reply.arguments()
            if args:
                self._last_id = int(args[0])

    def close_last(self) -> None:
        if not self._available or not self._last_id:
            return
        msg = QDBusMessage.createMethodCall(
            NOTIFICATIONS_SERVICE,
            NOTIFICATIONS_PATH,
            NOTIFICATIONS_IFACE,
            "CloseNotification",
        )
        msg << self._last_id
        self._bus.call(msg)
        self._last_id = 0
