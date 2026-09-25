"""Bottom player bar: cover, transport, seek, volume, likes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from yamusic.models import TrackInfo
from yamusic.services.playback import PlaybackController
from yamusic.ui.widgets.cover import CoverView


class PlayerBar(QFrame):
    """Fixed-height bar bound to :class:`PlaybackController`."""

    visualizer_mode_changed = Signal(int)
    lyrics_toggled = Signal()

    def __init__(self, controller: PlaybackController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedHeight(84)
        self._controller = controller
        self._seek_pressed = False
        self._duration = 0
        self._build_ui()
        self._connect()

    # ------------------------------------------------------------------ ui

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 10)
        root.setSpacing(16)

        # -- left: cover + titles + likes
        left = QHBoxLayout()
        left.setSpacing(12)
        self.cover = CoverView()
        self.cover.setFixedSize(56, 56)
        self.cover.set_radius(6)
        left.addWidget(self.cover)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = QLabel("—")
        self.title.setObjectName("TrackTitle")
        self.title.setWordWrap(False)
        self.artist = QLabel("Яндекс Музыка")
        self.artist.setObjectName("TrackArtist")
        self.codec = QLabel("")
        self.codec.setObjectName("Time")
        texts.addWidget(self.title)
        texts.addWidget(self.artist)
        texts.addWidget(self.codec)
        texts.addStretch(1)
        left.addLayout(texts, 1)

        self.btn_like = QPushButton("♡")
        self.btn_like.setObjectName("Like")
        self.btn_like.setCheckable(True)
        self.btn_like.setToolTip("Нравится")
        self.btn_dislike = QPushButton("⌀")
        self.btn_dislike.setObjectName("Like")
        self.btn_dislike.setToolTip("Не нравится (пропустить и обучить волну)")
        left.addWidget(self.btn_like)
        left.addWidget(self.btn_dislike)
        root.addLayout(left, 3)

        # -- center: transport + seek
        center = QVBoxLayout()
        center.setSpacing(4)
        transport = QHBoxLayout()
        transport.setSpacing(6)
        transport.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_prev = QPushButton("⏮")
        self.btn_prev.setObjectName("Transport")
        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("TransportBig")
        self.btn_next = QPushButton("⏭")
        self.btn_next.setObjectName("Transport")
        transport.addWidget(self.btn_prev)
        transport.addWidget(self.btn_play)
        transport.addWidget(self.btn_next)
        center.addLayout(transport)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(8)
        self.time_pos = QLabel("0:00")
        self.time_pos.setObjectName("Time")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.time_total = QLabel("0:00")
        self.time_total.setObjectName("Time")
        seek_row.addWidget(self.time_pos)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.time_total)
        center.addLayout(seek_row)
        root.addLayout(center, 5)

        # -- right: lyrics, visualizer, volume
        right = QHBoxLayout()
        right.setSpacing(8)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.btn_lyrics = QPushButton("Текст")
        self.btn_lyrics.setCheckable(True)
        self.btn_lyrics.setToolTip("Текст песни (караоке)")
        right.addWidget(self.btn_lyrics)

        self.viz_combo = QComboBox()
        self.viz_combo.addItem("Спектр", 0)
        self.viz_combo.addItem("Волна", 1)
        self.viz_combo.addItem("Круг", 2)
        self.viz_combo.setToolTip("Визуализатор")
        self.viz_combo.setFixedWidth(110)
        right.addWidget(self.viz_combo)

        self.btn_vol = QPushButton("🔊")
        self.btn_vol.setObjectName("Like")
        self.btn_vol.setToolTip("Заглушить")
        right.addWidget(self.btn_vol)

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(100)
        self.volume.setValue(self._controller.engine.volume)
        right.addWidget(self.volume)
        root.addLayout(right)

    # -------------------------------------------------------------- wiring

    def _connect(self) -> None:
        c = self._controller
        c.track_changed.connect(self._on_track)
        c.state_changed.connect(self._on_state)
        c.position_changed.connect(self._on_position)
        c.duration_changed.connect(self._on_duration)
        c.liked_changed.connect(self._on_liked)
        c.volume_changed.connect(self.volume.setValue)
        c.stream_info_ready.connect(self._on_stream_info)

        self.btn_play.clicked.connect(c.play_pause)
        self.btn_next.clicked.connect(lambda: c.next(manual=True))
        self.btn_prev.clicked.connect(c.previous)
        self.btn_like.clicked.connect(c.toggle_like)
        self.btn_dislike.clicked.connect(c.dislike)

        self.volume.sliderMoved.connect(c.set_volume)
        self.btn_vol.clicked.connect(self._toggle_mute)

        self.seek.sliderPressed.connect(self._seek_press)
        self.seek.sliderReleased.connect(self._seek_release)
        self.viz_combo.currentIndexChanged.connect(self.visualizer_mode_changed.emit)
        self.btn_lyrics.clicked.connect(self.lyrics_toggled.emit)

    # -- slots ------------------------------------------------------------

    def _on_track(self, track: TrackInfo | None) -> None:
        if track is None:
            self.title.setText("—")
            self.artist.setText("Яндекс Музыка")
            self.cover.clear()
            self.codec.setText("")
            self.btn_like.setChecked(False)
            self.seek.setRange(0, 0)
            self.time_pos.setText("0:00")
            self.time_total.setText("0:00")
            return
        self.title.setText(track.title)
        self.artist.setText(track.artist_line)
        self.btn_like.setChecked(track.liked)
        self._duration = track.duration_ms
        total_s = track.duration_ms // 1000
        self.time_total.setText(f"{total_s // 60}:{total_s % 60:02d}")
        self.seek.setRange(0, max(1, track.duration_ms))
        self.codec.setText("")

    def set_cover_path(self, path: str) -> None:
        self.cover.set_image_path(path)

    def _on_state(self, playing: bool) -> None:
        self.btn_play.setText("⏸" if playing else "▶")

    def _on_position(self, ms: int) -> None:
        if not self._seek_pressed:
            self.seek.setValue(ms)
        s = max(0, ms) // 1000
        self.time_pos.setText(f"{s // 60}:{s % 60:02d}")

    def _on_duration(self, ms: int) -> None:
        if ms > 0 and ms != self._duration:
            self._duration = ms
            self.seek.setRange(0, ms)
            s = ms // 1000
            self.time_total.setText(f"{s // 60}:{s % 60:02d}")

    def _on_liked(self, _track_id: str, liked: bool) -> None:
        self.btn_like.setChecked(liked)

    def _on_stream_info(self, _track_id: str, codec: str, kbps: int) -> None:
        label = "FLAC" if codec.lower() == "flac" else f"MP3 {kbps}"
        self.codec.setText(label)

    def set_visualizer_index(self, index: int) -> None:
        self.viz_combo.blockSignals(True)
        self.viz_combo.setCurrentIndex(index)
        self.viz_combo.blockSignals(False)

    def set_lyrics_checked(self, checked: bool) -> None:
        self.btn_lyrics.setChecked(checked)

    def _seek_press(self) -> None:
        self._seek_pressed = True

    def _seek_release(self) -> None:
        self._seek_pressed = False
        self._controller.seek(self.seek.value())

    def _toggle_mute(self) -> None:
        vol = self._controller.engine.volume
        if vol > 0:
            self._last_volume = vol
            self._controller.set_volume(0)
            self.btn_vol.setText("🔇")
        else:
            self._controller.set_volume(getattr(self, "_last_volume", 80))
            self.btn_vol.setText("🔊")
