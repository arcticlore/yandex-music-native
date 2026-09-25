"""Bootstrap of the new Qt shell.

Order matters: the login dialog runs *before* the main window exists, so the
service is created with a token that is already known to be valid. The desktop
integrations (MPRIS, notifications, tray) are attached after the window and
kept in module-level slots so Qt does not garbage-collect them.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from PySide6.QtWidgets import QApplication, QMessageBox

from core.audio_engine import AudioEngine
from core.config_manager import ConfigManager
from core.mpris import MprisService
from core.notifications import NotificationService
from core.playback_controller import PlaybackController
from core.yandex_service import YandexService
from ui.dialogs.auth_dialog import AuthDialog
from ui.main_window import MainWindow
from ui.theme import apply_theme
from ui.tray import TrayIcon

log = logging.getLogger(__name__)

APP_NAME = "Яндекс Музыка"
APP_ID = "yandex-music-native"
ORG_NAME = "yandex-music-native"


def setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("YML_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def authenticate(config: ConfigManager, app: QApplication) -> dict | None:
    """Run :class:`AuthDialog` and return the profile, or ``None`` on cancel."""
    dialog = AuthDialog(config)
    profile: dict[str, Any] = {}
    dialog.authenticated.connect(lambda data: profile.update(data))
    app.processEvents()
    if not dialog.start():
        return None
    if not profile:
        return None
    return dict(profile)


def build_window(
    config: ConfigManager,
    app: QApplication,
    *,
    controller: PlaybackController | None = None,
    service: YandexService | None = None,
    engine: AudioEngine | None = None,
) -> tuple[MainWindow, PlaybackController, dict[str, Any]]:
    """Create the playback stack and the window for an authorised session."""
    token = config.get_token() or ""
    if controller is None and not token:
        raise RuntimeError("Нет сохранённого токена: войдите в аккаунт заново")
    playback = controller or PlaybackController(
        service or YandexService(token),
        engine or AudioEngine(volume=config.get_volume()),
    )
    window = MainWindow(playback, config)
    return window, playback, {"service": playback.service, "engine": playback.engine}


def attach_integrations(
    window: MainWindow,
    playback: PlaybackController,
    config: ConfigManager,
    app: QApplication,
) -> dict[str, Any]:
    """Start MPRIS, desktop notifications and the tray icon."""
    held: dict[str, Any] = {}
    mpris = MprisService(
        playback,
        on_quit=app.quit,
        on_raise=lambda: (window.show(), window.raise_(), window.activateWindow()),
    )
    if not mpris.available:
        window.statusBar().showMessage("MPRIS недоступен (нет D-Bus сессии)", 6000)
    notifications = NotificationService(playback, config)
    tray = TrayIcon(playback)
    tray.create()
    tray.show_hide_requested.connect(window.show)
    tray.quit_requested.connect(app.quit)
    held.update(mpris=mpris, notifications=notifications, tray=tray)
    return held


def main(argv: list[str] | None = None) -> int:
    """Entry point of the new shell; returns the Qt exit code."""
    setup_logging()
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setOrganizationDomain(APP_ID)
    app.setDesktopFileName(APP_ID)
    apply_theme(app)

    config = ConfigManager()
    profile = authenticate(config, app)
    if profile is None:
        return 0

    try:
        window, playback, _held = build_window(config, app)
    except RuntimeError as exc:
        log.error("%s", exc)
        QMessageBox.critical(None, APP_NAME, str(exc))
        return 1

    playback.set_volume(config.get_volume())
    window.set_profile(
        str(profile.get("login") or profile.get("display_name") or ""),
        "Подписка Plus" if profile.get("has_plus") else "",
    )
    window.restore_page()
    window.show()
    held = attach_integrations(window, playback, config, app)
    app._yml_integrations = held  # noqa: SLF001
    app._yml_window = window  # noqa: SLF001
    app._yml_playback = playback  # noqa: SLF001
    app._yml_config = config  # noqa: SLF001
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
