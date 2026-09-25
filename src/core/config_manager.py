"""Configuration and credential storage for yandex-music-linux.

Tokens are kept in the system keyring (Secret Service / KWallet) through the
``keyring`` package. When no usable keyring backend exists the token is stored
inside ``~/.config/yandex-music-native/config.json`` with mode ``0600``; the
owning directory is created with mode ``0700``.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_ID = "yandex-music-native"
CONFIG_FILENAME = "config.json"
KEYRING_SERVICE = APP_ID
KEYRING_USERNAME = "oauth-token"

DEFAULT_SETTINGS: dict[str, Any] = {
    "volume": 80,
    "visualizer": "spectrum",
    "quality": "auto",
    "last_station": "user:onyourwave",
    "cache_tracks": True,
    "cache_limit_mb": 2048,
    "notifications": True,
    "theme": "dark",
}

VALID_VISUALIZERS = ("spectrum", "wave", "circular")
VALID_QUALITIES = ("auto", "lossless", "320", "192")


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_ID


class ConfigManager:
    """Thread-safe JSON configuration with secure token persistence."""

    def __init__(
        self,
        app_id: str = APP_ID,
        *,
        keyring_service: str = KEYRING_SERVICE,
        keyring_username: str = KEYRING_USERNAME,
    ) -> None:
        self._app_id = app_id
        self._keyring_service = keyring_service
        self._keyring_username = keyring_username
        self._lock = threading.RLock()
        self._dir = config_home() if app_id == APP_ID else config_home().parent / app_id
        self._path = self._dir / CONFIG_FILENAME
        self._data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._ensure_dir()
        self._load()

    # -- paths / permissions ----------------------------------------------

    @property
    def config_dir(self) -> Path:
        return self._dir

    @property
    def config_path(self) -> Path:
        return self._path

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._dir, stat.S_IRWXU)
        except OSError as exc:
            log.debug("cannot chmod config dir: %s", exc)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("cannot read config %s: %s", self._path, exc)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            backup = self._path.with_suffix(".json.broken")
            log.warning("invalid config json (%s), moved to %s", exc, backup.name)
            try:
                self._path.replace(backup)
            except OSError:
                pass
            return
        if not isinstance(data, dict):
            return
        merged = dict(DEFAULT_SETTINGS)
        for key, value in data.items():
            if key == "access_token":
                continue
            merged[key] = value
        self._data = merged

    def _write(self) -> None:
        self._ensure_dir()
        payload = dict(self._data)
        token = self._read_token_file()
        if token:
            payload["access_token"] = token
        self._atomic_write(payload)

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        self._ensure_dir()
        fd, tmp_name = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=str(self._dir)
        )
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._path)
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            log.error("cannot write config: %s", exc)
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    # -- generic settings ---------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return self._data[key]
            return DEFAULT_SETTINGS.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._write()

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(values)
            self._write()

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def reset(self) -> None:
        with self._lock:
            self._data = dict(DEFAULT_SETTINGS)
            self._write()

    @property
    def settings(self) -> dict[str, Any]:
        return self.all()

    def get_volume(self) -> int:
        return max(0, min(100, int(self.get("volume", 80))))

    def set_volume(self, value: int) -> None:
        self.set("volume", max(0, min(100, int(value))))

    def get_visualizer(self) -> str:
        value = str(self.get("visualizer", "spectrum"))
        return value if value in VALID_VISUALIZERS else "spectrum"

    def set_visualizer(self, value: str) -> None:
        name = str(value)
        self.set("visualizer", name if name in VALID_VISUALIZERS else "spectrum")

    def get_quality(self) -> str:
        value = str(self.get("quality", "auto"))
        return value if value in VALID_QUALITIES else "auto"

    def set_quality(self, value: str) -> None:
        name = str(value)
        self.set("quality", name if name in VALID_QUALITIES else "auto")

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Read a boolean setting, tolerating strings from hand-edited configs."""
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def set_bool(self, key: str, value: bool) -> None:
        self.set(key, bool(value))

    def get_notifications(self) -> bool:
        """Whether «now playing» desktop notifications are allowed."""
        return self.get_bool("notifications", True)

    def set_notifications(self, value: bool) -> None:
        self.set_bool("notifications", value)

    def get_theme(self) -> str:
        return str(self.get("theme", "dark"))

    def set_theme(self, value: str) -> None:
        self.set("theme", str(value) or "dark")

    # -- keyring ------------------------------------------------------------

    def _keyring_module(self) -> Any | None:
        try:
            import keyring
        except Exception as exc:
            log.debug("keyring unavailable: %s", exc)
            return None
        try:
            backend = keyring.get_keyring()
        except Exception as exc:
            log.debug("keyring backend detection failed: %s", exc)
            return None
        if backend is None:
            return None
        name = f"{type(backend).__module__}.{type(backend).__name__}"
        if "fail" in name.lower() or "null" in name.lower():
            log.debug("keyring backend is a stub: %s", name)
            return None
        return keyring

    def keyring_available(self) -> bool:
        return self._keyring_module() is not None

    def _keyring_get(self) -> str | None:
        keyring = self._keyring_module()
        if keyring is None:
            return None
        try:
            value = keyring.get_password(self._keyring_service, self._keyring_username)
        except Exception as exc:
            log.debug("keyring get failed: %s", exc)
            return None
        return value or None

    def _keyring_set(self, token: str) -> bool:
        keyring = self._keyring_module()
        if keyring is None:
            return False
        try:
            keyring.set_password(self._keyring_service, self._keyring_username, token)
            return True
        except Exception as exc:
            log.debug("keyring set failed: %s", exc)
            return False

    def _keyring_delete(self) -> bool:
        keyring = self._keyring_module()
        if keyring is None:
            return False
        try:
            keyring.delete_password(self._keyring_service, self._keyring_username)
            return True
        except Exception as exc:
            log.debug("keyring delete failed: %s", exc)
            return False

    # -- token file fallback -------------------------------------------------

    def _read_token_file(self) -> str | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        token = data.get("access_token")
        return token if isinstance(token, str) and token else None

    def _write_token_file(self, token: str | None) -> None:
        with self._lock:
            if token:
                payload = dict(self._data)
                payload["access_token"] = token
                self._atomic_write(payload)
            else:
                self._write()

    # -- token API -----------------------------------------------------------

    @property
    def storage_backend(self) -> str:
        return "keyring" if self.keyring_available() else "file"

    def get_token(self) -> str | None:
        with self._lock:
            token = self._keyring_get()
            if token:
                return token
            return self._read_token_file()

    def set_token(self, token: str) -> str:
        value = str(token).strip()
        if not value:
            raise ValueError("empty token")
        with self._lock:
            if self._keyring_set(value):
                self._drop_file_token()
                self._write()
                return "keyring"
            self._write_token_file(value)
            return "file"

    def delete_token(self) -> None:
        with self._lock:
            self._keyring_delete()
            self._drop_file_token()
            self._write()

    def has_token(self) -> bool:
        return bool(self.get_token())

    def _drop_file_token(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict) and "access_token" in data:
            data.pop("access_token", None)
            self._atomic_write(data)

    def file_mode(self) -> int | None:
        if not self._path.exists():
            return None
        return stat.S_IMODE(self._path.stat().st_mode)
