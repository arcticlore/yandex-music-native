"""Persistent settings (QSettings) and credential storage.

Token is stored in ``credentials.json`` with ``0600`` permissions. If the
optional ``secretstorage`` package (or compatible keyring backend) is
available, the token is kept in the Secret Service instead of on disk.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings

from yamusic.constants import APP_ID, APP_NAME, DEFAULT_CACHE_LIMIT_MB

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "yandex-music-native"
_KEYRING_USER = "oauth-token"


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def config_dir() -> Path:
    path = xdg_config_home() / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = xdg_cache_home() / APP_ID
    (path / "tracks").mkdir(parents=True, exist_ok=True)
    (path / "covers").mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    path = xdg_data_home() / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def _try_keyring_get() -> str | None:
    try:
        import secretstorage  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        bus = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(bus)
        if collection.is_locked():
            collection.unlock()
        for item in collection.get_all_items():
            if item.get_label() == f"{_KEYRING_SERVICE}/{_KEYRING_USER}":
                return item.get_secret().decode("utf-8")
    except Exception as exc:  # keyring must never break startup
        log.debug("keyring read failed: %s", exc)
    return None


def _try_keyring_set(token: str) -> bool:
    try:
        import secretstorage  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        bus = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(bus)
        if collection.is_locked():
            collection.unlock()
        label = f"{_KEYRING_SERVICE}/{_KEYRING_USER}"
        for item in collection.get_all_items():
            if item.get_label() == label:
                item.delete()
        collection.create_item(label, {"xdg:schema": "org.freedesktop.Secret.Generic"}, token.encode("utf-8"))
        return True
    except Exception as exc:
        log.debug("keyring write failed: %s", exc)
        return False


class Credentials:
    """OAuth token persistence with disk fallback (mode 0600)."""

    def __init__(self) -> None:
        self._path: Path = config_dir() / "credentials.json"

    def load(self) -> str | None:
        token = _try_keyring_get()
        if token:
            return token
        if not self._path.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("broken credentials file: %s", self._path)
            return None
        token = data.get("access_token")
        return token if isinstance(token, str) and token else None

    def save(self, token: str) -> None:
        if _try_keyring_set(token):
            if self._path.exists():
                self._path.unlink()
            return
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"access_token": token}, indent=2), encoding="utf-8")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self._path)

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


@dataclass(slots=True)
class AppOptions:
    """User-tunable runtime options (mirrored into QSettings)."""

    volume: int = 80
    quality: str = "auto"  # auto | lossless | 320 | 192
    cache_tracks: bool = True
    cache_limit_mb: int = DEFAULT_CACHE_LIMIT_MB
    notifications: bool = True
    visualizer_mode: int = 0  # 0 spectrum, 1 wave, 2 circular
    station: str = "user:onyourwave"
    last_geometry: bytes | None = field(default=None, repr=False)


class Settings:
    """Thin wrapper around :class:`QSettings` with typed accessors."""

    def __init__(self) -> None:
        self._s = QSettings(APP_ID, APP_NAME)

    @property
    def raw(self) -> QSettings:
        return self._s

    def options(self) -> AppOptions:
        s = self._s
        return AppOptions(
            volume=int(s.value("player/volume", 80)),
            quality=str(s.value("player/quality", "auto")),
            cache_tracks=bool(s.value("cache/enabled", True)),
            cache_limit_mb=int(s.value("cache/limit_mb", DEFAULT_CACHE_LIMIT_MB)),
            notifications=bool(s.value("ui/notifications", True)),
            visualizer_mode=int(s.value("ui/visualizer_mode", 0)),
            station=str(s.value("player/station", "user:onyourwave")),
        )

    # --- individual setters used by the settings page ---
    def set_volume(self, value: int) -> None:
        self._s.setValue("player/volume", max(0, min(100, value)))

    def set_quality(self, value: str) -> None:
        self._s.setValue("player/quality", value)

    def set_cache_tracks(self, enabled: bool) -> None:
        self._s.setValue("cache/enabled", enabled)

    def set_cache_limit_mb(self, mb: int) -> None:
        self._s.setValue("cache/limit_mb", max(128, mb))

    def set_notifications(self, enabled: bool) -> None:
        self._s.setValue("ui/notifications", enabled)

    def set_visualizer_mode(self, mode: int) -> None:
        self._s.setValue("ui/visualizer_mode", mode)

    def set_station(self, station: str) -> None:
        self._s.setValue("player/station", station)

    def sync(self) -> None:
        self._s.sync()
