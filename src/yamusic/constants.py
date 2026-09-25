"""Application-wide constants and desktop identity."""

from __future__ import annotations

APP_NAME = "Yandex Music Native"
APP_ID = "yandex-music-native"
ORG_NAME = "YandexMusicNative"
DESKTOP_FILE = f"{APP_ID}.desktop"
ICON_THEME_NAME = APP_ID

# D-Bus
MPRIS_SERVICE = "org.mpris.MediaPlayer2.YandexMusic"
MPRIS_OBJECT = "/org/mpris/MediaPlayer2"
MPRIS_ROOT_IFACE = "org.mpris.MediaPlayer2"
MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
NOTIFICATIONS_SERVICE = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"
NOTIFICATIONS_IFACE = "org.freedesktop.Notifications"

# Rotor («Моя волна»)
WAVE_STATION = "user:onyourwave"
ROTOR_FEEDBACK_FROM_PREFIX = "mobile-radio-user"

# Audio
STREAM_RATE = 44100
STREAM_CHANNELS = 2
FFT_SIZE = 2048
SPECTRUM_BANDS = 48
WAVE_POINTS = 256
ANALYZER_FPS = 60
POSITION_POLL_MS = 250

# Cache defaults
DEFAULT_CACHE_LIMIT_MB = 2048
COVER_SIZE = "400x400"

# Quality preference ladder: (codec, bitrate_kbps)
QUALITY_LADDER: tuple[tuple[str, int], ...] = (
    ("flac", 320),
    ("mp3", 320),
    ("mp3", 192),
    ("mp3", 128),
)
