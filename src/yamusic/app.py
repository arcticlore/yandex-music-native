"""Composition root: builds every service, wires signals, runs the event loop."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from yamusic import __version__
from yamusic.api.service import YandexApi
from yamusic.audio.analyzer import SpectrumAnalyzer
from yamusic.audio.engine import AudioEngine, ensure_mpv
from yamusic.cache.store import CacheStore
from yamusic.config import Settings
from yamusic.constants import APP_ID, APP_NAME, ORG_NAME
from yamusic.integration.mpris import MprisService
from yamusic.integration.notifications import NotificationService
from yamusic.integration.tray import TrayIcon
from yamusic.models import TrackInfo
from yamusic.services.lyrics import LyricsService
from yamusic.services.playback import PlaybackController
from yamusic.services.rotor import RotorService
from yamusic.ui.auth_dialog import AuthDialog
from yamusic.ui.main_window import MainWindow
from yamusic.ui.theme import apply_theme

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    level = logging.DEBUG if os.environ.get("YAMUSIC_DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # yandex-music prints a banner once — silence noisy loggers
    logging.getLogger("yandex_music").setLevel(logging.WARNING)


def _authenticate(api: YandexApi, app: QApplication, smoke: bool) -> bool:
    """Ensure a usable token exists; returns False when the user cancels."""
    token = api.credentials.load()
    if token:
        api.set_token(token)  # re-save consistently + install into worker
        api.load_account(lambda _s: log.info("account uid=%s", api.uid))
        return True
    if smoke:
        return True
    for _ in range(5):
        dialog = AuthDialog(api)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.authorized:
            return True
        if dialog.result() == QDialog.DialogCode.Rejected and not dialog.authorized:
            # user pressed Выход on first pass — ask once more softly
            answer = QMessageBox.question(
                None,
                APP_NAME,
                "Без входа доступны только настройки. Выйти?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                return False
    return False


def main() -> int:
    """Entry point registered as console script ``yandex-music-native``."""
    _setup_logging()
    smoke = os.environ.get("YAMUSIC_SMOKE") == "1"

    # Qt platform: works on both Wayland and X11 (user may force via env)
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(ORG_NAME)
    app.setOrganizationDomain(APP_ID)
    app.setDesktopFileName(APP_ID)
    app.setStyle("Fusion")
    app.setStyleSheet(apply_theme())

    icon_path = Path(__file__).parent / "resources" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    settings = Settings()
    opts = settings.options()

    # -- core services
    api = YandexApi()
    api.quality = opts.quality
    api.start(api.credentials.load())

    if not _authenticate(api, app, smoke):
        api.shutdown()
        return 0

    try:
        ensure_mpv()
    except RuntimeError as exc:
        log.error("%s", exc)
        QMessageBox.critical(None, APP_NAME, str(exc))
        api.shutdown()
        return 1
    cache = CacheStore(limit_mb=opts.cache_limit_mb)
    engine = AudioEngine()
    analyzer = SpectrumAnalyzer(engine)
    rotor = RotorService(api, settings)
    controller = PlaybackController(api, engine, rotor, cache, settings)
    lyrics = LyricsService(api)
    notifications = NotificationService(settings)

    window = MainWindow(api, engine, analyzer, rotor, controller, lyrics, cache, settings)

    mpris = MprisService(
        controller,
        on_quit=QApplication.quit,
        on_raise=lambda: (window.show(), window.raise_(), window.activateWindow()),
    )
    if not mpris.available:
        window.statusBar().showMessage("MPRIS недоступен (нет D-Bus сессии)", 5000)

    tray = TrayIcon(controller)
    tray.create()
    tray.show_hide_requested.connect(window.toggleVisibility if hasattr(window, "toggleVisibility") else window.show)
    tray.quit_requested.connect(app.quit)

    # notifications on track change (cover if already cached)
    def _notify(track: TrackInfo | None) -> None:
        if track is None:
            return
        cover_path = None
        if track.cover_url:
            local = cache.cover_path(track.cover_url)
            if local is not None:
                cover_path = str(local)
        notifications.notify_track(track, cover_path)

    controller.track_changed.connect(_notify)

    # cover double-duty: playbar + wave circular viz already wired in window

    # -- bring up the stream
    rotor.load_stations()
    analyzer.start()

    window.show()
    window.raise_()

    # optional: autostart wave
    if os.environ.get("YAMUSIC_AUTOSTART") == "1":
        QTimer.singleShot(400, controller.play_from_wave)

    # smoke mode: run the loop briefly, exercise clean shutdown, exit 0
    if smoke:
        QTimer.singleShot(3000, app.quit)

    log.info("%s %s started (smoke=%s, mpris=%s)", APP_NAME, __version__, smoke, mpris.available)

    try:
        code = app.exec()
    finally:
        log.info("shutting down…")
        try:
            controller.stop()
        except Exception:
            log.exception("controller stop")
        try:
            analyzer.stop()
        except Exception:
            log.exception("analyzer stop")
        try:
            engine.shutdown()
        except Exception:
            log.exception("engine shutdown")
        try:
            mpris.unregister()
        except Exception:
            log.exception("mpris unregister")
        try:
            tray.destroy()
        except Exception:
            log.exception("tray destroy")
        try:
            api.shutdown()
        except Exception:
            log.exception("api shutdown")
        try:
            cache.close()
            settings.sync()
        except Exception:
            log.exception("cache/settings close")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
