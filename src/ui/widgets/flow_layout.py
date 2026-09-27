"""A layout that wraps its children onto new rows.

Qt's box layouts are the reason chip rows used to be unreadable: six Russian
mood chips in a half-width column were asked to share one line, so every one of
them was squeezed below its own text width and the labels came out as
fragments.  A layout that has a height to fill wraps instead of squeezing, and
``heightForWidth`` lets the row above it make room.

This is the classic Qt flow layout: children keep their own size hint, the
layout reports the height it needs for a given width, and nothing is ever asked
to be smaller than it wants to be.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QWidgetItem, QWidget

SPACING = 8
"""Gap between chips, and the gap between one row and the next."""


class FlowLayout(QLayout):
    """Left-aligned chips that wrap when the row runs out of width."""

    def __init__(self, parent: QWidget | None = None, spacing: int = SPACING) -> None:
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self._spacing = max(0, int(spacing))
        if parent is not None:
            self.setContentsMargins(0, 0, 0, 0)

    def __del__(self) -> None:
        while self._items:
            self._items.pop()

    # -- QLayout interface -------------------------------------------------

    def addItem(self, item) -> None:  # noqa: N802 - Qt naming
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt naming
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802 - Qt naming
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt naming
        # Nothing expands: a chip that grew to fill the row would be a button
        # with a lot of empty space in it, and would still be one click wide.
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt naming
        return self._layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt naming
        super().setGeometry(rect)
        self._layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt naming
        if not self._items:
            return QSize()
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom(),
        )

    def clear(self) -> None:
        while self.count():
            item = self.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().setParent(None)

    def setSpacing(self, spacing: int) -> None:  # noqa: N802 - Qt naming
        self._spacing = max(0, int(spacing))
        self.invalidate()

    def spacing(self) -> int:
        return self._spacing

    def setContentsMargins(self, left: int, top: int, right: int, bottom: int) -> None:  # noqa: N802
        super().setContentsMargins(left, top, right, bottom)
        self.invalidate()

    def contentsMargins(self) -> QMargins:  # noqa: N802 - Qt naming
        margins = super().contentsMargins()
        return QMargins(margins.left(), margins.top(), margins.right(), margins.bottom())

    # -- geometry ----------------------------------------------------------

    def _layout(self, rect: QRect, test_only: bool) -> int:
        """Place every item, wrapping at ``rect``; return the height used."""
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if line_height > 0 and next_x - self._spacing > effective.right():
                # This chip does not fit the current row: start a new one under it.
                x = effective.x()
                y += line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


__all__ = ["SPACING", "FlowLayout"]
