"""«Now playing» desktop notifications over ``org.freedesktop.Notifications``.

Notifications are a pure view concern: the service listens to
``PlaybackController.track_changed`` and asks :class:`ConfigManager` whether the
user wants them. The D-Bus connection is injectable so the whole class can be
exercised without a session bus.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtDBus import QDBusConnection, QDBusMessage

from core.config_manager import ConfigManager
from core.playback_controller import PlaybackController, TrackMetadata

log = logging.getLogger(__name__)

NOTIFICATIONS_SERVICE = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"
NOTIFICATIONS_IFACE = "org.freedesktop.Notifications"
NOTIFICATION_TIMEOUT_MS = 5000
APP_ICON = "yandex-music-native"


def notification_hints(meta: TrackMetadata | None) -> dict[str, Any]:
    """D-Bus hints for a track: local cover first, remote URL as a fallback."""
    if meta is None:
        return {}
    hints: dict[str, Any] = {
        "category": "music",
        "urgency": 1,
        "resident": False,
        "transient": True,
    }
    cover = meta.cover_path
    if cover:
        path = Path(cover)
        try:
            if path.exists():
                hints["image-path"] = path.as_uri()
        except OSError:
            pass
    if "image-path" not in hints and meta.cover_url:
        hints["image-path"] = str(meta.cover_url)
    return hints


class NotificationService(QObject):
    """Sends a desktop notification for every track change."""

    def __init__(
        self,
        controller: PlaybackController,
        config: ConfigManager,
        parent: QObject | None = None,
        *,
        bus: QDBusConnection | None = None,
        app_name: str = "Яндекс Музыка",
        timeout_ms: int = NOTIFICATION_TIMEOUT_MS,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._config = config
        self._bus = bus if bus is not None else QDBusConnection.sessionBus()
        self._app_name = app_name
        self._timeout_ms = int(timeout_ms)
        self._last_id = 0
        controller.track_changed.connect(self._on_track)

    @property
    def available(self) -> bool:
        return self._bus.isConnected()

    @property
    def enabled(self) -> bool:
        return self._config.get_notifications()

    def set_enabled(self, value: bool) -> None:
        """Persist the on/off switch; the config file is the only source."""
        self._config.set_notifications(bool(value))

    @property
    def last_id(self) -> int:
        return self._last_id

    def _on_track(self, track: object) -> None:
        meta = track if isinstance(track, TrackMetadata) else None
        if meta is None:
            self.close_last()
            return
        self.notify_track(meta)

    def notify_track(self, track: TrackMetadata) -> bool:
        """Show one notification; returns whether a D-Bus call was made."""
        if not self.enabled:
            return False
        if not self.available:
            log.debug("notifications unavailable: no session bus")
            return False
        message = QDBusMessage.createMethodCall(
            NOTIFICATIONS_SERVICE,
            NOTIFICATIONS_PATH,
            NOTIFICATIONS_IFACE,
            "Notify",
        )
        message << self._app_name
        message << 0
        message << APP_ICON
        message << track.title
        message << track.artists_name or track.album
        message << []
        message << notification_hints(track)
        message << self._timeout_ms
        reply = self._bus.call(message)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            log.debug("notify failed: %s", reply.errorMessage())
            return False
        arguments = reply.arguments()
        if arguments:
            try:
                self._last_id = int(arguments[0])
            except (TypeError, ValueError):
                self._last_id = 0
        return True

    def close_last(self) -> None:
        """Dismiss the notification we raised (used when playback stops)."""
        if not self.available or not self._last_id:
            return
        message = QDBusMessage.createMethodCall(
            NOTIFICATIONS_SERVICE,
            NOTIFICATIONS_PATH,
            NOTIFICATIONS_IFACE,
            "CloseNotification",
        )
        message << self._last_id
        self._bus.call(message)
        self._last_id = 0


__all__ = [
    "APP_ICON",
    "NOTIFICATIONS_IFACE",
    "NOTIFICATIONS_PATH",
    "NOTIFICATIONS_SERVICE",
    "NOTIFICATION_TIMEOUT_MS",
    "NotificationService",
    "notification_hints",
]
