from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


from core.audio_cache import AudioCache
def test_audio_cache_get_save_and_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    c = AudioCache(max_mb=10)
    assert c.get("123") is None
    p = c.cache_path("123", "mp3")
    p.write_bytes(b"abc")
    assert c.get("123") is not None
    assert c.size_bytes() >= 3
    removed = c.clear()
    assert removed >= 1
    assert c.get("123") is None
