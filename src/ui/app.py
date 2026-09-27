"""Bootstrap of the new Qt shell.

Two entry paths exist. Without a stored token the modal :class:`AuthDialog` runs
*before* the main window exists, so the service is created with a token that is
already known to be valid. With a stored token the window is shown immediately
and the session is restored in a background thread: no popup, and a network
failure keeps the token so the next launch retries on its own.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from core.audio_engine import AudioEngine
from core.auth import AuthService
from core.config_manager import ConfigManager
from core.mpris import MprisService
from core.notifications import NotificationService
from core.playback_controller import PlaybackController
from core.yandex_service import YandexService
from ui.dialogs.auth_dialog import AuthDialog
from ui.main_window import MainWindow
from ui.pages.settings_page import SettingsPage
from ui.theme import apply_theme
from ui.tray import TrayIcon

log = logging.getLogger(__name__)

APP_NAME = "Яндекс Музыка"
APP_ID = "yandex-music-native"
ORG_NAME = "yandex-music-native"
RESTART_MESSAGE = "Сессия обновлена, приложение перезапускается…"


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


def profile_name(profile: dict[str, Any]) -> str:
    """The name to greet the user with: a person first, the login last.

    The sidebar is 228px wide, so this is the one line that says whose account
    is signed in.  A login is an email address, which identifies nobody to the
    person reading it; the account's own name does.  Order: the two name fields
    the API fills in for a person, then whatever the server calls the account,
    then the login, which is the only field that is always present.
    """
    full = " ".join(
        part for part in (str(profile.get("first_name") or ""), str(profile.get("last_name") or "")) if part
    )
    return full.strip() or str(profile.get("display_name") or profile.get("login") or "").strip()


def show_profile(window: MainWindow, profile: dict[str, Any]) -> None:
    """Fill the sidebar account card from a session profile."""
    window.set_profile(
        profile_name(profile),
        "Подписка Plus" if profile.get("has_plus") else "",
        str(profile.get("avatar_url") or ""),
    )


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
    if controller is None and service is None and not token:
        raise RuntimeError("Нет сохранённого токена: войдите в аккаунт заново")
    playback = controller or PlaybackController(
        service or YandexService(token),
        engine or AudioEngine(volume=config.get_volume()),
        # The saved quality has to be handed to the controller at startup, not
        # only through the settings page: otherwise every launch plays «auto»
        # whatever the user picked last, and the setting looks ignored.
        quality=config.get_quality(),
    )
    window = MainWindow(playback, config)
    apply_theme(app, config.get_theme())
    settings: SettingsPage = window.pages["settings"]  # type: ignore[assignment]
    settings.theme_changed.connect(lambda name: apply_theme(app, name))
    window.logout_requested.connect(lambda: logout_and_restart(config, app, window))
    return window, playback, {"service": playback.service, "engine": playback.engine}


def restore_session_in_background(
    config: ConfigManager,
    app: QApplication,
    window: MainWindow,
) -> AuthService:
    """Validate the stored token while the window is already on screen.

    The profile fills in as soon as the answer arrives; a transport failure only
    shows a status message with a retry button, because the token is still
    valid. A rejected token is gone from the config, so the user is offered a
    fresh login and the app restarts with the new session.
    """
    auth = AuthService(config)
    retry = QPushButton("Повторить")
    retry.setVisible(False)
    window.statusBar().addPermanentWidget(retry)
    retry.clicked.connect(lambda: auth.restore_session())
    auth.browser_login_started.connect(lambda: retry.setVisible(False))
    auth.status_changed.connect(lambda text: window.statusBar().showMessage(text, 6000))
    auth.plus_warning.connect(lambda text: window.statusBar().showMessage(text, 8000))
    auth.auth_success.connect(lambda profile: show_profile(window, profile))

    def on_error(message: str) -> None:
        window.statusBar().showMessage(message, 0)
        if config.get_token():
            retry.setVisible(True)
            return
        answer = QMessageBox.question(
            window,
            APP_NAME,
            f"{message}\n\nВойти заново?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            app.quit()
            return
        if authenticate(config, app) is None:
            app.quit()
            return
        log.info(RESTART_MESSAGE)
        QProcess.startDetached(sys.executable, [sys.argv[0]])
        app.quit()

    auth.auth_error.connect(on_error)
    auth.restore_session()
    return auth


def logout_and_restart(config: ConfigManager, app: QApplication, window: MainWindow) -> None:
    """Forget the session and start over.

    The token goes first and the process is replaced, not just the window
    closed: a running ``YandexService`` still holds the old token on its worker
    thread, so a same-process return to the login dialog would leave a
    half-authenticated app that looks signed in.  A restart is the only state
    here that is genuinely clean.
    """
    config.delete_token()
    log.info("signed out by the user")
    window.statusBar().showMessage("Вы вышли из аккаунта", 4000)
    QProcess.startDetached(sys.executable, [sys.argv[0]])
    app.quit()


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
    # Applied before the window exists so the first paint is already themed;
    # build_window re-applies the stored one and connects the live switch.
    apply_theme(app)

    config = ConfigManager()
    stored = config.get_token()
    auth: AuthService | None = None
    if stored:
        try:
            window, playback, _held = build_window(config, app)
        except RuntimeError as exc:
            log.error("%s", exc)
            QMessageBox.critical(None, APP_NAME, str(exc))
            return 1
        auth = restore_session_in_background(config, app, window)
    else:
        profile = authenticate(config, app)
        if profile is None:
            return 0
        try:
            window, playback, _held = build_window(config, app)
        except RuntimeError as exc:
            log.error("%s", exc)
            QMessageBox.critical(None, APP_NAME, str(exc))
            return 1
        show_profile(window, profile)

    playback.set_volume(config.get_volume())
    window.restore_page()
    window.show()
    held = attach_integrations(window, playback, config, app)
    # Quitting from the tray or over MPRIS never sends closeEvent, so the same
    # teardown is bound to the application itself. MainWindow.shutdown() is
    # idempotent, whichever path arrives first.
    app.aboutToQuit.connect(window.shutdown)
    app._yml_integrations = held  # noqa: SLF001
    app._yml_window = window  # noqa: SLF001
    app._yml_playback = playback  # noqa: SLF001
    app._yml_config = config  # noqa: SLF001
    app._yml_auth = auth  # noqa: SLF001
    try:
        return app.exec()
    finally:
        window.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
