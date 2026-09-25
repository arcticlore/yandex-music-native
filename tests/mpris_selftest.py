"""MPRIS2 self-test: register services, query properties via the session bus.

Two stacks are exercised on a real (temporary) session bus:

* the legacy ``yamusic.integration.mpris`` still reached from ``src/main.py``;
* the new ``core.mpris`` bound to ``core.playback_controller.PlaybackController``.

Run: dbus-run-session -- python tests/mpris_selftest.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/yml-mpris-selftest/config")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/yml-mpris-selftest/cache")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtDBus import (  # noqa: E402
    QDBusConnection,
    QDBusInterface,
    QDBusMessage,
    QDBusObjectPath,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.mpris import (  # noqa: E402
    MPRIS_OBJECT as CORE_OBJECT,
    MPRIS_PLAYER_IFACE as CORE_PLAYER_IFACE,
    MPRIS_ROOT_IFACE as CORE_ROOT_IFACE,
    MPRIS_SERVICE as CORE_SERVICE,
    MprisService as CoreMprisService,
)
from yamusic.api.service import YandexApi  # noqa: E402
from yamusic.audio.engine import AudioEngine, ensure_mpv  # noqa: E402
from yamusic.cache.store import CacheStore  # noqa: E402
from yamusic.config import Settings  # noqa: E402
from yamusic.constants import (  # noqa: E402
    MPRIS_OBJECT,
    MPRIS_PLAYER_IFACE,
    MPRIS_ROOT_IFACE,
    MPRIS_SERVICE,
)
from yamusic.integration.mpris import MprisService  # noqa: E402
from yamusic.models import TrackInfo  # noqa: E402
from yamusic.services.playback import PlaybackController  # noqa: E402
from yamusic.services.rotor import RotorService  # noqa: E402

Reply = QDBusMessage.MessageType.ReplyMessage
Error = QDBusMessage.MessageType.ErrorMessage

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    if ok:
        print(f"ok: {name} {detail}".rstrip())
    else:
        FAILURES.append(f"{name} {detail}")
        print(f"FAIL: {name} {detail}")


def error_of(reply: QDBusMessage) -> str:
    return reply.errorMessage() if reply.type() == Error else ""


def replied(reply: QDBusMessage, name: str) -> None:
    check(name, reply.type() == Reply, error_of(reply))


def pump(app: QCoreApplication, rounds: int = 40) -> None:
    for _ in range(rounds):
        app.processEvents()
        QCoreApplication.sendPostedEvents()


class BusMonitor:
    """External ``dbus-monitor`` listener proving that signals reach clients.

    ``QDBusConnection.connect`` with a Python slot is broken in this PySide6
    build, so the only honest subscriber is another process.
    """

    def __init__(self, rule: str) -> None:
        self.rule = rule
        self.process: subprocess.Popen | None = None
        self.output = ""

    @property
    def listening(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def __enter__(self) -> BusMonitor:
        if shutil.which("dbus-monitor") is None:
            print(f"SKIP: dbus-monitor is missing, cannot watch {self.rule}")
            return self
        self.process = subprocess.Popen(
            ["dbus-monitor", "--session", self.rule],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        time.sleep(0.5)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.process is None:
            return
        time.sleep(0.2)
        self.process.terminate()
        try:
            self.output = self.process.communicate(timeout=5)[0] or ""
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.output = self.process.communicate()[0] or ""
        self.process = None


# -- legacy stack -------------------------------------------------------------


def run_legacy(app: QCoreApplication, bus: QDBusConnection) -> None:
    ensure_mpv()
    settings = Settings()
    api = YandexApi()
    api.start(None)
    cache = CacheStore(limit_mb=128)
    engine = AudioEngine()
    rotor = RotorService(api, settings)
    controller = PlaybackController(api, engine, rotor, cache, settings)

    mpris = MprisService(controller, on_quit=lambda: None, on_raise=lambda: None)
    if not mpris.available:
        check("legacy mpris registration", False, "service not registered")
        api.shutdown()
        engine.shutdown()
        cache.close()
        return
    check("legacy mpris registration", True, MPRIS_SERVICE)

    fake = TrackInfo(
        id="100500:100501",
        title="Test Track",
        artists=["Tester"],
        album="Album",
        duration_ms=181_000,
        cover_url=None,
        available=True,
        raw=None,
    )
    controller._queue = [fake]
    controller._index = 0

    root = QDBusInterface(MPRIS_SERVICE, MPRIS_OBJECT, MPRIS_ROOT_IFACE, bus)
    player = QDBusInterface(MPRIS_SERVICE, MPRIS_OBJECT, MPRIS_PLAYER_IFACE, bus)
    check("legacy root interface", root.isValid(), root.lastError().message())
    check("legacy player interface", player.isValid(), player.lastError().message())

    identity = root.property("Identity")
    check("legacy Identity", identity == "Yandex Music Native", repr(identity))

    desk = root.property("DesktopEntry")
    check("legacy DesktopEntry", desk == "yandex-music-native", repr(desk))

    status = player.property("PlaybackStatus")
    check("legacy PlaybackStatus", status in ("Stopped", "Paused", "Playing"), repr(status))

    meta = player.property("Metadata")
    check("legacy Metadata is dict", isinstance(meta, dict), type(meta).__name__)
    if isinstance(meta, dict):
        check("legacy metadata title", meta.get("xesam:title") == "Test Track", meta.get("xesam:title"))
        raw_tid = meta.get("mpris:trackid")
        tid = raw_tid.path() if hasattr(raw_tid, "path") else str(raw_tid)
        check("legacy metadata trackid", tid.endswith("100500_100501"), tid)
        length = meta.get("mpris:length")
        check(
            "legacy metadata length µs",
            isinstance(length, int) and length == 181_000_000,
            f"{length!r}",
        )

    position = player.property("Position")
    check("legacy Position property", isinstance(position, int), f"{position!r} {type(position).__name__}")
    check("legacy CanSeek", player.property("CanSeek") is True, repr(player.property("CanSeek")))
    check("legacy CanControl", player.property("CanControl") is True, repr(player.property("CanControl")))

    # SetPosition with int64 (> 2^31 marshals as 'x' in PySide6, as clients send)
    replied(
        player.call(
            "SetPosition",
            QDBusObjectPath("/org/mpris/MediaPlayer2/Track/100500_100501"),
            5_000_000_000,
        ),
        "legacy SetPosition",
    )
    replied(
        player.call("SetPosition", QDBusObjectPath("/org/mpris/MediaPlayer2/Track/other"), 6_000_000_000),
        "legacy SetPosition foreign track id handled",
    )
    replied(player.call("Seek", 5_000_000_000), "legacy Seek")
    replied(player.call("PlayPause"), "legacy PlayPause on empty engine")

    controller.state_changed.emit(True)
    pump(app)
    status2 = player.property("PlaybackStatus")
    check("legacy status readable after state event", status2 in ("Stopped", "Paused", "Playing"), repr(status2))
    replied(root.call("Raise"), "legacy Raise")
    replied(root.call("Quit"), "legacy Quit")

    controller.stop()
    mpris.unregister()
    pump(app, 10)
    gone = QDBusInterface(MPRIS_SERVICE, MPRIS_OBJECT, MPRIS_PLAYER_IFACE, bus)
    check("legacy name released", not gone.isValid(), gone.lastError().message())

    api.shutdown()
    engine.shutdown()
    cache.close()


# -- core stack ---------------------------------------------------------------


def run_core(app: QCoreApplication, bus: QDBusConnection) -> None:
    from test_playback_controller import Rig, wave_of

    rig = Rig(app)
    quits: list[int] = []
    raises: list[int] = []
    mpris = CoreMprisService(
        rig.controller,
        on_quit=lambda: quits.append(1),
        on_raise=lambda: raises.append(1),
    )
    if not mpris.available:
        check("core mpris registration", False, "service not registered")
        rig.close()
        return
    check("core mpris registration", True, CORE_SERVICE)

    rig.controller.play_playlist([wave_of(301, title="First"), wave_of(302, title="Second")])
    rig.settle()
    pump(app)

    root = QDBusInterface(CORE_SERVICE, CORE_OBJECT, CORE_ROOT_IFACE, bus)
    player = QDBusInterface(CORE_SERVICE, CORE_OBJECT, CORE_PLAYER_IFACE, bus)
    properties_iface = QDBusInterface(
        CORE_SERVICE, CORE_OBJECT, "org.freedesktop.DBus.Properties", bus
    )
    check("core root interface", root.isValid(), root.lastError().message())
    check("core player interface", player.isValid(), player.lastError().message())
    check("core properties interface", properties_iface.isValid(), properties_iface.lastError().message())

    check("core Identity", root.property("Identity") == "yandex_music_native", repr(root.property("Identity")))
    check(
        "core DesktopEntry",
        root.property("DesktopEntry") == "yandex-music-native",
        repr(root.property("DesktopEntry")),
    )
    check("core CanQuit", root.property("CanQuit") is True, repr(root.property("CanQuit")))
    check("core CanRaise", root.property("CanRaise") is True, repr(root.property("CanRaise")))

    status = player.property("PlaybackStatus")
    check("core PlaybackStatus", status == "Playing", repr(status))
    check("core LoopStatus", player.property("LoopStatus") == "None", repr(player.property("LoopStatus")))
    check("core Shuffle", player.property("Shuffle") is False, repr(player.property("Shuffle")))
    check("core CanGoNext", player.property("CanGoNext") is True, repr(player.property("CanGoNext")))
    check("core CanGoPrevious", player.property("CanGoPrevious") is False, repr(player.property("CanGoPrevious")))

    meta = player.property("Metadata")
    check("core Metadata is dict", isinstance(meta, dict), type(meta).__name__)
    if isinstance(meta, dict):
        check("core metadata title", meta.get("xesam:title") == "First", meta.get("xesam:title"))
        check("core metadata artists", meta.get("xesam:artist") == ["Artist"], meta.get("xesam:artist"))
        raw_tid = meta.get("mpris:trackid")
        tid = raw_tid.path() if hasattr(raw_tid, "path") else str(raw_tid)
        check("core metadata trackid", tid == f"{CORE_OBJECT}/Track/301_7", tid)
        check("core metadata length µs", meta.get("mpris:length") == 180_000_000, f"{meta.get('mpris:length')!r}")
        check("core metadata rating", meta.get("xesam:userRating") == 0.0, f"{meta.get('xesam:userRating')!r}")

    position = player.property("Position")
    check("core Position property", isinstance(position, int), f"{position!r} {type(position).__name__}")
    volume = player.property("Volume")
    check("core Volume property", isinstance(volume, float) and 0.0 <= volume <= 1.0, repr(volume))

    # QtDBus never exports org.freedesktop.DBus.Properties.Set, so the volume
    # stays read-only on the bus; the call must fail cleanly and change nothing.
    set_reply = properties_iface.call("Set", CORE_PLAYER_IFACE, "Volume", 0.5)
    pump(app, 10)
    check("core Properties.Set rejected", set_reply.type() == Error, error_of(set_reply))
    check("core volume unchanged after Set", rig.controller.volume == 80, rig.controller.volume)
    check("core volume readback", player.property("Volume") == 0.8, repr(player.property("Volume")))

    monitor = BusMonitor(
        f"type='signal',sender='{CORE_SERVICE}',"
        f"path='{CORE_OBJECT}',interface='{CORE_PLAYER_IFACE}',member='Seeked'"
    )
    with monitor:
        check("core Seeked monitor attached", monitor.listening, monitor.listening)
        # MPRIS carries microseconds as int64, so a value below 2^31 is
        # marshalled with signature 'i' and rejected by the spec-correct slot.
        int64_offset = 5_000_000_000
        replied(player.call("Seek", int64_offset), "core Seek int64 offset")
        pump(app, 20)
        check(
            "core position after seek",
            rig.controller.position_ms >= 5_000,
            rig.controller.position_ms,
        )
    if monitor.listening or monitor.output:
        check("core Seeked emitted on the bus", "Seeked" in monitor.output, monitor.output[-200:])
    else:
        print("SKIP: Seeked signal check (dbus-monitor unavailable)")

    int64_position = 2**31 + 7_000_000
    replied(
        player.call("SetPosition", QDBusObjectPath(f"{CORE_OBJECT}/Track/301_7"), int64_position),
        "core SetPosition",
    )
    replied(
        player.call("SetPosition", QDBusObjectPath(f"{CORE_OBJECT}/Track/other"), int64_position + 1_000_000),
        "core SetPosition foreign track ignored",
    )
    pump(app, 10)
    check(
        "core SetPosition applied",
        rig.controller.position_ms == int64_position // 1000,
        rig.controller.position_ms,
    )

    replied(player.call("Pause"), "core Pause")
    pump(app, 10)
    check("core paused via bus", rig.controller.state == "paused", rig.controller.state)
    check("core Paused broadcast", player.property("PlaybackStatus") == "Paused", repr(player.property("PlaybackStatus")))
    replied(player.call("Play"), "core Play")
    pump(app, 10)
    check("core playing via bus", rig.controller.state == "playing", rig.controller.state)

    monitor = BusMonitor(
        f"type='signal',sender='{CORE_SERVICE}',path='{CORE_OBJECT}',"
        "interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'"
    )
    with monitor:
        check("core PropertiesChanged monitor attached", monitor.listening, monitor.listening)
        replied(player.call("Next"), "core Next")
        rig.settle()
        pump(app, 20)
        check("core next track", rig.controller.current.id == "302:7", rig.controller.current.id)
        check("core metadata updated", player.property("Metadata").get("xesam:title") == "Second", "")
    if monitor.listening or monitor.output:
        check("core PropertiesChanged on the bus", "PropertiesChanged" in monitor.output, monitor.output[-300:])
        check("core Metadata broadcast", "Metadata" in monitor.output, "")
    else:
        print("SKIP: PropertiesChanged check (dbus-monitor unavailable)")
    check("core CanGoNext after last", player.property("CanGoNext") is False, repr(player.property("CanGoNext")))

    replied(player.call("Previous"), "core Previous")
    rig.settle()
    pump(app, 20)
    check("core previous track", rig.controller.current.id == "301:7", rig.controller.current.id)

    replied(player.call("OpenUri", "https://example.invalid/track.mp3"), "core OpenUri handled")
    replied(player.call("PlayPause"), "core PlayPause")
    pump(app, 10)
    replied(player.call("PlayPause"), "core PlayPause back")
    replied(player.call("Stop"), "core Stop")
    pump(app, 10)
    check("core stopped via bus", rig.controller.state == "stopped", rig.controller.state)
    check("core Stopped status", player.property("PlaybackStatus") == "Stopped", repr(player.property("PlaybackStatus")))

    replied(root.call("Raise"), "core Raise")
    pump(app, 10)
    check("core raise callback", raises == [1], raises)
    replied(root.call("Quit"), "core Quit")
    pump(app, 10)
    check("core quit callback", quits == [1], quits)

    mpris.unregister()
    pump(app, 10)
    gone = QDBusInterface(CORE_SERVICE, CORE_OBJECT, CORE_PLAYER_IFACE, bus)
    check("core name released", not gone.isValid(), gone.lastError().message())

    rig.close()
    pump(app, 10)


def main() -> int:
    app = QApplication(sys.argv)
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        print("SKIP: no session bus")
        return 0

    run_legacy(app, bus)
    run_core(app, bus)

    if FAILURES:
        print(f"{len(FAILURES)} failures")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("MPRIS self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
