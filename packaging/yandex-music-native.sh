#!/usr/bin/env bash
# System launcher for Yandex Music Native (freedesktop install).
#
# One launcher for every packaging channel: it prefers the sources copied into
# <prefix>/lib/yandex-music-native, then an installed Python package, then a
# source checkout next to this script.
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
BIN_DIR="$(dirname "${SELF}")"
PREFIX="$(dirname "${BIN_DIR}")"
NAME="yandex-music-native"
LIBRARY="${PREFIX}/lib/${NAME}"

# Sources installed by "make install" / build-deb / PKGBUILD.
if [[ -f "${LIBRARY}/main.py" ]]; then
  export PYTHONPATH="${LIBRARY}${PYTHONPATH:+:${PYTHONPATH}}"
  exec python3 "${LIBRARY}/main.py" "$@"
fi

# Package installed with "pip install ." → console entry point.
if python3 -c "import ui.app" >/dev/null 2>&1; then
  exec python3 -m ui.app "$@"
fi

# Source checkout: walk up from this launcher to a tree with src/main.py.
dir="${BIN_DIR}"
for _ in $(seq 1 6); do
  if [[ -f "${dir}/src/main.py" ]]; then
    exec python3 "${dir}/src/main.py" "$@"
  fi
  dir="$(dirname "${dir}")"
done

echo "${NAME}: neither ${LIBRARY} nor an installed package was found" >&2
echo "${NAME}: run 'make install', 'pip install .' or use a source checkout" >&2
exit 1
