# Yandex Music — native Linux client

A fully native desktop client, **no Electron / WebView / browser technologies**.
Python 3.11+, **PySide6 (Qt 6)**, **libmpv** (FLAC/lossless + HQ 320), **NumPy FFT**
(60 FPS), **yandex-music** 3.x, MPRIS2, system tray, desktop notifications.

**Forbidden:** Flatpak, Snap.  
**Allowed:** portable **AppImage**, source installation via `Makefile`, native
`.deb` and `.rpm` packages and `PKGBUILD` (AUR).

## Packages

| Channel | Command |
|---|---|
| **AppImage** | `make appimage` → `dist/yandex-music-native-*.AppImage` |
| **.deb** | `make build-deb` → `dist/yandex-music-native_*_amd64.deb` |
| **.rpm** | `make build-rpm` → `dist/yandex-music-native-*.noarch.rpm` |
| **PKGBUILD (AUR)** | `makepkg -si` from the repository root |
| **pip** | `pip install .` |

### RPM (Fedora / RHEL / openSUSE)

The spec lives in `packaging/rpm/yandex-music-native.spec` (`noarch`, pure
Python) and builds from the current checkout:

```bash
sudo dnf install rpm-build
make build-rpm            # add --source-only for an SRPM, --with-source for both
sudo dnf install ./yandex-music-native-1.0.0-1.*.noarch.rpm   # from the directory holding it
# openSUSE:
sudo zypper install ./yandex-music-native-1.0.0-1.*.noarch.rpm
```

The package installs the stack into `/usr/share/yandex-music-native/`, the
launcher into `/usr/bin/yandex-music-native`, plus the desktop entry, icon,
D-Bus service and AppStream metainfo. It requires `python3-pyside6`,
`python3-numpy`, `python3-requests`, `python3-keyring`, `pulseaudio-utils` and
libmpv (`mpv-libs` on Fedora/RHEL, `libmpv2` on openSUSE); `yandex-music` and
`python-mpv` are pip-installed from `%post` when the system does not ship them.

## Documentation

The full documentation (architecture, configuration, packaging, development
workflow) is written in Russian and lives in [README.md](README.md).

## License

MIT. Not an official Yandex product; uses the public API through the
`yandex-music` library.

## Disclaimer

> The code is provided “as is.” Even the author no longer remembers how it
> works. Good luck!
