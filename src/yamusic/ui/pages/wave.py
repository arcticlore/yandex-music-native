"""«Моя волна» page: big visualizer + station tuning."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yamusic.models import StationInfo, TrackInfo
from yamusic.services.playback import PlaybackController
from yamusic.services.rotor import RotorService
from yamusic.ui.widgets.visualizers import CircularVisualizer, NeonWave, SpectrumBars

# mood/diversity values accepted by rotor_station_settings2
MOOD_ENERGY = (
    ("fun", "Весёлое"),
    ("calm", "Спокойное"),
    ("active", "Энергичное"),
    ("bright", "Светлое"),
)
DIVERSITY = (
    ("diverse", "Разнообразно"),
    ("strict", "Похожие"),
    ("maximum", "Максимум новизны"),
)
LANGUAGES = (
    ("not-russian", "Без русского"),
    ("russian", "Русские"),
    ("any", "Любой язык"),
)


class WavePage(QWidget):
    """Station picker, tuning controls and the main visualization stage."""

    apply_settings_requested = Signal(str, str, str)  # mood_energy, diversity, language

    def __init__(
        self,
        controller: PlaybackController,
        rotor: RotorService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._rotor = rotor
        self._mode = 0
        self._build_ui()
        rotor.stations_loaded.connect(self._on_stations)
        controller.track_changed.connect(self._on_track)
        controller.cover_ready.connect(self._on_cover)
        controller.state_changed.connect(lambda _p: self._refresh_stage())

    # ------------------------------------------------------------------ ui

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Моя волна")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.station_combo = QComboBox()
        self.station_combo.setFixedWidth(240)
        self.station_combo.addItem("Моя волна", "user:onyourwave")
        header.addWidget(QLabel("Станция:"))
        header.addWidget(self.station_combo)

        self.btn_start = QPushButton("Запустить")
        self.btn_start.setObjectName("Accent")
        header.addWidget(self.btn_start)
        root.addLayout(header)

        # tuning row
        tuning = QHBoxLayout()
        tuning.setSpacing(10)
        self.mood_combo = QComboBox()
        for value, label in MOOD_ENERGY:
            self.mood_combo.addItem(label, value)
        self.div_combo = QComboBox()
        for value, label in DIVERSITY:
            self.div_combo.addItem(label, value)
        self.lang_combo = QComboBox()
        for value, label in LANGUAGES:
            self.lang_combo.addItem(label, value)
        for combo in (self.mood_combo, self.div_combo, self.lang_combo):
            combo.setFixedWidth(160)
            tuning.addWidget(combo)
        self.btn_apply = QPushButton("Применить настройки")
        self.btn_apply.setToolTip("Смена настроения/разнообразия/языка перезапускает волну")
        tuning.addWidget(self.btn_apply)
        tuning.addStretch(1)
        root.addLayout(tuning)

        # stage: stacked visualizers
        self.stage = QWidget()
        self.stage.setMinimumHeight(320)
        self.stage.setStyleSheet("background: #0f0f13; border-radius: 14px;")
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)

        self.bars = SpectrumBars()
        self.wave = NeonWave()
        self.circle = CircularVisualizer()

        self.stack: list[QWidget] = [self.bars, self.wave, self.circle]
        for w in self.stack:
            w.hide()
            stage_layout.addWidget(w)
        self.stack[0].show()

        root.addWidget(self.stage, 1)

        # current track caption under stage
        caption_row = QHBoxLayout()
        self.now_label = QLabel("Выберите «Запустить», чтобы начать поток")
        self.now_label.setObjectName("TrackArtist")
        caption_row.addWidget(self.now_label, 1)
        self.hint = QLabel("Двойной клик по треку в списках — играть; клавиши мультимедиа работают через MPRIS")
        self.hint.setObjectName("Dim")
        caption_row.addWidget(self.hint)
        root.addLayout(caption_row)

        # wiring
        self.btn_start.clicked.connect(self._on_start)
        self.btn_apply.clicked.connect(self._on_apply)
        self.station_combo.currentIndexChanged.connect(self._on_station_changed)

    # -- public API used by MainWindow -------------------------------------

    def set_visualizer_mode(self, mode: int) -> None:
        mode = max(0, min(2, mode))
        if mode == self._mode:
            return
        self.stack[self._mode].hide()
        self._mode = mode
        self.stack[self._mode].show()

    def feed_spectrum(self, values: list[float]) -> None:
        self.bars.set_spectrum(values)
        self.circle.set_spectrum(values)

    def feed_wave(self, values: list[float]) -> None:
        self.wave.set_wave(values)

    def set_cover_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is not None:
            self.circle.set_cover(pixmap)
        else:
            self.circle.clear_cover()

    # -- slots --------------------------------------------------------------

    def _on_start(self) -> None:
        station = self.station_combo.currentData()
        if isinstance(station, str):
            self._rotor.set_station(station)
        self._rotor.start()
        self._controller.play_from_wave()
        self.now_label.setText("Загрузка первой партии треков…")

    def _on_apply(self) -> None:
        self.apply_settings_requested.emit(
            str(self.mood_combo.currentData()),
            str(self.div_combo.currentData()),
            str(self.lang_combo.currentData()),
        )

    def _on_station_changed(self, index: int) -> None:
        data = self.station_combo.itemData(index)
        if isinstance(data, str) and data:
            self._rotor.set_station(data)

    def _on_stations(self, stations: object) -> None:
        if not isinstance(stations, list):
            return
        current = self.station_combo.currentData()
        self.station_combo.blockSignals(True)
        self.station_combo.clear()
        # personal + mood/activity first, then genres (cap the list)
        ordered = sorted(
            (s for s in stations if isinstance(s, StationInfo)),
            key=lambda s: (0 if s.category == "personal" else 1 if s.category in ("mood", "activity") else 2),
        )
        for station in ordered[:80]:
            self.station_combo.addItem(station.name, station.id)
        idx = self.station_combo.findData(current)
        if idx < 0:
            idx = self.station_combo.findData("user:onyourwave")
        if idx >= 0:
            self.station_combo.setCurrentIndex(idx)
        self.station_combo.blockSignals(False)

    def _on_track(self, track: TrackInfo | None) -> None:
        if track is None:
            self.now_label.setText("Пауза")
            return
        self.now_label.setText(f"Сейчас: {track.artist_line} — {track.title}")

    def _on_cover(self, track_id: str, path: str) -> None:
        current = self._controller.current
        if current is not None and current.id == track_id:
            pm = QPixmap(path)
            if not pm.isNull():
                self.set_cover_pixmap(pm)

    def _refresh_stage(self) -> None:
        self.update()
