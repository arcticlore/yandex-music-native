"""The application window: sidebar, page stack and the transport bar.

The window owns no playback logic: it renders
:class:`~core.playback_controller.PlaybackController` state and forwards user
intent back to it, so the GUI stays replaceable and the controller remains
testable without Qt windows.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config_manager import ConfigManager
from core.playback_controller import PlaybackController, PlaybackState
from ui.pages.collection_page import CollectionPage
from ui.pages.search_page import SearchPage
from ui.pages.settings_page import SettingsPage
from ui.pages.wave_page import WavePage
from ui.theme import (
    ACCENT,
    COVER_SIZE,
    NAV_ITEM_HEIGHT,
    PLAY_BUTTON_SIZE,
    PLAYER_BAR_HEIGHT,
    SIDEBAR_WIDTH,
)
from ui.widgets.cover_frame import CoverFrame
from ui.widgets.like_button import LikeButton
from ui.widgets.track_list import format_duration

log = logging.getLogger(__name__)

APP_NAME = "Яндекс Музыка"
PAGES = (
    ("wave", "Моя волна"),
    ("collection", "Коллекция"),
    ("search", "Поиск"),
    ("settings", "Настройки"),
)
VISUALIZER_LABELS = {
    "spectrum": "Спектр",
    "wave": "Волна",
    "circular": "Круг",
}
SEEK_STEP_MS = 5000
THREAD_JOIN_MS = 1500
VOLUME_STEP = 5
LOSSLESS_BADGE = "FLAC Lossless"
MIN_WINDOW = (1120, 680)
SIDE_PANEL_WIDTH = 264
TITLE_WIDTH = 140


def quality_badge(track: object | None) -> str:
    """Short quality pill for the player bar: ``FLAC Lossless`` / ``HQ 320``."""
    if track is None:
        return ""
    if getattr(track, "lossless", False):
        return LOSSLESS_BADGE
    quality = str(getattr(track, "quality", "") or "").strip()
    if quality:
        return quality.upper()
    bitrate = int(getattr(track, "bitrate", 0) or 0)
    return f"{bitrate} kbps" if bitrate else ""


class VolumePanel(QWidget):
    """The right-hand section of the player bar.

    Qt delivers a wheel event to the widget under the cursor, so a filter on
    the panel alone would miss the mute button and the slider.  The panel
    therefore filters itself and every descendant, which makes «the wheel
    anywhere here changes the volume» true rather than nearly true.
    """

    wheel_step = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filtered: list[QObject] = []

    def wheel_step_for(self, delta: int) -> int:
        return VOLUME_STEP if delta > 0 else -VOLUME_STEP

    def _filter_descendants(self) -> None:
        for child in self.findChildren(QWidget):
            if child not in self._filtered:
                child.installEventFilter(self)
                self._filtered.append(child)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._filter_descendants()

    def event(self, event) -> bool:  # noqa: N802 - Qt naming
        # Qt does not run an event filter installed on the watched object
        # itself, so the panel handles its own wheel here and its children's
        # through the filter below.
        if event.type() == QEvent.Type.Wheel and self._take_wheel(event):
            return True
        return super().event(event)

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.Wheel and watched is not self and self._take_wheel(event):
            return True
        return super().eventFilter(watched, event)

    def _take_wheel(self, event) -> bool:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta:
            return False
        self.wheel_step.emit(self.wheel_step_for(delta))
        return True


class MainWindow(QMainWindow):
    """The shell: navigation on the left, pages in the middle, player below."""

    page_changed = Signal(str)
    logout_requested = Signal()

    def __init__(
        self,
        controller: PlaybackController,
        config: ConfigManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._config = config
        self._seeking = False
        self._closed = False
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(*MIN_WINDOW)
        self.resize(1180, 720)
        self._build_ui()
        self._connect_controller()
        self._install_shortcuts()
        self._refresh_all()

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        self.nav_buttons: dict[str, QPushButton] = {}
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {
            "wave": WavePage(self._controller),
            "collection": CollectionPage(self._controller),
            "search": SearchPage(self._controller),
            "settings": SettingsPage(self._config),
        }
        for key, _label in PAGES:
            self.stack.addWidget(self.pages[key])
        middle_layout.addWidget(self.stack, 1)
        middle_layout.addWidget(self._build_player_bar())
        root_layout.addWidget(middle, 1)
        self.setCentralWidget(root)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 20, 14, 16)
        layout.setSpacing(6)
        brand = QLabel("♪ Яндекс Музыка")
        brand.setObjectName("Brand")
        brand.setTextFormat(Qt.TextFormat.RichText)
        brand.setText(f'<span style="color:{ACCENT}">♪</span>&nbsp; Яндекс Музыка')
        layout.addWidget(brand)
        layout.addSpacing(14)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_rows: dict[str, QFrame] = {}
        for key, label in PAGES:
            row = QFrame()
            row.setObjectName("NavRow")
            row.setProperty("active", False)
            row.setFixedHeight(NAV_ITEM_HEIGHT)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(0)
            accent = QLabel()
            accent.setObjectName("NavAccent")
            accent.setProperty("active", False)
            accent.setFixedWidth(3)
            row_layout.addWidget(accent)
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, name=key: self.show_page(name))
            self.nav_group.addButton(button)
            row_layout.addWidget(button, 1)
            layout.addWidget(row)
            self.nav_buttons.setdefault(key, button)
            self.nav_rows.setdefault(key, row)
        layout.addStretch(1)
        layout.addWidget(self._build_profile_card())
        layout.addWidget(self._build_logout_button())
        return sidebar

    def _build_profile_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("ProfileCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        self.avatar_label = QLabel()
        self.avatar_label.setObjectName("ProfileAvatar")
        self.avatar_label.setFixedSize(40, 40)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.avatar_label)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.profile_label = QLabel("Не авторизован")
        self.profile_label.setObjectName("ProfileName")
        self.profile_label.setWordWrap(True)
        self.profile_hint = QLabel("Войдите, чтобы слушать")
        self.profile_hint.setObjectName("ProfileHint")
        text.addWidget(self.profile_label)
        text.addWidget(self.profile_hint)
        layout.addLayout(text, 1)

        self.plus_badge = QLabel("ПЛЮС")
        self.plus_badge.setObjectName("PlusBadge")
        self.plus_badge.setVisible(False)
        layout.addWidget(self.plus_badge, 0, Qt.AlignmentFlag.AlignTop)
        return card

    def _build_logout_button(self) -> QPushButton:
        self.logout_button = QPushButton("Выйти")
        self.logout_button.setObjectName("NavButton")
        self.logout_button.setToolTip("Выйти из аккаунта")
        self.logout_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logout_button.setFixedHeight(NAV_ITEM_HEIGHT)
        self.logout_button.clicked.connect(self.logout_requested.emit)
        return self.logout_button

    def _build_player_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("PlayerBar")
        bar.setFixedHeight(PLAYER_BAR_HEIGHT)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(16)
        layout.addWidget(self._build_now_playing(), 1)
        layout.addWidget(self._build_transport(), 3)
        layout.addWidget(self._build_volume_panel(), 1)
        return bar

    def _build_now_playing(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("NowPlaying")
        panel.setFixedWidth(SIDE_PANEL_WIDTH)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.cover_label = CoverFrame(COVER_SIZE)
        layout.addWidget(self.cover_label)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel("Ничего не играет")
        self.title_label.setObjectName("TrackTitle")
        self.title_label.setWordWrap(False)
        self.title_label.setMaximumWidth(TITLE_WIDTH)
        self.artist_label = QLabel("")
        self.artist_label.setObjectName("TrackArtist")
        self.artist_label.setMaximumWidth(TITLE_WIDTH)
        titles.addWidget(self.title_label)
        titles.addWidget(self.artist_label)
        layout.addLayout(titles)
        layout.addStretch(1)

        self.quality_badge = QLabel("")
        self.quality_badge.setObjectName("QualityBadge")
        self.quality_badge.setVisible(False)
        layout.addWidget(self.quality_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self.like_button = LikeButton()
        self.like_button.clicked.connect(self._on_like)
        layout.addWidget(self.like_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return panel

    def _build_transport(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("Transport")
        panel.setMinimumWidth(300)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        buttons = QHBoxLayout()
        buttons.setSpacing(14)
        buttons.addStretch(1)
        self.prev_button = QPushButton("⏮")
        self.prev_button.setObjectName("TransportButton")
        self.prev_button.setToolTip("Предыдущий трек")
        self.prev_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_button.setFixedSize(36, 36)
        self.prev_button.clicked.connect(self._controller.prev)
        buttons.addWidget(self.prev_button)

        self.play_button = QPushButton("▶")
        self.play_button.setObjectName("PlayButton")
        self.play_button.setToolTip("Играть / пауза")
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setFixedSize(PLAY_BUTTON_SIZE, PLAY_BUTTON_SIZE)
        self.play_button.clicked.connect(self._on_play)
        buttons.addWidget(self.play_button)

        self.next_button = QPushButton("⏭")
        self.next_button.setObjectName("TransportButton")
        self.next_button.setToolTip("Следующий трек")
        self.next_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_button.setFixedSize(36, 36)
        self.next_button.clicked.connect(self._controller.next)
        buttons.addWidget(self.next_button)
        buttons.addStretch(1)
        column.addLayout(buttons)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(10)
        self.position_label = QLabel("0:00")
        self.position_label.setObjectName("TimeLabel")
        self.position_label.setFixedWidth(38)
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        seek_row.addWidget(self.position_label)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("SeekSlider")
        self.seek_slider.setToolTip("Перемотка")
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setSingleStep(SEEK_STEP_MS)
        self.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        seek_row.addWidget(self.seek_slider, 1)
        self.duration_label = QLabel("0:00")
        self.duration_label.setObjectName("TimeLabel")
        self.duration_label.setFixedWidth(38)
        seek_row.addWidget(self.duration_label)
        column.addLayout(seek_row)
        return panel

    def _build_volume_panel(self) -> QWidget:
        self.volume_panel = VolumePanel()
        self.volume_panel.setObjectName("VolumePanel")
        self.volume_panel.setFixedWidth(SIDE_PANEL_WIDTH)
        self.volume_panel.setToolTip("Колесо мыши — громкость")
        layout = QHBoxLayout(self.volume_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addStretch(1)

        self.visualizer_button = QPushButton("Спектр")
        self.visualizer_button.setObjectName("ModeButton")
        self.visualizer_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.visualizer_button.setToolTip("Переключить визуализатор")
        self.visualizer_button.clicked.connect(self.cycle_visualizer)
        layout.addWidget(self.visualizer_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.mute_button = QPushButton("🔊")
        self.mute_button.setObjectName("TransportButton")
        self.mute_button.setToolTip("Выключить звук")
        self.mute_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_button.setFixedSize(32, 32)
        self.mute_button.clicked.connect(self._on_mute)
        layout.addWidget(self.mute_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("VolumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setSingleStep(VOLUME_STEP)
        self.volume_slider.setPageStep(VOLUME_STEP * 2)
        self.volume_slider.setFixedWidth(96)
        self.volume_slider.setToolTip("Громкость")
        self.volume_slider.valueChanged.connect(self._on_volume)
        layout.addWidget(self.volume_slider, 0, Qt.AlignmentFlag.AlignVCenter)
        self.volume_panel.wheel_step.connect(self._nudge_volume)
        return self.volume_panel

    def _connect_controller(self) -> None:
        controller = self._controller
        controller.track_changed.connect(self._refresh_track)
        controller.state_changed.connect(lambda _state: self._refresh_state())
        controller.position_changed.connect(self._on_position)
        controller.like_status_changed.connect(lambda *_: self._refresh_like())
        controller.cover_ready.connect(self._on_cover)
        controller.fft_data_ready.connect(self._on_fft)
        controller.waveform_data_ready.connect(self._on_waveform)
        controller.volume_changed.connect(lambda _value: self._refresh_volume())
        controller.seeked.connect(lambda _value: self._refresh_seek())
        controller.playback_error.connect(self._show_error)
        controller.wave_error.connect(self._show_error)
        settings: SettingsPage = self.pages["settings"]  # type: ignore[assignment]
        settings.visualizer_changed.connect(self.set_visualizer_mode)
        self.show_page("wave")

    def _install_shortcuts(self) -> None:
        for sequence, slot in (
            ("Ctrl+Q", self.close),
            ("Ctrl+F", lambda: self.show_page("search")),
            ("Ctrl+L", lambda: self.show_page("collection")),
            ("Space", self._on_play),
            ("Ctrl+Right", self._controller.next),
            ("Ctrl+Left", self._controller.prev),
            ("Ctrl+Up", lambda: self._controller.set_volume(self._controller.volume + 5)),
            ("Ctrl+Down", lambda: self._controller.set_volume(self._controller.volume - 5)),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(slot)

    # -- navigation ---------------------------------------------------------

    def show_page(self, key: str) -> str:
        """Show one of the four pages and remember it in the config."""
        if key not in self.pages:
            return self.current_page
        self.stack.setCurrentWidget(self.pages[key])
        button = self.nav_buttons.get(key)
        if button is not None and button.isCheckable():
            button.setChecked(True)
        self._mark_active(key)
        self._config.set("last_page", key)
        self.page_changed.emit(key)
        if key == "collection":
            page: CollectionPage = self.pages["collection"]  # type: ignore[assignment]
            page.refresh()
        return key

    @property
    def current_page(self) -> str:
        widget = self.stack.currentWidget()
        for key, page in self.pages.items():
            if page is widget:
                return key
        return PAGES[0][0]

    @property
    def wave_page(self) -> WavePage:
        return self.pages["wave"]  # type: ignore[return-value]

    def set_profile(self, login: str, detail: str = "") -> None:
        """Show the account card: name, hint and the gold «Плюс» badge."""
        text = login or "Не авторизован"
        self.profile_label.setText(text)
        signed_in = bool(login)
        hint = detail or ("" if signed_in else "Войдите, чтобы слушать")
        self.profile_hint.setText(hint)
        self.plus_badge.setVisible(bool(detail) and signed_in)
        self.logout_button.setEnabled(signed_in)

    # -- player bar ---------------------------------------------------------

    def set_visualizer_mode(self, mode: str) -> str:
        result = self.wave_page.set_visualizer_mode(mode)
        self.visualizer_button.setText(VISUALIZER_LABELS.get(result, result))
        return result

    def cycle_visualizer(self) -> str:
        modes = list(VISUALIZER_LABELS)
        current = self.wave_page.visualizer.mode
        index = modes.index(current) if current in modes else 0
        return self.set_visualizer_mode(modes[(index + 1) % len(modes)])

    def _on_play(self) -> None:
        if self._controller.current is None:
            self.wave_page.start_wave()
            return
        self._controller.toggle_play()
        self._refresh_state()

    def _on_like(self) -> None:
        if self._controller.current is None:
            return
        if self._controller.current.liked:
            self._controller.remove_like()
        else:
            self._controller.like()
        self._refresh_like()

    def _on_mute(self) -> None:
        if self._controller.volume > 0:
            self._last_volume = self._controller.volume
            self._controller.set_volume(0)
        else:
            self._controller.set_volume(getattr(self, "_last_volume", 80))

    def _on_volume(self, value: int) -> None:
        if value == self._controller.volume:
            return
        self._controller.set_volume(value)

    def _on_seek_moved(self, value: int) -> None:
        self._seeking = True
        self.position_label.setText(format_duration(value))

    def _on_seek_released(self) -> None:
        self._seeking = False
        self._controller.seek(self.seek_slider.value())
        self._refresh_seek()

    def _on_position(self, position_ms: int, duration_ms: int) -> None:
        if not self._seeking:
            self.seek_slider.setValue(position_ms)
            self.position_label.setText(format_duration(position_ms))
        self.seek_slider.setMaximum(max(0, duration_ms))
        self.duration_label.setText(format_duration(duration_ms))

    def _on_fft(self, values: object) -> None:
        self.wave_page.feed_spectrum(values)  # type: ignore[arg-type]

    def _on_waveform(self, values: object) -> None:
        self.wave_page.feed_waveform(values)  # type: ignore[arg-type]

    def _on_cover(self, track_id: str, path: str) -> None:
        current = self._controller.current
        if current is not None and current.id != track_id:
            return
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.cover_label.set_pixmap(pixmap)
            self.wave_page.set_cover_pixmap(pixmap)

    # -- controller mirroring -----------------------------------------------

    def _mark_active(self, key: str) -> None:
        """Light the pill and the 3px gold bar of the current page only."""
        for name, row in self.nav_rows.items():
            active = name == key
            accent = row.findChild(QLabel, "NavAccent")
            if row.property("active") == active:
                continue
            row.setProperty("active", active)
            row.style().unpolish(row)
            row.style().polish(row)
            if accent is not None:
                accent.setProperty("active", active)
                accent.style().unpolish(accent)
                accent.style().polish(accent)

    def _refresh_quality(self) -> None:
        """The badge next to the title: lossless, quality name or bitrate."""
        track = self._controller.current
        text = quality_badge(track)
        self.quality_badge.setText(text)
        self.quality_badge.setVisible(bool(text))

    def _refresh_all(self) -> None:
        self._refresh_track()
        self._refresh_state()
        self._refresh_volume()
        self._refresh_seek()
        self.set_visualizer_mode(self._config.get_visualizer())

    def _refresh_track(self) -> None:
        track = self._controller.current
        if track is None:
            self.title_label.setText("Ничего не играет")
            self.artist_label.setText("")
            self._refresh_quality()
            self._refresh_like()
            return
        self.title_label.setText(track.title)
        self.artist_label.setText(track.artists_name or track.album)
        self._refresh_quality()
        self._refresh_like()
        self._refresh_lists(track.id)

    def _refresh_lists(self, track_id: str) -> None:
        """Move the gold playing marker to ``track_id`` in every track list."""
        for key in ("collection", "search"):
            page = self.pages.get(key)
            if not isinstance(page, (CollectionPage, SearchPage)):
                continue
            for section in page.sections:
                widget = page.list_for(section)
                marker = getattr(widget, "set_tracks_playing", None)
                if callable(marker):
                    marker(track_id)

    def _refresh_like(self) -> None:
        track = self._controller.current
        self.like_button.setChecked(bool(track and track.liked))
        self.like_button.setEnabled(track is not None)

    def _refresh_state(self) -> None:
        state = self._controller.state
        if state == PlaybackState.PLAYING:
            self.play_button.setText("⏸")
        else:
            self.play_button.setText("▶")
        has_track = self._controller.current is not None
        self.play_button.setEnabled(has_track or state != PlaybackState.PAUSED)
        self.prev_button.setEnabled(has_track)
        self.next_button.setEnabled(has_track)

    def _refresh_volume(self) -> None:
        volume = self._controller.volume
        if self.volume_slider.value() != volume:
            blocked = self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(volume)
            self.volume_slider.blockSignals(blocked)
        self.mute_button.setText("🔇" if volume == 0 else "🔊")

    def _refresh_seek(self) -> None:
        duration = self._controller.duration_ms
        self.seek_slider.setMaximum(max(0, duration))
        if not self._seeking:
            self.seek_slider.setValue(self._controller.position_ms)
        self.duration_label.setText(format_duration(duration))

    def _nudge_volume(self, step: int) -> None:
        """A wheel notch over the right panel moves the volume by one step."""
        self._controller.set_volume(self._controller.volume + step)

    def _show_error(self, message: str) -> None:
        if message:
            self.statusBar().showMessage(message, 6000)

    # -- window -------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop everything this window owns; safe to call more than once.

        Playback, the visualizer clocks and the request thread are stopped in
        that order, then the thread is joined. Without the join Qt destroys a
        running ``QThread`` on exit, which aborts the process with
        ``QThread: Destroyed while thread is still running``.
        """
        if self._closed:
            return
        self._closed = True
        self.wave_page.visualizer.stop_all()
        self._controller.stop()
        self._controller.shutdown()
        service = self._controller.service
        service.shutdown()
        worker = service.worker
        worker.quit()
        worker.wait(THREAD_JOIN_MS)
        log.debug("window torn down in %s ms", THREAD_JOIN_MS)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._config.set_volume(self._controller.volume)
        self._config.set_visualizer(self.wave_page.visualizer.mode)
        self.shutdown()
        super().closeEvent(event)

    def restore_page(self) -> str:
        """Show the page the user left open last time."""
        return self.show_page(str(self._config.get("last_page", "wave") or "wave"))


__all__ = [
    "APP_NAME",
    "MIN_WINDOW",
    "PAGES",
    "SIDE_PANEL_WIDTH",
    "SEEK_STEP_MS",
    "THREAD_JOIN_MS",
    "VISUALIZER_LABELS",
    "VOLUME_STEP",
    "MainWindow",
    "quality_badge",
]
