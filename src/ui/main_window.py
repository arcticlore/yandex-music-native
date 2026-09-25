"""The application window: sidebar, page stack and the transport bar.

The window owns no playback logic: it renders
:class:`~core.playback_controller.PlaybackController` state and forwards user
intent back to it, so the GUI stays replaceable and the controller remains
testable without Qt windows.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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
from ui.widgets.track_list import format_duration

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
MIN_WINDOW = (1020, 660)


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
        sidebar.setFixedWidth(212)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 18)
        layout.setSpacing(8)
        brand = QLabel("♪ Яндекс Музыка")
        brand.setObjectName("Brand")
        layout.addWidget(brand)
        layout.addSpacing(10)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for key, label in PAGES:
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, name=key: self.show_page(name))
            self.nav_group.addButton(button)
            layout.addWidget(button)
            self.nav_buttons.setdefault(key, button)
        layout.addStretch(1)

        self.profile_label = QLabel("Не авторизован")
        self.profile_label.setObjectName("Dim")
        self.profile_label.setWordWrap(True)
        layout.addWidget(self.profile_label)
        self.logout_button = QPushButton("Выйти")
        self.logout_button.setObjectName("NavButton")
        self.logout_button.clicked.connect(self.logout_requested.emit)
        layout.addWidget(self.logout_button)
        return sidebar

    def _build_player_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("PlayerBar")
        bar.setFixedHeight(96)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(14)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(64, 64)
        self.cover_label.setObjectName("Cover")
        self.cover_label.setScaledContents(True)
        layout.addWidget(self.cover_label)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = QLabel("Ничего не играет")
        self.title_label.setObjectName("TrackTitle")
        self.artist_label = QLabel("")
        self.artist_label.setObjectName("TrackArtist")
        titles.addWidget(self.title_label)
        titles.addWidget(self.artist_label)
        layout.addLayout(titles, 1)

        self.like_button = QPushButton("♥")
        self.like_button.setObjectName("LikeButton")
        self.like_button.setCheckable(True)
        self.like_button.setToolTip("Нравится")
        self.like_button.clicked.connect(self._on_like)
        layout.addWidget(self.like_button)

        self.prev_button = QPushButton("⏮")
        self.prev_button.setToolTip("Предыдущий трек")
        self.prev_button.clicked.connect(self._controller.prev)
        layout.addWidget(self.prev_button)

        self.play_button = QPushButton("▶")
        self.play_button.setObjectName("Accent")
        self.play_button.setToolTip("Играть / пауза")
        self.play_button.clicked.connect(self._on_play)
        layout.addWidget(self.play_button)

        self.next_button = QPushButton("⏭")
        self.next_button.setToolTip("Следующий трек")
        self.next_button.clicked.connect(self._controller.next)
        layout.addWidget(self.next_button)

        seek_row = QVBoxLayout()
        seek_row.setSpacing(2)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        seek_row.addWidget(self.seek_slider)
        times = QHBoxLayout()
        self.position_label = QLabel("0:00")
        self.position_label.setObjectName("Dim")
        self.duration_label = QLabel("0:00")
        self.duration_label.setObjectName("Dim")
        times.addWidget(self.position_label)
        times.addStretch(1)
        times.addWidget(self.duration_label)
        seek_row.addLayout(times)
        layout.addLayout(seek_row, 2)

        self.mute_button = QPushButton("🔊")
        self.mute_button.setToolTip("Выключить звук")
        self.mute_button.clicked.connect(self._on_mute)
        layout.addWidget(self.mute_button)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(self._on_volume)
        layout.addWidget(self.volume_slider)

        self.visualizer_button = QPushButton("Спектр")
        self.visualizer_button.setToolTip("Переключить визуализатор")
        self.visualizer_button.clicked.connect(self.cycle_visualizer)
        layout.addWidget(self.visualizer_button)
        return bar

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
        text = login or "Не авторизован"
        self.profile_label.setText(f"{text}\n{detail}" if detail else text)

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
            self.cover_label.setPixmap(
                pixmap.scaled(
                    64,
                    64,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.wave_page.set_cover_pixmap(pixmap)

    # -- controller mirroring -----------------------------------------------

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
            return
        self.title_label.setText(track.title)
        self.artist_label.setText(track.artists_name or track.album)
        self._refresh_like()

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

    def _show_error(self, message: str) -> None:
        if message:
            self.statusBar().showMessage(message, 6000)

    # -- window -------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._config.set_volume(self._controller.volume)
        self._config.set_visualizer(self.wave_page.visualizer.mode)
        super().closeEvent(event)

    def restore_page(self) -> str:
        """Show the page the user left open last time."""
        return self.show_page(str(self._config.get("last_page", "wave") or "wave"))


__all__ = ["APP_NAME", "MIN_WINDOW", "PAGES", "SEEK_STEP_MS", "VISUALIZER_LABELS", "MainWindow"]
