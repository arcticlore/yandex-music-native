from __future__ import annotations

import os
from pathlib import Path
from threading import RLock

DEFAULT_MAX_MB = 1024
_PART = ".part"
LOCK = RLock()


def _cache_root() -> Path:
    home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    root = home / "yandex-music-native" / "tracks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_name(track_id: str, ext: str = "mp3") -> str:
    tid = str(track_id).replace("/", "_").replace("\\", "_").replace(":", "_")
    ex = ext.lstrip(".") or "mp3"
    return f"{tid}.{ex}"


class AudioCache:
    def __init__(self, max_mb: int = DEFAULT_MAX_MB) -> None:
        self._root = _cache_root()
        self._max_bytes = max(0, int(max_mb)) * 1024 * 1024
        self._lock = LOCK

    @property
    def root(self) -> Path:
        return self._root

    def get(self, track_id: str) -> Path | None:
        with self._lock:
            for p in self._root.glob(f"{str(track_id).replace('/', '_')}.*"):
                if p.suffix == _PART or not p.is_file():
                    continue
                return p
            return None

    def cache_path(self, track_id: str, ext: str = "mp3") -> Path:
        return self._root / _safe_name(track_id, ext)

    def save_from_url(self, track_id: str, stream_url: str, ext: str = "mp3") -> Path | None:
        import http.client
        from urllib.parse import urlparse

        dst = self.cache_path(track_id, ext)
        part = dst.with_suffix(dst.suffix + _PART)
        try:
            parsed = urlparse(stream_url)
            conn = (
                http.client.HTTPSConnection(parsed.netloc, timeout=30)
                if parsed.scheme == "https"
                else http.client.HTTPConnection(parsed.netloc, timeout=30)
            )
            conn.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""))
            resp = conn.getresponse()
            if resp.status not in (200, 206):
                return None
            with part.open("wb") as f:
                while True:
                    chunk = resp.read(512 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            part.replace(dst)
            self._cleanup()
            return dst
        except Exception:
            try:
                part.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    def clear(self) -> int:
        with self._lock:
            removed = 0
            for p in self._root.glob("*"):
                try:
                    if p.is_file():
                        p.unlink()
                        removed += 1
                except Exception:
                    continue
            return removed

    def size_bytes(self) -> int:
        with self._lock:
            s = 0
            for p in self._root.glob("*"):
                try:
                    if p.is_file():
                        s += p.stat().st_size
                except Exception:
                    continue
            return s

    def _cleanup(self) -> None:
        if self._max_bytes <= 0:
            return
        with self._lock:
            items = []
            for p in self._root.glob("*"):
                try:
                    if p.is_file() and p.suffix != _PART:
                        st = p.stat()
                        items.append((st.st_mtime, st.st_size, p))
                except Exception:
                    continue
            total = sum(sz for _, sz, _ in items)
            if total <= self._max_bytes:
                return
            items.sort(key=lambda x: (x[0], id(x[2])))
            for _, sz, p in items:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
                total -= sz
                if total <= self._max_bytes:
                    break


audio_cache = AudioCache()
