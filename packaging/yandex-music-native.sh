#!/usr/bin/env bash
# System launcher for Yandex Music Native (freedesktop install).
# Prefers an installed Python package; falls back to repo-relative source.
set -euo pipefail

# When installed under PREFIX/lib, sources sit next to this script's real path
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
BIN_DIR="$(dirname "${SELF}")"
# Check for a sibling lib checkout: ../lib/yandex-music-native/src
CANDIDATES=(
  "${BIN_DIR}/../lib/yandex-music-native/src"
  "/usr/local/lib/yandex-music-native/src"
  "/usr/lib/yandex-music-native/src"
)

export PYTHONPATH="${PYTHONPATH:-}"

for cand in "${CANDIDATES[@]}"; do
  if [[ -f "${cand}/yamusic/app.py" ]]; then
    export PYTHONPATH="${cand}${PYTHONPATH:+:${PYTHONPATH}}"
    break
  fi
done

# Installed package?
if python3 -c "import yamusic.app" >/dev/null 2>&1; then
  exec python3 -c "from yamusic.app import main; raise SystemExit(main())" "$@"
fi

# Source tree fallback: walk up from this launcher to a checkout with src/yamusic
dir="$(cd "${BIN_DIR}" && pwd)"
for _ in $(seq 1 6); do
  if [[ -f "${dir}/src/yamusic/app.py" ]]; then
    exec python3 "${dir}/src/main.py" "$@"
  fi
  dir="$(dirname "${dir}")"
done

# Last resort: PYTHONPATH already set by Makefile install layout
if [[ -n "${PYTHONPATH}" ]]; then
  exec python3 -m yamusic "$@"
fi

echo "yandex-music-native: package not found (pip install or make install)" >&2
exit 1
