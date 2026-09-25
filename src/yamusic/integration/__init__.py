"""Linux desktop integration (MPRIS2, tray, notifications)."""

from yamusic.integration.mpris import MprisService
from yamusic.integration.notifications import NotificationService
from yamusic.integration.tray import TrayIcon

__all__ = ["MprisService", "NotificationService", "TrayIcon"]
