"""MPRIS2 D-Bus service bound to :class:`~core.playback_controller.PlaybackController`.

The service owns two QtDBus adaptors registered under
``org.mpris.MediaPlayer2.yandex_music_native`` and keeps their properties in
sync with the controller: ``track_changed`` refreshes ``Metadata``,
``state_changed`` refreshes ``PlaybackStatus``, ``position_changed`` keeps the
cached ``Position`` and ``seeked`` emits ``Seeked``, ``like_status_changed``
updates ``xesam:userRating`` and ``volume_changed`` updates ``Volume``.

Incoming calls from the desktop shell (GNOME Shell media widget, Plasma,
Waybar, media keys) are translated into controller transport calls. Nothing
here knows about widgets, so the same object works under a headless test.

QtDBus implements ``org.freedesktop.DBus.Properties`` with ``Get``/``GetAll``
only — it never exports ``Set`` for adaptor properties — so ``Volume`` and
``LoopStatus`` are read-only on the bus and the volume is changed from the tray
or the main window instead.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import ClassInfo, Property, QObject, Signal, Slot
from PySide6.QtDBus import (
    QDBusAbstractAdaptor,
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
)

from core.playback_controller import PlaybackController, PlaybackState, QueueMode

log = logging.getLogger(__name__)

MPRIS_ROOT_IFACE = "org.mpris.MediaPlayer2"
MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
MPRIS_SERVICE = "org.mpris.MediaPlayer2.yandex_music_native"
MPRIS_OBJECT = "/org/mpris/MediaPlayer2"
MPRIS_IDENTITY = "yandex_music_native"
MPRIS_DESKTOP_ENTRY = "yandex-music-native"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

_SAFE_ID = re.compile(r"[^A-Za-z0-9_]")

_MPRIS_STATUS = {
    PlaybackState.STOPPED: "Stopped",
    PlaybackState.PAUSED: "Paused",
    PlaybackState.PLAYING: "Playing",
    PlaybackState.BUFFERING: "Playing",
}


def track_object_path(track_id: str) -> QDBusObjectPath:
    """Stable object path for a track id (``id:album`` is not a valid path)."""
    safe = _SAFE_ID.sub("_", str(track_id)) or "0"
    return QDBusObjectPath(f"{MPRIS_OBJECT}/Track/{safe}")


def art_url(meta: Any) -> str | None:
    """``file://`` URL of a cached cover, falling back to the remote one."""
    local = getattr(meta, "cover_path", None)
    if local:
        path = Path(local)
        try:
            if path.exists():
                return path.as_uri()
        except OSError:
            pass
    remote = getattr(meta, "cover_url", None)
    return str(remote) if remote else None


@ClassInfo({"D-Bus Interface": MPRIS_ROOT_IFACE})
class _RootAdaptor(QDBusAbstractAdaptor):
    quit_requested = Signal()
    raise_requested = Signal()

    @Slot()
    def Quit(self) -> None:
        self.quit_requested.emit()

    @Slot()
    def Raise(self) -> None:
        self.raise_requested.emit()

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
        return MPRIS_IDENTITY

    @Property(str, constant=True)
    def DesktopEntry(self) -> str:
        return MPRIS_DESKTOP_ENTRY

    @Property("QStringList", constant=True)
    def SupportedUriSchemes(self) -> list[str]:
        return ["file", "http", "https"]

    @Property("QStringList", constant=True)
    def SupportedMimeTypes(self) -> list[str]:
        return ["audio/mpeg", "audio/flac", "audio/ogg"]


