"""Asynchronous cover loading for the list rows.

A list of thirty tracks must not spawn thirty blocking downloads, and it must
not wait for the network either: the row is painted with a gradient placeholder
the moment it appears and swaps in the artwork when it lands.  This loader owns
that swap.

The pieces are deliberately borrowed from the playback controller rather than
re-invented: :func:`core.playback_controller.download_cover` already does the
atomic write with timeouts and a size cap, and
:func:`~core.playback_controller.cover_file_name` already names cache entries, so
a cover downloaded for the player bar and one downloaded for a list are the same
file and the second one is a cache hit.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap

from core.playback_controller import (
    cover_cache_dir,
    cover_file_name,
    default_cover_path,
    download_cover,
)

log = logging.getLogger(__name__)

MAX_CONCURRENT = 3
PENDING_LIMIT = 120
"""Cap on queued downloads: a long result list must not fill the pool."""

_CACHE_DIR = cover_cache_dir()
_MEMO: dict[str, QPixmap | None] = {}


def cached_pixmap(url: str | None) -> QPixmap | None:
    """The cover for ``url`` if it is already on disk, memoised per URL.

    Memoising matters because the same album cover is requested by every track
    of that album, and decoding a JPEG sixty times for one visible screen is the
    kind of waste that shows up as a stutter while scrolling.
    """
    if not url:
        return None
    if url in _MEMO:
        return _MEMO[url]
    path = _CACHE_DIR / cover_file_name(url)
    pixmap: QPixmap | None = None
    try:
        if path.isFile() and path.stat().st_size > 0:
            candidate = QPixmap(str(path))
            pixmap = None if candidate.isNull() else candidate
    except Exception:  # pragma: no cover - unreadable cache entry
        pixmap = None
    if len(_MEMO) < 512:
        _MEMO[url] = pixmap
    return pixmap


def _decode(path: str) -> QPixmap | None:
    try:
        pixmap = QPixmap(path)
    except Exception:  # pragma: no cover - unreadable file
        return None
    return None if pixmap.isNull() else pixmap


def circular_pixmap(pixmap: QPixmap | None, size: int) -> QPixmap | None:
    """``pixmap`` cropped to a ``size`` square and masked into a circle.

    A QLabel paints a pixmap as a rectangle, so an account avatar would show as
    a hard-edged square in the middle of a rounded card.  The crop is a centred
    square taken with a painter rather than ``QPixmap.scaled``: scaling asks Qt
    to work out the device pixel ratio of the result, and the caller asked for a
    pixel size.  Drawing at the device size and setting the ratio afterwards
    keeps the result exactly ``size`` logical pixels wide and still sharp on a
    HiDPI screen, where the mask is an antialiased ellipse in the same device
    pixels rather than a staircase.
    """
    if pixmap is None or pixmap.isNull() or size <= 0:
        return None
    ratio = pixmap.devicePixelRatio()
    if not ratio or ratio <= 0:
        ratio = 1.0
    side = max(1, int(round(size * ratio)))
    out = QPixmap(side, side)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    width, height = pixmap.width(), pixmap.height()
    edge = max(1, min(width, height))
    source = QRect((width - edge) // 2, (height - edge) // 2, edge, edge)
    painter.drawPixmap(QRect(0, 0, side, side), pixmap, source)
    painter.end()
    out.setDevicePixelRatio(ratio)
    mask = QPixmap(side, side)
    mask.fill(Qt.GlobalColor.transparent)
    painter = QPainter(mask)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(Qt.GlobalColor.white))
    painter.drawEllipse(0, 0, side - 1, side - 1)
    painter.end()
    out.setMask(mask.mask())
    return out


class _Signals(QObject):
    loaded = Signal(str, object)
    """``(url, QPixmap | None)`` - ``None`` means «no artwork for this URL»."""


class _Task(QRunnable):
    """One download, off the GUI thread."""

    def __init__(self, url: str, target: Path, signals: _Signals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._url = url
        self._target = target
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - exercised through the loader
        path: str | None = None
        try:
            self._target.parent.mkdir(parents=True, exist_ok=True)
            download_cover(self._url, self._target)
            path = str(self._target)
        except Exception as exc:  # noqa: BLE001
            log.debug("cover download failed for %s: %s", self._url, exc)
            fallback = default_cover_path()
            path = str(fallback) if fallback is not None else None
        pixmap = _decode(path) if path else None
        self._signals.loaded.emit(self._url, pixmap)


class CoverLoader(QObject):
    """Fetches list covers in the background and reports them by URL.

    One loader serves every list in the window: the URL, not the row, is the
    identity, so a cover that arrives while a list is being rebuilt is still
    applied by whoever asks for that URL next.
    """

    loaded = Signal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_CONCURRENT)
        self._signals = _Signals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._queued: set[str] = set()

    def cached(self, url: str | None) -> QPixmap | None:
        return cached_pixmap(url)

    def request(self, url: str | None) -> None:
        """Deliver the cover of ``url`` through :attr:`loaded` as soon as it exists.

        A cached cover is delivered through a zero-delay timer rather than
        directly, so a caller inside a ``set_tracks`` loop always gets its
        answer from the event loop and the painting happens once per batch.
        """
        if not url:
            return
        pixmap = cached_pixmap(url)
        if pixmap is not None:
            QTimer.singleShot(0, lambda: self.loaded.emit(url, pixmap))
            return
        if url in self._queued or len(self._queued) > PENDING_LIMIT:
            return
        self._queued.add(url)
        target = _CACHE_DIR / cover_file_name(url)
        self._pool.start(_Task(url, target, self._signals))

    def _on_loaded(self, url: str, pixmap) -> None:
        self._queued.discard(url)
        if len(_MEMO) < 512:
            _MEMO[url] = pixmap
        self.loaded.emit(url, pixmap)

    def wait_for(self, urls: list[str], timeout_ms: int = 4000) -> bool:
        """Block until every URL in ``urls`` is cached; used by the tests."""
        pending = {url for url in urls if url and not cached_pixmap(url)}
        deadline = 0
        while pending and deadline < timeout_ms:
            self._pool.waitForDone(50)
            deadline += 50
            pending = {url for url in pending if not cached_pixmap(url)}
        return not pending

    def clear(self) -> None:
        self._queued.clear()
        _MEMO.clear()

    def stop(self) -> None:
        """Drop queued work; used on window shutdown."""
        self._pool.clear()
        self._queued.clear()


__all__ = ["CoverLoader", "MAX_CONCURRENT", "PENDING_LIMIT", "cached_pixmap", "circular_pixmap"]
