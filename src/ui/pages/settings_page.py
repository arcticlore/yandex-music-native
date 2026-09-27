"""«Настройки»: everything that lives in :class:`core.config_manager.ConfigManager`.

The page is the single writer for the persisted preferences; the shell reads
them once at start-up, so a change takes effect on the next launch except for
the visualizer, the theme and the switches that reach the stage, which are
applied immediately.

The layout is a set of titled cards rather than one long column of controls.
A preference page is a list, and a list nobody can scan is a list nobody
changes: grouping by subject - sound, the stage, appearance, the account - lets
the eye find the one row it is after, and it leaves room to grow a card without
renumbering the page.  The cards are also a scroll area, because the honest
number of preferences no longer fits a 600px-tall window and a control that
needs a taller window is a control that is never changed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.config_manager import VALID_QUALITIES, VALID_THEMES, VALID_VISUALIZERS, ConfigManager
from core.playback_controller import cover_cache_dir
from ui.theme import (
    BADGE_HEIGHT,
    COVER_SIZE,
    PAGE_PADDING,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
)
from ui.widgets.chips import ChipGroup

VISUALIZER_CHOICES = (
    ("spectrum", "Спектр"),
    ("wave", "Волна"),
    ("circular", "Круг"),
    ("meters", "Уровни"),
)
QUALITY_CHOICES = (
    ("auto", "Авто"),
    ("lossless", "Без потерь"),
    ("320", "320 кбит/с"),
    ("192", "192 кбит/с"),
)
THEME_CHOICES = (
    ("obsidian", "Obsidian Dark"),
    ("cyberpunk", "Neon Cyberpunk"),
    ("oled", "OLED Pure Black"),
)
TRAY_CHOICES = (("always", "Всегда"), ("playing", "Только при игре"), ("never", "Не показывать"))

CARD_WIDTH = 420
"""Cards share one width so the page reads as a column, not a staircase."""
ANIMATIONS_LABEL = "Анимации"
NORMALIZATION_LABEL = "Нормализация громкости"
CLEAR_CACHE_LABEL = "Очистить кэш обложек"


def _filtered(values: tuple[str, ...], choices: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    allowed = set(values)
    return tuple((key, label) for key, label in choices if key in allowed)


def plural_files(count: int) -> str:
    """``3`` becomes «3 файла» - Russian needs three, not two.

    «1 файл», «2 файла», «5 файлов».  Getting this wrong is the kind of thing
    that makes an otherwise careful interface feel machine-made, and the rule
    is short enough to just write down.
    """
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        word = "файл"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        word = "файла"
    else:
        word = "файлов"
    return f"{count} {word}"


def human_size(size: float) -> str:
    """``1536`` becomes «1,5 МБ» - the one place a number becomes words."""
    value = max(0.0, float(size))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024.0 or unit == "ГБ":
            shown = f"{value:.0f}" if unit == "Б" else f"{value:.1f}".replace(".", ",")
            return f"{shown} {unit}"
        value /= 1024.0
    return "0 Б"


def cache_size(path: Path | None = None) -> int:
    """Bytes in the cover cache; a missing directory is zero, not an error."""
    target = path or cover_cache_dir()
    if not target.exists():
        return 0
    total = 0
    for entry in target.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def pill_height(label: QLabel, floor: int = BADGE_HEIGHT, padding: int = 6) -> int:
    """The height a small capsule needs for the text the sheet gives it.

    ``BADGE_HEIGHT`` is the design, but a font the platform cannot honour at
    the size the sheet asks for comes out much taller, and a hard height would
    then cut the word in half.  Measured rather than guessed, and polished
    first: the sheet only lands on the widget once the style has seen it, so
    measuring before that would read the default font and over-size the pill.
    """
    style = label.style()
    if style is not None:
        style.polish(label)
    return max(floor, QFontMetrics(label.font()).height() + padding)


class ScrollArea(QScrollArea):
    """A scroll area that gives its content the height the content needs.

    A plain ``QScrollArea`` is not enough here.  The chip groups answer
    ``heightForWidth``, so the column's height depends on its width, and Qt
    cannot settle that while the content is still being resized - it leaves the
    content at its *minimum* instead.  The visible result is a page that scrolls
    by just enough to be a pixel too short everywhere, squeezing the last pixel
    out of every label.  Recomputing on each viewport resize is the width-safe
    way to ask for the height the layout actually wants.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._last_height = -1

    def setWidget(self, widget: QWidget) -> None:  # noqa: N802 - Qt naming
        super().setWidget(widget)
        self._sync_height()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        content = self.widget()
        if content is None:
            return
        layout = content.layout()
        needed = layout.sizeHint().height() if layout is not None else content.sizeHint().height()
        wanted = max(needed, self.viewport().height())
        # Only touch the minimum when it really changed: this runs from
        # ``resizeEvent``, and a redundant set here is a resize loop.
        if wanted != self._last_height:
            self._last_height = wanted
            content.setMinimumHeight(wanted)


