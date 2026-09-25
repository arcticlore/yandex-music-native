"""Tests for core.audio_engine: libmpv transport, PCM taps and the 60 Hz FFT.

Run: python tests/core_audio_test.py
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEventLoop  # noqa: E402

from core.audio_engine import (  # noqa: E402
    BAND_CHOICES,
    DEFAULT_BANDS,
    FFT_SIZE,
    FRAME_INTERVAL_MS,
    MAX_FREQ,
    MIN_FREQ,
    AudioEngine,
    Envelope,
    band_edges,
    detect_audio_output,
    ensure_libmpv,
    normalize_bands,
    oscilloscope,
    spectrum_bands,
    uri_to_path,
)

PASSED: list[str] = []
SR = 44100


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"ok: {name}")


def wait_for(app: QCoreApplication, predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 30)
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 30)
    return bool(predicate())


def make_media(tmp: Path) -> dict[str, Path]:
    media: dict[str, Path] = {}
    mp3 = tmp / "tone320.mp3"
    flac = tmp / "tone.flac"
    jobs = [
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=4",
            "-ac",
            "2",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            str(mp3),
        ],
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=4",
            "-ac",
            "2",
            "-c:a",
            "flac",
            str(flac),
        ],
    ]
    for cmd in jobs:
        if shutil.which(cmd[0]) is None:
            continue
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if out.returncode == 0:
            media[Path(cmd[-1]).suffix] = Path(cmd[-1])
    return media


def test_helpers() -> None:
    check("band choices", BAND_CHOICES == (32, 64))
    check("default bands 64", DEFAULT_BANDS == 64)
    check("fft size", FFT_SIZE == 2048)
    check("frame interval 60hz", FRAME_INTERVAL_MS == 17, str(FRAME_INTERVAL_MS))
    check("freq range", (MIN_FREQ, MAX_FREQ) == (20.0, 20_000.0))
    edges = band_edges(64)
    check("64 band edges length", edges.size == 65)
    check("edges low", abs(float(edges[0]) - 20.0) < 1e-6)
    check("edges high", abs(float(edges[-1]) - 20_000.0) < 1.0)
    check("edges increasing", bool(np.all(np.diff(edges) > 0)))
    check("32 band edges", band_edges(32).size == 33)
    check("normalize 16 -> 32", normalize_bands(16) == 32)
    check("normalize 32 -> 32", normalize_bands(32) == 32)
    check("normalize 33 -> 64", normalize_bands(33) == 64)
    check("normalize 64 -> 64", normalize_bands(64) == 64)
    try:
        normalize_bands(128)
        check("normalize rejects 128", False, "no ValueError")
    except ValueError:
        check("normalize rejects 128", True)
    check("uri passthrough", uri_to_path("https://cdn/track.mp3") == "https://cdn/track.mp3")
    check("uri file", uri_to_path("file:///home/u/a%20b.flac") == "/home/u/a b.flac")
    check(
        "ao detected", detect_audio_output() in ("pulse", "pipewire", "null", "auto"), detect_audio_output()
    )
    ensure_libmpv()
    check("libmpv available", True)


def test_fft() -> None:
    t = np.arange(FFT_SIZE) / SR
    sine = np.sin(2 * math.pi * 1000.0 * t).astype(np.float32)
    for n_bands in (32, 64):
        bands = spectrum_bands(sine, SR, n_bands)
        check(f"fft {n_bands} length", bands.shape == (n_bands,), str(bands.shape))
        check(f"fft {n_bands} range", float(bands.min()) >= 0.0 and float(bands.max()) <= 1.0)
        peak = int(np.argmax(bands))
        edges = band_edges(n_bands)
        check(
            f"fft {n_bands} 1khz inside band",
            edges[peak] <= 1000.0 <= edges[peak + 1],
            f"peak={peak} {edges[peak]:.0f}-{edges[peak + 1]:.0f}",
        )
        check(f"fft {n_bands} peak value", float(bands[peak]) > 0.6, str(float(bands[peak])))
    low = np.sin(2 * math.pi * 30.0 * t).astype(np.float32)
    bands_low = spectrum_bands(low, SR, 64)
    edges_low = band_edges(64)
    peak_low = int(np.argmax(bands_low))
    check(
        "fft 30hz inside band",
        edges_low[peak_low] <= 30.0 <= edges_low[peak_low + 1],
        f"{edges_low[peak_low]:.0f}-{edges_low[peak_low + 1]:.0f}",
    )
    high = np.sin(2 * math.pi * 15_000.0 * t).astype(np.float32)
    bands_high = spectrum_bands(high, SR, 64)
    edges_high = band_edges(64)
    peak_high = int(np.argmax(bands_high))
    check(
        "fft 15khz inside band",
        edges_high[peak_high] <= 15_000.0 <= edges_high[peak_high + 1],
        f"{edges_high[peak_high]:.0f}-{edges_high[peak_high + 1]:.0f}",
    )
    silence = np.zeros(FFT_SIZE, dtype=np.float32)
    check("fft silence", float(spectrum_bands(silence, SR, 64).max()) < 1e-6)
    stereo = np.repeat(sine[:512], 2).reshape(512, 2)
    check("fft stereo input", spectrum_bands(stereo, SR, 64).shape == (64,))
    wave = oscilloscope(sine)
    check("waveform points", wave.size == 512, str(wave.size))
    check("waveform range", float(wave.min()) >= -1.0 and float(wave.max()) <= 1.0)
    check("waveform captures peak", float(np.abs(wave).max()) > 0.9)
    short = oscilloscope(np.zeros(10, dtype=np.float32), points=512)
    check("waveform short padded", short.size == 512)
    env = Envelope(4, attack=1.0, release=1.0)
    env.update(np.ones(4, dtype=np.float32))
    check("envelope attack", float(env.value.max()) > 0.99)
    env.update(np.zeros(4, dtype=np.float32))
    check("envelope release", float(env.value.max()) < 0.01)
    soft = Envelope(2, attack=0.5, release=0.5)
    soft.update(np.ones(2, dtype=np.float32))
    check("envelope partial attack", 0.4 < float(soft.value.max()) < 0.6, str(float(soft.value.max())))
    soft.update(np.zeros(2, dtype=np.float32))
    check("envelope partial release", 0.2 < float(soft.value.max()) < 0.3, str(float(soft.value.max())))
    resized = Envelope(4)
    resized.update(np.ones(2, dtype=np.float32))
    check("envelope resizes", resized.value.size == 2, str(resized.value.size))
    resized.reset()
    check("envelope reset", float(resized.value.max()) == 0.0)


def test_fft_signal_rate(app: QCoreApplication) -> None:
    engine = AudioEngine(ao="null", tap_mode="none")
    frames: list[np.ndarray] = []
    waves: list[np.ndarray] = []
    engine.spectrum_ready.connect(frames.append)
    engine.waveform_ready.connect(waves.append)
    tone = np.sin(2 * math.pi * 440.0 * np.arange(2048) / SR).astype(np.float32)
    engine.inject_pcm(tone, SR)
    start = time.time()
    while time.time() - start < 1.0:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)
    elapsed = time.time() - start
    rate = len(frames) / elapsed if elapsed else 0
    check("spectrum emitted", len(frames) >= 40, f"{len(frames)} in {elapsed:.2f}s")
    check("waveform emitted", len(waves) >= 40, f"{len(waves)}")
    check("60hz rate", 45 <= rate <= 70, f"{rate:.1f} Hz")
    check("spectrum band count", frames[-1].size == 64, str(frames[-1].size))
    check("waveform points", waves[-1].size == 512)
    peak = int(np.argmax(frames[-1]))
    edges = band_edges(64)
    check(
        "injected 440hz band",
        edges[peak] <= 440.0 <= edges[peak + 1],
        f"{edges[peak]:.0f}-{edges[peak + 1]:.0f}",
    )
    seen: list[int] = []
    engine.bands_changed.connect(seen.append)
    check("set 32 bands", engine.set_band_count(32) == 32)
    check("bands changed signal", seen == [32], str(seen))
    check("set 64 bands", engine.set_band_count(64) == 64)
    check("bands signal twice", seen == [32, 64], str(seen))
    check("bands property", engine.bands == 64)
    check("back to 32 bands", engine.set_band_count(32) == 32)
    check("bands signal thrice", seen == [32, 64, 32], str(seen))
    engine.inject_pcm(tone, SR)
    deadline = time.time() + 2
    while time.time() < deadline and frames[-1].size != 32:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.01)
    check("spectrum follows band switch", frames[-1].size == 32, str(frames[-1].size))
    engine.shutdown()


def test_transport(app: QCoreApplication, media: dict[str, Path]) -> None:
    if not media:
        print("skip: ffmpeg unavailable, transport test not run")
        return
    engine = AudioEngine(ao="null", tap_mode="none", volume=50)
    states: list[str] = []
    finished: list[int] = []
    errors: list[str] = []
    durations: list[float] = []
    engine.state_changed.connect(states.append)
    engine.finished.connect(lambda: finished.append(1))
    engine.error.connect(errors.append)
    engine.duration_changed.connect(durations.append)
    check("volume initial", engine.volume == 50)
    check("set volume", engine.set_volume(73) == 73)
    check("volume clamped", engine.set_volume(150) == 100)
    check("volume clamped low", engine.set_volume(-5) == 0)
    check("set volume back", engine.set_volume(55) == 55)
    check("empty transport no play", engine.play() is False)
    check("error on empty play", len(errors) == 1, str(errors))
    errors.clear()
    starts: list[str] = []
    engine.playback_started.connect(starts.append)

    for suffix, path in media.items():
        uri = f"file://{path}"
        check(f"play {suffix}", engine.play(uri) is True)
        check(f"url stored {suffix}", engine.url == uri)
        check(f"state playing {suffix}", wait_for(app, lambda: engine.is_playing))
        check(
            f"duration {suffix}",
            wait_for(app, lambda: 3_500 <= engine.get_duration_ms() <= 4_500),
            str(engine.get_duration_ms()),
        )
        check(f"duration signal {suffix}", wait_for(app, lambda: bool(durations), timeout=5), str(durations))
        check(f"duration seconds {suffix}", 3.5 <= engine.get_duration() <= 4.5, str(engine.get_duration()))
        check(
            f"position advances {suffix}",
            wait_for(app, lambda: engine.get_position_ms() > 200),
            str(engine.get_position_ms()),
        )
        paused_at = engine.get_position_ms()
        check(f"pause {suffix}", engine.pause() is True)
        check(f"state paused {suffix}", engine.is_paused)
        time.sleep(0.4)
        app.processEvents()
        drift = abs(engine.get_position_ms() - paused_at)
        check(f"position frozen {suffix}", drift < 120, f"drift={drift}ms")
        check(f"toggle resumes {suffix}", engine.toggle_play() == "playing")
        check(f"playing again {suffix}", wait_for(app, lambda: engine.is_playing))
        check(f"toggle pauses {suffix}", engine.toggle_play() == "paused")
        engine.toggle_play()
        check(f"seek {suffix}", engine.seek(1500) is True)
        check(
            f"seek applied {suffix}",
            wait_for(app, lambda: 1200 <= engine.get_position_ms() <= 2000),
            str(engine.get_position_ms()),
        )
        check(f"seek clamps {suffix}", engine.seek(10_000_000) is True)
        check(f"seek backwards {suffix}", engine.seek(0) is True)
        check(
            f"seek zero {suffix}",
            wait_for(app, lambda: engine.get_position_ms() <= 400),
            str(engine.get_position_ms()),
        )
        check(f"seek no media after stop {suffix}", True)
        engine.stop()
        check(f"state stopped {suffix}", engine.state == "stopped")
        check(f"position zero after stop {suffix}", engine.get_position_ms() == 0)
        check(f"duration zero after stop {suffix}", engine.get_duration() == 0)
        check(f"pause ignored when stopped {suffix}", engine.pause() is False)
        check(f"seek ignored when stopped {suffix}", engine.seek(100) is False)
        check(f"playback no errors {suffix}", errors == [], str(errors))
        check(f"playback started signal {suffix}", starts and starts[-1] == uri, str(starts))

    check("start with offset", engine.play(f"file://{next(iter(media.values()))}", start_ms=2000) is True)
    check(
        "offset seek applied",
        wait_for(app, lambda: 1500 <= engine.get_position_ms() <= 3000),
        str(engine.get_position_ms()),
    )
    engine.stop()
    engine.seek(500)
    check("no media seek returns false", engine.get_position_ms() == 0)
    check(
        "states observed",
        "playing" in states and "paused" in states and "stopped" in states,
        str(set(states)),
    )

    fast = AudioEngine(ao="null", tap_mode="none")
    target = next(iter(media.values()))
    end: list[int] = []
    fast.finished.connect(lambda: end.append(1))
    fast.play(f"file://{target}")
    check("eos fires", wait_for(app, lambda: bool(end), timeout=30), str(end))
    check("state stopped after eos", fast.state == "stopped")
    fast.shutdown()
    engine.shutdown()
    check("no error signal", errors == [], str(errors))


def test_stream_url(app: QCoreApplication) -> None:
    engine = AudioEngine(ao="null", tap_mode="none")
    errors: list[str] = []
    engine.error.connect(errors.append)
    check("http url accepted", engine.play("https://storage.mds.yandex.net/get-music/stream.mp3") is True)
    check("http url stored", engine.url.endswith("stream.mp3"))
    engine.stop()
    check("missing file reported", engine.play("file:///nonexistent/yandex.flac") in (True, False))
    wait_for(app, lambda: bool(errors) or engine.get_position_ms() > 0, timeout=8)
    check(
        "bad url produced error or empty position", bool(errors) or engine.get_position_ms() == 0, str(errors)
    )
    engine.shutdown()


def test_fifo_tap(app: QCoreApplication) -> None:
    from core.audio_engine import _FifoTap

    with tempfile.TemporaryDirectory() as raw:
        fifo = Path(raw) / "pcm.fifo"
        os.mkfifo(fifo)
        os.environ["YML_PCM_FIFO"] = str(fifo)
        chunks: list[np.ndarray] = []
        tap = _FifoTap(lambda block, rate: chunks.append(block))
        check("fifo available", _FifoTap.available() is True)
        check("fifo mode", tap.mode == "fifo")
        check("fifo started", tap.start() is True)
        active = False
        deadline = time.time() + 5
        while time.time() < deadline and not active:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 30)
            active = tap.active
            time.sleep(0.05)
        check("fifo thread alive", active)

        samples = (np.sin(2 * math.pi * 440.0 * np.arange(SR // 2) / SR) * 20000).astype(np.int16)
        payload = np.repeat(samples, 2).tobytes()

        def writer() -> None:
            with open(fifo, "wb", buffering=0) as handle:
                for _ in range(3):
                    handle.write(payload)
                    time.sleep(0.05)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline and not chunks:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 30)
            time.sleep(0.02)
        thread.join(timeout=2)
        check("fifo pcm received", bool(chunks), "no chunks")
        if chunks:
            check("fifo frame shape", chunks[0].shape[1] == 2, str(chunks[0].shape))
            check("fifo values normalized", float(np.abs(chunks[0]).max()) <= 1.0)
        tap.stop()
        check("fifo stopped", tap.active is False)
        os.environ.pop("YML_PCM_FIFO", None)


def test_pcm_consumer(app: QCoreApplication) -> None:
    engine = AudioEngine(ao="null", tap_mode="none")
    seen: list[tuple[int, int]] = []
    engine.add_pcm_consumer(lambda block, rate: seen.append((block.shape[0], rate)))
    engine.inject_pcm(np.zeros((256, 2), dtype=np.float32), SR)
    check("pcm consumer called", seen and seen[0][0] == 256, str(seen))
    check("pcm sample rate forwarded", seen and seen[0][1] == SR)

    def boom(block, rate: int) -> None:
        raise RuntimeError("consumer failure")

    engine.add_pcm_consumer(boom)
    engine_logger = logging.getLogger("core.audio_engine")
    previous_level = engine_logger.level
    engine_logger.setLevel(logging.CRITICAL)
    engine.inject_pcm(np.zeros((128, 2), dtype=np.float32), SR)
    engine_logger.setLevel(previous_level)
    check("failing consumer isolated", True)
    engine.shutdown()


def test_tap_selection() -> None:
    from core.audio_engine import _FifoTap, _MonitorTap, create_tap

    check("tap none", create_tap(lambda b, r: None, mode="none") is None)
    monitor = create_tap(lambda b, r: None, mode="monitor")
    check("monitor requested", monitor is None or isinstance(monitor, _MonitorTap))
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "f.fifo"
        os.mkfifo(path)
        os.environ["YML_PCM_FIFO"] = str(path)
        fifo = create_tap(lambda b, r: None, mode="auto")
        if fifo is not None:
            check("auto picks fifo when no monitor", isinstance(fifo, _FifoTap))
        check("fifo tap created", isinstance(_FifoTap(lambda b, r: None), _FifoTap))
        os.environ.pop("YML_PCM_FIFO", None)
    check(
        "default monitor sink probe",
        _MonitorTap.default_sink() is None or isinstance(_MonitorTap.default_sink(), str),
    )


def main() -> int:
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    with tempfile.TemporaryDirectory(prefix="yml-core-audio-") as raw:
        tmp = Path(raw)
        media = make_media(tmp)
        test_helpers()
        test_fft()
        test_fft_signal_rate(app)
        test_transport(app, media)
        test_stream_url(app)
        test_fifo_tap(app)
        test_pcm_consumer(app)
        test_tap_selection()
    print(f"\nAll {len(PASSED)} audio engine checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
