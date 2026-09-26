"""The application window: sidebar, page stack and the transport bar.

The window owns no playback logic: it renders
:class:`~core.playback_controller.PlaybackController` state and forwards user
intent back to it, so the GUI stays replaceable and the controller remains
testable without Qt windows.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
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
    BADGE_HEIGHT,
    COVER_SIZE,
    ICON_BUTTON,
    NAV_ITEM_HEIGHT,
    PLAYER_BAR_HEIGHT,
    PLAYER_COVER_SIZE,
    PLAYER_LEFT_WIDTH,
    PLAYER_RIGHT_WIDTH,
    PLAY_BUTTON_SIZE,
    SIDEBAR_WIDTH,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
    TIME_LABEL_WIDTH,
    VOLUME_SLIDER_WIDTH,
)
from ui.widgets.cover_frame import CoverFrame
from ui.widgets.icons import pause as pause_icon
from ui.widgets.icons import play as play_icon
from ui.widgets.icons import (
    repeat as repeat_icon,
)
from ui.widgets.icons import (
    shuffle as shuffle_icon,
)
from ui.widgets.icons import (
    skip as skip_icon,
)
from ui.widgets.icons import (
    speaker as speaker_icon,
)
from ui.widgets.icons import visualizer_icon
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
REPEAT_LABELS = {"off": "выключен", "all": "всё", "one": "трек"}
SEEK_STEP_MS = 5000
THREAD_JOIN_MS = 1500
VOLUME_STEP = 5
LOSSLESS_BADGE = "FLAC"
LOSSY_BADGE = "HQ"
MIN_WINDOW = (1120, 680)
SIDE_PANEL_WIDTH = PLAYER_RIGHT_WIDTH
"""The fixed width of the right-hand section, kept as a public alias."""

PLAYER_MARGIN = 20
PLAYER_PAD_Y = 14
PLAYER_SECTION_GAP = 24
SEEK_ROW_HEIGHT = 20
MIN_ELIDE_WIDTH = 96
STATUS_BAR_HEIGHT = 28
TRANSPORT_MIN_WIDTH = 280
MODE_BUTTON_TOOLTIP = "Режим визуализации"


def quality_badge(track: object | None) -> str:
    """Short quality pill for the player bar: ``FLAC`` or ``HQ``.

    The bar has 260px for the whole left section, so the pill carries the flag
    and not the bitrate; the full wording stays available as the tooltip.
    """
    if track is None:
        return ""
    if getattr(track, "lossless", False):
        return LOSSLESS_BADGE
    return LOSSY_BADGE


def quality_detail(track: object | None) -> str:
    """The long form of :func:`quality_badge`, shown in the pill's tooltip."""
    if track is None:
        return ""
    if getattr(track, "lossless", False):
        return "FLAC Lossless"
    quality = str(getattr(track, "quality", "") or "").strip()
    if quality:
        return quality.upper()
    bitrate = int(getattr(track, "bitrate", 0) or 0)
    return f"{bitrate} kbps" if bitrate else LOSSY_BADGE


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
        self._title_text = ""
        self._artist_text = ""
        self._profile_text = "Не авторизован"
        self._profile_hint_text = ""
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(*MIN_WINDOW)
        self.resize(1180, 720)
        self._build_ui()
        self._connect_controller()
        self._install_shortcuts()
        self._refresh_all()

    # -- construction -------------------------------------------------------

    @staticmethod
    def _elided(label: QLabel, text: str) -> str:
        """``text`` shortened with an ellipsis to what ``label`` can show.

        Qt does not elide a ``QLabel`` on its own, and a fixed maximum width
        either truncates the title in a wide window or clips it in a narrow one.
        The full string stays in the tooltip, so nothing becomes unreachable.
        """
        full = text or ""
        metrics = QFontMetrics(label.font())
        width = max(label.width(), MIN_ELIDE_WIDTH)
        return metrics.elidedText(full, Qt.TextElideMode.ElideRight, width)

    def _eliding_label(self, object_name: str, text: str) -> QLabel:
        """A single-line label that elides itself when the window is resized."""
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setWordWrap(False)
        label.setMinimumWidth(MIN_ELIDE_WIDTH)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setToolTip(text)
        return label

    def _refresh_elided_texts(self) -> None:
        """Re-elide the labels whose width follows the window."""
        for label, text in (
            (self.title_label, self._title_text),
            (self.artist_label, self._artist_text),
            (self.profile_label, self._profile_text),
            (self.profile_hint, self._profile_hint_text),
        ):
            if text:
                label.setText(self._elided(label, text))

    def _set_elided(self, label: QLabel, attribute: str, text: str) -> None:
        setattr(self, attribute, text)
        label.setToolTip(text)
        label.setText(self._elided(label, text))

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
        # A fixed-height message band: the footer is part of the frame, so a
        # message appearing must not push the layout around.
        self.statusBar().setFixedHeight(STATUS_BAR_HEIGHT)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(SPACE_MD, SPACE_XL, SPACE_MD, SPACE_LG)
        layout.setSpacing(SPACE_SM)
        brand = QLabel("♪ Яндекс Музыка")
        brand.setObjectName("Brand")
        brand.setTextFormat(Qt.TextFormat.RichText)
        brand.setText(f'<span style="color:{ACCENT}">♪</span>&nbsp; Яндекс Музыка')
        layout.addWidget(brand)
        layout.addSpacing(SPACE_LG)

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
        layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        layout.setSpacing(SPACE_MD)
        self.avatar_label = QLabel()
        self.avatar_label.setObjectName("ProfileAvatar")
        self.avatar_label.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.avatar_label)

        text = QVBoxLayout()
        text.setSpacing(SPACE_XS)
        text.setContentsMargins(0, 0, 0, 0)
        self.profile_label = QLabel("Не авторизован")
        self.profile_label.setObjectName("ProfileName")
        self.profile_label.setWordWrap(False)
        self.profile_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.profile_hint = QLabel("Войдите, чтобы слушать")
        self.profile_hint.setObjectName("ProfileHint")
        self.profile_hint.setWordWrap(False)
        self.profile_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self.profile_label)
        text.addWidget(self.profile_hint)
        layout.addLayout(text, 1)

        # The badge sits in the second line of the card, so a long login cannot
        # push it out of the sidebar and a «ПЛЮС» cannot overlap the name.
        self.plus_badge = QLabel("ПЛЮС")
        self.plus_badge.setObjectName("PlusBadge")
        self.plus_badge.setVisible(False)
        # The fixed height is what makes the painted radius the one the token
        # asks for: a label sized by its font comes out taller than BADGE_HEIGHT
        # and the pill comes out squashed.
        self.plus_badge.setFixedHeight(BADGE_HEIGHT)
        self.plus_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._profile_badge_row = QHBoxLayout()
        self._profile_badge_row.setContentsMargins(0, 0, 0, 0)
        self._profile_badge_row.setSpacing(SPACE_XS)
        self._profile_badge_row.addWidget(self.plus_badge)
        self._profile_badge_row.addStretch(1)
        text.addLayout(self._profile_badge_row)
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
        """The classic three-section bar: metadata, transport, volume.

        The two outer sections are fixed, so the transport column is the only
        thing that reflows; the seek row underneath it always spans the whole
        centre, which is what makes the bar read as one object rather than three
        floating panels.
        """
        bar = QFrame()
        bar.setObjectName("PlayerBar")
        bar.setFixedHeight(PLAYER_BAR_HEIGHT)
        self.player_bar = bar
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(PLAYER_MARGIN, PLAYER_PAD_Y, PLAYER_MARGIN, PLAYER_PAD_Y)
        layout.setSpacing(PLAYER_SECTION_GAP)
        layout.addWidget(self._build_now_playing(), 0)
        layout.addWidget(self._build_transport(), 1)
        layout.addWidget(self._build_volume_panel(), 0)
        return bar

    def _build_now_playing(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("NowPlaying")
        panel.setFixedWidth(PLAYER_LEFT_WIDTH)
        panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_MD)
        self.cover_label = CoverFrame(PLAYER_COVER_SIZE)
        layout.addWidget(self.cover_label, 0, Qt.AlignmentFlag.AlignVCenter)

        titles = QVBoxLayout()
        titles.setSpacing(SPACE_XS)
        titles.setContentsMargins(0, 0, 0, 0)
        # The like button shares the title line: the heart belongs next to the
        # name it likes, and the artist line below keeps the FLAC / HQ pill.
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(SPACE_XS)
        self.title_label = self._eliding_label("TrackTitle", "Ничего не играет")
        title_row.addWidget(self.title_label, 1)
        self.like_button = LikeButton()
        self.like_button.clicked.connect(self._on_like)
        title_row.addWidget(self.like_button, 0, Qt.AlignmentFlag.AlignVCenter)
        titles.addLayout(title_row)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(SPACE_SM)
        self.artist_label = self._eliding_label("TrackArtist", "")
        meta_row.addWidget(self.artist_label, 1)
        self.quality_badge = QLabel("")
        self.quality_badge.setObjectName("QualityBadge")
        self.quality_badge.setVisible(False)
        self.quality_badge.setFixedHeight(BADGE_HEIGHT)
        self.quality_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta_row.addWidget(self.quality_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        titles.addLayout(meta_row)
        layout.addLayout(titles, 1)
        return panel

    def _transport_button(
        self,
        name: str,
        tooltip: str,
        icon: QIcon,
        slot,
    ) -> QPushButton:
        button = QPushButton()
        button.setObjectName(name)
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(ICON_BUTTON, ICON_BUTTON)
        button.setIconSize(QSize(ICON_BUTTON - 16, ICON_BUTTON - 16))
        button.clicked.connect(slot)
        return button

    def _build_transport(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("Transport")
        panel.setMinimumWidth(TRANSPORT_MIN_WIDTH)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_MD)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_MD)
        buttons.addStretch(1)
        self.shuffle_button = self._transport_button(
            "ShuffleButton", "Перемешать", shuffle_icon(ICON_BUTTON - 12), self._on_shuffle
        )
        buttons.addWidget(self.shuffle_button)
        self.prev_button = self._transport_button(
            "TransportButton",
            "Предыдущий трек",
            skip_icon(ICON_BUTTON - 12, backwards=True),
            self._controller.prev,
        )
        buttons.addWidget(self.prev_button)

        self.play_button = QPushButton()
        self.play_button.setObjectName("PlayButton")
        self.play_button.setIcon(play_icon(18))
        self.play_button.setIconSize(QSize(18, 18))
        self.play_button.setToolTip("Играть / пауза")
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setFixedSize(PLAY_BUTTON_SIZE, PLAY_BUTTON_SIZE)
        self.play_button.clicked.connect(self._on_play)
        buttons.addWidget(self.play_button)

        self.next_button = self._transport_button(
            "TransportButton",
            "Следующий трек",
            skip_icon(ICON_BUTTON - 12),
            self._controller.next,
        )
        buttons.addWidget(self.next_button)
        self.repeat_button = self._transport_button(
            "RepeatButton", "Повтор", repeat_icon(ICON_BUTTON - 12), self._on_repeat
        )
        buttons.addWidget(self.repeat_button)
        buttons.addStretch(1)
        column.addLayout(buttons)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(SPACE_MD)
        self.position_label = QLabel("0:00")
        self.position_label.setObjectName("TimeLabel")
        self.position_label.setFixedWidth(TIME_LABEL_WIDTH)
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        seek_row.addWidget(self.position_label)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("SeekSlider")
        self.seek_slider.setToolTip("Перемотка")
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setFixedHeight(SEEK_ROW_HEIGHT)
        self.seek_slider.setSingleStep(SEEK_STEP_MS)
        self.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        seek_row.addWidget(self.seek_slider, 1)
        self.duration_label = QLabel("0:00")
        self.duration_label.setObjectName("TimeLabel")
        self.duration_label.setFixedWidth(TIME_LABEL_WIDTH)
        seek_row.addWidget(self.duration_label)
        column.addLayout(seek_row)
        return panel

    def _build_volume_panel(self) -> QWidget:
        self.volume_panel = VolumePanel()
        self.volume_panel.setObjectName("VolumePanel")
        self.volume_panel.setFixedWidth(PLAYER_RIGHT_WIDTH)
        self.volume_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.volume_panel.setToolTip("Колесо мыши — громкость")
        layout = QHBoxLayout(self.volume_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_MD)

        # The visualiser mode is an icon, not a word: 240px has to hold a button,
        # a speaker and a slider, and the tooltip still names the mode.
        self.visualizer_button = QPushButton()
        self.visualizer_button.setObjectName("ModeButton")
        self.visualizer_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.visualizer_button.setToolTip(MODE_BUTTON_TOOLTIP)
        self.visualizer_button.setCheckable(True)
        self.visualizer_button.setFixedSize(ICON_BUTTON, ICON_BUTTON)
        self.visualizer_button.clicked.connect(self.cycle_visualizer)
        layout.addWidget(self.visualizer_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        self.mute_button = QPushButton()
        self.mute_button.setObjectName("TransportButton")
        self.mute_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_button.setFixedSize(ICON_BUTTON, ICON_BUTTON)
        self.mute_button.setIconSize(QSize(ICON_BUTTON - 16, ICON_BUTTON - 16))
        self.mute_button.clicked.connect(self._on_mute)
        layout.addWidget(self.mute_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("VolumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setSingleStep(VOLUME_STEP)
        self.volume_slider.setPageStep(VOLUME_STEP * 2)
        self.volume_slider.setFixedWidth(VOLUME_SLIDER_WIDTH)
        self.volume_slider.setToolTip("Громкость")
        self.volume_slider.valueChanged.connect(self._on_volume)
        layout.addWidget(self.volume_slider, 0, Qt.AlignmentFlag.AlignVCenter)
        self.volume_panel.wheel_step.connect(self._nudge_volume)
        self._set_mute_icon(False)
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
        # A click on the stage changes the style behind the button, so the icon
        # and the tooltip follow it in both directions.
        self.wave_page.visualizer_mode_changed.connect(lambda _mode: self._refresh_mode_button())
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
        self._set_elided(self.profile_label, "_profile_text", text)
        signed_in = bool(login)
        hint = detail or ("" if signed_in else "Войдите, чтобы слушать")
        self._set_elided(self.profile_hint, "_profile_hint_text", hint)
        self.plus_badge.setVisible(bool(detail) and signed_in)
        self.logout_button.setEnabled(signed_in)

    # -- player bar ---------------------------------------------------------

    def set_visualizer_mode(self, mode: str) -> str:
        result = self.wave_page.set_visualizer_mode(mode)
        self._refresh_mode_button()
        return result

    def cycle_visualizer(self) -> str:
        """Advance the stage to the next style, from the button or a click."""
        return self.set_visualizer_mode(self.wave_page.visualizer.cycle_mode())

    def _refresh_mode_button(self) -> None:
        """Keep the mode button's icon and tooltip in step with the stage."""
        mode = self.wave_page.visualizer.mode
        self.visualizer_button.setIcon(visualizer_icon(mode, ICON_BUTTON - 12))
        label = VISUALIZER_LABELS.get(mode, mode)
        self.visualizer_button.setToolTip(f"{MODE_BUTTON_TOOLTIP}: {label}")
        self.visualizer_button.setChecked(mode == "circular")

    def _on_shuffle(self) -> None:
        self._controller.toggle_shuffle()
        self._refresh_queue_buttons()

    def _on_repeat(self) -> None:
        self._controller.cycle_repeat()
        self._refresh_queue_buttons()

    def _refresh_queue_buttons(self) -> None:
        """Show the shuffle and repeat state as lit or unlit buttons."""
        shuffle = self._controller.shuffle
        self.shuffle_button.setChecked(shuffle)
        self.shuffle_button.setToolTip("Перемешать: вкл" if shuffle else "Перемешать")
        repeat = self._controller.repeat
        self.repeat_button.setChecked(repeat != "off")
        self.repeat_button.setToolTip(f"Повтор: {REPEAT_LABELS.get(repeat, repeat)}")

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
        """The badge next to the title: FLAC for lossless, HQ otherwise."""
        track = self._controller.current
        text = quality_badge(track)
        self.quality_badge.setText(text)
        self.quality_badge.setVisible(bool(text))
        self.quality_badge.setToolTip(quality_detail(track))
        lossless = bool(track is not None and getattr(track, "lossless", False))
        self.quality_badge.setProperty("lossless", "true" if lossless else "false")
        self.quality_badge.style().unpolish(self.quality_badge)
        self.quality_badge.style().polish(self.quality_badge)

    def _refresh_all(self) -> None:
        self._refresh_track()
        self._refresh_state()
        self._refresh_volume()
        self._refresh_seek()
        self._refresh_queue_buttons()
        self.set_visualizer_mode(self._config.get_visualizer())

    def _refresh_track(self) -> None:
        track = self._controller.current
        if track is None:
            self._set_elided(self.title_label, "_title_text", "Ничего не играет")
            self._set_elided(self.artist_label, "_artist_text", "")
            self._refresh_quality()
            self._refresh_like()
            return
        self._set_elided(self.title_label, "_title_text", track.title)
        self._set_elided(self.artist_label, "_artist_text", track.artists_name or track.album)
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
        playing = state == PlaybackState.PLAYING
        self.play_button.setIcon(pause_icon(18) if playing else play_icon(18))
        has_track = self._controller.current is not None
        self.play_button.setEnabled(has_track or state != PlaybackState.PAUSED)
        self.prev_button.setEnabled(has_track)
        self.next_button.setEnabled(has_track)

    def _set_mute_icon(self, muted: bool) -> None:
        self.mute_button.setIcon(speaker_icon(ICON_BUTTON - 16, muted=muted))
        self.mute_button.setToolTip("Включить звук" if muted else "Выключить звук")

    def _refresh_volume(self) -> None:
        volume = self._controller.volume
        if self.volume_slider.value() != volume:
            blocked = self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(volume)
            self.volume_slider.blockSignals(blocked)
        self._set_mute_icon(volume == 0)

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
        self._config.set_shuffle(self._controller.shuffle)
        self._config.set_repeat(self._controller.repeat)
        self.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._refresh_elided_texts()

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
