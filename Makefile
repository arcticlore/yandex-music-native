# yandex-music-native — GitHub repository Makefile
#
#   make run            — dev launch inside .venv (auto-created)
#   sudo make install   — /usr/bin, /usr/share/applications, /usr/share/icons/hicolor
#   make install-user   — same layout under ~/.local (no root)
#   sudo make uninstall — remove every installed program file
#
# No Flatpak/Snap. Optional: make build-deb / make appimage

APP         := yandex-music-native
PROJECT     := yandex-music-linux
PREFIX      ?= /usr
DESTDIR     ?=
PYTHON      ?= python3
VENV        ?= .venv
VENV_PY     := $(VENV)/bin/python
VENV_PIP    := $(VENV)/bin/pip
PIP         ?= $(PYTHON) -m pip
SRC         := src
PACKAGE     := packaging
ASSETS      := $(PACKAGE)/icons/hicolor
METAINFO    := org.arcticlore.YandexMusicNative.metainfo.xml
DIST        := dist

# system paths (freedesktop / XDG)
BINDIR      := $(PREFIX)/bin
APPDIR      := $(PREFIX)/share/applications
ICONDIR     := $(PREFIX)/share/icons/hicolor
METAINFODIR := $(PREFIX)/share/metainfo
DBUSDIR     := $(PREFIX)/share/dbus-1/services
LIBDIR      := $(PREFIX)/lib/$(APP)
# user-mode mirror
USER_PREFIX := $(HOME)/.local

.PHONY: all help venv run install install-user uninstall test test-all lint format \
        build-deb appimage clean distclean deps check

all: run

help:
	@echo "$(PROJECT) — targets:"
	@echo "  make run           run app in $(VENV)"
	@echo "  sudo make install  install to $(PREFIX)/..."
	@echo "  make install-user  install to $(USER_PREFIX)/..."
	@echo "  sudo make uninstall remove installed files"
	@echo "  make test | lint | format | check | build-deb | appimage | clean"
	@echo "  make test-all      pytest + MPRIS round-trip (private session bus)"

# --- venv + run -----------------------------------------------------------

venv: $(VENV_PY)

$(VENV_PY):
	$(PYTHON) -m venv --system-site-packages $(VENV)
	$(VENV_PIP) install -U pip setuptools wheel
	$(VENV_PIP) install -r requirements.txt
	$(VENV_PIP) install -e ".[dev]"

deps: $(VENV_PY)

run: $(VENV_PY)
	@# env of the caller (YML_AUDIO_AO, YML_LOG_LEVEL, ...) is passed through
	$(VENV_PY) $(SRC)/main.py

# --- system install (root) ------------------------------------------------
# bin → /usr/bin, .desktop → /usr/share/applications,
# icons → /usr/share/icons/hicolor, package → site-packages

