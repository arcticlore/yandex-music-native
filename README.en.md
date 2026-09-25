# Yandex Music — native Linux client

A fully native desktop client, **no Electron / WebView / browser technologies**.
Python 3.11+, **PySide6 (Qt 6)**, **libmpv** (FLAC/lossless + HQ 320), **NumPy FFT**
(60 FPS), **yandex-music** 3.x, MPRIS2, system tray, desktop notifications.

**Forbidden:** Flatpak, Snap.  
**Allowed:** portable **AppImage**, source installation via `Makefile`, native
`.deb` packages and `PKGBUILD` (AUR).

## Documentation

The full documentation (architecture, configuration, packaging, development
workflow) is written in Russian and lives in [README.md](README.md).

## License

MIT. Not an official Yandex product; uses the public API through the
`yandex-music` library.

## Disclaimer

> The code is provided “as is.” Even the author no longer remembers how it
> works. Good luck!
