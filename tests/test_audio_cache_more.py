from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


from core.audio_cache import AudioCache
def test_lru_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    c = AudioCache(max_mb=1)
    p1 = c.cache_path("1", "mp3")
    p1.write_bytes(b"x" * 200000)
    p2 = c.cache_path("2", "mp3")
    p2.write_bytes(b"x" * 200000)
    p3 = c.cache_path("3", "mp3")
    p3.write_bytes(b"x" * 200000)
    c._cleanup()
