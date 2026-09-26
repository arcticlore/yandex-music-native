"""The shared chrome of a result page: one header row and one framed surface.

«Коллекция» и «Поиск» both answer the same question - *what did I ask for and
what came back* - so both render their output inside this one frame instead of
nesting a bordered list inside a bordered tab pane.  A result list that carries
its own border has to be inset, and two nested radii are what produce the broken
corners and the interrupted lines along the edge of a window; here the frame
belongs to the panel, the rows paint their own pills, and the list itself is
transparent to the frame.

Switching sections is a segmented control rather than a row of tabs: the tab bar
is hidden and :class:`SegmentBar` draws one dark backing with the active segment
carved out of it in ``SURFACE_HOVER``.  The ``QTabWidget`` still owns the pages,
so ``page.tabs.currentIndex()`` and ``setTabText()`` keep working exactly as
before - only the painted part of the switch moved.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.theme import CONTROL_HEIGHT, SEGMENT_HEIGHT, SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XS
from ui.widgets.track_list import apply_row_delegate


def header_row(title: str) -> tuple[QHBoxLayout, QLabel]:
    """The line above the frame: title, then whatever the page adds.

    No stretch is added here, because a page either fills the rest of the line
    with a field or pushes its action to the far end, and only the page knows
    which of the two it wants. The line is ``CONTROL_HEIGHT`` tall, so a field
    and a button share one baseline.
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_LG)
    label = QLabel(title)
    label.setObjectName("PageTitle")
    row.addWidget(label)
    return row, label


class SegmentBar(QFrame):
    """One rounded backing with a lit pill per section.

    The segments are checkable buttons in an exclusive group, so the active one
    is a repaint of the same widget rather than a new widget per click, and the
    bar sizes itself to its content instead of stretching across the panel.
    """

    changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentBar")
        # The bar's own 1px frame eats into the content box, so it is added
        # here: without it the layout clips the segment to 38 of its 40 pixels.
        self.setFixedHeight(SEGMENT_HEIGHT + 2 * SPACE_SM + 2)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        layout.setSpacing(SPACE_XS)
        self._buttons: list[QPushButton] = []
        self._active = 0

    @property
    def buttons(self) -> list[QPushButton]:
        return list(self._buttons)

    def add_segment(self, text: str, index: int) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Segment")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedHeight(SEGMENT_HEIGHT)
        button.clicked.connect(lambda _checked, position=index: self.set_active(position, notify=True))
        self._buttons.append(button)
        self.layout().addWidget(button)
        self._sync()
        return button

    def set_text(self, index: int, text: str) -> None:
        """Rename one segment; the bar re-measures itself afterwards."""
        if 0 <= index < len(self._buttons):
            self._buttons[index].setText(text)
            self.updateGeometry()

    def set_active(self, index: int, *, notify: bool = False) -> None:
        """Light ``index``.

        ``notify`` is for a click: the checked state alone would not move the
        stacked view underneath, so the click also announces the new section.
        A tab change that arrives from the other side calls this without it and
        the two never chase each other.
        """
        if not 0 <= index < len(self._buttons):
            return
        if index != self._active:
            self._active = index
            if notify:
                self.changed.emit(index)
        self._sync()

    @property
    def active(self) -> int:
        return self._active

    def _sync(self) -> None:
        """Check the active segment and let the layout re-measure the bar."""
        for position, button in enumerate(self._buttons):
            button.setChecked(position == self._active)
        self.updateGeometry()


class ResultsPanel(QFrame):
    """A bordered panel with a segmented switch and one view per section."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultsPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_MD)

        self.segments = SegmentBar(self)
        layout.addWidget(self.segments, 0, Qt.AlignmentFlag.AlignLeft)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # The painted switch is the segment bar; the tab bar underneath only
        # exists so the pages stay a QStackedWidget with stable indices.
        self.tabs.tabBar().setVisible(False)
        self.segments.changed.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.segments.set_active)
        layout.addWidget(self.tabs, 1)

    def add_page(self, widget: QWidget, title: str) -> QWidget:
        """Add one result view under ``title`` and return the view."""
        index = self.tabs.addTab(widget, title)
        self.segments.add_segment(title, index)
        self.segments.set_active(self.tabs.currentIndex())
        return widget

    def add_result_list(self, title: str) -> QListWidget:
        """Add an empty, delegate-styled list for a non-track section."""
        listing = apply_row_delegate(QListWidget())
        listing.setObjectName("TrackList")
        self.add_page(listing, title)
        return listing

    @staticmethod
    def page_button(caption: str) -> QPushButton:
        """A 48px action for the header row; it keeps its natural width."""
        button = QPushButton(caption)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedHeight(CONTROL_HEIGHT)
        button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        return button

    def set_section_titles(self, titles: dict[str, str]) -> None:
        """Rename the sections from a ``{section: title}`` mapping."""
        for index, title in enumerate(titles.values()):
            if index < self.tabs.count():
                self.tabs.setTabText(index, title)
            self.segments.set_text(index, title)


__all__ = ["ResultsPanel", "SegmentBar", "header_row"]
