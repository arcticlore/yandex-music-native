# Яндекс Музыка — нативный Linux-клиент

[![CI](https://github.com/yandex-music-linux/yandex-music-linux/actions/workflows/ci.yml/badge.svg)](https://github.com/yandex-music-linux/yandex-music-linux/actions/workflows/ci.yml)
[![Release](https://github.com/yandex-music-linux/yandex-music-linux/actions/workflows/release.yml/badge.svg)](https://github.com/yandex-music-linux/yandex-music-linux/actions/workflows/release.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/PySide6-6.6%2B-41b883.svg)](https://doc.qt.io/qtforpython/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Открытый **GitHub-репозиторий**: полностью нативный десктопный клиент **без
Electron / WebView / браузерных технологий**. Python 3.11+, **PySide6 (Qt 6)**,
**libmpv** (FLAC/lossless + HQ 320), **NumPy FFT** (60 FPS), **yandex-music** 3.x,
MPRIS2, трей, уведомления.

**Запрещено:** Flatpak, Snap.  
**Разрешено:** portable **AppImage**, установка из исходников (`Makefile`),
нативные `.deb` и `PKGBUILD` (AUR).

## Архитектура

```
UI (PySide6)  ←сигналы/слоты→  Services (MVVM)  ←callbacks→  YandexApi
                                                     │
              AudioEngine (libmpv + parec PCM tap)
                                   │
                        SpectrumAnalyzer (QThread, 60 FPS FFT)
                                   │
                    spectrum/wave → Visualizers (QPainter)

ApiWorker (QThread + asyncio) — сеть, кэш, OAuth device flow
CacheStore (SQLite)           — треки/обложки, LRU
MPRIS2 / Tray / Notifications — Linux desktop integration
```

Правила:

* UI-поток не делает сеть и FFT — только приём готовых сигналов.
* Все вызовы `yandex-music` — через `ApiWorker.submit()`.
* Воспроизведение — **libmpv**; для визуализатора PCM снимается `parec`
  с монитора default sink (Pulse/PipeWire). Нет pulse — визуализация в idle,
  плеер работает.

## Возможности

| Модуль | Что реализовано |
|---|---|
| Авторизация | OAuth Device Flow (код в браузере) и вход по токену; Secret Service / `credentials.json` (0600) |
| Плеер | HQ 320 / **Lossless FLAC** (libmpv), буферизация, перемотка, громкость, кэш треков |
| Моя волна | `user:onyourwave` + feedback `radioStarted/trackStarted/trackFinished/skip`, лайк/дизлайк |
| Визуализаторы | Полосы с гравитацией пиков, неон-волна, круговой эквалайзер — 60 FPS |
| Контент | Поиск, плейлисты, любимые, дрил-даун артиста/альбома |
| Тексты | Обычный текст + караоке-синхронизация |
| Desktop | MPRIS2 (медиаклавиши), трей, `org.freedesktop.Notifications` |

## Системные зависимости

```bash
# Fedora
sudo dnf install mpv-libs python3-pyside6 python3-numpy pulseaudio-utils
# Debian/Ubuntu
sudo apt install libmpv2 python3-pyside6 python3-numpy pulseaudio-utils
# Arch
sudo pacman -S mpv python-pyside6 python-numpy libmpv
```

Python-пакеты: `pip install -r requirements.txt`

## Запуск из исходников

```bash
python3 src/main.py
# или
make run
```

Окружение:

* `YAMUSIC_SMOKE=1` — без авторизации, автовыход (CI),
* `YAMUSIC_AUTOSTART=1` — сразу «Моя волна»,
* `YAMUSIC_DEBUG=1` — debug-логи,
* `YAMUSIC_AO=null` — тестовый вывод без звука,
* `QT_QPA_PLATFORM=offscreen` — headless.

## Установка (XDG / freedesktop)

```bash
sudo make install        # /usr/local/bin, share/applications, hicolor icons, dbus
make install-user        # ~/.local/bin (без root)
sudo make uninstall
```

Содержимое:

* `bin/yandex-music-native` — лаунчер
* `share/applications/yandex-music-native.desktop`
* `share/icons/hicolor/scalable/apps/yandex-music-native.svg`
* `share/metainfo/org.yamusic.YandexMusicNative.metainfo.xml`
* `share/dbus-1/services/yandex-music-native.service`

## Пакеты и AppImage

| Канал | Команда |
|---|---|
| **AppImage** | `make appimage` → `dist/yandex-music-native-*.AppImage` |
| **.deb** | `make build-deb` → `dist/yandex-music-native_*_amd64.deb` |
| **PKGBUILD (AUR)** | `makepkg -si` из корня репозитория |
| **pip** | `pip install .` |

CI: `.github/workflows/release.yml` на тег `v*` собирает `.deb`, source
`.tar.gz` и `.AppImage` и публикует GitHub Release.

## Тесты

```bash
make test          # ruff-free fast gate: pytest + compileall
make test-all      # + MPRIS round-trip + живой аудио-пайплайн (нужны mpv/parec)
make lint          # ruff check src tests
make check         # lint + test (то же, что гоняет CI)
```

Подробно:

```bash
QT_QPA_PLATFORM=offscreen YAMUSIC_AO=null python3 -m pytest -q   # 91 тест
dbus-run-session -- python3 tests/mpris_selftest.py               # MPRIS round-trip
YAMUSIC_AO=null python3 tests/audio_pipeline.py                   # PCM→FFT + mpv
```

`pytest` собирает 91 тест: 17 сценариев `PlaybackController`, 55 интеграционных
тестов десктопных сервисов (MPRIS2, уведомления, трей — `test_desktop_integration.py`),
16 тестов авторизации (`auth_test.py`) и три обёртки над self-contained чек-раннерами
(`smoke.py`, `yandex_service_test.py`, `core_audio_test.py` — 710 внутренних
проверок). MPRIS round-trip запускается на отдельной шине (`dbus-run-session`),
чтобы не зависеть от сессии рабочего стола.

## Структура репозитория

```
.
├── .github/workflows/ci.yml       # ruff + pytest на push/PR (Ubuntu, 3.11/3.12)
├── .github/workflows/release.yml  # deb + tar.gz + AppImage → Release (тег v*)
├── data/
│   ├── yandex-music-native.desktop # XDG Desktop Entry
│   └── icons/yandex-music-native.svg
├── packaging/
│   ├── yandex-music-native.desktop
│   ├── yandex-music-native.metainfo.xml
│   ├── yandex-music-native.sh      # системный лаунчер
│   ├── dbus/yandex-music-native.service
│   ├── icons/hicolor/...
│   └── debian/build-deb.sh
├── scripts/build-appimage.sh
├── src/
│   ├── main.py                     # точка входа
│   ├── core/                       # новый слой: auth, audio_engine,
│   │   │                           # yandex_service, playback_controller, config
│   │   └── yamusic/                # UI (PySide6) поверх core
│   └── yamusic/
│       ├── api/                    # worker (QThread+asyncio), facade, auth
│       ├── audio/                  # libmpv engine, FFT, analyzer 60 FPS
│       ├── cache/                  # SQLite + файлы
│       ├── services/               # playback, rotor, lyrics
│       ├── integration/            # MPRIS2, tray, notifications
│       ├── ui/                     # theme, widgets, pages
│       └── resources/
├── tests/                          # pytest + self-contained чек-раннеры
├── Makefile                        # run/test/lint/install/install-user/uninstall
├── PKGBUILD                        # Arch / AUR
├── pyproject.toml
├── requirements.txt
├── LICENSE
└── README.md
```

## Лицензия

MIT. Не является официальным продуктом Яндекса; использует публичный API
через библиотеку `yandex-music`.