install:
	install -d "$(DESTDIR)$(BINDIR)"
	install -d "$(DESTDIR)$(LIBDIR)"
	cp -a $(SRC)/core $(SRC)/ui "$(DESTDIR)$(LIBDIR)/"
	install -m 0755 $(SRC)/main.py "$(DESTDIR)$(LIBDIR)/main.py"
	find "$(DESTDIR)$(LIBDIR)" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	install -m 0755 $(PACKAGE)/$(APP).sh "$(DESTDIR)$(BINDIR)/$(APP)"
	install -d "$(DESTDIR)$(APPDIR)"
	install -m 0644 $(PACKAGE)/$(APP).desktop "$(DESTDIR)$(APPDIR)/$(APP).desktop"
	install -d "$(DESTDIR)$(METAINFODIR)"
	install -m 0644 $(PACKAGE)/$(APP).metainfo.xml \
	  "$(DESTDIR)$(METAINFODIR)/$(METAINFO)"
	install -d "$(DESTDIR)$(DBUSDIR)"
	install -m 0644 $(PACKAGE)/dbus/$(APP).service \
	  "$(DESTDIR)$(DBUSDIR)/$(APP).service"
	sed -i 's|/usr/local/bin|$(BINDIR)|' \
	  "$(DESTDIR)$(DBUSDIR)/$(APP).service" 2>/dev/null || true
	# hicolor icons (scalable SVG + any prebuilt PNGs)
	install -d "$(DESTDIR)$(ICONDIR)/scalable/apps"
	install -m 0644 $(ASSETS)/scalable/apps/$(APP).svg \
	  "$(DESTDIR)$(ICONDIR)/scalable/apps/$(APP).svg"
	@for size in 16 24 32 48 64 128 256; do \
	  src=$(ASSETS)/$${size}x$${size}/apps/$(APP).png; \
	  if [ -f "$$src" ]; then \
	    install -d "$(DESTDIR)$(ICONDIR)/$${size}x$${size}/apps"; \
	    install -m 0644 "$$src" \
	      "$(DESTDIR)$(ICONDIR)/$${size}x$${size}/apps/$(APP).png"; \
	  fi; \
	done
	# Python package into system site-packages
	$(PIP) install --break-system-packages --no-deps . \
	  2>/dev/null || $(PIP) install --no-deps . || true
	-command -v update-desktop-database >/dev/null 2>&1 && \
	  update-desktop-database "$(DESTDIR)$(APPDIR)" 2>/dev/null || true
	-command -v gtk-update-icon-cache >/dev/null 2>&1 && \
	  gtk-update-icon-cache -q "$(DESTDIR)$(ICONDIR)" 2>/dev/null || true
	@echo "Installed: $(BINDIR)/$(APP), $(APPDIR)/$(APP).desktop"

# --- user install (no root) → ~/.local ------------------------------------

install-user:
	$(MAKE) install PREFIX="$(USER_PREFIX)" DESTDIR=""
	@echo "Installed: $(USER_PREFIX)/bin/$(APP)"
	@echo "Ensure $(USER_PREFIX)/bin is on PATH"

# --- full removal ---------------------------------------------------------

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/$(APP)"
	rm -f "$(DESTDIR)$(APPDIR)/$(APP).desktop"
	rm -f "$(DESTDIR)$(METAINFODIR)/$(METAINFO)"
	rm -f "$(DESTDIR)$(DBUSDIR)/$(APP).service"
	rm -f "$(DESTDIR)$(ICONDIR)/scalable/apps/$(APP).svg"
	@for size in 16 24 32 48 64 128 256; do \
	  rm -f "$(DESTDIR)$(ICONDIR)/$${size}x$${size}/apps/$(APP).png"; \
	done
	rm -rf "$(DESTDIR)$(LIBDIR)"
	-$(PIP) uninstall -y $(PROJECT) 2>/dev/null || true
	-$(PIP) uninstall -y $(APP) 2>/dev/null || true
	-command -v update-desktop-database >/dev/null 2>&1 && \
	  update-desktop-database "$(DESTDIR)$(APPDIR)" 2>/dev/null || true
	@echo "Uninstalled program files under $(DESTDIR)$(PREFIX)"

uninstall-user:
	$(MAKE) uninstall PREFIX="$(USER_PREFIX)" DESTDIR=""

# --- quality / packaging --------------------------------------------------

# fast gate: syntax + import/lint errors only (F,E9) and formatting
lint:
	ruff check $(SRC) tests
	ruff format --check $(SRC) tests

# rewrite files in the canonical style
format:
	ruff format $(SRC) tests

# full local gate: same as CI
test: $(VENV_PY)
	QT_QPA_PLATFORM=offscreen YML_AUDIO_AO=null $(VENV_PY) -m pytest -q
	$(VENV_PY) -m compileall -q $(SRC) tests

# optional extra: MPRIS round-trip needs a private session bus
# (the live PCM→FFT→mpv pipeline already runs inside the pytest gate)
test-all: test
	dbus-run-session -- env QT_QPA_PLATFORM=offscreen YML_AUDIO_AO=null \
	  $(VENV_PY) tests/mpris_selftest.py

check: lint test

build-deb:
	bash packaging/debian/build-deb.sh

appimage:
	bash scripts/build-appimage.sh

clean:
	rm -rf $(DIST) build *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

distclean: clean
	rm -rf $(VENV)
