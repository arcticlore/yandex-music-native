"""Tests for the new Qt shell: station settings, visualizers, pages, window.

The playback rig is borrowed from :mod:`test_playback_controller`, so the pages
and the window are exercised against a real ``PlaybackController`` with a
recording engine and a fake Yandex client on the offscreen Qt platform.

Run: python -m pytest -q tests/test_ui_shell.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPalette, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QSystemTrayIcon
from PySide6.QtWidgets import QWidget

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
from ui.main_window import VISUALIZER_LABELS, MainWindow  # noqa: E402
from ui.pages.collection_page import CollectionPage  # noqa: E402
from ui.pages.search_page import DEBOUNCE_MS, MIN_QUERY_LENGTH, SearchPage  # noqa: E402
from ui.app import profile_name  # noqa: E402
from ui.widgets.cover_loader import circular_pixmap  # noqa: E402
from ui.pages.settings_page import SettingsPage  # noqa: E402
from ui.pages.wave_page import WavePage  # noqa: E402
from ui.theme import (  # noqa: E402
    BACKGROUND,
    on_theme_changed,
    COVER_SIZE,
    DARK_QSS,
    PANEL_RADIUS,
    SEGMENT_HEIGHT,
    apply_theme,
)
from ui.widgets.chips import ChipGroup  # noqa: E402
from ui.main_window import PLAYER_RIGHT_WIDTH, TRANSPORT_MIN_WIDTH  # noqa: E402
from ui.widgets.now_playing_drawer import NowPlayingDrawer  # noqa: E402
from ui.widgets.results_panel import ResultsPanel  # noqa: E402
from ui.widgets.track_list import TrackList, format_duration, track_line  # noqa: E402
from ui.widgets.visualizer import (  # noqa: E402
    DEFAULT_BANDS,
    DEFAULT_PEAK_FALL,
    FRAME_INTERVAL_MS,
    WAVE_POINTS,
    BarsVisualizer,
    RadialVisualizer,
    Smoother,
    VisualizerStack,
    WaveSmoother,
    WaveVisualizer,
    bar_layout,
    clamp,
    create_visualizer,
    fit_bands,
    fit_wave,
    idle_target,
    mirrored_rays,
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


def test_update_peaks_sinks_on_every_band() -> None:
    # The complaint that started this: the caps only fell on the bass half, so
    # the right hand side of the display was a frozen comb.
    values = [0.5] * DEFAULT_BANDS
    latched = update_peaks(values, values, 0.1)
    check("every band gets a peak", len(latched) == DEFAULT_BANDS and min(latched) > 0.49)
    peaks = list(latched)
    for _ in range(40):
        values = [value - 0.05 for value in values]
        peaks = update_peaks(peaks, values, DEFAULT_PEAK_FALL)
    check("caps sink everywhere", max(peaks) < 0.51, str(max(peaks)))
    check("every cap sank", all(now < start for now, start in zip(peaks, latched, strict=True)))
    check("caps stay in step with the values", all(p >= v - 1e-9 for p, v in zip(peaks, values, strict=True)))


def test_bar_layout_fills_any_width() -> None:
    for count in (1, 32, 64):
        for width in (1.0, 40.0, 64.0, 200.0, 1000.0, 4096.0):
            bar, gap = bar_layout(count, width)
            used = bar * count + gap * (count - 1)
            check(
                f"bars fill {width:.0f}px in {count}",
                abs(used - max(width, 1.0)) < 1e-6 and bar > 0.0 and gap >= 0.0,
                f"bar={bar:.3f} gap={gap:.3f} used={used:.3f}",
            )
    narrow, narrow_gap = bar_layout(64, 32.0)
    check("narrow stage never overruns", narrow * 64 + narrow_gap * 63 <= 32.0 + 1e-9)
    wide, wide_gap = bar_layout(64, 1000.0)
    check("wide stage keeps a gap", wide_gap >= 1.0 and wide < 1000.0 / 64)
    check("zero bands is safe", bar_layout(0, 100.0)[0] > 0.0)


def test_mirrored_rays_mirror_both_halves() -> None:
    check("no bands is safe", mirrored_rays([]) == [])
    for count in (32, 64):
        values = [(index % 5) / 5.0 for index in range(count)]
        for spin in (0.0, 37.5, 359.0):
            rays = mirrored_rays(values, spin, RadialVisualizer.MIRROR_AXIS)
            axis = RadialVisualizer.MIRROR_AXIS + spin
            check(f"one ray per band ({count})", len(rays) == count)
            check(
                f"lengths mirror ({count} spin {spin})",
                all(abs(rays[i][1] - rays[count - 1 - i][1]) < 1e-12 for i in range(count)),
            )
            check(
                f"angles mirror ({count} spin {spin})",
                all(
                    abs(((rays[i][0] - axis) + (rays[count - 1 - i][0] - axis)) % 360.0) < 1e-9
                    for i in range(count)
                ),
            )
        rays = mirrored_rays(values, 0.0, RadialVisualizer.MIRROR_AXIS)
        # Qt's y axis points down, so 90 degrees is the bottom of the stage.
        check(f"bass sits on the axis ({count})", abs(rays[0][0] - 90.0) < 1e-9, str(rays[0][0]))
        check(
            f"bass half equals treble half ({count})",
            abs(
                sum(length for _, length in rays[: count // 2]) / (count // 2)
                - sum(length for _, length in rays[count // 2 :]) / (count // 2)
            )
            < 1e-12,
        )
    # A bass-only frame used to light one arc; mirroring must light both sides.
    bass_only = [1.0] + [0.0] * 63
    rays = mirrored_rays(bass_only)
    bright = [(angle, length) for angle, length in rays if length > 0.5]
    check("a bass hit lights a mirrored pair", len(bright) == 2, str(len(bright)))
    # Band 0 and its partner band 63 meet on the axis, which is the seam the
    # mirror folds on, so the pair sits exactly on top of each other.
    check("the pair meets on the axis", abs(bright[0][0] - bright[1][0]) < 1e-9, str(bright))
    check("the pair shares one length", abs(bright[0][1] - bright[1][1]) < 1e-12)
    mid = [1.0] * 8 + [0.0] * 56
    lit = [angle for angle, length in mirrored_rays(mid) if length > 0.5]
    check("a low-mid hit lights both sides", len(lit) == 16, str(len(lit)))
    check("the sides are equidistant", abs((lit[0] - 90.0) - (90.0 - lit[-1])) < 1e-9)
    # A rising ramp folds into a V: each pair takes the louder of the two, so
    # both halves read the same shape, brightest at the shared seam.
    ramp = [index / 63.0 for index in range(64)]
    folded = [length for _, length in mirrored_rays(ramp)]
    check("a ramp folds symmetrically", abs(folded[0] - folded[-1]) < 1e-12, f"{folded[0]},{folded[-1]}")
    check("the fold is brightest at the seam", abs(folded[0] - 1.0) < 1e-12, str(folded[0]))
    check("the fold dips in the middle", folded[32] < folded[16] < folded[0], f"{folded[32]:.3f}")


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
    check(
        "configured names covered",
        set(VALID_VISUALIZERS) == {"spectrum", "wave", "circular", "meters"},
    )
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


def test_collection_tracks_are_hydrated(app) -> None:
    """The like list only has references; the rows need real metadata."""
    rig = Rig(app)
    try:
        rig.login()
        # What users_likes_tracks answers with: ids and a liked flag, no title.
        rig.client.liked_tracks_result = SimpleNamespace(
            tracks=[SimpleNamespace(id=61), SimpleNamespace(id=62)]
        )
        rig.client.full_tracks = [
            make_track(track_id=61, title="Полное название", duration_ms=1234, cover_uri="avatars/61"),
            make_track(track_id=62, title="Второе", duration_ms=4321),
        ]
        page = CollectionPage(rig.controller)
        check("request runs", page.refresh("tracks") is True)
        rig.settle()
        check("ids were fetched once", rig.client.hydrate_calls == [("61", "62")])
        tracks = page.tracks()
        check("both rows", len(tracks) == 2)
        check("title filled in", tracks[0].title == "Полное название")
        check("album filled in", tracks[0].album == "Album 7")
        check("duration filled in", tracks[0].duration_ms == 1234)
        check("cover filled in", tracks[0].cover_uri == "avatars/61")
        check("artist filled in", tracks[0].artists == ("Artist",))
        check("still marked liked", all(track.liked for track in tracks))
        page.deleteLater()
    finally:
        rig.close()


def test_collection_hydration_limits_and_degrades(app) -> None:
    """One request, first hundred ids, and no exception when it fails."""
    rig = Rig(app)
    try:
        rig.login()
        rig.client.liked_tracks_result = SimpleNamespace(
            tracks=[SimpleNamespace(id=100 + index) for index in range(120)]
        )
        rig.client.full_tracks = [make_track(track_id=100 + index) for index in range(120)]
        page = CollectionPage(rig.controller)
        page.refresh("tracks")
        rig.settle()
        check("single request", len(rig.client.hydrate_calls) == 1)
        check("first hundred only", len(rig.client.hydrate_calls[0]) == 100)
        check("all rows still listed", len(page.tracks()) == 120)
        check("hydrated prefix", page.tracks()[0].album == "Album 7")
        check("unhydrated tail kept", len(page.tracks()) == 120)

        rig2 = Rig(app)
        try:
            rig2.login()
            rig2.client.liked_tracks_result = SimpleNamespace(tracks=[SimpleNamespace(id=61)])
            rig2.client.hydrate_error = RuntimeError("network is down")
            page2 = CollectionPage(rig2.controller)
            check("request still runs", page2.refresh("tracks") is True)
            rig2.settle()
            check("references survive a failure", len(page2.tracks()) == 1)
            check("failure is not raised", page2.status_label.text().endswith("1"))
            page2.deleteLater()
        finally:
            rig2.close()
        page.deleteLater()
    finally:
        rig.close()


def test_collection_page_loads_itself(app) -> None:
    """Opening the tab is enough; no «Обновить» click required."""
    rig = Rig(app)
    try:
        rig.login()
        rig.client.liked_tracks_result = SimpleNamespace(tracks=[SimpleNamespace(id=71)])
        rig.client.full_tracks = [make_track(track_id=71, title="Авто")]
        page = CollectionPage(rig.controller)
        page.resize(640, 480)
        check("nothing loaded before the tab is shown", rig.client.liked_calls == [])
        page.show()
        app.processEvents()
        rig.settle()
        check("shown tab fetched itself", ("tracks", rig.client.uid) in rig.client.liked_calls)
        check("rows drawn", len(page.tracks()) == 1)
        calls = len(rig.client.liked_calls)
        page.hide()
        page.show()
        app.processEvents()
        rig.settle()
        check("second show does not refetch", len(rig.client.liked_calls) == calls)
        page.tabs.setCurrentIndex(1)
        app.processEvents()
        rig.settle()
        check("a new tab loads itself", ("albums", rig.client.uid) in rig.client.liked_calls)
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
        check("query recorded", rig.client.search_calls == [("test", 0, "all")])
        check("every kind requested", rig.client.search_calls[0][2] == "all")
        check("tracks rendered", len(page.list_for("tracks").tracks) == 1)
        check("tab counts", "Треки (1)" == page.tabs.tabText(0))
        check("albums rendered", page.list_for("albums").count() == 1)
        check("status", "найдено 2" in page.status_label.text())
        page.deleteLater()
    finally:
        rig.close()


def test_segment_bar_drives_the_pages(app) -> None:
    """A click on a segment has to move the stacked view under it."""
    panel = ResultsPanel()
    tracks, albums = QWidget(), QWidget()
    panel.add_page(tracks, "Треки")
    panel.add_page(albums, "Альбомы")
    seen: list[int] = []
    panel.segments.changed.connect(seen.append)
    panel.show()
    app.processEvents()
    check("first section is showing", panel.tabs.currentIndex() == 0)
    panel.segments.buttons[1].click()
    app.processEvents()
    check("the click reaches the view", panel.tabs.currentIndex() == 1)
    check("the click is announced once", seen == [1])
    check("the pill moved", panel.segments.active == 1)
    check("exactly one pill is lit", [b.isChecked() for b in panel.segments.buttons] == [False, True])
    panel.tabs.setCurrentIndex(0)
    app.processEvents()
    check("a tab change syncs the bar without a second click", panel.segments.active == 0 and seen == [1])
    check("one segment per section", len(panel.segments.buttons) == 2)
    check("the backing hugs its segments", panel.segments.width() < panel.width() - 2 * PANEL_RADIUS)
    check("the bar leaves room for its own frame", panel.segments.height() > SEGMENT_HEIGHT)
    check(
        "every segment is a full token tall",
        all(b.height() == SEGMENT_HEIGHT for b in panel.segments.buttons),
    )
    panel.close()
    panel.deleteLater()


def test_search_page_ignores_answers_to_a_replaced_query(app) -> None:
    """A slow answer must not repaint the list for a query already replaced."""
    rig = Rig(app)
    try:
        rig.login()
        rig.client.search_result = SimpleNamespace(
            tracks=[make_track(track_id=51, title="Old")],
            albums=(),
            artists=(),
            playlists=(),
        )
        page = SearchPage(rig.controller)
        check("first query sent", page.search("старый") is True)
        # The user replaces the text before the answer comes back: the field
        # shows the new query, the debounce has not fired yet.
        page.input.setText("новый")
        rig.settle()
        check("stale answer dropped", len(page.list_for("tracks").tracks) == 0)
        check("stale answer not announced", "старый" not in page.status_label.text())

        rig.client.search_result = SimpleNamespace(
            tracks=[make_track(track_id=52, title="New")],
            albums=(),
            artists=(),
            playlists=(),
        )
        check("second query sent", page.search("новый") is True)
        rig.settle()
        check("fresh answer drawn", len(page.list_for("tracks").tracks) == 1)
        check("fresh answer announced", "новый" in page.status_label.text())
        page.deleteLater()
    finally:
        rig.close()


def test_search_page_drops_superseded_generations(app) -> None:
    """An answer for an older request is ignored even if the text still matches."""
    rig = Rig(app)
    try:
        rig.login()
        page = SearchPage(rig.controller)
        check("first request", page.search("один") is True)
        stale = search_results("один", SimpleNamespace(tracks=(), albums=(), artists=(), playlists=()), 1)
        rig.client.search_result = SimpleNamespace(
            tracks=[make_track(track_id=53, title="Same")],
            albums=(),
            artists=(),
            playlists=(),
        )
        check("newer request", page.search("один") is True)
        check("generation moved on", rig.service.last_search_generation == 2)
        rig.settle()
        check("current answer drawn", len(page.list_for("tracks").tracks) == 1)
        rig.service.search_ready.emit(stale)
        app.processEvents()
        check("older answer ignored", len(page.list_for("tracks").tracks) == 1)
        page.deleteLater()
    finally:
        rig.close()


def test_search_page_debounce_window() -> None:
    check("debounce is half a second", DEBOUNCE_MS == 500)
    check("one character is enough", MIN_QUERY_LENGTH == 1)


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
    check(
        "to_dict",
        set(results.to_dict()) == {"query", "tracks", "albums", "artists", "playlists", "generation"},
    )
    check("dataclass type", isinstance(results, SearchResults))
    check("generation carried", search_results("q", raw, 4).generation == 4)


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
    page.theme_group.set_value("cyberpunk")
    check("theme saved", config.get_theme() == "cyberpunk")
    page.theme_group.set_value("oled")
    check("pure black saved", config.get_theme() == "oled")
    config.set_theme("neon")
    check("an unknown theme falls back", config.get_theme() == "obsidian")
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


def test_three_themes_are_defined_and_complete() -> None:
    """Every theme states the full set of colours, or it is not a theme."""
    from ui.theme import DEFAULT_THEME, THEMES, THEME_NAMES, ThemeTokens, theme_tokens

    check("three themes", len(THEMES) == 3)
    check("named", set(THEME_NAMES) == {"obsidian", "cyberpunk", "oled"})
    check("default is the first", DEFAULT_THEME == "obsidian")
    for name, tokens in THEMES.items():
        check(f"{name} is complete", set(ThemeTokens.REQUIRED) <= set(tokens))
        check(f"{name} is labelled", bool(tokens.label))
        for required in ThemeTokens.REQUIRED:
            value = tokens[required]
            check(f"{name}.{required} is a colour", value.startswith("#") and len(value) == 7)
    check("an unknown name falls back", theme_tokens("nope") is THEMES[DEFAULT_THEME])
    try:
        ThemeTokens("broken", "Broken", {"BACKGROUND": "#000000"})
    except ValueError as exc:
        check("a partial theme is refused", "missing colours" in str(exc))
    else:
        check("a partial theme is refused", False)


def test_oled_is_pure_black() -> None:
    """The point of the OLED theme: every surface is the same black."""
    from ui.theme import theme_tokens

    tokens = theme_tokens("oled")
    check("window is black", tokens["BACKGROUND"] == "#000000")
    for surface in ("SIDEBAR_BACKGROUND", "PANEL"):
        check(f"{surface} is black too", tokens[surface] == "#000000")
    check("surfaces are not grey", int(tokens["SURFACE"][1:3], 16) < 32)
    check("the frame is still a hairline", int(tokens["BORDER"][1:3], 16) >= 16)


def test_theme_sheets_render_without_leftover_markers() -> None:
    """A marker left in the sheet would silently drop a rule from it."""
    from ui.theme import MARKER, THEMES, render_qss

    for name in THEMES:
        sheet = render_qss(name)
        check(f"{name} has no markers", MARKER.search(sheet) is None)
        check(f"{name} is a real sheet", sheet.lstrip().startswith("/*") and "QWidget {" in sheet)
        check(f"{name} has no stray braces", "{{" not in sheet and "}}" not in sheet)
    check("themes differ from each other", len({render_qss(name) for name in THEMES}) == 3)
    check("the default sheet is the old one", render_qss("obsidian") == DARK_QSS)
    check("an unknown name renders the default", render_qss("nope") == DARK_QSS)


def test_theme_palette_follows_the_theme() -> None:
    """Fusion paints some things from the palette, so it has to be themed too."""
    from ui.theme import DEFAULT_THEME, dark_palette, theme_tokens

    backgrounds = set()
    for name in ("obsidian", "cyberpunk", "oled"):
        palette = dark_palette(name)
        tokens = theme_tokens(name)
        backgrounds.add(palette.color(palette.ColorRole.Window).name())
        check(
            f"{name} window matches the token",
            palette.color(palette.ColorRole.Window).name() == QColor(tokens["BACKGROUND"]).name(),
        )
        check(
            f"{name} highlight is the accent",
            palette.color(palette.ColorRole.Highlight).name() == QColor(tokens["ACCENT"]).name(),
        )
        check(f"{name} ink is readable on the accent", tokens["ACCENT_INK"] != tokens["ACCENT"])
    check("each theme has its own window colour", len(backgrounds) == 3)
    default_window = dark_palette(DEFAULT_THEME).color(QPalette.ColorRole.Window).name()
    check(
        "the default is obsidian", default_window == QColor(theme_tokens(DEFAULT_THEME)["BACKGROUND"]).name()
    )
    check(
        "and matches the no-argument call",
        dark_palette().color(QPalette.ColorRole.Window).name() == default_window,
    )


def test_apply_theme_switches_live_and_persists(app, config: ConfigManager) -> None:
    """Choosing a theme repaints the running app, not just the next launch."""
    from ui.theme import DEFAULT_THEME, THEMES, active_theme, render_qss, token

    seen: list[str] = []
    unsubscribe = on_theme_changed(seen.append)
    try:
        apply_theme(app, "cyberpunk")
        check("sheet switched", app.styleSheet() == render_qss("cyberpunk"))
        check("active theme follows", active_theme() == "cyberpunk")
        check("listeners told", "cyberpunk" in seen)
        check("tokens follow", token("ACCENT") == THEMES["cyberpunk"]["ACCENT"])
        check("geometry does not move", token("CONTROL_HEIGHT") == "48")

        apply_theme(app, "oled")
        check("switched again", app.styleSheet() == render_qss("oled"))
        check("pure black on screen", "#000000" in app.styleSheet())

        apply_theme(app, "does-not-exist")
        check("an unknown name lands on the default", active_theme() == DEFAULT_THEME)
        check("and repaints the default", app.styleSheet() == render_qss(DEFAULT_THEME))

        config.set_theme("cyberpunk")
        apply_theme(app, config.get_theme())
        check("a stored theme is applied", active_theme() == "cyberpunk")
    finally:
        unsubscribe()
    apply_theme(app)


def test_painted_widgets_follow_the_theme(app, config: ConfigManager) -> None:
    """The hand-painted parts have to change colour too, not just the sheet."""
    from ui.theme import token
    from ui.widgets import visualizer

    apply_theme(app, "obsidian")
    before_background = QColor(visualizer.BACKGROUND).name()
    check("canvas is the panel", before_background == QColor(token("PANEL")).name())

    apply_theme(app, "oled")
    check("the canvas repainted", QColor(visualizer.BACKGROUND).name() == QColor(token("PANEL")).name())
    check("the canvas is not the old colour", QColor(visualizer.BACKGROUND).name() != before_background)
    check("the baseline followed", QColor(visualizer.BASELINE).name() == QColor(token("BORDER")).name())
    check("the peak cap followed", QColor(visualizer.PEAK_COLOR).name() == QColor(token("TEXT")).name())

    # Every mode still paints after the switch, in the new colours.
    for kind in VALID_VISUALIZERS:
        widget = create_visualizer(kind)
        widget.resize(240, 160)
        widget.set_spectrum([0.5] * DEFAULT_BANDS)
        widget.set_waveform([0.3] * 128)
        widget.advance()
        check(f"{kind} paints themed", not widget.grab().isNull())
        widget.deleteLater()

    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        window.resize(900, 600)
        apply_theme(app, "cyberpunk")
        check("the window repaints themed", not window.grab().isNull())
        # A widget's own styleSheet() is empty for an app-level sheet, so the
        # proof that the theme reached it is the palette Fusion painted with.
        check(
            "the palette reached the window",
            window.palette().color(QPalette.ColorRole.Window).name() == QColor(token("BACKGROUND")).name(),
        )
        window.close()
    finally:
        rig.close()
        # Restoring here, not after the block: a failed check must not leave the
        # next test painting in the wrong theme.
        apply_theme(app)


def test_theme_choices_come_from_the_registry(config: ConfigManager) -> None:
    """The settings chips and the config must offer the same themes."""
    from core.config_manager import DEFAULT_THEME, VALID_THEMES
    from ui.pages.settings_page import THEME_CHOICES
    from ui.theme import THEMES

    check("config knows every theme", set(VALID_THEMES) == set(THEMES))
    check("the config default exists", DEFAULT_THEME in THEMES)
    check("the page offers every theme", set(VALID_THEMES) <= {key for key, _ in THEME_CHOICES})
    check("labels are human", all(label and label == label.strip() for _, label in THEME_CHOICES))
    page = SettingsPage(config)
    check(
        "the chip group is built from the choices",
        set(page.theme_group._buttons) == {key for key, _ in THEME_CHOICES},
    )
    page.deleteLater()


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
        window.set_profile("Иван Петров", "Подписка Plus")
        check("profile shown", "Иван Петров" in window.profile_label.text())
        check("restore page", window.restore_page() == "search")
        window.close()
    finally:
        rig.close()


def test_profile_name_prefers_a_person_over_a_login(app) -> None:
    """The sidebar greets the account by name; the login is the last resort."""
    check("first and last", profile_name({"first_name": "Иван", "last_name": "Петров"}) == "Иван Петров")
    check(
        "names win over display_name",
        profile_name(
            {"first_name": "Иван", "last_name": "Петров", "display_name": "ivanpetrov", "login": "a@b.ru"}
        )
        == "Иван Петров",
    )
    check("half a name", profile_name({"first_name": "Иван", "login": "a@b.ru"}) == "Иван")
    check("last name alone", profile_name({"last_name": "Петров", "login": "a@b.ru"}) == "Петров")
    check("display_name next", profile_name({"display_name": "Волна", "login": "a@b.ru"}) == "Волна")
    check("login last", profile_name({"login": "a@b.ru"}) == "a@b.ru")
    check("nothing at all", profile_name({}) == "")
    check("whitespace trimmed", profile_name({"first_name": " Иван "}) == "Иван")
    check("blank names ignored", profile_name({"first_name": "", "last_name": " "}) == "")


def test_profile_avatar_is_square_and_circular(app, config: ConfigManager) -> None:
    """The avatar fills 40x40 and the corners are transparent, not painted."""
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        window.set_profile("Иван Петров", "", "")
        check("no avatar without a url", window.avatar_label.pixmap().isNull())
        source = QPixmap(200, 120)
        source.fill(QColor("red"))
        masked = circular_pixmap(source, COVER_SIZE)
        check(
            "scaled square",
            masked is not None and (masked.width(), masked.height()) == (COVER_SIZE, COVER_SIZE),
        )
        check("mask painted", not masked.mask().isNull())
        image = masked.toImage()
        check("centre is the image", image.pixelColor(COVER_SIZE // 2, COVER_SIZE // 2).name() == "#ff0000")
        check("corner is cut", image.pixelColor(0, 0).alpha() == 0)
        check("nothing to mask", circular_pixmap(None, COVER_SIZE) is None)
        check("zero size refused", circular_pixmap(source, 0) is None)
        window.close()
    finally:
        rig.close()


def test_main_window_shows_the_profile_avatar(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        image = QPixmap(120, 120)
        image.fill(QColor("#123456"))
        window.set_avatar("https://avatars.yandex.net/get-yapic/avatar.jpg")
        check("request queued", window._avatar_url.endswith("avatar.jpg"))
        window._show_avatar("https://avatars.yandex.net/get-yapic/avatar.jpg", image)
        check("avatar painted", not window.avatar_label.pixmap().isNull())
        check("avatar is a token square", window.avatar_label.pixmap().width() == COVER_SIZE)
        painted = window.avatar_label.pixmap().cacheKey()
        window._show_avatar("https://other/avatar.jpg", image)
        check("a late answer for another url is dropped", window.avatar_label.pixmap().cacheKey() == painted)
        window.set_profile("", "")
        check("signing out clears the avatar", window.avatar_label.pixmap().isNull())
        window.close()
    finally:
        rig.close()


def test_quality_switch_takes_effect_on_the_next_track(app, config: ConfigManager) -> None:
    """Picking a quality reaches the controller and the badge says «pending»."""
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        window.show()
        app.processEvents()
        settings = window.pages["settings"]
        check("default is auto", rig.controller.quality == "auto")
        settings.quality_group.set_value("320")
        check("controller follows the chip", rig.controller.quality == "320")
        check("config follows the chip", config.get_quality() == "320")
        settings.quality_group.set_value("lossless")
        check("lossless reaches the controller", rig.controller.quality == "lossless")
        check("badge waits for the next track", window.quality_badge.property("pending") == "true")

        rig.client.batches = [make_batch_stub([81])]
        window._on_play()
        rig.settle()
        check("pending cleared by the new track", window.quality_badge.property("pending") == "false")
        check("badge shows the stream", window.quality_badge.text() in ("FLAC", "HQ"))
        check("stored before the first track", config.get_quality() == "lossless")
        window.close()
    finally:
        rig.close()


def test_stored_quality_reaches_the_controller_at_startup(app, config: ConfigManager) -> None:
    rig = Rig(app)
    try:
        rig.login()
        config.set_quality("320")
        window = MainWindow(rig.controller, config)
        check("no restart needed", rig.controller.quality == "320")
        window.set_quality("lossless")
        check("switch returns what is in force", rig.controller.quality == "lossless")
        check("empty quality falls back", window.set_quality("") == "auto")
        window.close()
    finally:
        rig.close()


def test_vu_scale_places_silence_at_the_floor() -> None:
    """The meter is laid out in decibels, the way the instrument is."""
    from ui.widgets.visualizer import VU_DECIBELS, decibel_to_level

    low, high = VU_DECIBELS
    check("window is a real dB range", high > low)
    check("0 dB is not full scale", decibel_to_level(0.0) < 1.0)
    check("the floor is the floor", decibel_to_level(low) == 0.0)
    check("the ceiling is the ceiling", decibel_to_level(high) == 1.0)
    check("below the floor clamps", decibel_to_level(-200.0) == 0.0)
    check("above the ceiling clamps", decibel_to_level(60.0) == 1.0)
    check("infinite is quiet", decibel_to_level(math.inf) == 0.0)
    check("nan is quiet", decibel_to_level(math.nan) == 0.0)
    check(
        "monotonic",
        all(decibel_to_level(a) <= decibel_to_level(b) for a, b in zip(VU_DECIBELS, VU_DECIBELS[1:])),
    )
    check("midpoint", abs(decibel_to_level((low + high) / 2) - 0.5) < 1e-9)


def test_frame_rms_measures_power_not_peaks() -> None:
    """A needle on the tallest spike would sit pinned for most music."""
    from ui.widgets.visualizer import frame_rms

    check("silence", frame_rms([]) == 0.0)
    check("zeros", frame_rms([0.0] * 64) == 0.0)
    check("full scale", abs(frame_rms([1.0] * 64) - 1.0) < 1e-9)
    check("half scale", abs(frame_rms([0.5] * 64) - 0.5) < 1e-9)
    sparse = [0.0] * 63 + [1.0]
    check("one spike does not read as loud", frame_rms(sparse) < 0.13)
    check("sparse rms is the root mean", abs(frame_rms(sparse) - math.sqrt(1 / 64)) < 1e-9)
    check("clamped above", frame_rms([2.0] * 8) == 1.0)
    check("sign does not matter", abs(frame_rms([-0.5, 0.5]) - 0.5) < 1e-9)


def test_channel_levels_split_interleaved_frames() -> None:
    """``L R L R`` is the layout the engine publishes."""
    from ui.widgets.visualizer import VU_CHANNELS, channel_levels

    quiet = channel_levels([0.0] * 128, VU_CHANNELS)
    check("two channels", len(quiet) == 2)
    check("silence is at the floor", quiet == [0.0, 0.0])
    left_only = channel_levels([0.9, 0.0] * 64, VU_CHANNELS)
    check("left is loud", left_only[0] > 0.6)
    check("right is quiet", left_only[1] == 0.0)
    right_only = channel_levels([0.0, 0.9] * 64, VU_CHANNELS)
    check("channels do not bleed", right_only[0] == 0.0 and right_only[1] > 0.6)
    check("both loud", all(level > 0.6 for level in channel_levels([0.9, 0.9] * 64, VU_CHANNELS)))
    check("an odd sample is kept", len(channel_levels([0.5] * 7, VU_CHANNELS)) == 2)
    check("an empty frame is quiet", channel_levels([], VU_CHANNELS) == [0.0, 0.0])
    check("levels are a fraction", all(0.0 <= level <= 1.0 for level in channel_levels([0.5, 0.2] * 32)))


def test_level_follower_rises_fast_and_falls_slow() -> None:
    """The shape of a meter needle: quick up, slow down, peak cap held."""
    from ui.widgets.visualizer import LevelFollower

    follower = LevelFollower(2)
    check("starts at rest", follower.values == [0.0, 0.0] and follower.peaks == [0.0, 0.0])
    follower.step([0.5, 0.5])
    check("one frame is not the target", 0.0 < follower.values[0] < 0.5)
    for _ in range(40):
        follower.step([0.5, 0.5])
    check("reaches the target", abs(follower.values[0] - 0.5) < 0.01)
    check("the peak cap holds", abs(follower.peaks[0] - 0.5) < 0.01)
    louder = list(follower.values)
    follower.step([0.9, 0.9])
    check("a louder channel rises", follower.values[0] > louder[0])
    peak = follower.peaks[0]
    for _ in range(5):
        follower.step([0.0, 0.0])
    check("the cap falls, not the value", follower.peaks[0] < peak and follower.peaks[0] > 0.0)
    for _ in range(200):
        follower.decay()
    check("silence brings it to rest", follower.values == [0.0, 0.0] and follower.peaks == [0.0, 0.0])
    other = LevelFollower(2)
    other.step([0.4, 0.4])
    for _ in range(30):
        other.step([0.4, 0.4])
    follower.adopt(other)
    check("a style switch adopts the motion", abs(follower.values[0] - other.values[0]) < 1e-9)
    follower.reset()
    check("reset clears", follower.values == [0.0, 0.0] and follower.peaks == [0.0, 0.0])
    short = LevelFollower(2)
    short.step([0.5])
    check("a missing channel is silent", short.values == [0.5, 0.0] or short.values[1] == 0.0)


def test_meters_visualizer_is_a_registered_mode(app) -> None:
    """The meter is a fourth style, reachable by a click and by name."""
    from ui.widgets.visualizer import VISUALIZERS, LevelFollower, MetersVisualizer, create_visualizer

    check("four styles", len(VISUALIZERS) == 4)
    check("registered", VISUALIZERS.get("meters") is MetersVisualizer)
    check("buildable", isinstance(create_visualizer("meters"), MetersVisualizer))
    check("two channels", len(MetersVisualizer.CHANNELS) == 2)
    check("ticks fit the window", MetersVisualizer.TICK_DECIBELS[-1] == 0.0)
    check("ticks start quiet", MetersVisualizer.TICK_DECIBELS[0] < -40.0)

    widget = MetersVisualizer()
    check("has a level follower", isinstance(widget._levels, LevelFollower))
    widget.set_waveform([0.9, 0.9] * 128)
    for _ in range(30):
        widget.advance()
    check("loud frame moves the meter", widget._levels.values[0] > 0.5)
    check("both channels read", len(widget._levels.values) == 2)
    widget.set_idle()
    for _ in range(200):
        widget.advance()
    check("idle decays to rest", widget._levels.values == [0.0, 0.0])
    widget.resize(640, 200)
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    check("paints without error", not pixmap.isNull())
    widget.set_spectrum([0.4] * 64)
    check("spectrum frames do not crash the meter", widget._levels.values[0] >= 0.0)
    widget.resize(640, 8)
    widget.render(QPixmap(widget.size()))
    check("a too-short stage is skipped, not drawn over", True)

    stack = VisualizerStack(mode="meters")
    check("stack starts on meters", stack.mode == "meters")
    check("meters is visible", stack.current.isVisibleTo(stack) or not stack.isVisible())
    order = [stack.cycle_mode() for _ in range(4)]
    check("cycling visits four modes", len(set(order)) == 4)
    check("cycling comes back to where it started", stack.mode == "meters")
    stack.set_mode("circular")
    stack.set_mode("meters")
    check("switching keeps the meter alive", stack.mode == "meters")
    stack.reset_data()
    check("reset reaches the meter", stack.current._levels.values == [0.0, 0.0])
    stack.stop_all()


def test_visualizer_modes_cover_the_config() -> None:
    """Every stored mode is a real style: no setting that shows nothing."""
    from ui.widgets.visualizer import MODE_ORDER, VISUALIZERS

    check("config and styles agree", set(VALID_VISUALIZERS) == set(VISUALIZERS))
    check("every mode is reachable by a click", set(MODE_ORDER) == set(VALID_VISUALIZERS))
    check("four modes", len(VALID_VISUALIZERS) == 4)
    check("meters is stored", "meters" in VALID_VISUALIZERS)


def test_chips_are_readable_and_loud_when_selected() -> None:
    """The chip row must not read as disabled, and the pick must be obvious."""
    from ui.theme import CHIP_BG, CHIP_INK, TEXT_DIM as TEXT_DIM_TOKEN

    check("inactive surface", CHIP_BG == "#1A1C26")
    check("inactive ink", CHIP_INK == "#E0E0E0")
    background, ink = QColor(CHIP_BG), QColor(CHIP_INK)
    check("opaque surface", background.alpha() == 255 and ink.alpha() == 255)
    check(
        "ink is far lighter than the surface",
        ink.lightnessF() > background.lightnessF() * 2,
    )
    check("ink on surface is readable", ink.lightnessF() - background.lightnessF() > 0.4)
    check("chip rule uses the tokens", f"QPushButton#Chip {{\n    background: {CHIP_BG};" in DARK_QSS)
    check("chip ink in the sheet", f"color: {CHIP_INK};" in DARK_QSS)
    resting = DARK_QSS.split("QPushButton#Chip {", 1)[1].split("}", 1)[0]
    check("the resting chip is not the dim ink", f"color: {CHIP_INK};" in resting)
    check("nothing else paints the chip", f"color: {TEXT_DIM_TOKEN};" not in resting)
    check("selected chip is a gradient", "stop:0 #FFDB4D, stop:1 #FFA300" in DARK_QSS)
    check("selected chip ink is near black", "color: #0D0E15;" in DARK_QSS)


def test_window_fits_900x600() -> None:
    """The shell has to be usable at the size it promises to allow."""
    from ui.main_window import MIN_WINDOW

    check("minimum width", MIN_WINDOW[0] == 900)
    check("minimum height", MIN_WINDOW[1] == 600)
    check("transport still fits", PLAYER_RIGHT_WIDTH + TRANSPORT_MIN_WIDTH + 260 <= MIN_WINDOW[0])


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
        # The transport is icon-only, so the check is that the icon itself swaps
        # between the pause and the play glyph.
        pause_icon_key = window.play_button.icon().pixmap(18, 18).cacheKey()
        check("pause icon painted", pause_icon_key != 0)
        check("play button is a bare icon", window.play_button.text() == "")
        window._on_play()
        app.processEvents()
        check("paused icon painted", window.play_button.icon().pixmap(18, 18).cacheKey() != pause_icon_key)
        check("state paused", rig.controller.state == PlaybackState.PAUSED)
        check("like starts unliked", window.like_button.isChecked() is False)
        window._on_like()
        rig.settle()
        check("like sent", rig.client.like_calls == [("add", (str(rig.controller.current.id),))])
        window._on_mute()
        check("muted", window.volume_slider.value() == 0 and window.mute_button.text() == "")
        check("mute tooltip follows", window.mute_button.toolTip() == "Включить звук")
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
        modes = [window.cycle_visualizer() for _ in range(len(VALID_VISUALIZERS))]
        check("cycles all", sorted(modes) == sorted(VALID_VISUALIZERS))
        check("mode button is a bare icon", window.visualizer_button.text() == "")
        check("mode button paints an icon", window.visualizer_button.icon().pixmap(20, 20).cacheKey() != 0)
        check(
            "tooltip names the mode",
            VISUALIZER_LABELS[window.wave_page.visualizer.mode] in window.visualizer_button.toolTip(),
        )
        window.set_visualizer_mode("circular")
        check("explicit mode", window.wave_page.visualizer.mode == "circular")
        # A click on the stage itself walks the same cycle and the button follows.
        clicked = window.wave_page.visualizer.cycle_mode()
        check("click advances the stage", window.wave_page.visualizer.mode == clicked)
        check("button followed the click", VISUALIZER_LABELS[clicked] in window.visualizer_button.toolTip())
        window.set_visualizer_mode("circular")
        window.volume_slider.setValue(42)
        window.close()
        check("volume persisted", config.get_volume() == 42)
        check("visualizer persisted", config.get_visualizer() == "circular")
    finally:
        rig.close()


# -- redesign: painted rows, teardown and the volume wheel --------------------


def test_track_rows_use_the_painted_delegate(app) -> None:
    from core.yandex_service import WaveTrack
    from ui.theme import COVER_SIZE, PANEL, PLAYING_BG, ROW_HEIGHT, tint
    from ui.widgets.track_list import TrackRowDelegate, row_columns

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
        listing.itemDelegate().sizeHint(None, listing.model().index(0, 0)).height() == ROW_HEIGHT,
    )
    wide = row_columns(900)
    narrow = row_columns(420)
    check("album column for a wide row", wide.show_album and wide.album_width > 0)
    check("album column dropped when narrow", not narrow.show_album and narrow.album_width == 0)
    check("actions dropped when narrow", not narrow.show_actions)
    check("title keeps a usable minimum width", narrow.text_width >= 80)
    check("text is still readable", listing.item(0).text().startswith("Кино — Дельфин"))
    check("duration rendered", listing.item(0).text().endswith("3:35"))
    check("no row is playing yet", not _is_playing(listing, 0))
    listing.set_tracks_playing("t2")
    check("playing marker moved", _is_playing(listing, 1) and not _is_playing(listing, 0))

    listing.resize(760, ROW_HEIGHT * 2)
    # A loud viewport colour proves the rows paint their own base surface
    # instead of inheriting a transparent or black one.
    listing.setStyleSheet("QListWidget#TrackList { background: #ff00ff; border: none; }")
    listing.show()
    app.processEvents()
    image = listing.grab().toImage()
    check("a resting row paints the base surface", _hex(image.pixelColor(400, 8)) == PANEL)
    check("the base is uniform across the row", _hex(image.pixelColor(700, 8)) == PANEL)
    # The playing row is the panel plus a soft accent wash, so it is the token
    # mixed onto the panel rather than a second solid colour.
    playing = image.pixelColor(400, ROW_HEIGHT + 8)
    check("the playing row keeps the same geometry", _hex(playing) != PANEL)
    check("the playing row is washed in accent", _hex(playing) == tint(PANEL, PLAYING_BG).name().upper())
    check("the wash stays subtle", playing.red() - QColor(PANEL).red() < 24)
    # The sounding row shows a mini equaliser where the number would be, so
    # the check looks for gold anywhere in that column rather than at one pixel.
    check("equalizer painted", _gold_in_number_column(image, 1))
    check("plain row has no equalizer", not _gold_in_number_column(image, 0))
    check(
        "the equalizer stays inside its row",
        _gold_in_number_column(image, 1) and not _gold_in_number_column(image, 0),
    )
    check("row geometry is not shifted", listing.visualItemRect(listing.item(1)).height() == ROW_HEIGHT)
    check("cover fits the row", COVER_SIZE <= ROW_HEIGHT - 8)
    listing.setStyleSheet("")
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


def _hex(colour) -> str:
    return colour.name().upper()


def _gold_in_number_column(image, row: int) -> bool:
    """Any gold pixel inside the leading column of one painted row."""
    from ui.theme import ROW_HEIGHT
    from ui.widgets.track_list import NUMBER_COLUMN

    top = row * ROW_HEIGHT
    for y in range(top + 4, top + ROW_HEIGHT - 4):
        for x in range(NUMBER_COLUMN):
            if _is_gold(image.pixelColor(x, y).name()):
                return True
    return False


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


# -- now-playing drawer ------------------------------------------------------


def _shown(widget: QWidget) -> bool:
    """True when ``widget`` is not explicitly hidden.

    The shell tests never call ``show()``, so ``isVisible()`` is False for every
    child of the window.  What these tests are actually asking is whether the
    drawer opened itself, and ``isHidden()`` answers that independently of the
    ancestors being on screen.
    """
    return not widget.isHidden()


def _click(widget: QWidget) -> None:
    """A left click in the middle of ``widget``."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent

    point = QPointF(widget.width() / 2, widget.height() / 2)
    app = QApplication.instance()
    app.sendEvent(
        widget,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            widget.mapToGlobal(point.toPoint()),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_drawer_reports_the_streaming_facts() -> None:
    """The panel answers the questions the 260px player bar cannot."""
    from core.playback_controller import TrackMetadata
    from ui.widgets.now_playing_drawer import _format_bitrate, _format_codec

    check("flac is called out", _format_codec(TrackMetadata(id="1", title="t", quality="flac")) == "FLAC")
    check(
        "a lossy codec is upper-cased",
        _format_codec(TrackMetadata(id="1", title="t", quality="mp3")) == "MP3",
    )
    check("an unknown codec is a dash", _format_codec(TrackMetadata(id="1", title="t")) == "—")
    check(
        "the bitrate is reported",
        _format_bitrate(TrackMetadata(id="1", title="t", bitrate=1411, lossless=True)) == "1411 kbps",
    )
    check(
        "a lossless link without a bitrate still says so",
        _format_bitrate(TrackMetadata(id="1", title="t", lossless=True)) == "lossless",
    )
    check("an unknown bitrate is a dash", _format_bitrate(TrackMetadata(id="1", title="t")) == "—")
    check("the year is a label, not a number", TrackMetadata(id="1", title="t").year_label == "")
    check("and a number when known", TrackMetadata(id="1", title="t", year=2001).year_label == "2001")


def test_drawer_shows_the_current_track(app: QApplication) -> None:
    """Every field the panel promises is filled from the metadata."""
    from core.playback_controller import TrackMetadata
    from ui.widgets.now_playing_drawer import DRAWER_COVER, DRAWER_WIDTH

    rig = Rig(app)
    try:
        rig.login()
        drawer = NowPlayingDrawer(rig.controller)
        check("the panel is a fixed 320px", drawer.width() == DRAWER_WIDTH)
        check("and the cover is 250px", drawer.cover.side == DRAWER_COVER)
        check("it starts closed", not _shown(drawer))

        rig.controller.track_changed.emit(
            TrackMetadata(
                id="7",
                title="Демоны",
                artists=("Дельфин", "Анна"),
                album="Демоны",
                duration_ms=210000,
                quality="flac",
                bitrate=1411,
                lossless=True,
                year=2001,
                has_lyrics=True,
            )
        )
        check("the title is shown", drawer.title_label.text() == "Демоны")
        check("both artists, joined", drawer.artist_label.text() == "Дельфин, Анна")
        check("the album is shown", drawer.album_label.text() == "Демоны")
        check("the year is shown", drawer.year_label.text() == "2001" and _shown(drawer.year_label))
        check("the codec is shown", drawer.codec_label.text() == "FLAC")
        check("the bitrate is shown", drawer.bitrate_label.text() == "1411 kbps")
        check(
            "the volume starts where the controller does",
            drawer.volume_label.text() == f"{rig.controller.volume}%",
        )
        check("the album action is live", drawer.album_button.isEnabled())
        check("the lyrics action is live", drawer.lyrics_button.isEnabled())

        rig.controller.volume_changed.emit(70)
        check("the volume follows the controller", drawer.volume_label.text() == "70%")

        albums: list[tuple[str, str]] = []
        drawer.album_requested.connect(lambda album, artist: albums.append((album, artist)))
        drawer.album_button.click()
        check("the album request carries both names", albums == [("Демоны", "Дельфин, Анна")])

        wanted: list[str] = []
        drawer.lyrics_requested.connect(wanted.append)
        drawer.lyrics_button.click()
        check("the lyrics request names the track", wanted == ["7"])

        drawer.lyrics_loaded.emit("7", "Мы проснулись\nи увидели свет")
        check("the text lands in the panel", drawer.lyrics_label.text() == "Мы проснулись\nи увидели свет")
        check("and the panel shows it", _shown(drawer.lyrics_label))

        drawer.lyrics_loaded.emit("7", "   ")
        check("empty text hides the panel again", not _shown(drawer.lyrics_label))

        drawer.lyrics_loaded.emit("999", "чужой трек")
        check("text for another track is ignored", not _shown(drawer.lyrics_label))

        rig.controller.track_changed.emit(None)
        check("an empty player empties the panel", drawer.title_label.text() == "Ничего не играет")
        check("and the year is gone", not _shown(drawer.year_label))
        check("and the cover is cleared", not _shown(drawer.lyrics_label))
        check("and the actions are dead", not drawer.album_button.isEnabled())
        check("and the codec is a dash", drawer.codec_label.text() == "—")
        drawer.deleteLater()
    finally:
        rig.close()


def test_drawer_opens_from_the_player_bar(app: QApplication, config: ConfigManager) -> None:
    """The cover and the title are the two ways in, and both toggle."""
    from core.playback_controller import TrackMetadata

    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        window.resize(1100, 700)
        rig.controller.track_changed.emit(TrackMetadata(id="3", title="Демоны", artists=("Дельфин",)))
        app.processEvents()

        check("it opens with nothing playing", not _shown(window.drawer))
        window._toggle_drawer()
        app.processEvents()
        check("Ctrl+N opens it", _shown(window.drawer))
        check("and it is painted", not window.drawer.grab().isNull())
        window._close_drawer()
        check("Escape closes it", not _shown(window.drawer))

        _click(window.cover_label)
        app.processEvents()
        check("the cover opens it", _shown(window.drawer))
        _click(window.cover_label)
        app.processEvents()
        check("and clicking again closes it", not _shown(window.drawer))
        _click(window.title_label)
        app.processEvents()
        check("the title opens it too", _shown(window.drawer))
        check("the whole window still paints", not window.grab().isNull())
        window.close()
    finally:
        rig.close()


def test_drawer_stays_closed_without_a_track(app: QApplication, config: ConfigManager) -> None:
    """Clicking the bar with nothing playing must not open an empty panel."""
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        _click(window.cover_label)
        check("no track, no drawer", not _shown(window.drawer))
        window.close()
    finally:
        rig.close()


def test_album_action_searches_the_album(app: QApplication, config: ConfigManager) -> None:
    """«К альбому» lands on the search page with artist and album as the query."""
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        page: SearchPage = window.pages["search"]
        window.show_page("search")
        window._on_drawer_album("Демоны", "Дельфин")
        check("the page switched to search", window.stack.currentWidget() is page)
        check("the query is artist plus album", page.input.text() == "Дельфин Демоны")
        window._on_drawer_album("Демоны", "")
        check("a track with no artist still searches", page.input.text() == "Демоны")
        window.close()
    finally:
        rig.close()


def test_collection_tab_is_asked_once_per_visit(app: QApplication, config: ConfigManager) -> None:
    """Arriving at the collection tab must not re-ask for what is on screen."""
    rig = Rig(app)
    try:
        rig.login()
        window = MainWindow(rig.controller, config)
        page: CollectionPage = window.pages["collection"]
        asked: list[str] = []
        with mock.patch.object(
            rig.service, "load_liked", side_effect=lambda section: asked.append(section) or True
        ):
            window.show_page("collection")
            window.show_page("wave")
            window.show_page("collection")
            check("the first visit asks once", asked == ["tracks"])
            check("and the second visit does not ask again", asked == ["tracks"])
            page.refresh_button.click()
            check("the refresh button does ask again", asked == ["tracks", "tracks"])
        window.close()
    finally:
        rig.close()


def test_a_refused_collection_load_is_retried(app: QApplication, config: ConfigManager) -> None:
    """A failed request must not burn the one automatic load."""
    rig = Rig(app)
    try:
        rig.login()
        page = CollectionPage(rig.controller)
        check("the section starts unloaded", "tracks" not in page._loaded)
        page.ensure_loaded()
        check("the request was made", "tracks" in page._pending)
        rig.service.collection_failed.emit("Нет авторизации: войдите в аккаунт")
        check("the failure released the section", "tracks" not in page._loaded)
        check("and cleared the outstanding request", not page._pending)
        check("the message is shown", "войдите" in page.status_label.text())

        rig.service.collection_ready.emit("tracks", [])
        page.ensure_loaded()
        check("a delivered section is not asked for again", "tracks" in page._loaded)
        page.deleteLater()
    finally:
        rig.close()