@ClassInfo({"D-Bus Interface": MPRIS_PLAYER_IFACE})
class _PlayerAdaptor(QDBusAbstractAdaptor):
    Seeked = Signal("qlonglong")

    def _svc(self) -> MprisService:
        return self.parent()  # type: ignore[return-value]

    # -- incoming methods ----------------------------------------------------

    @Slot()
    def Play(self) -> None:
        ctl = self._svc().controller
        if ctl.state in (PlaybackState.PLAYING, PlaybackState.BUFFERING):
            return
        ctl.toggle_play()

    @Slot()
    def Pause(self) -> None:
        ctl = self._svc().controller
        if ctl.is_playing:
            ctl.toggle_play()

    @Slot()
    def PlayPause(self) -> None:
        self._svc().controller.toggle_play()

    @Slot()
    def Stop(self) -> None:
        self._svc().controller.stop()

    @Slot()
    def Next(self) -> None:
        self._svc().controller.next()

    @Slot()
    def Previous(self) -> None:
        self._svc().controller.prev()

    @Slot("qlonglong")
    def Seek(self, offset_us: int) -> None:
        ctl = self._svc().controller
        if ctl.current is None:
            return
        target = max(0, ctl.position_ms + int(offset_us) // 1000)
        ctl.seek(target)

    @Slot(QDBusObjectPath, "qlonglong")
    def SetPosition(self, track_id: QDBusObjectPath, position_us: int) -> None:
        ctl = self._svc().controller
        current = ctl.current
        if current is None:
            return
        if track_id.path() != track_object_path(current.id).path():
            return
        ctl.seek(max(0, int(position_us) // 1000))

    @Slot(str)
    def OpenUri(self, uri: str) -> None:
        log.info("OpenUri is not supported: %s", uri)

    # -- properties ---------------------------------------------------------

    @Property(str)
    def PlaybackStatus(self) -> str:
        return self._svc().playback_status()

    @Property("qlonglong")
    def Position(self) -> int:
        return self._svc().position_us

    @Property(dict)
    def Metadata(self) -> dict[str, Any]:
        return self._svc().metadata()

    @Property(float)
    def Volume(self) -> float:
        return self._svc().volume

    @Property(float, constant=True)
    def Rate(self) -> float:
        return 1.0

    @Property(bool)
    def Shuffle(self) -> bool:
        return self._svc().controller.mode == QueueMode.RADIO

    @Property(str, constant=True)
    def LoopStatus(self) -> str:
        return "None"

    @Property(bool)
    def CanGoNext(self) -> bool:
        return self._svc().can_go_next()

    @Property(bool)
    def CanGoPrevious(self) -> bool:
        return self._svc().can_go_previous()

    @Property(bool, constant=True)
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

    @Property(float, constant=True)
    def MinimumRate(self) -> float:
        return 1.0

    @Property(float, constant=True)
    def MaximumRate(self) -> float:
        return 1.0

    def emit_seeked(self, position_ms: int) -> None:
        self.Seeked.emit(int(position_ms) * 1000)


class MprisService(QObject):
    """Publishes the controller on the session bus and mirrors its signals."""

    def __init__(
        self,
        controller: PlaybackController,
        on_quit: Callable[[], None] | None = None,
        on_raise: Callable[[], None] | None = None,
        parent: QObject | None = None,
        *,
        bus: QDBusConnection | None = None,
        register: bool = True,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._bus = bus if bus is not None else QDBusConnection.sessionBus()
        self._available = False
        self._position_ms = 0
        self._art_path: str | None = None

        self._root = _RootAdaptor(self)
        self._player = _PlayerAdaptor(self)
        self._root.quit_requested.connect(on_quit or (lambda: None))
        self._root.raise_requested.connect(on_raise or (lambda: None))

        if register:
            self._register()

        controller.track_changed.connect(self._on_track)
        controller.state_changed.connect(self._on_state)
        controller.position_changed.connect(self._on_position)
        controller.seeked.connect(self._on_seeked)
        controller.like_status_changed.connect(self._on_like)
        controller.cover_ready.connect(self._on_cover)
        controller.volume_changed.connect(self._on_volume)

    # -- registration -------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether the bus name and object are owned by this process."""
        return self._available

    def _register(self) -> bool:
        if not self._bus.isConnected():
            log.warning("MPRIS: no session bus — media keys and shell controls disabled")
            return False
        if not self._bus.registerService(MPRIS_SERVICE):
            log.warning("MPRIS: bus name %s is already taken", MPRIS_SERVICE)
            return False
        if not self._bus.registerObject(MPRIS_OBJECT, self):
            log.warning("MPRIS: cannot export %s", MPRIS_OBJECT)
            self._bus.unregisterService(MPRIS_SERVICE)
            return False
        self._available = True
        log.info("MPRIS: %s registered", MPRIS_SERVICE)
        return True

    def unregister(self) -> None:
        """Release the object and the bus name (call before the event loop ends)."""
        if not self._available:
            return
        self._bus.unregisterObject(MPRIS_OBJECT)
        self._bus.unregisterService(MPRIS_SERVICE)
        self._available = False
        log.info("MPRIS: %s released", MPRIS_SERVICE)

    # -- property computation ------------------------------------------------

    def playback_status(self) -> str:
        """MPRIS status; a track that is still resolving counts as playing."""
        if self.controller.current is None:
            return "Stopped"
        return _MPRIS_STATUS.get(self.controller.state, "Stopped")

    def metadata(self) -> dict[str, Any]:
        track = self.controller.current
        if track is None:
            return {}
        meta: dict[str, Any] = {
            "mpris:trackid": track_object_path(track.id),
            "mpris:length": int(max(track.duration_ms, self.controller.duration_ms)) * 1000,
            "xesam:title": track.title,
            "xesam:artist": list(track.artists) or ["Unknown"],
            "xesam:album": track.album,
            "xesam:userRating": 1.0 if track.liked else 0.0,
        }
        art = art_url(track) or (Path(self._art_path).as_uri() if self._art_path else None)
        if art:
            meta["mpris:artUrl"] = art
        return {key: value for key, value in meta.items() if value not in ("", [], None)}

    @property
    def position_us(self) -> int:
        return max(0, int(self._position_ms or self.controller.position_ms)) * 1000

    @property
    def volume(self) -> float:
        return max(0.0, min(1.0, self.controller.volume / 100.0))

    def can_go_next(self) -> bool:
        ctl = self.controller
        if ctl.mode == QueueMode.RADIO:
            return True
        return ctl.remaining > 0

    def can_go_previous(self) -> bool:
        ctl = self.controller
        if ctl.mode == QueueMode.RADIO:
            return ctl.position > 0
        return ctl.position > 0 or ctl.position_ms > 3000

    # -- controller signals -------------------------------------------------

    def _broadcast(
        self,
        interface: str,
        changed: dict[str, Any],
        invalidated: list[str] | None = None,
    ) -> None:
        if not self._available:
            return
        message = QDBusMessage.createSignal(MPRIS_OBJECT, PROPERTIES_IFACE, "PropertiesChanged")
        message << interface << changed << (invalidated or [])
        self._bus.send(message)

    def _player_changed(self, **extra: Any) -> None:
        changed = {
            "PlaybackStatus": self.playback_status(),
            "Metadata": self.metadata(),
            "CanGoNext": self.can_go_next(),
            "CanGoPrevious": self.can_go_previous(),
            "CanPause": self.controller.current is not None,
            "CanSeek": self.controller.current is not None,
        }
        changed.update(extra)
        self._broadcast(MPRIS_PLAYER_IFACE, changed)

    def _on_track(self, _track: object) -> None:
        self._position_ms = 0
        self._art_path = None
        self._player_changed()

    def _on_state(self, _state: str) -> None:
        self._broadcast(
            MPRIS_PLAYER_IFACE,
            {
                "PlaybackStatus": self.playback_status(),
                "CanGoNext": self.can_go_next(),
                "CanGoPrevious": self.can_go_previous(),
            },
        )

    def _on_position(self, position_ms: int, _duration_ms: int) -> None:
        self._position_ms = max(0, int(position_ms))

    def _on_seeked(self, position_ms: int) -> None:
        self._position_ms = max(0, int(position_ms))
        if not self._available:
            return
        self._player.emit_seeked(self._position_ms)

    def _on_like(self, track_id: str, _liked: bool) -> None:
        current = self.controller.current
        if current is None or current.id != track_id:
            return
        self._broadcast(MPRIS_PLAYER_IFACE, {"Metadata": self.metadata()})

    def _on_cover(self, track_id: str, path: str) -> None:
        current = self.controller.current
        if current is None or current.id != track_id:
            return
        self._art_path = path
        self._broadcast(MPRIS_PLAYER_IFACE, {"Metadata": self.metadata()})

    def _on_volume(self, _percent: int) -> None:
        self._broadcast(MPRIS_PLAYER_IFACE, {"Volume": self.volume})


__all__ = [
    "MPRIS_DESKTOP_ENTRY",
    "MPRIS_IDENTITY",
    "MPRIS_OBJECT",
    "MPRIS_PLAYER_IFACE",
    "MPRIS_ROOT_IFACE",
    "MPRIS_SERVICE",
    "MprisService",
    "art_url",
    "track_object_path",
]
