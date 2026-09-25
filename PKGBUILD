# Maintainer: Yandex Music Native Contributors <dev@localhost>
# PKGBUILD for Arch Linux / AUR: yandex-music-native
pkgname=yandex-music-native
pkgver=1.0.0
pkgrel=1
pkgdesc="Native Qt6/libmpv desktop client for Yandex Music (no Electron)"
arch=('x86_64' 'aarch64')
url="https://github.com/arcticlore/yandex-music-native"
license=('MIT')
depends=(
  'python'
  'python-pyside6'
  'python-numpy'
  'python-aiohttp'
  'mpv'
  'libmpv'
  'hicolor-icon-theme'
)
optdepends=(
  'pulseaudio-utils: PCM tap for spectrum visualizer (parec)'
  'pipewire-pulse: PCM tap for spectrum visualizer (parec)'
  'python-secretstorage: Secret Service token storage'
)
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
source=("$pkgname-$pkgver.tar.gz::https://github.com/arcticlore/yandex-music-native/archive/refs/tags/v$pkgver.tar.gz")
sha256sums=('SKIP')

prepare() {
  cd "$pkgname-$pkgver"
  # pure-python deps not in official repos → vendored at build time via pip in package()
}

build() {
  cd "$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

package() {
  cd "$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl

  # launcher + desktop integration
  install -Dm755 packaging/yandex-music-native.sh \
    "$pkgdir/usr/bin/yandex-music-native"
  install -Dm644 packaging/yandex-music-native.desktop \
    "$pkgdir/usr/share/applications/yandex-music-native.desktop"
  install -Dm644 packaging/yandex-music-native.metainfo.xml \
    "$pkgdir/usr/share/metainfo/org.arcticlore.YandexMusicNative.metainfo.xml"
  install -Dm644 packaging/icons/hicolor/scalable/apps/yandex-music-native.svg \
    "$pkgdir/usr/share/icons/hicolor/scalable/apps/yandex-music-native.svg"
  install -Dm644 packaging/dbus/yandex-music-native.service \
    "$pkgdir/usr/share/dbus-1/services/yandex-music-native.service"
  # sources fallback path for the shell launcher (core + ui stack)
  install -dm755 "$pkgdir/usr/lib/yandex-music-native"
  cp -a src/core src/ui "$pkgdir/usr/lib/yandex-music-native/"
  install -Dm755 src/main.py "$pkgdir/usr/lib/yandex-music-native/main.py"

  # python deps packaged separately in AUR-style split or via depends
  install -d "$pkgdir/usr/lib/python3.12/site-packages"
}
