"""Disk cache: audio tracks, cover images and a SQLite index.

Layout::

    <cache>/tracks/<track_base_id>.<ext>
    <cache>/covers/<sha1(url)>.img
    <data>/index.db

All methods are called from the GUI thread; background downloads write files
in the worker thread and register them here on completion.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from pathlib import Path

from yamusic.config import cache_dir, data_dir

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id         TEXT PRIMARY KEY,
    path       TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    artists    TEXT NOT NULL DEFAULT '',
    album      TEXT NOT NULL DEFAULT '',
    duration   INTEGER NOT NULL DEFAULT 0,
    ext        TEXT NOT NULL DEFAULT 'mp3',
    size       INTEGER NOT NULL DEFAULT 0,
    created    REAL NOT NULL,
    last_used  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS covers (
    url_hash   TEXT PRIMARY KEY,
    path       TEXT NOT NULL,
    size       INTEGER NOT NULL DEFAULT 0,
    last_used  REAL NOT NULL
);
"""


class CacheStore:
    """SQLite-backed index over the track/cover file stores."""

    def __init__(self, limit_mb: int = 2048) -> None:
        self.root = cache_dir()
        self.tracks_dir = self.root / "tracks"
        self.covers_dir = self.root / "covers"
        self.db_path = data_dir() / "index.db"
        self.limit_bytes = max(128, limit_mb) * 1024 * 1024
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def set_limit_mb(self, mb: int) -> None:
        self.limit_bytes = max(128, mb) * 1024 * 1024

    # -- tracks -----------------------------------------------------------

    @staticmethod
    def base_id(track_id: str) -> str:
        return track_id.split(":")[0]

    def track_path(self, track_id: str) -> Path | None:
        """Return a cached file for ``track_id`` or ``None``."""
        base = self.base_id(track_id)
        with self._lock:
            row = self._conn.execute("SELECT path FROM tracks WHERE id = ?", (base,)).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE tracks SET last_used = ? WHERE id = ?", (time.time(), base)
                )
                self._conn.commit()
        if row:
            path = Path(row["path"])
            if path.exists():
                return path
            with self._lock:
                self._conn.execute("DELETE FROM tracks WHERE id = ?", (base,))
                self._conn.commit()
        # fast path: file exists even without index (crashed registration)
        for ext in ("mp3", "flac", "ogg", "m4a", "opus"):
            guess = self.tracks_dir / f"{base}.{ext}"
            if guess.exists():
                return guess
        return None

    def put_track(
        self,
        track_id: str,
        path: Path,
        title: str = "",
        artists: str = "",
        album: str = "",
        duration_ms: int = 0,
    ) -> None:
        base = self.base_id(track_id)
        if not path.exists():
            return
        ext = path.suffix.lstrip(".") or "mp3"
        size = path.stat().st_size
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tracks (id, path, title, artists, album, duration, ext, size, created, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    path=excluded.path, title=excluded.title, artists=excluded.artists,
                    album=excluded.album, duration=excluded.duration, ext=excluded.ext,
                    size=excluded.size, last_used=excluded.last_used
                """,
                (base, str(path), title, artists, album, duration_ms, ext, size, now, now),
            )
            self._conn.commit()

    # -- covers -----------------------------------------------------------

    @staticmethod
    def cover_hash(url: str) -> str:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()

    def cover_path(self, url: str) -> Path | None:
        key = self.cover_hash(url)
        with self._lock:
            row = self._conn.execute("SELECT path FROM covers WHERE url_hash = ?", (key,)).fetchone()
            if row:
                self._conn.execute("UPDATE covers SET last_used = ? WHERE url_hash = ?", (time.time(), key))
                self._conn.commit()
        if row:
            path = Path(row["path"])
            if path.exists():
                return path
        guess = self.covers_dir / f"{key}.img"
        return guess if guess.exists() else None

    def cover_file(self, url: str) -> Path:
        """Deterministic destination path for a cover URL (may not exist yet)."""
        return self.covers_dir / f"{self.cover_hash(url)}.img"

    def put_cover(self, url: str, path: Path) -> None:
        if not path.exists():
            return
        key = self.cover_hash(url)
        size = path.stat().st_size
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO covers (url_hash, path, size, last_used)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(url_hash) DO UPDATE SET path=excluded.path,
                    size=excluded.size, last_used=excluded.last_used
                """,
                (key, str(path), size, time.time()),
            )
            self._conn.commit()

    # -- eviction ---------------------------------------------------------

    def total_bytes(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(size), 0) AS total FROM tracks"
            ).fetchone()
            covers = self._conn.execute(
                "SELECT COALESCE(SUM(size), 0) AS total FROM covers"
            ).fetchone()
        return int(row["total"]) + int(covers["total"])

    def evict(self) -> int:
        """Delete least-recently-used entries until under the size limit."""
        removed = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, path, size FROM tracks ORDER BY last_used ASC"
            ).fetchall()
            covers = self._conn.execute(
                "SELECT url_hash, path, size FROM covers ORDER BY last_used ASC"
            ).fetchall()
            total = sum(int(r["size"]) for r in rows) + sum(int(c["size"]) for c in covers)
            for row in rows:
                if total <= self.limit_bytes:
                    break
                path = Path(row["path"])
                try:
                    if path.exists():
                        path.unlink()
                except OSError as exc:
                    log.debug("evict file failed: %s", exc)
                self._conn.execute("DELETE FROM tracks WHERE id = ?", (row["id"],))
                total -= int(row["size"])
                removed += 1
            for row in covers:
                if total <= self.limit_bytes:
                    break
                path = Path(row["path"])
                try:
                    if path.exists():
                        path.unlink()
                except OSError as exc:
                    log.debug("evict cover failed: %s", exc)
                self._conn.execute("DELETE FROM covers WHERE url_hash = ?", (row["url_hash"],))
                total -= int(row["size"])
                removed += 1
            self._conn.commit()
        if removed:
            log.info("cache evicted %d entries, now %d bytes", removed, self.total_bytes())
        return removed

    def stats(self) -> tuple[int, int]:
        """(entry_count, total_bytes)."""
        with self._lock:
            count = self._conn.execute(
                "SELECT (SELECT COUNT(*) FROM tracks) + (SELECT COUNT(*) FROM covers) AS c"
            ).fetchone()
        return int(count["c"]), self.total_bytes()

    def clear(self) -> None:
        with self._lock:
            for row in self._conn.execute("SELECT path FROM tracks").fetchall():
                try:
                    Path(row["path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            for row in self._conn.execute("SELECT path FROM covers").fetchall():
                try:
                    Path(row["path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            self._conn.execute("DELETE FROM tracks")
            self._conn.execute("DELETE FROM covers")
            self._conn.commit()
        for leftover in self.tracks_dir.glob("*"):
            try:
                if leftover.is_file():
                    leftover.unlink()
            except OSError:
                pass
