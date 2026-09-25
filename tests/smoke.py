"""Unit smoke tests: FFT correctness, lyric parsers, model converters.

Run: python tests/smoke.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from yamusic.audio.fft import (  # noqa: E402
    Smoother,
    band_edges,
    oscilloscope,
    spectrum_bands,
)
from yamusic.models import (  # noqa: E402
    build_lyric_document,
    parse_lrc,
)


def test_band_edges() -> None:
    edges = band_edges(48)
    assert edges.size == 49
    assert abs(float(edges[0]) - 40.0) < 1e-6
    assert float(edges[-1]) >= 15_900
    assert np.all(np.diff(edges) > 0), "edges must be strictly increasing"
    print("ok: band_edges")


def test_sine_peak_location() -> None:
    sr = 44100
    freq = 1000.0
    t = np.arange(2048) / sr
    wave = np.sin(2 * math.pi * freq * t).astype(np.float32)
    bands = spectrum_bands(wave, sr, 48)
    assert bands.shape == (48,)
    peak = int(np.argmax(bands))
    edges = band_edges(48)
    # peak band must contain 1 kHz
    lo, hi = edges[peak], edges[peak + 1]
    assert lo <= freq <= hi, f"peak band [{lo:.0f},{hi:.0f}] must contain {freq}"
    assert bands.max() > 0.5, "loud sine should reach high normalised value"
    print(f"ok: sine peak at band {peak} ({lo:.0f}-{hi:.0f} Hz)")


def test_silence_is_low() -> None:
    sr = 44100
    silence = np.zeros(2048, dtype=np.float32)
    bands = spectrum_bands(silence, sr, 48)
    assert float(bands.max()) < 0.2
    print("ok: silence floor")


def test_oscilloscope_shape() -> None:
    sr = 44100
    t = np.arange(4096) / sr
    wave = np.sin(2 * math.pi * 220 * t).astype(np.float32)
    osc = oscilloscope(wave, 256)
    assert osc.shape == (256,)
    assert float(np.abs(osc).max()) > 0.5
    print("ok: oscilloscope")


def test_smoother_attack_release() -> None:
    sm = Smoother(8, attack=0.9, release=0.05, peak_fall=0.02)
    up, peaks = sm.update(np.ones(8, dtype=np.float32))
    assert float(up.min()) > 0.5, "fast attack expected"
    down, peaks2 = sm.update(np.zeros(8, dtype=np.float32))
    assert float(down[0]) < float(up[0]), "release should decay"
    assert float(peaks2.min()) >= float(down.min()), "peak >= value"
    print("ok: smoother")


def test_lrc_parsing() -> None:
    text = "[00:10.50]first line\n[00:15]second line\n[00:20.250]third\n"
    lines = parse_lrc(text)
    assert len(lines) == 3
    assert lines[0].start_ms == 10_500
    assert lines[1].start_ms == 15_000
    assert lines[0].end_ms == 15_000  # next start
    print("ok: lrc")


def test_yandex_sync_json() -> None:
    payload = {
        "result": {
            "lyrics": {
                "lines": [
                    {"lyrics": "привет", "lineStartMs": 1000, "lineEndMs": 2000},
                    {"lyrics": "мир", "lineStartMs": 2000, "lineEndMs": 3000},
                ]
            }
        }
    }
    doc = build_lyric_document("1:1", "привет\nмир", payload, None)
    assert doc.synced
    assert doc.line_at(1500) == 0
    assert doc.line_at(2500) == 1
    assert doc.line_at(0) == -1 or doc.line_at(0) == -1
    print("ok: yandex sync json")


def test_plain_lyrics() -> None:
    doc = build_lyric_document("2:2", "line one\nline two", None, None)
    assert not doc.synced
    assert len(doc.lines) == 2
    print("ok: plain lyrics")


def main() -> int:
    tests = [
        test_band_edges,
        test_sine_peak_location,
        test_silence_is_low,
        test_oscilloscope_shape,
        test_smoother_attack_release,
        test_lrc_parsing,
        test_yandex_sync_json,
        test_plain_lyrics,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {test.__name__}: {exc!r}", file=sys.stderr)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
