"""Native Yandex Music client for Linux.

Layers:
    api      — async network bridge (QThread + asyncio + yandex-music)
    audio    — GStreamer playback engine and FFT spectrum analyzer
    cache    — disk cache (tracks, covers) backed by SQLite
    services — playback/rotor/lyrics view-models
    integration — MPRIS2, system tray, desktop notifications
    ui       — PySide6 widgets, pages and QSS theme
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
