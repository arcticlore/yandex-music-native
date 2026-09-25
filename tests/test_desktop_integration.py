"""Tests for the desktop integrations: MPRIS2, notifications and the tray.

The controller rig is borrowed from :mod:`test_playback_controller` so the
integrations are exercised against a real ``PlaybackController`` with a
recording engine, a fake Yandex client and a single Qt event loop.

Run: python -m pytest -q tests/test_desktop_integration.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtDBus import QDBusMessage, QDBusObjectPath
from PySide6.QtGui import QIcon, QWheelEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("YAMUSIC_AO", "null")
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="yml-desktop-config-")
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="yml-desktop-cache-")

from core.config_manager import ConfigManager  # noqa: E402
from core.mpris import (  # noqa: E402
    MPRIS_DESKTOP_ENTRY,
    MPRIS_IDENTITY,
    MPRIS_OBJECT,
    MPRIS_PLAYER_IFACE,
    MPRIS_ROOT_IFACE,
    MPRIS_SERVICE,
    MprisService,
    art_url,
    track_object_path,
)
from core.notifications import (  # noqa: E402
    NOTIFICATIONS_IFACE,
    NOTIFICATIONS_PATH,
    NOTIFICATIONS_SERVICE,
    NotificationService,
    notification_hints,
)
from core.playback_controller import PlaybackState, QueueMode, TrackMetadata  # noqa: E402
from test_playback_controller import Rig, wave_of  # noqa: E402
from ui.tray import (  # noqa: E402
    STATE_LABELS,
    TrayIcon,
    TrayIconWidget,
    make_icon,
    step_volume,
    tooltip_text,
)


# -- fakes ------------------------------------------------------------------


class FakeBus:
    """Minimal ``QDBusConnection`` stand-in that records calls and signals."""

    def __init__(self, connected: bool = True, reply: int = 7) -> None:
        self.connected = connected
        self.calls: list[QDBusMessage] = []
        self.signals: list[QDBusMessage] = []
        self.registered_service = ""
        self.registered_object = ""
        self.reply = reply

    def isConnected(self) -> bool:  # noqa: N802 - Qt naming
        return self.connected

    def registerService(self, name: str) -> bool:  # noqa: N802
        if not self.connected:
            return False
        self.registered_service = name
        return True

    def registerObject(self, path: str, obj: object) -> bool:  # noqa: N802
        self.registered_object = path
        return True

    def unregisterObject(self, path: str) -> bool:  # noqa: N802
        self.registered_object = ""
        return True

    def unregisterService(self, name: str) -> bool:  # noqa: N802
        self.registered_service = ""
        return True

    def call(self, message: QDBusMessage) -> QDBusMessage:
        self.calls.append(message)
        reply = QDBusMessage.createReply(message)
        reply.setArguments([self.reply])
        return reply

    def send(self, message: QDBusMessage) -> bool:
        self.signals.append(message)
        return True

    def method_calls(self) -> list[tuple[str, list[object]]]:
        return [(message.member(), list(message.arguments())) for message in self.calls]

    def properties_changed(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for message in self.signals:
            if message.member() == "PropertiesChanged" and message.arguments():
                out.append((str(message.arguments()[0]), dict(message.arguments()[1])))
        return out


class FakeTray(QObject):
    """``QSystemTrayIcon`` stand-in with the signals the tray relies on."""

    activated = Signal(object)
    volume_stepped = Signal(int)

    def __init__(self, icon: object = None) -> None:
        super().__init__()
        self.icon = icon
        self.tool_tip = ""
        self.menu: object | None = None
        self.visible = False
        self.messages: list[tuple] = []
        self.deleted = 0

    def setIcon(self, icon: object) -> None:  # noqa: N802
        self.icon = icon

    def setToolTip(self, text: str) -> None:  # noqa: N802
        self.tool_tip = text

    def setContextMenu(self, menu: object) -> None:  # noqa: N802
        self.menu = menu

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def showMessage(self, title: str, body: str, icon: object = None, msecs: int = 3000) -> None:  # noqa: N802
        self.messages.append((title, body, msecs))

    def deleteLater(self) -> None:  # noqa: N802
        self.deleted += 1


class BusyBus(FakeBus):
    """A bus where the MPRIS name is already taken."""

    def registerService(self, name: str) -> bool:  # noqa: N802
        return False


@pytest.fixture()
def rig(app: QApplication):
    instance = Rig(app)
    yield instance
    instance.close()


@pytest.fixture()
def playing(rig: Rig) -> Rig:
    tracks = [wave_of(301, title="First"), wave_of(302, title="Second"), wave_of(303, title="Third")]
    assert rig.controller.play_playlist(tracks) is True
    assert rig.settle()
    return rig


@pytest.fixture()
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return ConfigManager()


# -- pure helpers -------------------------------------------------------------


def test_mpris_constants() -> None:
    assert MPRIS_SERVICE == "org.mpris.MediaPlayer2.yandex_music_native"
    assert MPRIS_OBJECT == "/org/mpris/MediaPlayer2"
    assert MPRIS_IDENTITY == "yandex_music_native"
    assert MPRIS_DESKTOP_ENTRY == "yandex-music-native"
    assert MPRIS_ROOT_IFACE == "org.mpris.MediaPlayer2"
    assert MPRIS_PLAYER_IFACE == "org.mpris.MediaPlayer2.Player"


def test_track_object_path() -> None:
    path = track_object_path("123:456")
    assert path.path() == "/org/mpris/MediaPlayer2/Track/123_456"
    weird = track_object_path("weird/id with space").path()
    assert weird.endswith("weird_id_with_space")
    assert track_object_path("").path().endswith("/0")
    cyrillic = track_object_path("юникод").path()
    assert all(char.isascii() and (char.isalnum() or char == "_") for char in cyrillic.rsplit("/", 1)[-1])


def test_art_url_prefers_local_file(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"x")
    meta = TrackMetadata(id="1:2", title="t", cover_path=str(cover), cover_url="https://a/b.jpg")
    assert art_url(meta) == cover.as_uri()
    missing = TrackMetadata(id="1:2", title="t", cover_path=str(tmp_path / "gone.jpg"), cover_url="https://a/b.jpg")
    assert art_url(missing) == "https://a/b.jpg"
    assert art_url(TrackMetadata(id="1:2", title="t")) is None


def test_tooltip_text() -> None:
    meta = TrackMetadata(id="1:2", title="Song", artists=("A", "B"))
    text = tooltip_text(meta, PlaybackState.PLAYING)
    assert text.splitlines()[0] == "Song — A, B"
    assert STATE_LABELS[PlaybackState.PLAYING] in text
    assert tooltip_text(None, PlaybackState.STOPPED) == "Яндекс Музыка"
    solo = TrackMetadata(id="1:2", title="Song", artists=(), album="Live")
    assert tooltip_text(solo, PlaybackState.PAUSED).splitlines()[0] == "Song — Live"


def test_step_volume() -> None:
    assert step_volume(50, 1) == 55
    assert step_volume(50, -1) == 45
    assert step_volume(98, 1) == 100
    assert step_volume(2, -1) == 0
    assert step_volume(50, 3, 10) == 80


def test_notification_hints(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"x")
    meta = TrackMetadata(id="1:2", title="t", artists=("A",), cover_path=str(cover))
    hints = notification_hints(meta)
    assert hints["image-path"] == cover.as_uri()
    assert hints["category"] == "music"
    remote = TrackMetadata(id="1:2", title="t", cover_url="https://a/b.jpg")
    assert notification_hints(remote)["image-path"] == "https://a/b.jpg"
    assert notification_hints(None) == {}


# -- MPRIS: properties ---------------------------------------------------------


def test_mpris_metadata(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    meta = mpris.metadata()
    assert meta["xesam:title"] == "First"
    assert meta["xesam:artist"] == ["Artist"]
    assert meta["xesam:album"] == "Album 7"
    assert meta["mpris:length"] == 180_000_000
    assert meta["xesam:userRating"] == 0.0
    assert meta["mpris:trackid"].path() == "/org/mpris/MediaPlayer2/Track/301_7"
    assert "mpris:artUrl" not in meta
    assert mpris.metadata() == meta


def test_mpris_metadata_empty_without_track(rig: Rig) -> None:
    mpris = MprisService(rig.controller, register=False)
    assert mpris.metadata() == {}
    assert mpris.playback_status() == "Stopped"


def test_mpris_playback_status(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    assert mpris.playback_status() == "Playing"
    playing.controller.toggle_play()
    assert mpris.playback_status() == "Paused"
    playing.controller.stop()
    assert mpris.playback_status() == "Stopped"


def test_mpris_status_while_buffering(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    playing.controller._set_buffering(True)
    assert playing.controller.state == PlaybackState.BUFFERING
    assert mpris.playback_status() == "Playing"
    playing.controller._set_buffering(False)


def test_mpris_volume_property(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    assert mpris.volume == pytest.approx(0.8)
    playing.controller.set_volume(35)
    assert mpris.volume == pytest.approx(0.35)
    assert mpris._player.Volume == pytest.approx(0.35)
    playing.controller.set_volume(100)
    assert mpris.volume == 1.0


def test_mpris_position_property(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    assert mpris.position_us == 0
    playing.controller.seek(5_000)
    assert playing.controller.position_ms == 5_000
    assert mpris.position_us == 5_000_000


def test_mpris_capabilities(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    assert mpris.can_go_next() is True
    assert mpris.can_go_previous() is False
    playing.controller.seek(8_000)
    assert mpris.can_go_previous() is True
    playing.controller.next()
    playing.controller.next()
    playing.settle()
    assert mpris.can_go_next() is False
    assert mpris.can_go_previous() is True
    assert mpris._player.CanPause is True
    assert mpris._player.CanSeek is True
    assert mpris._player.CanControl is True
    assert mpris._player.Shuffle is False
    assert mpris._player.LoopStatus == "None"
    assert mpris._player.Rate == 1.0


def test_mpris_capabilities_in_radio(playing: Rig) -> None:
    playing.client.batches = []
    mpris = MprisService(playing.controller, register=False)
    assert playing.controller.start_wave() is True
    playing.settle()
    assert playing.controller.mode == QueueMode.RADIO
    assert mpris._player.Shuffle is True
    assert mpris.can_go_next() is True


# -- MPRIS: incoming methods ---------------------------------------------------


def test_mpris_play_pause_stop(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    player = mpris._player
    player.Pause()
    assert playing.controller.state == PlaybackState.PAUSED
    player.PlayPause()
    assert playing.controller.state == PlaybackState.PLAYING
    player.Pause()
    player.Play()
    assert playing.controller.state == PlaybackState.PLAYING
    player.Stop()
    assert playing.controller.state == PlaybackState.STOPPED
    player.Play()
    playing.settle()
    assert playing.controller.state in (PlaybackState.PLAYING, PlaybackState.BUFFERING)


def test_mpris_play_ignored_while_buffering(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    playing.controller._set_buffering(True)
    mpris._player.Play()
    assert playing.controller.state == PlaybackState.BUFFERING
    playing.controller._set_buffering(False)


def test_mpris_next_previous(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    mpris._player.Next()
    playing.settle()
    assert playing.controller.current.id == "302:7"
    mpris._player.Previous()
    playing.settle()
    assert playing.controller.current.id == "301:7"


def test_mpris_seek_and_set_position(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    player = mpris._player
    player.Seek(10_000_000)
    assert playing.controller.position_ms == 10_000
    player.Seek(-4_000_000)
    assert playing.controller.position_ms == 6_000
    player.SetPosition(QDBusObjectPath(track_object_path("301:7").path()), 20_000_000)
    assert playing.controller.position_ms == 20_000
    player.SetPosition(QDBusObjectPath("/org/mpris/MediaPlayer2/Track/foreign"), 30_000_000)
    assert playing.controller.position_ms == 20_000


def test_mpris_seek_without_track(rig: Rig) -> None:
    mpris = MprisService(rig.controller, register=False)
    mpris._player.Seek(5_000_000)
    mpris._player.SetPosition(QDBusObjectPath("/org/mpris/MediaPlayer2/Track/1"), 5_000_000)
    assert rig.engine.seeks == []


def test_mpris_open_uri_is_logged(playing: Rig, caplog: pytest.LogCaptureFixture) -> None:
    mpris = MprisService(playing.controller, register=False)
    with caplog.at_level("INFO", logger="core.mpris"):
        mpris._player.OpenUri("https://example.invalid/track.mp3")
    assert "OpenUri" in caplog.text


def test_mpris_root_adaptor(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    assert mpris._root.Identity == "yandex_music_native"
    assert mpris._root.DesktopEntry == "yandex-music-native"
    assert mpris._root.CanQuit is True
    assert mpris._root.CanRaise is True
    assert mpris._root.HasTrackList is False
    assert "file" in mpris._root.SupportedUriSchemes
    assert "audio/flac" in mpris._root.SupportedMimeTypes


# -- MPRIS: registration and broadcasts ----------------------------------------


def test_mpris_registers_and_releases(playing: Rig) -> None:
    bus = FakeBus()
    mpris = MprisService(playing.controller, bus=bus)
    assert mpris.available is True
    assert bus.registered_service == MPRIS_SERVICE
    assert bus.registered_object == MPRIS_OBJECT
    mpris.unregister()
    assert mpris.available is False
    assert bus.registered_service == ""
    assert bus.registered_object == ""
    mpris.unregister()


def test_mpris_without_session_bus(playing: Rig) -> None:
    mpris = MprisService(playing.controller, bus=FakeBus(connected=False))
    assert mpris.available is False
    assert mpris._broadcast(MPRIS_PLAYER_IFACE, {"Volume": 1.0}) is None
    mpris.unregister()


def test_mpris_name_taken(playing: Rig) -> None:
    mpris = MprisService(playing.controller, bus=BusyBus())
    assert mpris.available is False


def test_mpris_broadcasts_on_track_and_state(playing: Rig) -> None:
    bus = FakeBus()
    mpris = MprisService(playing.controller, bus=bus)
    bus.signals.clear()
    playing.controller.next()
    playing.settle()
    assert mpris.metadata()["xesam:title"] == "Second"
    changed = [payload for _, payload in bus.properties_changed()]
    assert changed
    assert any("Metadata" in payload for payload in changed)
    assert any(payload.get("PlaybackStatus") == "Playing" for payload in changed)
    bus.signals.clear()
    playing.controller.toggle_play()
    assert any(payload.get("PlaybackStatus") == "Paused" for _, payload in bus.properties_changed())


def test_mpris_broadcasts_volume(playing: Rig) -> None:
    bus = FakeBus()
    mpris = MprisService(playing.controller, bus=bus)
    bus.signals.clear()
    playing.controller.set_volume(20)
    assert mpris.volume == pytest.approx(0.2)
    assert any(
        payload.get("Volume") == pytest.approx(0.2) for _, payload in bus.properties_changed()
    )


def test_mpris_like_updates_rating(playing: Rig) -> None:
    bus = FakeBus()
    mpris = MprisService(playing.controller, bus=bus)
    assert mpris.metadata()["xesam:userRating"] == 0.0
    bus.signals.clear()
    assert playing.controller.like() is True
    playing.settle()
    assert playing.controller.current.liked is True
    assert mpris.metadata()["xesam:userRating"] == 1.0
    assert any(
        payload.get("Metadata", {}).get("xesam:userRating") == 1.0
        for _, payload in bus.properties_changed()
    )


def test_mpris_like_of_other_track_ignored(playing: Rig) -> None:
    bus = FakeBus()
    mpris = MprisService(playing.controller, bus=bus)
    bus.signals.clear()
    playing.controller.like_status_changed.emit("999:9", True)
    assert mpris.metadata()["xesam:userRating"] == 0.0
    assert bus.properties_changed() == []


def test_mpris_cover_updates_arturl(playing: Rig, tmp_path: Path) -> None:
    bus = FakeBus()
    mpris = MprisService(playing.controller, bus=bus)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"x")
    bus.signals.clear()
    playing.controller.cover_ready.emit(playing.controller.current.id, str(cover))
    assert mpris.metadata()["mpris:artUrl"] == cover.as_uri()
    assert any(
        payload.get("Metadata", {}).get("mpris:artUrl") == cover.as_uri()
        for _, payload in bus.properties_changed()
    )


def test_mpris_cover_of_other_track_ignored(playing: Rig, tmp_path: Path) -> None:
    bus = FakeBus()
    mpris = MprisService(playing.controller, bus=bus)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"x")
    bus.signals.clear()
    playing.controller.cover_ready.emit("999:9", str(cover))
    assert "mpris:artUrl" not in mpris.metadata()
    assert bus.properties_changed() == []


def test_mpris_seeked_signal(playing: Rig) -> None:
    mpris = MprisService(playing.controller, bus=FakeBus())
    seen: list[int] = []
    mpris._player.Seeked.connect(seen.append)
    playing.controller.seek(3_000)
    assert seen == [3_000_000]
    assert mpris.position_us == 3_000_000
    playing.controller.prev()
    assert seen[-1] == 0


def test_mpris_position_signal_does_not_emit_seeked(playing: Rig) -> None:
    mpris = MprisService(playing.controller, register=False)
    seen: list[int] = []
    mpris._player.Seeked.connect(seen.append)
    playing.controller.position_changed.emit(1_500, 180_000)
    assert mpris.position_us == 1_500_000
    assert seen == []


# -- notifications -------------------------------------------------------------


def test_notifications_disabled_by_config(playing: Rig, config: ConfigManager) -> None:
    bus = FakeBus()
    config.set_notifications(False)
    service = NotificationService(playing.controller, config, bus=bus)
    assert service.enabled is False
    assert service.notify_track(playing.controller.current) is False
    assert bus.calls == []


def test_notifications_enabled_by_default(config: ConfigManager) -> None:
    assert config.get_notifications() is True
    assert config.get_bool("cache_tracks") is True
    config.set("notifications", "false")
    assert config.get_notifications() is False
    config.set("notifications", "yes")
    assert config.get_notifications() is True
    config.set_notifications(False)
    assert config.get("notifications") is False


def test_notification_message(playing: Rig, config: ConfigManager) -> None:
    bus = FakeBus(reply=42)
    service = NotificationService(playing.controller, config, bus=bus)
    assert service.available is True
    assert service.notify_track(playing.controller.current) is True
    member, args = bus.method_calls()[0]
    assert member == "Notify"
    assert bus.calls[0].service() == NOTIFICATIONS_SERVICE
    assert bus.calls[0].path() == NOTIFICATIONS_PATH
    assert bus.calls[0].interface() == NOTIFICATIONS_IFACE
    assert args[0] == "Яндекс Музыка"
    assert args[2] == "yandex-music-native"
    assert args[3] == "First"
    assert args[4] == "Artist"
    assert args[6]["category"] == "music"
    assert args[7] == 5000
    assert service.last_id == 42


def test_notification_on_track_change(playing: Rig, config: ConfigManager) -> None:
    bus = FakeBus()
    service = NotificationService(playing.controller, config, bus=bus)
    bus.calls.clear()
    playing.controller.next()
    playing.settle()
    titles = [args[3] for _, args in bus.method_calls()]
    assert titles == ["Second"]
    assert service.last_id == 7


def test_notification_disabled_stops_signals(playing: Rig, config: ConfigManager) -> None:
    bus = FakeBus()
    service = NotificationService(playing.controller, config, bus=bus)
    service.set_enabled(False)
    bus.calls.clear()
    playing.controller.next()
    playing.settle()
    assert bus.calls == []
    service.set_enabled(True)
    playing.controller.next()
    playing.settle()
    assert bus.calls


def test_notification_without_bus(playing: Rig, config: ConfigManager) -> None:
    service = NotificationService(playing.controller, config, bus=FakeBus(connected=False))
    assert service.available is False
    assert service.notify_track(playing.controller.current) is False
    service.close_last()


def test_notification_close(playing: Rig, config: ConfigManager) -> None:
    bus = FakeBus()
    service = NotificationService(playing.controller, config, bus=bus)
    service.notify_track(playing.controller.current)
    assert service.last_id == 7
    bus.calls.clear()
    service.close_last()
    assert bus.method_calls()[0][0] == "CloseNotification"
    assert service.last_id == 0
    bus.calls.clear()
    service.close_last()
    assert bus.calls == []


def test_notification_on_stop(rig: Rig, config: ConfigManager) -> None:
    bus = FakeBus()
    service = NotificationService(rig.controller, config, bus=bus)
    service.notify_track(TrackMetadata(id="1:2", title="x"))
    bus.calls.clear()
    rig.controller.track_changed.emit(None)
    assert bus.method_calls()[0][0] == "CloseNotification"


# -- tray ----------------------------------------------------------------------


def _tray(playing: Rig) -> tuple[TrayIcon, FakeTray]:
    """A tray whose system-tray icon is replaced by a recording double."""
    box: list[FakeTray] = []

    def factory(icon: object = None) -> FakeTray:
        tray = FakeTray(icon)
        box.append(tray)
        return tray

    tray = TrayIcon(playing.controller, widget_factory=factory, available=True)
    assert tray.create() is True
    return tray, box[0]


def test_tray_creates_menu(playing: Rig) -> None:
    tray, icon = _tray(playing)
    assert tray.available is True
    assert icon.visible is True
    labels = [action.text() for action in icon.menu.actions() if not action.isSeparator()]
    assert labels == ["Пауза", "Следующий", "Предыдущий", "Моя волна", "Выход"]


def test_tray_create_is_idempotent(playing: Rig) -> None:
    calls: list[FakeTray] = []

    def factory(_icon: object = None) -> FakeTray:
        tray = FakeTray()
        calls.append(tray)
        return tray

    tray = TrayIcon(playing.controller, widget_factory=factory, available=True)
    assert tray.create() is True
    assert tray.create() is True
    assert len(calls) == 1


def test_tray_unavailable_returns_false(playing: Rig) -> None:
    tray = TrayIcon(playing.controller, available=False)
    assert tray.create() is False
    assert tray.available is False
    assert tray.show_message("t", "b") is False


def test_tray_left_click_toggles_window(playing: Rig) -> None:
    tray, icon = _tray(playing)
    seen: list[bool] = []
    tray.show_hide_requested.connect(lambda: seen.append(True))
    icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert seen == [True]
    icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert seen == [True, True]
    icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)
    assert seen == [True, True]
    icon.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert len(seen) == 3


def test_tray_wheel_changes_volume(playing: Rig) -> None:
    tray, icon = _tray(playing)
    requested: list[int] = []
    tray.volume_requested.connect(requested.append)
    icon.volume_stepped.emit(1)
    assert playing.controller.volume == 85
    icon.volume_stepped.emit(-1)
    assert playing.controller.volume == 80
    assert requested == [85, 80]
    for _ in range(30):
        icon.volume_stepped.emit(1)
    assert playing.controller.volume == 100
    for _ in range(30):
        icon.volume_stepped.emit(-1)
    assert playing.controller.volume == 0


def test_tray_wheel_event_parsing(app: QApplication) -> None:
    widget = TrayIconWidget()
    seen: list[int] = []
    widget.volume_stepped.connect(seen.append)

    def wheel(delta_y: int) -> QWheelEvent:
        return QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(0, 0),
            QPoint(0, delta_y),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )

    assert widget.event(wheel(120)) is True
    assert widget.event(wheel(-120)) is True
    widget.event(QEvent(QEvent.Type.User))
    assert seen == [1, -1]


def test_tray_menu_actions(playing: Rig) -> None:
    tray, icon = _tray(playing)
    assert tray.action_play.text() == "Пауза"
    tray.action_next.trigger()
    playing.settle()
    assert playing.controller.current.id == "302:7"
    tray.action_prev.trigger()
    playing.settle()
    assert playing.controller.current.id == "301:7"
    tray.action_play.trigger()
    assert playing.controller.state == PlaybackState.PAUSED
    quit_seen: list[bool] = []
    tray.quit_requested.connect(lambda: quit_seen.append(True))
    tray.action_quit.trigger()
    assert quit_seen == [True]


def test_tray_wave_action(playing: Rig) -> None:
    tray, _icon = _tray(playing)
    tray.action_wave.trigger()
    playing.settle()
    assert playing.controller.mode == QueueMode.RADIO
    assert playing.controller.state in (PlaybackState.PLAYING, PlaybackState.BUFFERING)


def test_tray_tooltip_tracks_and_state(playing: Rig) -> None:
    tray, icon = _tray(playing)
    assert icon.tool_tip.startswith("First — Artist")
    assert STATE_LABELS[PlaybackState.PLAYING] in icon.tool_tip
    playing.controller.toggle_play()
    assert STATE_LABELS[PlaybackState.PAUSED] in icon.tool_tip
    playing.controller.next()
    playing.settle()
    assert icon.tool_tip.startswith("Second — Artist")
    playing.controller.stop()
    assert icon.tool_tip == f"Second — Artist\n{STATE_LABELS[PlaybackState.STOPPED]}"


def test_tray_action_enabled_states(playing: Rig) -> None:
    tray, _icon = _tray(playing)
    assert tray.action_play.text() == "Пауза"
    assert tray.action_next.isEnabled() is True
    playing.controller.stop()
    assert tray.action_play.text() == "Играть"
    assert tray.action_play.isEnabled() is True
    assert tray.action_next.isEnabled() is False


def test_tray_cover_icon(playing: Rig, tmp_path: Path) -> None:
    tray, icon = _tray(playing)
    assert isinstance(icon.icon, QIcon)
    before = icon.icon
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"x")
    playing.controller.cover_ready.emit(playing.controller.current.id, str(cover))
    assert isinstance(icon.icon, QIcon)
    assert icon.icon is not before
    playing.controller.cover_ready.emit("999:9", str(cover))
    assert icon.icon is not before


def test_tray_destroy_is_idempotent(playing: Rig) -> None:
    tray, icon = _tray(playing)
    tray.destroy()
    assert icon.visible is False
    assert icon.deleted == 1
    assert tray.available is False
    tray.destroy()


def test_tray_show_message(playing: Rig) -> None:
    tray, icon = _tray(playing)
    assert tray.show_message("Now", "Song") is True
    assert icon.messages[-1][:2] == ("Now", "Song")


def test_make_icon_pixmaps(app: QApplication) -> None:
    assert not make_icon(True).isNull()
    assert not make_icon(False).isNull()


# -- one ConfigManager only ----------------------------------------------------


def test_new_integrations_use_core_config_only() -> None:
    legacy = ("yamusic.config", "yamusic.settings")
    for module in ("core/mpris.py", "core/notifications.py", "ui/tray.py"):
        source = (ROOT / "src" / module).read_text(encoding="utf-8")
        for needle in legacy:
            assert needle not in source, f"{module} still uses {needle}"
    notifications = (ROOT / "src" / "core/notifications.py").read_text(encoding="utf-8")
    assert "from core.config_manager import ConfigManager" in notifications
