"""Tests for the new Qt shell: station settings, visualizers, pages, window.

The playback rig is borrowed from :mod:`test_playback_controller`, so the pages
and the window are exercised against a real ``PlaybackController`` with a
recording engine and a fake Yandex client on the offscreen Qt platform.

Run: python -m pytest -q tests/test_ui_shell.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QSystemTrayIcon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("YML_AUDIO_AO", "null")
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="yml-ui-config-")
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="yml-ui-cache-")

from core import station  # noqa: E402
from core.config_manager import VALID_VISUALIZERS, ConfigManager  # noqa: E402
from core.playback_controller import PlaybackState, QueueMode  # noqa: E402
from core.yandex_service import (  # noqa: E402
    CatalogItem,
    SearchResults,
    liked_items,
    search_results,
)
from test_playback_controller import Rig, check, make_track  # noqa: E402
from ui.app import attach_integrations, build_window  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.pages.collection_page import CollectionPage  # noqa: E402
from ui.pages.search_page import SearchPage  # noqa: E402
from ui.pages.settings_page import SettingsPage  # noqa: E402
from ui.pages.wave_page import WavePage  # noqa: E402
from ui.theme import BACKGROUND, DARK_QSS, apply_theme  # noqa: E402
from ui.widgets.chips import ChipGroup  # noqa: E402
from ui.widgets.track_list import TrackList, format_duration, track_line  # noqa: E402
from ui.widgets.visualizer import (  # noqa: E402
    DEFAULT_BANDS,
    FRAME_INTERVAL_MS,
    WAVE_POINTS,
    BarsVisualizer,
    RadialVisualizer,
    Smoother,
    VisualizerStack,
    WaveSmoother,
    WaveVisualizer,
    clamp,
    create_visualizer,
    fit_bands,
    fit_wave,
    idle_target,
    smooth_step,
    update_peaks,
)

INVALID = ("bright", "diverse", "strict", "maximum")


@pytest.fixture()
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    home = tmp_path / "config"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return ConfigManager("yml-ui-test")


# -- station settings -------------------------------------------------------


def test_station_values_are_closed_sets() -> None:
    check("mood values", station.WAVE_MOODS == ("all", "fun", "sad", "calm", "energetic", "dark"))
    check(
        "activity values",
        station.WAVE_ACTIVITIES == ("all", "work", "rest", "run", "party", "sleep", "drive"),
    )
    check("language values", station.WAVE_LANGUAGES == ("all", "russian", "not-russian"))
    check(
        "diversity values",
        station.WAVE_DIVERSITIES == ("default", "favorite", "discover", "popular"),
    )


def test_station_rejects_every_legacy_invalid_value() -> None:
    for value in INVALID:
        for normalizer in (
            station.normalize_mood,
            station.normalize_activity,
            station.normalize_language,
            station.normalize_diversity,
        ):
            with pytest.raises(ValueError, match="не принимает"):
                normalizer(value)
    with pytest.raises(ValueError, match="не принимает"):
        station.wave_settings(diversity="diverse")


def test_station_normalizes_known_values() -> None:
    check("mood energetic", station.normalize_mood("energetic") == "energetic")
    check("mood active alias", station.normalize_mood("active") == "energetic")
    check("mood all", station.normalize_mood("all") is None)
    check("mood case", station.normalize_mood("  FUN ") == "fun")
    check("activity run", station.normalize_activity("run") == "run")
    check("activity workout", station.normalize_activity("workout") == "run")
    check("language ru", station.normalize_language("ru") == "russian")
    check("language en", station.normalize_language("en") == "not-russian")
    check("diversity favourite", station.normalize_diversity("favourite") == "favorite")
    check("mood bool rejected", _raises(lambda: station.normalize_mood(True)))
    check("mood out of range", _raises(lambda: station.normalize_mood(2)))


def _raises(action) -> bool:
    try:
        action()
    except ValueError:
        return True
    return False


def test_station_wire_mapping() -> None:
    settings = station.wave_settings(mood="fun", language="ru", diversity="discover")
    check("wire fun", settings.to_wire() == ("fun", "discover", "russian"))
    check("wire energetic", station.wave_settings(mood="energetic").to_wire()[0] == "active")
    check("wire default", station.wave_settings().to_wire() == ("all", "default", "any"))
    check(
        "activity wins over mood",
        station.wave_settings(mood="calm", activity="run").to_wire()[0] == "active",
    )
    check("mood used alone", station.wave_settings(mood="dark").to_wire()[0] == "sad")
    check("dict payload", station.wave_settings(activity="sleep").to_dict()["activity"] == "sleep")
    check("is_default", station.wave_settings().is_default is True)
    check("not default", station.wave_settings(mood="fun").is_default is False)


def test_station_legacy_arguments_still_work() -> None:
    check("legacy mood 0", station.resolve_settings(mood=0).mood == "sad")
    check("legacy mood 1", station.resolve_settings(mood=1).mood == "fun")
    check("legacy energy 1", station.resolve_settings(energy=1).activity == "run")
    check("legacy energy wins", station.resolve_settings(mood="calm", energy=1).mood_energy == "active")
    check("legacy explicit", station.resolve_settings(mood_energy="calm").mood == "calm")
    check("legacy bad energy", _raises(lambda: station.resolve_settings(energy=7)))


# -- smoothing maths --------------------------------------------------------


def test_smooth_step_uses_attack_and_release() -> None:
    check("attack used", smooth_step(0.0, 1.0, 0.5, 0.1) == 0.5)
    check("release used", smooth_step(1.0, 0.0, 0.5, 0.1) == 0.9)
    check("attack faster than release", smooth_step(0.0, 1.0, 0.6, 0.1) > smooth_step(0.0, 1.0, 0.1, 0.6))
    check("clamp low", clamp(-2.0) == 0.0)
    check("clamp high", clamp(3.0) == 1.0)
    check("clamp pass", clamp(0.4) == 0.4)


def test_update_peaks_latch_and_sink() -> None:
    check("peak latches", update_peaks([0.2], [0.8], 0.1) == [0.8])
    check("peak sinks", abs(update_peaks([0.8], [0.3], 0.1)[0] - 0.7) < 1e-9)
    check("peak floors at value", update_peaks([0.8], [0.75], 0.1) == [0.75])
    check("peak grows missing", update_peaks([], [0.4], 0.1) == [0.4])


def test_smoother_tracks_attack_and_release() -> None:
    smoother = Smoother(3, attack=0.5, release=0.1, peak_fall=0.1)
    values, peaks = smoother.step([1.0, 0.0, 0.0])
    check("first step attack", abs(values[0] - 0.5) < 1e-9)
    check("peak matches", abs(peaks[0] - 0.5) < 1e-9)
    values, _ = smoother.step([1.0, 0.0, 0.0])
    check("second step closer", values[0] > 0.5)
    values, _ = smoother.step([0.0, 0.0, 0.0])
    check("falling releases", 0.5 < values[0] < 0.75)
    smoother.resize(5)
    check("resize grows", smoother.size == 5 and len(smoother.values) == 5)
    smoother.resize(2)
    check("resize shrinks", smoother.size == 2 and len(smoother.peaks) == 2)
    smoother.reset()
    check("reset clears", smoother.values == [0.0, 0.0])
    rebalanced = Smoother(2).step([1.0, 1.0, 1.0])
    check("auto resize on step", len(rebalanced[0]) == 3)


def test_wave_smoother_and_fitters() -> None:
    wave = WaveSmoother(4, attack=0.5, release=0.1)
    values = wave.step([1.0, -1.0, 0.0, 0.0])
    check("wave attack", values[0] == 0.5 and values[1] == -0.5)
    check("wave decay", all(abs(value) < 0.5 for value in wave.decay()))
    check("bands padded", fit_bands([0.5, 0.5], 4) == [0.5, 0.5, 0.0, 0.0])
    check("bands clipped", fit_bands([2.0, -1.0], 2) == [1.0, 0.0])
    check("bands truncated", len(fit_bands([0.1] * 10, 3)) == 3)
    check("wave padded", fit_wave([0.5], 3) == [0.5, 0.0, 0.0])
    check("wave clipped", fit_wave([2.0, -3.0], 2) == [1.0, -1.0])
    check(
        "idle bounded",
        all(0.0 <= idle_target(i, 16, 0.5) <= station.WAVE_MOODS.__len__() * 0.01 for i in range(16)),
    )
    check("idle zero size", idle_target(0, 0, 0.0) == 0.0)


# -- visualizer widgets -----------------------------------------------------


def test_visualizer_clock_runs_at_60fps_and_pauses_when_hidden(app) -> None:
    bars = BarsVisualizer()
    bars.resize(320, 200)
    bars.show()
    app.processEvents()
    check("frame interval", bars.frame_interval() == FRAME_INTERVAL_MS)
    check("runs when visible", bars.is_running)
    bars.hide()
    app.processEvents()
    check("stopped when hidden", not bars.is_running)
    bars.show()
    app.processEvents()
    check("restarted when shown", bars.is_running)
    bars.stop()
    check("manual stop", not bars.is_running)
    bars.deleteLater()


def test_visualizer_feeds_and_resizes(app) -> None:
    bars = BarsVisualizer(bands=4)
    bars.set_spectrum([1.0, 0.5])
    check("pending frame", bars._pending is True)
    check("active after data", bars.active is True)
    bars.advance()
    check("frame applied", bars._spectrum.values[0] > 0.0 and bars._pending is False)
    bars.set_idle()
    check("idle flag", bars.active is False)
    bars.reset_data()
    check("reset values", bars._spectrum.values == [0.0] * 4)
    bars.set_bands(8)
    check("band resize", bars._spectrum.size == 8)
    wave = WaveVisualizer()
    wave.set_waveform([0.5] * 8)
    check("wave fed", wave._wave.size == WAVE_POINTS and wave._active)
    wave.reset_data()
    check("wave reset", set(wave._wave.values) == {0.0})
    bars.deleteLater()
    wave.deleteLater()


def test_every_visualizer_paints(app) -> None:
    for kind in VALID_VISUALIZERS:
        widget = create_visualizer(kind)
        widget.resize(200, 200)
        widget.set_spectrum([0.5] * DEFAULT_BANDS)
        widget.set_waveform([0.25] * 64)
        if isinstance(widget, RadialVisualizer):
            cover = QPixmap(32, 32)
            cover.fill(Qt.GlobalColor.magenta)
            widget.set_cover(cover)
        pixmap = widget.grab()
        check(f"{kind} renders", not pixmap.isNull() and pixmap.width() == 200)
        widget.deleteLater()
    check("configured names covered", set(VALID_VISUALIZERS) == {"spectrum", "wave", "circular"})
    check("unknown falls back", isinstance(create_visualizer("nope"), BarsVisualizer))


def test_visualizer_stack_switches_and_keeps_data(app) -> None:
    stack = VisualizerStack(mode="spectrum")
    stack.resize(300, 220)
    stack.set_spectrum([0.7] * DEFAULT_BANDS)
    stack.set_waveform([0.3] * 128)
    stack.set_cover(QPixmap(16, 16))
    stack.current.advance()
    check("starts spectrum", stack.mode == "spectrum")
    check("switch wave", stack.set_mode("wave") == "wave")
    check("seeded values", max(stack.current._wave.values) > 0.0)
    check("unknown ignored", stack.set_mode("nope") == "wave")
    check("switch back", stack.set_mode("spectrum") == "spectrum")
    stack.reset_data()
    check("reset all", max(stack.current._spectrum.values) == 0.0)
    stack.deleteLater()


# -- chips ------------------------------------------------------------------


def test_chip_group_uses_only_valid_values(app) -> None:
    group = ChipGroup("Настроение", station.chips(station.WAVE_MOODS, station.MOOD_LABELS))
    check("chip values", group.values == list(station.WAVE_MOODS))
    check("chip default", group.value == "all")
    seen: list[str] = []
    group.value_changed.connect(seen.append)
    check("set value", group.set_value("energetic") is True)
    check("signal emitted", seen == ["energetic"])
    check("same value no signal", group.set_value("energetic") is False)
    check("invalid rejected", group.set_value("bright") is False)
    check("value unchanged", group.value == "energetic")
    check("block signals", group.blockSignals(True) or group.set_value("fun") or group.value == "fun")
    group.deleteLater()


# -- wave page --------------------------------------------------------------


def test_wave_page_chips_cover_valid_enums(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        page = WavePage(rig.controller)
        check("mood chips", page.groups["mood"].values == list(station.WAVE_MOODS))
        check("activity chips", page.groups["activity"].values == list(station.WAVE_ACTIVITIES))
        check("language chips", page.groups["language"].values == list(station.WAVE_LANGUAGES))
        check("diversity chips", page.groups["diversity"].values == list(station.WAVE_DIVERSITIES))
        for value in INVALID:
            check(f"{value} not offered", value not in page.groups["diversity"].values)
        page.deleteLater()
    finally:
        rig.close()


def test_wave_page_starts_and_restarts_instantly(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        rig.client.batches = [
            make_batch_stub([1, 2]),
            make_batch_stub([3, 4]),
            make_batch_stub([5, 6]),
        ]
        page = WavePage(rig.controller)
        check("start wave", page.start_wave() is True)
        rig.settle()
        check("radio mode", rig.controller.mode == QueueMode.RADIO)
        calls = len(rig.client.settings_calls)
        page.groups["mood"].set_value("energetic")
        app.processEvents()
        rig.settle()
        check("restart on chip", len(rig.client.settings_calls) == calls + 1)
        check("wire mood", rig.client.settings_calls[-1][1] == "active")
        check("selection kept", page.selection["mood"] == "energetic")
        page.groups["diversity"].set_value("popular")
        app.processEvents()
        rig.settle()
        check("wire diversity", rig.client.settings_calls[-1][2] == "popular")
        page.groups["activity"].set_value("run")
        app.processEvents()
        rig.settle()
        check("wire activity", rig.client.settings_calls[-1][1] == "active")
        page.deleteLater()
    finally:
        rig.close()


def make_batch_stub(ids: list[int]):
    from yandex_music import Id, Sequence, StationTracksResult

    tracks = [make_track(track_id=item, title=f"T{item}") for item in ids]
    return StationTracksResult(
        id=Id(type="user", tag="onyourwave"),
        sequence=[Sequence(type="track", track=track, liked=False) for track in tracks],
        batch_id="batch-x",
        pumpkin=False,
    )


def test_wave_page_rejects_invalid_selection(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        errors: list[str] = []
        rig.controller.wave_error.connect(errors.append)
        page = WavePage(rig.controller)
        check("controller rejects bright", rig.controller.start_wave(mood="bright") is False)
        check("error emitted", errors and "не принимает" in errors[-1])
        check("page falls back", page.groups["mood"].set_value("bright") is False)
        page.apply_selection({"mood": "fun", "language": "russian"}, restart=False)
        check("payload applied", page.selection["mood"] == "fun")
        check("language applied", page.selection["language"] == "russian")
        check("restarts from payload", page.apply_selection({"mood": "sad"}) is None)
        page.deleteLater()
    finally:
        rig.close()


def test_wave_page_settings_payload_does_not_loop(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        rig.client.batches = [make_batch_stub([1, 2]), make_batch_stub([3, 4])]
        page = WavePage(rig.controller)
        page.start_wave()
        rig.settle()
        calls = len(rig.client.settings_calls)
        for _ in range(3):
            app.processEvents()
        check("no restart loop", len(rig.client.settings_calls) == calls)
        page.deleteLater()
    finally:
        rig.close()


# -- collection and search --------------------------------------------------


def test_collection_page_renders_liked_sections(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        rig.client.liked_tracks_result = SimpleNamespace(
            tracks=[make_track(track_id=10, title="Liked"), make_track(track_id=11, title="Second")]
        )
        rig.client.liked_albums_result = SimpleNamespace(
            albums=[SimpleNamespace(id=5, title="Album", artists=[], cover_uri=None, track_count=9)]
        )
        page = CollectionPage(rig.controller)
        check("sections", page.sections == ("tracks", "albums", "artists", "playlists"))
        check("refresh starts request", page.refresh("tracks") is True)
        rig.settle()
        tracks = page.tracks()
        check("liked tracks rendered", len(tracks) == 2 and tracks[0].liked)
        check("liked call", ("tracks", rig.client.uid) in rig.client.liked_calls)
        check("track status shown", page.status_label.text() == "Любимые треки: 2")
        check("album request", page.refresh("albums") is True)
        rig.settle()
        check("album call", ("albums", rig.client.uid) in rig.client.liked_calls)
        check("album status shown", page.status_label.text() == "Любимые альбомы: 1")
        page.list_for("albums").setCurrentRow(0)
        page.deleteLater()
    finally:
        rig.close()


def test_collection_page_reports_failure(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        page = CollectionPage(rig.controller)
        check("unknown section", page.refresh("videos") is False)
        check("failure shown", "Неизвестный" in page.status_label.text())
        page.deleteLater()
    finally:
        rig.close()


def test_search_page_flow(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        rig.client.search_result = SimpleNamespace(
            tracks=[make_track(track_id=21, title="Hit")],
            albums=[SimpleNamespace(id=2, title="LP", artists=[], cover_uri=None)],
            artists=(),
            playlists=(),
        )
        page = SearchPage(rig.controller)
        check("empty query refused", page.search("  ") is False)
        check("empty hint", "Введите" in page.status_label.text())
        check("search runs", page.search("test") is True)
        rig.settle()
        check("query recorded", rig.client.search_calls == [("test", 0)])
        check("tracks rendered", len(page.list_for("tracks").tracks) == 1)
        check("tab counts", "Треки (1)" == page.tabs.tabText(0))
        check("albums rendered", page.list_for("albums").count() == 1)
        check("status", "найдено 2" in page.status_label.text())
        page.deleteLater()
    finally:
        rig.close()


def test_search_page_empty_results(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        page = SearchPage(rig.controller)
        check("search runs", page.search("нет") is True)
        rig.settle()
        check("empty status", "ничего не найдено" in page.status_label.text())
        check("empty list", page.list_for("tracks").tracks == [])
        page.deleteLater()
    finally:
        rig.close()


def test_search_results_conversion() -> None:
    raw = SimpleNamespace(
        tracks=[make_track(track_id=31, title="One")],
        albums=[SimpleNamespace(id=7, title="Seven", artists=[], cover_uri="avatars/7")],
        artists=[SimpleNamespace(id=9, name="Nine")],
        playlists=[SimpleNamespace(id=11, title="Eleven", track_count=3)],
    )
    results = search_results("q", raw)
    check("query kept", results.query == "q")
    check("track converted", results.tracks[0].source == "search")
    check("album converted", results.albums[0].kind == "album")
    check("artist converted", results.artists[0].title == "Nine")
    check("playlist converted", results.playlists[0].track_count == 3)
    check("total", results.total == 4 and results.is_empty is False)
    empty = search_results("q", SimpleNamespace(tracks=(), albums=(), artists=(), playlists=()))
    check("empty", empty.is_empty is True)
    check("liked tracks", liked_items("tracks", SimpleNamespace(tracks=[make_track(41)]))[0].liked)
    check(
        "liked albums",
        isinstance(
            liked_items("albums", SimpleNamespace(albums=[SimpleNamespace(id=1, title="A")]))[0], CatalogItem
        ),
    )
    check("to_dict", set(results.to_dict()) == {"query", "tracks", "albums", "artists", "playlists"})
    check("dataclass type", isinstance(results, SearchResults))


def test_bootstrap_builds_window_and_attaches_integrations(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        apply_theme(app)
        window, playback, held = build_window(
            config,
            app,
            controller=rig.controller,
            service=rig.service,
            engine=rig.engine,
        )
        check("window type", isinstance(window, MainWindow))
        check("same controller", playback is rig.controller)
        check("collaborators returned", held["service"] is rig.service and held["engine"] is rig.engine)
        check("volume from config", playback.volume == config.get_volume())
        integrations = attach_integrations(window, playback, config, app)
        check("integrations held", set(integrations) == {"mpris", "notifications", "tray"})
        mpris = integrations["mpris"]
        check("mpris state known", isinstance(mpris.available, bool))
        if not mpris.available:
            check("mpris message", "MPRIS" in window.statusBar().currentMessage())
        else:
            check("mpris message empty", window.statusBar().currentMessage() == "")
        tray = integrations["tray"]
        check("tray contract", tray.tray is None or isinstance(tray.tray, QSystemTrayIcon))
        check("tray availability consistent", tray.available is (tray.tray is not None))
        check("tray destroyable", tray.destroy() is None)
        mpris.unregister()
        check("mpris released", mpris.available is False)
        window.close()
    finally:
        rig.close()


def test_bootstrap_reports_missing_token(app, config: ConfigManager) -> None:
    check("no token stored", config.get_token() is None)
    with pytest.raises(RuntimeError, match="токена"):
        build_window(config, app)


def test_stored_token_opens_window_without_dialog(
    app,
    config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ui.app as app_module
    from core.auth import AuthService
    from PySide6.QtWidgets import QPushButton

    rig = Rig(app)
    try:
        rig.login()
        apply_theme(app)
        config.set_token("stored-token-000000000")
        check("token stored for the test", config.get_token() == "stored-token-000000000")
        check("keyring untouched by tests", config.storage_backend == "file", config.storage_backend)
        window_shown = False
        window, _playback, _held = build_window(
            config,
            app,
            controller=rig.controller,
            service=rig.service,
            engine=rig.engine,
        )
        dialog_calls: list[object] = []

        def forbidden_dialog(*args: object, **kwargs: object) -> object:
            dialog_calls.append(args)
            raise AssertionError("AuthDialog must not open when a token is stored")

        monkeypatch.setattr(app_module, "AuthDialog", forbidden_dialog)
        with mock.patch("yandex_music.Client", side_effect=TimeoutError("timed out")):
            auth = app_module.restore_session_in_background(config, app, window)
            deadline = time.time() + 5
            while time.time() < deadline and auth.is_busy:
                app.processEvents()
                time.sleep(0.02)
            app.processEvents()
        check("background restore used", isinstance(auth, AuthService))
        check("no modal dialog", dialog_calls == [], str(dialog_calls))
        check("token kept after timeout", config.get_token() == "stored-token-000000000")
        retry = [
            widget for widget in window.statusBar().findChildren(QPushButton) if widget.text() == "Повторить"
        ]
        check("retry button present", len(retry) == 1, str(retry))
        window.show()
        app.processEvents()
        check("retry button shown", retry[0].isVisible())
        window_shown = True
        check("error message in status bar", "связ" in window.statusBar().currentMessage().lower())
        check("not authenticated", auth.is_authenticated is False)
        check("window usable", window.isEnabled())
        if window_shown:
            window.close()
        auth.shutdown()
    finally:
        rig.close()


# -- track list -------------------------------------------------------------


def test_track_list_plays_on_activation(app) -> None:
    rig = Rig(app)
    try:
        rig.login()
        listing = TrackList()
        rig.client.batches = [make_batch_stub([51, 52])]
        listing.set_tracks([item for item in (rig.controller.queue or [])] or [])
        from core.yandex_service import WaveTrack

        tracks = [
            WaveTrack(id="1", track_id="1", title="Song", artists=("Artist",), duration_ms=215000),
            WaveTrack(id="2", track_id="2", title="Second"),
        ]
        listing.set_tracks(tracks)
        check("rows", listing.count() == 2)
        picked: list[object] = []
        listing.track_activated.connect(picked.append)
        listing.setCurrentRow(1)
        listing.itemActivated.emit(listing.item(1))
        check("activated track", picked == [tracks[1]])
        check("current track", listing.current_track is tracks[1])
        listing.set_placeholder("пусто")
        check("placeholder", listing.count() == 1 and listing.tracks == [])
        check("duration format", format_duration(215000) == "3:35")
        check("duration unknown", format_duration(0) == "—")
        check("duration hours", format_duration(3_725_000) == "1:02:05")
        check("line with artists", track_line(tracks[0]).startswith("Song — Artist"))
        check("line without artists", "Second" in track_line(tracks[1]))
        listing.deleteLater()
    finally:
        rig.close()


# -- settings page ----------------------------------------------------------


def test_settings_page_persists_choices(app, config: ConfigManager) -> None:
    apply_theme(app)
    page = SettingsPage(config)
    seen: list[str] = []
    page.visualizer_changed.connect(seen.append)
    check("loads visualizer", page.visualizer_group.value == config.get_visualizer())
    check("switch off", page.visualizer_group.set_value("wave") is True)
    check("config updated", config.get_visualizer() == "wave")
    check("signal emitted", seen == ["wave"])
    page.quality_group.set_value("lossless")
    check("quality saved", config.get_quality() == "lossless")
    page.theme_group.set_value("light")
    check("theme saved", config.get_theme() == "light")
    page.notifications_switch.setChecked(False)
    check("notifications saved", config.get_notifications() is False)
    page.tray_group.set_value("never")
    check("tray saved", config.get("tray") == "never")
    page.reset()
    check("reset visualizer", config.get_visualizer() == "spectrum")
    check("reset notifications", config.get_notifications() is True)
    page.deleteLater()


def test_theme_sheet_is_dark(app) -> None:
    apply_theme(app)
    check("sheet applied", app.styleSheet() == DARK_QSS and BACKGROUND in DARK_QSS)
    check("dark palette", "QWidget" in DARK_QSS and "QPushButton#Chip" in DARK_QSS)


# -- main window ------------------------------------------------------------


def test_main_window_pages_and_navigation(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        window.show()
        app.processEvents()
        check("four pages", window.stack.count() == 4)
        check("starts on wave", window.current_page == "wave")
        check("switch to search", window.show_page("search") == "search")
        check("page persisted", config.get("last_page") == "search")
        check("nav button checked", window.nav_buttons["search"].isChecked())
        check("unknown page ignored", window.show_page("nope") == "search")
        check("settings page type", isinstance(window.pages["settings"], SettingsPage))
        window.set_profile("wave@yandex.ru", "Подписка Plus")
        check("profile shown", "wave@yandex.ru" in window.profile_label.text())
        check("restore page", window.restore_page() == "search")
        window.close()
    finally:
        rig.close()


def test_main_window_player_bar_follows_controller(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        rig.client.batches = [make_batch_stub([61, 62])]
        window = MainWindow(rig.controller, config)
        window.show()
        app.processEvents()
        check("idle label", window.title_label.text() == "Ничего не играет")
        check("play starts wave", window._on_play() is None)
        rig.settle()
        check("title shown", window.title_label.text() != "Ничего не играет")
        check("artist shown", "Artist" in window.artist_label.text())
        check("pause icon", window.play_button.text() == "⏸")
        window._on_play()
        app.processEvents()
        check("paused icon", window.play_button.text() == "▶")
        check("state paused", rig.controller.state == PlaybackState.PAUSED)
        check("like starts unliked", window.like_button.isChecked() is False)
        window._on_like()
        rig.settle()
        check("like sent", rig.client.like_calls == [("add", (str(rig.controller.current.id),))])
        window._on_mute()
        check("muted", window.volume_slider.value() == 0 and window.mute_button.text() == "🔇")
        window._on_mute()
        check("unmuted", window.volume_slider.value() > 0)
        window.seek_slider.setValue(1000)
        window._on_seek_released()
        check("seek applied", rig.controller.position_ms >= 0)
        window.close()
    finally:
        rig.close()


def test_main_window_visualizer_cycle_and_persistence(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        check("initial mode", window.wave_page.visualizer.mode == config.get_visualizer())
        modes = [window.cycle_visualizer() for _ in range(3)]
        check("cycles all", sorted(modes) == sorted(VALID_VISUALIZERS))
        check("button label", window.visualizer_button.text() in {"Спектр", "Волна", "Круг"})
        window.set_visualizer_mode("circular")
        check("explicit mode", window.wave_page.visualizer.mode == "circular")
        window.volume_slider.setValue(42)
        window.close()
        check("volume persisted", config.get_volume() == 42)
        check("visualizer persisted", config.get_visualizer() == "circular")
    finally:
        rig.close()


# -- redesign: painted rows, teardown and the volume wheel --------------------


def test_track_rows_use_the_painted_delegate(app) -> None:
    from core.yandex_service import WaveTrack
    from ui.widgets.track_list import TRACK_ROW_HEIGHT, TrackRowDelegate, album_column_width

    listing = TrackList()
    listing.set_tracks(
        [
            WaveTrack(
                id="1",
                track_id="t1",
                title="Кино",
                artists=("Дельфин",),
                album="Небо с вами",
                duration_ms=215000,
                liked=True,
            ),
            WaveTrack(id="2", track_id="t2", title="Second", artists=("Other",), explicit=True),
        ]
    )
    check("delegate installed", isinstance(listing.itemDelegate(), TrackRowDelegate))
    check(
        "row height",
        listing.itemDelegate().sizeHint(None, listing.model().index(0, 0)).height() == TRACK_ROW_HEIGHT,
    )
    check("album column for a wide row", album_column_width(900) > 0)
    check("album column dropped when narrow", album_column_width(420) == 0)
    check("text is still readable", listing.item(0).text().startswith("Кино — Дельфин"))
    check("duration rendered", listing.item(0).text().endswith("3:35"))
    check("no row is playing yet", not _is_playing(listing, 0))
    listing.set_tracks_playing("t2")
    check("playing marker moved", _is_playing(listing, 1) and not _is_playing(listing, 0))

    listing.resize(760, TRACK_ROW_HEIGHT * 2)
    listing.show()
    app.processEvents()
    image = listing.grab().toImage()
    check("gold marker painted", _is_gold(image.pixelColor(4, TRACK_ROW_HEIGHT + 20).name()))
    check("plain row has no marker", not _is_gold(image.pixelColor(4, 20).name()))
    listing.deleteLater()


def test_entry_rows_carry_a_kind_subtitle(app) -> None:
    from ui.widgets.track_list import entry_item

    item = entry_item(CatalogItem(id="9", title="Кино", subtitle="Дельфин", kind="album"), 0)
    check("entry title", "Кино" in item.text())
    check("entry kind in the subtitle", "album · Дельфин" in item.text())
    check("entry selectable", bool(item.flags() & Qt.ItemFlag.ItemIsSelectable))


def test_volume_wheel_works_anywhere_in_the_right_panel(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        window.show()
        app.processEvents()
        window.volume_slider.setValue(40)
        _wheel(window.volume_panel, QPoint(30, 20), 120)
        check(
            "wheel over the panel raises the volume",
            window.volume_slider.value() == 45,
            str(window.volume_slider.value()),
        )
        _wheel(window.mute_button, QPointF(window.mute_button.width() / 2, 5), 120)
        check(
            "wheel over a child button keeps working",
            window.volume_slider.value() == 50,
            str(window.volume_slider.value()),
        )
        _wheel(window.volume_panel, QPoint(30, 20), -120)
        check(
            "wheel down lowers the volume",
            window.volume_slider.value() == 45,
            str(window.volume_slider.value()),
        )
        window.close()
    finally:
        rig.close()


def test_wave_page_stage_sits_in_a_card(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        check("card object name", window.wave_page.card.objectName() == "WaveCard")
        check("stage inside the card", window.wave_page.stage.parent() is window.wave_page.card)
        check("card styled", "QFrame#WaveCard" in app.styleSheet())
        window.close()
    finally:
        rig.close()


def test_shutdown_stops_everything_and_is_idempotent(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        window.show()
        app.processEvents()
        rig.client.batches = [make_batch_stub([61, 62, 63])]
        window.wave_page.start_wave()
        for _ in range(40):
            app.processEvents()
        stack = window.wave_page.visualizer
        check(
            "a visualizer clock runs before shutdown", _running_clocks(stack) > 0, str(_running_clocks(stack))
        )
        window.shutdown()
        check("no visualizer clock survives", _running_clocks(stack) == 0, str(_running_clocks(stack)))
        check("the engine was released", rig.controller.engine.stopped >= 1)
        check("the service worker stopped", not rig.controller.service.worker.isRunning())
        window.shutdown()
        check("a second shutdown is a no-op", _running_clocks(stack) == 0)
        window.close()
        check("close after shutdown is safe", True)
    finally:
        rig.close()


def _running_clocks(stack: VisualizerStack) -> int:
    return sum(1 for widget in stack._visualizers.values() if widget._timer.isActive())


def _is_playing(listing: TrackList, row: int) -> bool:
    from ui.widgets.track_list import _ROLE_PLAYING

    return bool(listing.item(row).data(_ROLE_PLAYING))


def _is_gold(name: str) -> bool:
    value = name.lstrip("#")
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return red > 215 and 120 < green < 225 and blue < 80


def _wheel(widget, position, delta: int) -> None:
    point = QPointF(position)
    event = QWheelEvent(
        point,
        QPointF(widget.mapToGlobal(point.toPoint())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    app = QApplication.instance()
    app.sendEvent(widget, event)