class SettingsCard(QFrame):
    """One titled group of controls, with room for a hint under the heading."""

    def __init__(self, title: str, hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setMaximumWidth(CARD_WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_XL, SPACE_MD, SPACE_XL, SPACE_MD)
        outer.setSpacing(SPACE_MD)
        heading = QLabel(title)
        heading.setObjectName("SettingsCardTitle")
        outer.addWidget(heading)
        if hint:
            note = QLabel(hint)
            note.setObjectName("Dim")
            note.setWordWrap(True)
            outer.addWidget(note)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(SPACE_MD)
        outer.addLayout(self.body)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def switch_row(self, text: str, tooltip: str = "") -> QCheckBox:
        """A checkbox with its label on the left, the way a setting reads."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text)
        label.setWordWrap(True)
        row.addWidget(label, 1)
        box = QCheckBox()
        box.setObjectName("SettingSwitch")
        if tooltip:
            box.setToolTip(tooltip)
            label.setToolTip(tooltip)
        row.addWidget(box, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(row)
        self._rows = getattr(self, "_rows", {})
        self._rows[text] = box
        return box

    def button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_SM)
        self.body.addLayout(row)
        return row


class SettingsPage(QWidget):
    """Titled cards for every persisted preference, plus the account and cache."""

    visualizer_changed = Signal(str)
    quality_changed = Signal(str)
    theme_changed = Signal(str)
    notifications_changed = Signal(bool)
    tray_changed = Signal(str)
    animations_changed = Signal(bool)
    normalization_changed = Signal(bool)
    cache_cleared = Signal()
    logout_requested = Signal()

    def __init__(
        self,
        config: ConfigManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._cache_dir: Path | None = None
        self._build_ui()
        self.load()

    # -- construction --------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        title = QLabel("Настройки")
        title.setObjectName("PageTitle")
        header = QVBoxLayout()
        header.setContentsMargins(PAGE_PADDING, PAGE_PADDING, 0, 0)
        header.addWidget(title)
        outer.addLayout(header)

        scroll = ScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(PAGE_PADDING, SPACE_MD, PAGE_PADDING, PAGE_PADDING)
        column.setSpacing(SPACE_XL)
        column.setAlignment(Qt.AlignmentFlag.AlignTop)
        column.addWidget(self._sound_card())
        column.addWidget(self._stage_card())
        column.addWidget(self._appearance_card())
        column.addWidget(self._account_card())
        column.addWidget(self._cache_card())
        column.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Dim")
        footer = QVBoxLayout()
        footer.setContentsMargins(PAGE_PADDING, 0, PAGE_PADDING, PAGE_PADDING)
        footer.addWidget(self.status_label)
        outer.addLayout(footer)

        self.visualizer_group.value_changed.connect(self._on_visualizer)
        self.quality_group.value_changed.connect(self._on_quality)
        self.theme_group.value_changed.connect(self._on_theme)
        self.tray_group.value_changed.connect(self._on_tray)
        self.notifications_switch.toggled.connect(self._on_notifications)
        self.animations_switch.toggled.connect(self._on_animations)
        self.normalization_switch.toggled.connect(self._on_normalization)

    def _sound_card(self) -> QWidget:
        card = SettingsCard("Звук", "Качество применяется к следующему треку: текущий уже скачивается.")
        self.quality_group = ChipGroup("Качество", _filtered(VALID_QUALITIES, QUALITY_CHOICES))
        card.add(self.quality_group)
        self.normalization_switch = card.switch_row(
            NORMALIZATION_LABEL,
            "Компенсировать громкость на визуализаторе, чтобы ползунок не гасил картинку.",
        )
        return card

    def _stage_card(self) -> QWidget:
        card = SettingsCard("Визуализация", None)
        self.visualizer_group = ChipGroup("Стиль", _filtered(VALID_VISUALIZERS, VISUALIZER_CHOICES))
        card.add(self.visualizer_group)
        self.animations_switch = card.switch_row(
            ANIMATIONS_LABEL,
            "Выключить плавную прокрутку текста и вращение индикатора загрузки.",
        )
        return card

    def _appearance_card(self) -> QWidget:
        card = SettingsCard("Оформление и уведомления", None)
        self.theme_group = ChipGroup("Тема", _filtered(VALID_THEMES, THEME_CHOICES))
        card.add(self.theme_group)
        self.tray_group = ChipGroup("Значок в трее", TRAY_CHOICES)
        card.add(self.tray_group)
        self.notifications_switch = card.switch_row("Уведомления «сейчас играет»")
        return card

    def _account_card(self) -> QWidget:
        card = SettingsCard("Аккаунт", None)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_MD)
        self.account_avatar = QLabel()
        self.account_avatar.setObjectName("ProfileAvatar")
        self.account_avatar.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.account_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.account_avatar)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        self.account_label = QLabel("Не авторизован")
        self.account_label.setObjectName("ProfileName")
        self.account_hint = QLabel("Войдите, чтобы слушать")
        self.account_hint.setObjectName("ProfileHint")
        text.addWidget(self.account_label)
        text.addWidget(self.account_hint)
        row.addLayout(text, 1)
        self.plus_badge = QLabel("ПЛЮС")
        self.plus_badge.setObjectName("PlusBadge")
        self.plus_badge.setFixedHeight(pill_height(self.plus_badge))
        self.plus_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.plus_badge.setVisible(False)
        row.addWidget(self.plus_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        card.body.addLayout(row)
        buttons = card.button_row()
        self.logout_button = QPushButton("Выйти")
        self.logout_button.setObjectName("NavButton")
        self.logout_button.setEnabled(False)
        self.logout_button.setToolTip("Выйти из аккаунта и забыть сохранённый токен")
        self.logout_button.clicked.connect(self.logout_requested.emit)
        buttons.addWidget(self.logout_button)
        buttons.addStretch(1)
        return card

    def _cache_card(self) -> QWidget:
        card = SettingsCard("Кэш обложек", "Обложки хранятся на диске, чтобы не тянуть их каждый раз.")
        buttons = card.button_row()
        self.cache_label = QLabel("—")
        self.cache_label.setObjectName("Dim")
        buttons.addWidget(self.cache_label)
        buttons.addStretch(1)
        self.clear_cache_button = QPushButton(CLEAR_CACHE_LABEL)
        self.clear_cache_button.setObjectName("NavButton")
        self.clear_cache_button.clicked.connect(self.clear_cache)
        buttons.addWidget(self.clear_cache_button)
        return card

    # -- account and cache ---------------------------------------------------

    def set_account(self, login: str, detail: str = "", avatar_url: str = "") -> None:
        """Mirror the sidebar card here, so the account is visible in its own place."""
        signed_in = bool(login)
        self.account_label.setText(login or "Не авторизован")
        self.account_hint.setText(detail or ("" if signed_in else "Войдите, чтобы слушать"))
        self.account_hint.setVisible(not signed_in or bool(detail))
        self.plus_badge.setVisible(bool(detail) and signed_in)
        self.logout_button.setEnabled(signed_in)
        self.account_avatar.setText("")

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Keep the badge right when the theme hands us a different font."""
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            wanted = pill_height(self.plus_badge)
            if self.plus_badge.height() != wanted:
                self.plus_badge.setFixedHeight(wanted)

    def set_avatar_pixmap(self, pixmap) -> None:
        """Show an already-loaded avatar; the sidebar fetches it."""
        if pixmap is not None and not pixmap.isNull():
            self.account_avatar.setPixmap(pixmap.scaled(COVER_SIZE, COVER_SIZE))

    @property
    def cache_bytes(self) -> int:
        return cache_size(self._cache_dir)

    def refresh_cache_size(self) -> int:
        """Recount the covers on disk and show it."""
        total = self.cache_bytes
        count = 0
        target = self._cache_dir or cover_cache_dir()
        if target.exists():
            count = sum(1 for entry in target.rglob("*") if entry.is_file())
        self.cache_label.setText(f"{human_size(total)} · {plural_files(count)}")
        self.clear_cache_button.setEnabled(total > 0)
        return total

    def clear_cache(self) -> int:
        """Delete the cached covers; the next track downloads its own again."""
        target = self._cache_dir or cover_cache_dir()
        removed = 0
        if target.exists():
            removed = sum(1 for entry in target.rglob("*") if entry.is_file())
            shutil.rmtree(target, ignore_errors=True)
        self.refresh_cache_size()
        self.status_label.setText(f"Кэш очищен: {plural_files(removed)}")
        self.cache_cleared.emit()
        return removed

    def set_animations_enabled(self, enabled: bool) -> bool:
        """Move the switch without emitting.

        The window pushes the stored value down to this page at start-up and
        when the switch is flipped elsewhere.  Emitting from here would bounce
        the change straight back to the sender, and the collection page and the
        drawer would be told about a setting they did not just change.
        """
        box = self.animations_switch
        blocked = box.blockSignals(True)
        box.setChecked(bool(enabled))
        box.blockSignals(blocked)
        return bool(enabled)

    def set_cache_dir(self, path: Path | str | None) -> None:
        """Point the card at a different directory, for tests and profiles."""
        self._cache_dir = Path(path) if path is not None else None
        self.refresh_cache_size()

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        """Show the stored values without emitting change signals."""
        for group, value in (
            (self.visualizer_group, self._config.get_visualizer()),
            (self.quality_group, self._config.get_quality()),
            (self.theme_group, self._config.get_theme()),
            (self.tray_group, self._config.get("tray", "always")),
        ):
            blocked = group.blockSignals(True)
            group.set_value(value)
            group.blockSignals(blocked)
        for box, value in (
            (self.notifications_switch, self._config.get_notifications()),
            (self.animations_switch, self._config.animations_enabled()),
            (self.normalization_switch, self._config.volume_normalization()),
        ):
            box.blockSignals(True)
            box.setChecked(bool(value))
            box.blockSignals(False)
        self.refresh_cache_size()

    def reset(self) -> None:
        self._config.reset()
        self.load()
        self.status_label.setText("Настройки сброшены к значениям по умолчанию")
        self.visualizer_changed.emit(self._config.get_visualizer())
        self.theme_changed.emit(self._config.get_theme())
        self.animations_changed.emit(self._config.animations_enabled())
        self.normalization_changed.emit(self._config.volume_normalization())

    # -- slots --------------------------------------------------------------

    def _on_visualizer(self, value: str) -> None:
        self._config.set_visualizer(value)
        self.status_label.setText(f"Визуализатор: {value}")
        self.visualizer_changed.emit(value)

    def _on_quality(self, value: str) -> None:
        self._config.set_quality(value)
        self.status_label.setText(f"Качество: {value}")
        self.quality_changed.emit(value)

    def _on_theme(self, value: str) -> None:
        self._config.set_theme(value)
        label = dict(THEME_CHOICES).get(value, value)
        self.status_label.setText(f"Тема: {label}")
        self.theme_changed.emit(value)

    def _on_tray(self, value: str) -> None:
        self._config.set("tray", value)
        self.status_label.setText(f"Значок в трее: {value}")
        self.tray_changed.emit(value)

    def _on_notifications(self, checked: bool) -> None:
        self._config.set_notifications(checked)
        self.status_label.setText("Уведомления включены" if checked else "Уведомления выключены")
        self.notifications_changed.emit(checked)

    def _on_animations(self, checked: bool) -> None:
        self._config.set_animations_enabled(checked)
        self.status_label.setText("Анимации включены" if checked else "Анимации выключены")
        self.animations_changed.emit(checked)

    def _on_normalization(self, checked: bool) -> None:
        self._config.set_volume_normalization(checked)
        self.status_label.setText(
            "Визуализатор не зависит от громкости" if checked else "Визуализатор следует за громкостью"
        )
        self.normalization_changed.emit(checked)


__all__ = [
    "ANIMATIONS_LABEL",
    "CLEAR_CACHE_LABEL",
    "pill_height",
    "ScrollArea",
    "plural_files",
    "NORMALIZATION_LABEL",
    "QUALITY_CHOICES",
    "THEME_CHOICES",
    "TRAY_CHOICES",
    "VISUALIZER_CHOICES",
    "SettingsCard",
    "SettingsPage",
    "cache_size",
    "human_size",
]
