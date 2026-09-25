Name:           yandex-music-native
Version:        1.0.0
Release:        1%{?dist}
Summary:        Native Qt6/libmpv desktop client for Yandex Music

License:        MIT
URL:            https://github.com/arcticlore/yandex-music-native
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3 >= 3.11

# Pure Python application: nothing is compiled, the tarball carries the tree.
Requires:       python3 >= 3.11
Requires:       python3-pyside6
Requires:       python3-numpy
Requires:       python3-requests
Requires:       python3-keyring
Requires:       pulseaudio-utils
# libmpv is mpv-libs on Fedora/RHEL and libmpv2 on openSUSE
Requires:       (mpv-libs or libmpv2 or libmpv)
# yandex-music pulls requests[socks]; the proxy support is a soft dependency
Recommends:     python3-pysocks

%description
Lightweight native desktop client for Yandex Music built with PySide6 (Qt 6),
libmpv and NumPy. No Electron and no WebView. Plays lossless FLAC and HQ 320,
exposes MPRIS2 with media keys, a system tray and desktop notifications, and
renders a 60 FPS FFT visualizer.

%prep
%autosetup -n %{name}-%{version}

# no build step: the application is pure Python

%check
# syntax-check every shipped module, keeping the bytecode out of the package
PYTHONPYCACHEPREFIX=%{_tmppath}/pycache %{__python3} -m compileall -q main.py core ui

%install
# sources go to /usr/share: a noarch package must not write into /usr/lib
install -d %{buildroot}%{_datadir}/%{name}
cp -a src/core src/ui %{buildroot}%{_datadir}/%{name}/
install -m 0755 src/main.py %{buildroot}%{_datadir}/%{name}/main.py
find %{buildroot}%{_datadir}/%{name} -type d -name __pycache__ -exec rm -rf {} +

install -d %{buildroot}%{_bindir}
install -m 0755 packaging/%{name}.sh %{buildroot}%{_bindir}/%{name}

install -d %{buildroot}%{_datadir}/applications
install -m 0644 packaging/%{name}.desktop %{buildroot}%{_datadir}/applications/%{name}.desktop

install -d %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -m 0644 packaging/icons/hicolor/scalable/apps/%{name}.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg

install -d %{buildroot}%{_datadir}/dbus-1/services
sed -e 's|/usr/local/bin/%{name}|%{_bindir}/%{name}|' \
  packaging/dbus/%{name}.service > %{buildroot}%{_datadir}/dbus-1/services/%{name}.service

install -d %{buildroot}%{_datadir}/metainfo
install -m 0644 packaging/%{name}.metainfo.xml \
  %{buildroot}%{_datadir}/metainfo/org.arcticlore.YandexMusicNative.metainfo.xml

%post
# desktop and icon caches: the tools stay optional
command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database -q %{_datadir}/applications || :
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -q %{_datadir}/icons/hicolor || :
# python-mpv and yandex-music are not packaged by Fedora or openSUSE
if ! %{__python3} -c "import mpv" >/dev/null 2>&1 || \
   ! %{__python3} -c "import yandex_music" >/dev/null 2>&1; then
  echo "Installing the pip-only dependencies (yandex-music, python-mpv)..."
  %{__python3} -m pip install --quiet --break-system-packages \
    "yandex-music>=3.0" "python-mpv>=1.0.5" || \
  %{__python3} -m pip install --quiet "yandex-music>=3.0" "python-mpv>=1.0.5" || :
fi

%postun
command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database -q %{_datadir}/applications || :
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -q %{_datadir}/icons/hicolor || :

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_datadir}/%{name}/main.py
%{_datadir}/%{name}/core
%{_datadir}/%{name}/ui
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
%{_datadir}/dbus-1/services/%{name}.service
%{_datadir}/metainfo/org.arcticlore.YandexMusicNative.metainfo.xml

%changelog
* Fri Sep 25 2026 Yandex Music Linux Contributors <arcticlore@users.noreply.github.com> - 1.0.0-1
- First RPM release for Fedora, RHEL and openSUSE: the yamusic package is
  gone, the core/ui stack ships with a launcher, desktop entry, icon, D-Bus
  service and AppStream metadata.
