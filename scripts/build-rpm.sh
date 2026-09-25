#!/usr/bin/env bash
# Build a native RPM for Yandex Music Native (Fedora / RHEL / openSUSE).
#
# Stages the sources into a release tarball, runs rpmbuild and copies the
# resulting packages to dist/:
#   dist/yandex-music-native-<version>-<release>.<arch>.rpm
#   dist/yandex-music-native-<version>-<release>.src.rpm   (--with-source)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="yandex-music-native"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "${ROOT}/pyproject.toml" | head -1)"
VERSION="${VERSION:-1.0.0}"
SPEC="${ROOT}/packaging/rpm/${NAME}.spec"
RPMBUILD="${RPMBUILD:-rpmbuild}"
TOPDIR="${TOPDIR:-${ROOT}/build/rpm}"
OUTDIR="${OUTDIR:-${ROOT}/dist}"
MODE="bb"
EXTRA=()

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [-- rpmbuild options]

  --source-only     build the SRPM only (rpmbuild -bs)
  --with-source     build the binary RPM and the SRPM (rpmbuild -ba)
  --topdir DIR      rpmbuild tree (default: build/rpm)
  --outdir DIR      where to copy the packages (default: dist)
  -h, --help        this help
EOF
}

die() {
  echo "$(basename "$0"): $*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --source-only) MODE="bs"; shift ;;
    --with-source) MODE="ba"; shift ;;
    --topdir) TOPDIR="${2:?--topdir needs a value}"; shift 2 ;;
    --outdir) OUTDIR="${2:?--outdir needs a value}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    --) shift; EXTRA+=("$@"); break ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done

command -v "${RPMBUILD}" >/dev/null 2>&1 || die \
  "rpmbuild not found: install rpm-build (Fedora/RHEL) or rpm (openSUSE)"
[[ -f "${SPEC}" ]] || die "missing spec: ${SPEC}"

SOURCES="${TOPDIR}/SOURCES"
STAGE="${SOURCES}/.stage"
PREFIX_DIR="${STAGE}/${NAME}-${VERSION}"
TARBALL="${SOURCES}/${NAME}-${VERSION}.tar.gz"

rm -rf "${STAGE}" "${TARBALL}"
mkdir -p "${PREFIX_DIR}" "${OUTDIR}"

# the tree the spec unpacks: application stack, packaging assets, metadata
cp -a "${ROOT}/src" "${ROOT}/packaging" "${PREFIX_DIR}/"
cp -a "${ROOT}/LICENSE" "${ROOT}/README.md" "${ROOT}/README.en.md" \
  "${ROOT}/pyproject.toml" "${ROOT}/requirements.txt" "${PREFIX_DIR}/"
find "${PREFIX_DIR}" \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \) \
  -exec rm -rf {} + 2>/dev/null || true

# reproducible tarball: sorted, root-owned, fixed mtime
MTIME="${SOURCE_DATE_EPOCH:-$(git -C "${ROOT}" log -1 --format=%ct 2>/dev/null || date +%s)}"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@${MTIME}" \
  -C "${STAGE}" -czf "${TARBALL}" "${NAME}-${VERSION}"
echo "Source tarball: ${TARBALL}"

"${RPMBUILD}" \
  --define "_topdir ${TOPDIR}" \
  --define "_sourcedir ${SOURCES}" \
  --define "version ${VERSION}" \
  -"${MODE}" "${SPEC}" ${EXTRA[@]+"${EXTRA[@]}"}

shopt -s nullglob
built=("${TOPDIR}"/RPMS/*/*.rpm "${TOPDIR}"/SRPMS/*.rpm)
((${#built[@]})) || die "rpmbuild produced no packages under ${TOPDIR}"
cp -f "${built[@]}" "${OUTDIR}/"

echo
echo "Packages in ${OUTDIR}:"
ls -lh "${OUTDIR}"/*.rpm
