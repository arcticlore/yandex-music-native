#!/usr/bin/env bash
# Build a native .deb for Yandex Music Native (Debian/Ubuntu, amd64).
# Produces dist/yandex-music-native_<ver>_amd64.deb
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAME="yandex-music-native"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "${ROOT}/pyproject.toml" | head -1)"
VERSION="${VERSION:-0.1.0}"
ARCH_DEB="amd64"
DIST="${ROOT}/dist"
PKG="${DIST}/${NAME}_${VERSION}_${ARCH_DEB}"
PREFIX="/usr"

rm -rf "${PKG}"
mkdir -p \
  "${PKG}/DEBIAN" \
  "${PKG}${PREFIX}/bin" \
  "${PKG}${PREFIX}/lib/${NAME}" \
  "${PKG}${PREFIX}/share/applications" \
  "${PKG}${PREFIX}/share/icons/hicolor/scalable/apps" \
  "${PKG}${PREFIX}/share/metainfo" \
  "${PKG}${PREFIX}/share/dbus-1/services"

# package sources
cp -a "${ROOT}/src/yamusic" "${PKG}${PREFIX}/lib/${NAME}/"
cp "${ROOT}/src/main.py" "${PKG}${PREFIX}/lib/${NAME}/main.py"
cp "${ROOT}/packaging/yandex-music-native.sh" "${PKG}${PREFIX}/bin/${NAME}"
chmod 0755 "${PKG}${PREFIX}/bin/${NAME}"
# rewrite fallback path for /usr layout
sed -i 's|/usr/local/lib/yandex-music-native/src|/usr/lib/yandex-music-native/src|g' \
  "${PKG}${PREFIX}/bin/${NAME}"

cp "${ROOT}/packaging/yandex-music-native.desktop" \
  "${PKG}${PREFIX}/share/applications/"
cp "${ROOT}/packaging/yandex-music-native.metainfo.xml" \
  "${PKG}${PREFIX}/share/metainfo/org.yamusic.YandexMusicNative.metainfo.xml"
cp "${ROOT}/packaging/icons/hicolor/scalable/apps/${NAME}.svg" \
  "${PKG}${PREFIX}/share/icons/hicolor/scalable/apps/"
cp "${ROOT}/packaging/dbus/${NAME}.service" \
  "${PKG}${PREFIX}/share/dbus-1/services/"
# deb installs under /usr, not /usr/local
sed -i 's|/usr/local/bin|/usr/bin|' "${PKG}${PREFIX}/share/dbus-1/services/${NAME}.service"

# deps: runtime Python packages via apt where available; rest via pip note
cat > "${PKG}/DEBIAN/control" <<EOF
Package: ${NAME}
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: ${ARCH_DEB}
Depends: python3 (>= 3.11), python3-pyside6.qtwidgets, python3-numpy,
 libmpv2 | libmpv1 | libmpv, pulseaudio-utils | pipewire-pulse
Recommends: python3-aiohttp
Maintainer: Yandex Music Native Contributors <dev@localhost>
Homepage: https://github.com/yandex-music-native/yandex-music-native
Description: Native Qt6/libmpv desktop client for Yandex Music
 Lightweight open-source Linux client without Electron or WebView.
 Streams lossless FLAC / HQ 320, integrates MPRIS2, tray and
 notifications, ships a 60 FPS FFT visualizer.
EOF

# postinst: pip install pure deps if missing (yandex-music, python-mpv)
cat > "${PKG}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if ! python3 -c "import yandex_music" >/dev/null 2>&1 \
   || ! python3 -c "import mpv" >/dev/null 2>&1; then
  echo "Installing Python dependencies (yandex-music, python-mpv)…"
  python3 -m pip install --break-system-packages -q \
    "yandex-music>=3.0" "python-mpv>=1.0.5" || \
  python3 -m pip install -q "yandex-music>=3.0" "python-mpv>=1.0.5" || true
fi
command -v update-desktop-database >/dev/null && \
  update-desktop-database /usr/share/applications || true
exit 0
EOF
chmod 0755 "${PKG}/DEBIAN/postinst"

cat > "${PKG}/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && \
  update-desktop-database /usr/share/applications || true
exit 0
EOF
chmod 0755 "${PKG}/DEBIAN/postrm"

dpkg-deb --build --root-owner-group "${PKG}" "${DIST}/${NAME}_${VERSION}_${ARCH_DEB}.deb"
echo "OK: ${DIST}/${NAME}_${VERSION}_${ARCH_DEB}.deb"
ls -lh "${DIST}/${NAME}_${VERSION}_${ARCH_DEB}.deb"
