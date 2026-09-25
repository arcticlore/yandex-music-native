"""MPRIS2 D-Bus integration (media keys, system volume widget, players applets)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    ClassInfo,
    Property,
    QObject,
    Signal,
    Slot,
)
from PySide6.QtDBus import (
    QDBusAbstractAdaptor,
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
)

from yamusic.constants import (
    MPRIS_OBJECT,
    MPRIS_PLAYER_IFACE,
    MPRIS_ROOT_IFACE,
    MPRIS_SERVICE,
)
from yamusic.models import TrackInfo
from yamusic.services.playback import PlaybackController

log = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"[^A-Za-z0-9_]")


def _track_object_path(track_id: str) -> QDBusObjectPath:
    safe = _SAFE_ID.sub("_", track_id) or "0"
    return QDBusObjectPath(f"{MPRIS_OBJECT}/Track/{safe}")


@ClassInfo({"D-Bus Interface": MPRIS_ROOT_IFACE})
class _RootAdaptor(QDBusAbstractAdaptor):
    raise_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)

    @Property(bool, constant=True)
    def CanQuit(self) -> bool:
        return True

    @Property(bool, constant=True)
    def CanRaise(self) -> bool:
        return True

    @Property(bool, constant=True)
    def CanSetFullscreen(self) -> bool:
        return False

    @Property(bool, constant=True)
    def HasTrackList(self) -> bool:
        return False

    @Property(str, constant=True)
    def Identity(self) -> str:
        return "Yandex Music Native"

    @Property(str, constant=True)
    def DesktopEntry(self) -> str:
        return "yandex-music-native"

    @Property("QStringList", constant=True)
    def SupportedUriSchemes(self) -> list[str]:
        return ["file", "http", "https"]

    @Property("QStringList", constant=True)
    def SupportedMimeTypes(self) -> list[str]:
        return ["audio/mpeg", "audio/flac", "audio/ogg"]

    @Slot()
    def Quit(self) -> None:
        self.quit_requested.emit()

    @Slot()
    def Raise(self) -> None:
        self.raise_requested.emit()


@ClassInfo({"D-Bus Interface": MPRIS_PLAYER_IFACE})
class _PlayerAdaptor(QDBusAbstractAdaptor):
    Seeked = Signal("qlonglong")

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)

    # delegate to MprisService (parent)
    def _svc(self) -> "MprisService":
        return self.parent()  # type: ignore[return-value]

    # -- methods ---------------------------------------------------------

    @Slot()
    def Next(self) -> None:
        self._svc().controller.next(manual=True)

    @Slot()
    def Previous(self) -> None:
        self._svc().controller.previous()

    @Slot()
    def Pause(self) -> None:
        ctl = self._svc().controller
        if ctl.playing:
            ctl.play_pause()

    @Slot()
    def PlayPause(self) -> None:
        self._svc().controller.play_pause()

    @Slot()
    def Stop(self) -> None:
        self._svc().controller.stop()

    @Slot()
    def Play(self) -> None:
        ctl = self._svc().controller
        if not ctl.playing:
            ctl.play_pause()

    @Slot("qlonglong")
    def Seek(self, offset_us: int) -> None:
        ctl = self._svc().controller
        target = max(0, ctl.position_ms + int(offset_us) // 1000)
        ctl.seek(target)

    @Slot(QDBusObjectPath, "qlonglong")
    def SetPosition(self, track_id: QDBusObjectPath, position_us: int) -> None:
        ctl = self._svc().controller
        current = ctl.current
        if current is None:
            return
        if track_id.path() != _track_object_path(current.id).path():
            return
        ctl.seek(max(0, int(position_us) // 1000))

    @Slot(str)
    def OpenUri(self, uri: str) -> None:
        log.info("OpenUri ignored: %s", uri)

    # -- properties ------------------------------------------------------

    @Property(str)
    def PlaybackStatus(self) -> str:
        ctl = self._svc().controller
        if ctl.current is None:
            return "Stopped"
        return "Playing" if ctl.playing else "Paused"

    @Property(str)
    def LoopStatus(self) -> str:
        return "None"

    @Slot(str)
    def set_LoopStatus(self, _value: str) -> None:
        pass

    @Property(float)
    def Rate(self) -> float:
        return 1.0

    @Property(bool)
    def Shuffle(self) -> bool:
        return self._svc().controller.station_mode

    @Property(float)
    def Volume(self) -> float:
        return self._svc().controller.engine.volume / 100.0

    @Slot(float)
    def set_Volume(self, value: float) -> None:
        self._svc().controller.set_volume(int(max(0.0, min(1.0, value)) * 100))

    @Property(float, constant=True)
    def MinimumRate(self) -> float:
        return 1.0

    @Property(float, constant=True)
    def MaximumRate(self) -> float:
        return 1.0

    @Property(bool)
    def CanGoNext(self) -> bool:
        return self._svc().controller.can_next()

    @Property(bool)
    def CanGoPrevious(self) -> bool:
        return self._svc().controller.can_previous()

    @Property(bool)
    def CanPlay(self) -> bool:
        return True

    @Property(bool)
    def CanPause(self) -> bool:
        return self._svc().controller.current is not None

    @Property(bool)
    def CanSeek(self) -> bool:
        return self._svc().controller.current is not None

    @Property(bool, constant=True)
    def CanControl(self) -> bool:
        return True

    @Property("qlonglong")
    def Position(self) -> int:
        return int(self._svc().controller.position_ms) * 1000

    @Property(dict)
    def Metadata(self) -> dict[str, Any]:
        return self._svc().build_metadata()

    def emit_seeked(self, position_ms: int) -> None:
        self.Seeked.emit(int(position_ms) * 1000)


class MprisService(QObject):
    """Owns MPRIS adaptors and keeps D-Bus properties in sync with the player."""

    def __init__(
        self,
        controller: PlaybackController,
        on_quit: Callable[[], None],
        on_raise: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.available = False
        self._art_path: str | None = None

        self._root = _RootAdaptor(self)
        self._player = _PlayerAdaptor(self)
        self._root.quit_requested.connect(on_quit)
        self._root.raise_requested.connect(on_raise)

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.warning("MPRIS: no session bus — media keys disabled")
            return
        if not bus.registerService(MPRIS_SERVICE):
            log.warning("MPRIS: service %s busy", MPRIS_SERVICE)
            return
        if not bus.registerObject(MPRIS_OBJECT, self):
            log.warning("MPRIS: failed to register object")
            return
        self.available = True

        controller.track_changed.connect(self._on_track)
        controller.state_changed.connect(self._on_state)
        controller.volume_changed.connect(self._on_volume)
        controller.seeked.connect(self._on_seek)
        controller.cover_ready.connect(self._on_cover)

    # -- property change broadcasting ------------------------------------

    def _broadcast(self, iface: str, changed: dict[str, Any], invalidated: list[str] | None = None) -> None:
        if not self.available:
            return
        bus = QDBusConnection.sessionBus()
        msg = QDBusMessage.createSignal(
            MPRIS_OBJECT, "org.freedesktop.DBus.Properties", "PropertiesChanged"
        )
        msg << iface << changed << (invalidated or [])
        bus.send(msg)

    def build_metadata(self) -> dict[str, Any]:
        track = self.controller.current
        if track is None:
            return {}
        meta: dict[str, Any] = {
            "mpris:trackid": _track_object_path(track.id),
            "xesam:title": track.title,
            "xesam:artist": [track.artist_line] if track.artists else ["Unknown"],
            "xesam:album": track.album,
            "xesam:contentCreated": "",
            "mpris:length": int(max(track.duration_ms, self.controller.duration_ms)) * 1000,
        }
        if self._art_path:
            meta["mpris:artUrl"] = Path(self._art_path).as_uri()
        if track.cover_url and not self._art_path:
            meta["mpris:artUrl"] = track.cover_url
        # drop empty string entries — some clients dislike them
        return {k: v for k, v in meta.items() if v not in ("", [], None)}

    # -- controller signals ----------------------------------------------

    def _on_track(self, track: TrackInfo | None) -> None:
        del track
        # art may still be loading; refresh again on cover_ready
        self._broadcast(
            MPRIS_PLAYER_IFACE,
            {
                "Metadata": self.build_metadata(),
                "PlaybackStatus": self._player.PlaybackStatus,
                "CanPause": self._player.CanPause,
                "CanSeek": self._player.CanSeek,
            },
        )

    def _on_state(self, _playing: bool) -> None:
        self._broadcast(
            MPRIS_PLAYER_IFACE,
            {
                "PlaybackStatus": self._player.PlaybackStatus,
                "CanGoNext": self._player.CanGoNext,
                "CanGoPrevious": self._player.CanGoPrevious,
            },
        )

    def _on_volume(self, _percent: int) -> None:
        self._broadcast(MPRIS_PLAYER_IFACE, {"Volume": self._player.Volume})

    def _on_seek(self, position_ms: int) -> None:
        if not self.available:
            return
        self._player.emit_seeked(position_ms)

    def _on_cover(self, track_id: str, path: str) -> None:
        current = self.controller.current
        if current is None or current.id != track_id:
            return
        self._art_path = path
        self._broadcast(MPRIS_PLAYER_IFACE, {"Metadata": self.build_metadata()})

    def unregister(self) -> None:
        if not self.available:
            return
        bus = QDBusConnection.sessionBus()
        bus.unregisterObject(MPRIS_OBJECT)
        bus.unregisterService(MPRIS_SERVICE)
        self.available = False
