"""«Моя волна»: the visual stage plus the four station selectors.

The selectors are built from :mod:`core.station`, whose value sets are exactly
the ones ``rotor_station_settings2`` accepts. Picking a chip while the station
is running restarts «Моя волна» immediately, so a new mood is audible without
pressing anything else.

The four groups sit in a two-by-two grid of pill chips rather than in four full
width rows: each axis is a self-contained block with its own label, so the
selectors read as one settings panel instead of a wall of buttons.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.playback_controller import PlaybackController, PlaybackState, QueueMode
from core.station import (
    ACTIVITY_LABELS,
    DIVERSITY_LABELS,
    LANGUAGE_LABELS,
    MOOD_LABELS,
    WAVE_ACTIVITIES,
    WAVE_DIVERSITIES,
    WAVE_LANGUAGES,
    WAVE_MOODS,
    chips,
)
from ui.theme import PAGE_PADDING, SPACE_LG, SPACE_MD
from ui.widgets.chips import ChipGroup
from ui.widgets.visualizer import VisualizerStack

MOOD_CHOICES = chips(WAVE_MOODS, MOOD_LABELS)
ACTIVITY_CHOICES = chips(WAVE_ACTIVITIES, ACTIVITY_LABELS)
LANGUAGE_CHOICES = chips(WAVE_LANGUAGES, LANGUAGE_LABELS)
DIVERSITY_CHOICES = chips(WAVE_DIVERSITIES, DIVERSITY_LABELS)

VISUALIZER_HINT = "Нажмите на визуализатор, чтобы сменить режим"


class WavePage(QWidget):
    """Station stage: visualizer, chip selectors and the start button."""

    selection_changed = Signal(dict)
    play_requested = Signal()
    settings_error = Signal(str)
    visualizer_mode_changed = Signal(str)
    """A click on the stage, or a change made elsewhere, landed on ``mode``."""

    def __init__(
        self,
        controller: PlaybackController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._selection = {
            "mood": "all",
            "activity": "all",
            "language": "all",
            "diversity": "default",
        }
        self._build_ui()
        self._connect(controller)

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(PAGE_PADDING, PAGE_PADDING, PAGE_PADDING, PAGE_PADDING)
        root.setSpacing(SPACE_LG)

        header = QHBoxLayout()
        title = QLabel("Моя волна")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.start_button = QPushButton("Запустить волну")
        self.start_button.setObjectName("Accent")
        self.start_button.clicked.connect(self._on_start)
        header.addWidget(self.start_button)
        root.addLayout(header)

        self.card = QFrame()
        self.card.setObjectName("WaveCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        self.stage = VisualizerStack(self.card)
        # A click on the stage walks through the styles, and whoever shows the
        # mode as an icon is told about it so the two never disagree.
        self.stage.mode_changed.connect(self.visualizer_mode_changed)
        card_layout.addWidget(self.stage, 1)
        root.addWidget(self.card, 1)

        self.groups = {
            "mood": ChipGroup("Настроение", MOOD_CHOICES),
            "activity": ChipGroup("Занятие", ACTIVITY_CHOICES),
            "language": ChipGroup("Язык", LANGUAGE_CHOICES),
            "diversity": ChipGroup("Подбор", DIVERSITY_CHOICES),
        }
        # Two by two: each axis keeps its own label and its own row of pills, and
        # the four blocks share the width of the page instead of stacking into
        # one column of full-width chip rows.
        selectors = QGridLayout()
        selectors.setContentsMargins(0, 0, 0, 0)
        selectors.setHorizontalSpacing(SPACE_LG)
        selectors.setVerticalSpacing(SPACE_MD)
        for position, group in enumerate(self.groups.values()):
            selectors.addWidget(group, position // 2, position % 2)
        selectors.setColumnStretch(0, 1)
        selectors.setColumnStretch(1, 1)
        root.addLayout(selectors)

        footer = QHBoxLayout()
        self.status_label = QLabel("Выберите «Запустить волну», чтобы начать поток")
        self.status_label.setObjectName("Dim")
        footer.addWidget(self.status_label, 1)
        self.mode_hint = QLabel(VISUALIZER_HINT)
        self.mode_hint.setObjectName("Dim")
        self.mode_hint.setToolTip(VISUALIZER_HINT)
        footer.addWidget(self.mode_hint)
        root.addLayout(footer)

        for axis, group in self.groups.items():
            group.value_changed.connect(lambda value, key=axis: self._on_chip(key, value))

    def _connect(self, controller: PlaybackController) -> None:
        controller.track_changed.connect(self._on_track)
        controller.state_changed.connect(self._on_state)
        controller.wave_started.connect(lambda _tracks: self._on_wave_started())
        controller.wave_settings_changed.connect(self.on_settings_changed)
        controller.wave_error.connect(self._on_error)
        controller.cover_ready.connect(self._on_cover)

    # -- public API ---------------------------------------------------------

    @property
    def selection(self) -> dict[str, str]:
        return dict(self._selection)

    @property
    def visualizer(self) -> VisualizerStack:
        return self.stage

    def set_visualizer_mode(self, mode: str) -> str:
        return self.stage.set_mode(mode)

    def feed_spectrum(self, values: Sequence[float]) -> None:
        self.stage.set_spectrum(values)

    def feed_waveform(self, values: Sequence[float]) -> None:
        self.stage.set_waveform(values)

    def set_cover_pixmap(self, pixmap: QPixmap | None) -> None:
        self.stage.set_cover(pixmap)

    def on_settings_changed(self, payload: dict) -> None:
        """Reflect settings reported by the controller without re-triggering."""
        if not isinstance(payload, dict):
            return
        self.apply_selection(payload, restart=False)

    def apply_selection(self, payload: dict, restart: bool = True) -> None:
        """Adopt a settings payload; optionally restart the running station."""
        updated = dict(self._selection)
        for axis in updated:
            value = payload.get(axis)
            if isinstance(value, str):
                updated[axis] = value
        self._selection = updated
        for axis, group in self.groups.items():
            previous = group.blockSignals(True)
            group.set_value(updated[axis])
            group.blockSignals(previous)
        self.selection_changed.emit(dict(self._selection))
        if restart and self._is_wave_running():
            self._start()

    def start_wave(self) -> bool:
        """Start «Моя волна» with the current chip selection."""
        return self._start()

    # -- slots --------------------------------------------------------------

    def _on_chip(self, axis: str, value: str) -> None:
        self._selection[axis] = value
        self.selection_changed.emit(self.selection)
        if self._is_wave_running():
            self._start()

    def _on_start(self) -> None:
        self.play_requested.emit()
        self._start()

    def _start(self) -> bool:
        started = self._controller.start_wave(
            mood=self._selection["mood"],
            activity=self._selection["activity"],
            language=self._selection["language"],
            diversity=self._selection["diversity"],
        )
        if not started:
            self.status_label.setText("Не удалось применить настройки волны")
        return started

    def _is_wave_running(self) -> bool:
        if self._controller.mode != QueueMode.RADIO:
            return False
        return self._controller.state in (
            PlaybackState.PLAYING,
            PlaybackState.PAUSED,
            PlaybackState.BUFFERING,
        )

    def _on_wave_started(self) -> None:
        self.status_label.setText("Волна запущена")

    def _on_track(self, track: object) -> None:
        if track is None:
            return
        title = getattr(track, "title", "")
        artists = getattr(track, "artists_name", "")
        self.status_label.setText(f"Сейчас: {artists} — {title}" if artists else f"Сейчас: {title}")

    def _on_state(self, state: str) -> None:
        if state == PlaybackState.STOPPED.value and not self._is_wave_running():
            self.stage.set_idle()

    def _on_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.settings_error.emit(message)

    def _on_cover(self, track_id: str, path: str) -> None:
        current = self._controller.current
        if current is not None and current.id != track_id:
            return
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.set_cover_pixmap(pixmap)


__all__ = [
    "ACTIVITY_CHOICES",
    "DIVERSITY_CHOICES",
    "LANGUAGE_CHOICES",
    "MOOD_CHOICES",
    "VISUALIZER_HINT",
    "WavePage",
]
