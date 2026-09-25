"""Audio pipeline test: libmpv playback + PCM → FFT peak check.

1. Injects a synthetic 1 kHz tone through AudioEngine into SpectrumAnalyzer
   and asserts the spectrum peak lands in the 1 kHz band (works headless).
2. If a tone WAV can be loaded, verifies mpv reports duration and advances
   position (libmpv smoke; ao forced to null).

Run: python tests/audio_pipeline.py
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("YAMUSIC_AO", "null")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from yamusic.audio.analyzer import SpectrumAnalyzer  # noqa: E402
from yamusic.audio.engine import AudioEngine, ensure_mpv  # noqa: E402
from yamusic.audio.fft import band_edges  # noqa: E402

TONE_HZ = 1000.0
SR = 44100
DURATION_S = 3


def write_tone(path: Path, freq: float = TONE_HZ, seconds: float = DURATION_S) -> None:
    frames = int(SR * seconds)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        buf = bytearray()
        for i in range(frames):
            value = int(0.4 * 32767 * math.sin(2 * math.pi * freq * i / SR))
            buf += struct.pack("<hh", value, value)
        fh.writeframes(bytes(buf))


def inject_and_check(app: QCoreApplication, engine: AudioEngine, analyzer: SpectrumAnalyzer) -> bool:
    received: list[list[float]] = []
    waves: list[list[float]] = []
    analyzer.spectrum.connect(received.append)
    analyzer.wave.connect(waves.append)

    # continuous 1 kHz tone into the PCM path (1 second of audio)
    t = np_linspace(TONE_HZ, SR, 1.0)
    chunk = np_stack_stereo(t)
    deadline = time.time() + 3.0
    fed = 0
    while time.time() < deadline and not received:
        engine.inject_pcm(chunk, SR)
        fed += 1
        for _ in range(15):
            app.processEvents()
            time.sleep(0.01)
        if fed > 40:
            break

    if not received:
        print("FAIL: no spectrum frames after PCM injection")
        return False
    print(f"ok: injected PCM → {len(received)} spectrum frames, {len(waves)} wave frames")
    best = max(received, key=sum)
    peak = int(max(range(len(best)), key=lambda i: best[i]))
    edges = band_edges(len(best))
    lo, hi = float(edges[peak]), float(edges[peak + 1])
    print(f"peak band {peak} → {lo:.0f}-{hi:.0f} Hz (expected ~{TONE_HZ:.0f})")
    if not (lo <= TONE_HZ <= hi):
        print(f"FAIL: peak band [{lo:.0f},{hi:.0f}] does not cover {TONE_HZ}")
        return False
    if max(best) < 0.5:
        print(f"FAIL: peak too low: {max(best)}")
        return False
    return True


def np_linspace(freq: float, sr: int, seconds: float):
    import numpy as np

    n = int(sr * seconds)
    t = np.arange(n, dtype=np.float32) / sr
    return (0.5 * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def np_stack_stereo(mono) -> "object":
    import numpy as np

    return np.stack([mono, mono], axis=1)


def check_mpv_playback(app: QCoreApplication, engine: AudioEngine) -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="yamusic-audio-")) / "tone.wav"
    write_tone(tmp)
    engine.set_volume(0)
    engine.load(tmp.as_uri())
    deadline = time.time() + 5.0
    pos = 0
    dur = 0
    while time.time() < deadline:
        app.processEvents()
        pos = engine.position_ms()
        dur = engine.duration_ms()
        if pos >= 300 and dur >= 2000:
            break
        time.sleep(0.05)
    engine.stop()
    app.processEvents()
    print(f"ok: mpv position={pos}ms duration={dur}ms")
    if dur < 1000:
        print("FAIL: mpv duration too small / load failed")
        return False
    if pos < 100:
        print("FAIL: mpv position did not advance")
        return False
    return True


def main() -> int:
    app = QCoreApplication(sys.argv)
    ensure_mpv()

    engine = AudioEngine()
    analyzer = SpectrumAnalyzer(engine)
    analyzer.start()

    ok_fft = inject_and_check(app, engine, analyzer)
    ok_mpv = check_mpv_playback(app, engine)

    analyzer.stop()
    engine.shutdown()

    if not (ok_fft and ok_mpv):
        return 1
    print("audio pipeline test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
