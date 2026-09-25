#!/usr/bin/env bash
# Build a portable AppImage for Yandex Music Native (x86_64).
#
# Channels: linuxdeploy + plugin-qt (preferred) or appimagetool fallback.
# Bundles: app sources, Python deps (user-site), Qt6 plugins when available.
# System libs (libmpv, glibc) are resolved at runtime via host — for a fully
# static bundle install libmpv into AppDir/usr/lib and set LD_LIBRARY_PATH.
#
# Usage: scripts/build-appimage.sh
# Env:   APPIMAGE_TOOL, LINUXDEPLOY, ARCH (default x86_64)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="${ARCH:-x86_64}"
DIST="${ROOT}/dist"
STAGE="${DIST}/yandex-music-native.AppDir"
APP="${APPIMAGE_TOOL:-appimagetool}"
NAME="yandex-music-native"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "${ROOT}/pyproject.toml" | head -1)"
VERSION="${VERSION:-1.0.0}"

mkdir -p "${DIST}"
rm -rf "${STAGE}"
mkdir -p \
  "${STAGE}/usr/bin" \
  "${STAGE}/usr/lib/${NAME}" \
  "${STAGE}/usr/share/applications" \
  "${STAGE}/usr/share/icons/hicolor/scalable/apps" \
  "${STAGE}/usr/share/metainfo" \
  "${STAGE}/usr/lib/python3.12/site-packages" 2>/dev/null || true

# --- sources: the core + ui stack, started by src/main.py -------------------
cp -a "${ROOT}/src/core" "${ROOT}/src/ui" "${STAGE}/usr/lib/${NAME}/"
cp "${ROOT}/src/main.py" "${STAGE}/usr/lib/${NAME}/main.py"
find "${STAGE}/usr/lib/${NAME}" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
cp "${ROOT}/packaging/yandex-music-native.desktop" "${STAGE}/usr/share/applications/"
cp "${ROOT}/packaging/yandex-music-native.metainfo.xml" \
  "${STAGE}/usr/share/metainfo/org.arcticlore.YandexMusicNative.metainfo.xml"
cp "${ROOT}/packaging/icons/hicolor/scalable/apps/${NAME}.svg" \
  "${STAGE}/usr/share/icons/hicolor/scalable/apps/"
# AppImage root desktop/icon (required by appimagetool)
cp "${ROOT}/packaging/yandex-music-native.desktop" "${STAGE}/"
cp "${ROOT}/packaging/icons/hicolor/scalable/apps/${NAME}.svg" "${STAGE}/${NAME}.svg"

# --- python deps (pure wheels + manylinux binaries) -----------------------
SITE="${STAGE}/usr/lib/${NAME}/_deps"
mkdir -p "${SITE}"
python3 -m pip install --quiet --upgrade --target "${SITE}" \
  -r "${ROOT}/requirements.txt"

# --- optional: host libmpv into AppDir (best effort) ----------------------
MPV_LIB="$(python3 - <<'PY'
import ctypes.util, os
p = ctypes.util.find_library("mpv")
if p:
    print(os.path.realpath(p) if os.path.isabs(p) else p)
PY
)"
if [[ -n "${MPV_LIB}" && -f "${MPV_LIB}" ]]; then
  mkdir -p "${STAGE}/usr/lib"
  cp -aL "${MPV_LIB}" "${STAGE}/usr/lib/" 2>/dev/null || true
fi

# --- AppRun ----------------------------------------------------------------
cat > "${STAGE}/AppRun" <<EOF
#!/usr/bin/env bash
set -euo pipefail
HERE="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="\${HERE}/usr/lib/${NAME}:\${HERE}/usr/lib/${NAME}/_deps\${PYTHONPATH:+:\${PYTHONPATH}}"
export LD_LIBRARY_PATH="\${HERE}/usr/lib\${LD_LIBRARY_PATH:+:\${LD_LIBRARY_PATH}}"
export XDG_DATA_DIRS="\${HERE}/usr/share\${XDG_DATA_DIRS:+:\${XDG_DATA_DIRS}}"
export QT_PLUGIN_PATH="\${QT_PLUGIN_PATH:-}"
exec python3 "\${HERE}/usr/lib/${NAME}/main.py" "\$@"
EOF
chmod +x "${STAGE}/AppRun"

# desktop file must use AppRun-style icon name at root
sed -i 's|^Exec=.*|Exec=AppRun|' "${STAGE}/${NAME}.desktop" 2>/dev/null || true

# --- package ---------------------------------------------------------------
OUT="${DIST}/${NAME}-${VERSION}-${ARCH}.AppImage"
ARCH="${ARCH}" "${APP}" "${STAGE}" "${OUT}" || {
  echo "appimagetool failed — AppDir left at ${STAGE}" >&2
  exit 1
}
echo "OK: ${OUT}"
ls -lh "${OUT}"
