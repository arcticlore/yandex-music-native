# Яндекс Музыка — нативный Linux-клиент

[![CI](https://github.com/arcticlore/yandex-music-native/actions/workflows/ci.yml/badge.svg)](https://github.com/arcticlore/yandex-music-native/actions/workflows/ci.yml)
[![Release](https://github.com/arcticlore/yandex-music-native/actions/workflows/release.yml/badge.svg)](https://github.com/arcticlore/yandex-music-native/actions/workflows/release.yml)
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
| Плеер | HQ 320 / **Lossless FLAC** (libmpv), буферизация, перемотка, громкость |
| Моя волна | `user:onyourwave`, настройки mood/activity/language/diversity с мгновенным рестартом, feedback `radioStarted/trackStarted/trackFinished/skip`, лайк/дизлайк |
| Визуализаторы | Полосы с гравитацией пиков, неон-волна, круговой спектр с обложкой — 60 FPS, attack/release-сглаживание |
| Контент | Поиск с счётчиками по вкладкам, коллекция: треки, альбомы, исполнители, плейлисты |
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

* `YML_AUDIO_AO=pipewire|pulse|null` — звуковой сервер (по умолчанию autodetect),
* `YML_PCM_TAP=auto|parec|fifo|off` — источник PCM для визуализатора,
* `YML_PCM_FIFO` / `YML_PCM_WRITER` — свой FIFO и команда-писатель в него,
* `YML_HWACCEL=auto|yes|no` — аппаратное декодирование,
* `YML_OAUTH_CLIENT_ID` — свой OAuth client id,
* `YML_LOG_LEVEL=DEBUG` — подробные логи,
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
* `share/metainfo/org.arcticlore.YandexMusicNative.metainfo.xml`
* `lib/yandex-music-native/{main.py,core,ui}` — исходники стека для офлайн-запуска
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
make lint          # ruff check + ruff format --check (src, tests)
make format        # ruff format — привести код к каноническому виду
make test          # pytest + compileall
make test-all      # + MPRIS round-trip (нужна приватная шина dbus)
make check         # lint + test (то же, что гоняет CI)
```

Подробно:

```bash
QT_QPA_PLATFORM=offscreen YML_AUDIO_AO=null python3 -m pytest -q
dbus-run-session -- python3 tests/mpris_selftest.py                # MPRIS round-trip
```

`pytest` собирает 120+ тестов: сценарии `PlaybackController`
(`test_playback_controller.py`), десктопные интеграции — MPRIS2, уведомления,
трей (`test_desktop_integration.py`), новый Qt-оболочечный стек —
валидаторы станции, визуализаторы, страницы, окно (`test_ui_shell.py`), гейты
релиза (`test_packaging.py`), авторизация (`auth_test.py`) и self-contained
чек-раннеры `yandex_service_test.py` и `core_audio_test.py` (живой
PCM→FFT→mpv пайплайн, ~450 внутренних проверок). MPRIS round-trip запускается
на отдельной шине (`dbus-run-session`), чтобы не зависеть от сессии рабочего
стола.

## Структура репозитория

```
.
├── .github/workflows/ci.yml       # ruff + pytest на push/PR (Ubuntu, 3.11/3.12)
├── .github/workflows/release.yml  # deb + tar.gz + AppImage → Release (тег v*)
├── packaging/
│   ├── yandex-music-native.desktop # XDG Desktop Entry
│   ├── yandex-music-native.metainfo.xml # AppStream (id org.arcticlore.*)
│   ├── yandex-music-native.sh      # системный лаунчер (общий для deb/AUR)
│   ├── dbus/yandex-music-native.service
│   ├── icons/hicolor/...
│   └── debian/build-deb.sh
├── scripts/build-appimage.sh
├── src/
│   ├── main.py                     # точка входа → ui.app:main
│   ├── core/                       # auth, audio_engine, config_manager,
│   │   │                           # mpris, notifications, playback_controller,
│   │   │                           # station, yandex_service
│   └── ui/                         # app, main_window, theme, tray,
│       ├── dialogs/                #   auth_dialog
│       ├── pages/                  #   wave, collection, search, settings
│       └── widgets/                #   chips, track_list, visualizer
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

## Дисклеймер

> Код предоставляется «как есть». Автор сам уже не помнит, как он работает.
> Удачи!
