"""Settings page: playback quality, cache, notifications, session."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from yamusic.cache.store import CacheStore
from yamusic.config import Settings
from yamusic.services.playback import PlaybackController

QUALITIES = (
    ("auto", "Авто (Lossless при подписке)"),
    ("lossless", "Только FLAC"),
    ("320", "MP3 320 kbps"),
    ("192", "MP3 192 kbps"),
)


class SettingsPage(QWidget):
    """Runtime preferences; changes apply immediately."""

    logout_requested = Signal()

    def __init__(
        self,
        settings: Settings,
        cache: CacheStore,
        controller: PlaybackController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cache = cache
        self._controller = controller
        opts = settings.options()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)

        title = QLabel("Настройки")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)

        self.quality = QComboBox()
        for value, label in QUALITIES:
            self.quality.addItem(label, value)
        idx = self.quality.findData(opts.quality)
        if idx >= 0:
            self.quality.setCurrentIndex(idx)
        self.quality.currentIndexChanged.connect(self._quality_changed)
        form.addRow("Качество потока", self.quality)

        self.cache_enabled = QCheckBox("Кэшировать треки на диск для мгновенного повтора")
        self.cache_enabled.setChecked(opts.cache_tracks)
        self.cache_enabled.toggled.connect(self._cache_toggled)
        form.addRow("Кэш", self.cache_enabled)

        self.cache_limit = QSpinBox()
        self.cache_limit.setRange(128, 100_000)
        self.cache_limit.setSingleStep(256)
        self.cache_limit.setSuffix(" МБ")
        self.cache_limit.setValue(opts.cache_limit_mb)
        self.cache_limit.valueChanged.connect(self._limit_changed)
        form.addRow("Лимит кэша", self.cache_limit)

        self.notifications = QCheckBox("Показывать уведомление о новом треке")
        self.notifications.setChecked(opts.notifications)
        self.notifications.toggled.connect(lambda v: self._settings.set_notifications(v))
        form.addRow("Уведомления", self.notifications)

        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setSuffix(" %")
        self.volume.setValue(opts.volume)
        self.volume.valueChanged.connect(self._controller.set_volume)
        form.addRow("Громкость", self.volume)

        root.addLayout(form)

        stats = self._cache.stats()
        self.stats_label = QLabel(
            f"Кэш: {stats[0]} файлов, {stats[1] / (1024 * 1024):.1f} МБ"
        )
        self.stats_label.setObjectName("Dim")
        root.addWidget(self.stats_label)

        actions = QHBoxLayout()
        self.btn_clear = QPushButton("Очистить кэш")
        self.btn_clear.clicked.connect(self._clear_cache)
        self.btn_logout = QPushButton("Выйти из аккаунта")
        self.btn_logout.setStyleSheet("color: #ff5c7a;")
        self.btn_logout.clicked.connect(self.logout_requested.emit)
        actions.addWidget(self.btn_clear)
        actions.addWidget(self.btn_logout)
        actions.addStretch(1)
        root.addLayout(actions)

        info = QLabel(
            "Медиаклавиши и системный виджет громкости работают через MPRIS2. "
            "Сборка готова к упаковке в AppImage/Flatpak (см. packaging/)."
        )
        info.setObjectName("Dim")
        info.setWordWrap(True)
        root.addWidget(info)
        root.addStretch(1)

    def refresh_stats(self) -> None:
        count, size = self._cache.stats()
        self.stats_label.setText(f"Кэш: {count} файлов, {size / (1024 * 1024):.1f} МБ")

    def _quality_changed(self, index: int) -> None:
        value = str(self.quality.itemData(index))
        self._controller.set_quality(value)

    def _cache_toggled(self, enabled: bool) -> None:
        self._settings.set_cache_tracks(enabled)

    def _limit_changed(self, value: int) -> None:
        self._settings.set_cache_limit_mb(value)
        self._cache.set_limit_mb(value)

    def _clear_cache(self) -> None:
        self._cache.clear()
        self.refresh_stats()
