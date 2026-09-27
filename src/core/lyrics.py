"""LRC parsing and the timed lookup that drives the karaoke view.

The drawer needs two things from a lyrics payload: the lines, and for each
moment of the track, which line is being sung.  Both live here, away from Qt,
because the interesting failures - a line with three timestamps, a two-digit
fraction, a stray ``[00:12]`` with no text - are arithmetic, and arithmetic is
testable without an event loop.

Two shapes come back from the API.  A synchronised track gives LRC, lines with
timestamps, and the view can follow it.  An unsynchronised one gives plain text,
which is still worth showing, so it is returned as untimed lines rather than
thrown away: :attr:`Lyrics.synchronized` is what tells the view which of the
two it has.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

__all__ = ["LyricLine", "Lyrics", "parse_lrc"]

_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_METADATA = re.compile(r"^\[(ar|ti|al|by|au|length|re|ve|offset):(.*)\]$", re.IGNORECASE)
_TAG = re.compile(r"\[([a-zA-Z#]+):(.*)\]")
"""Any ``[name:value]`` tag, used to spot a line that is *only* metadata.

A line such as ``[00:12.00]`` with nothing after it is a real thing in the wild
- the instrumental gap before the first sung line.  It has a time and no text,
so it must be kept as a timed empty line or the highlight would jump over the
silence and skip a line.
"""

MIN_FRACTION_DIGITS = 2
"""``.5`` and ``.50`` both mean a half second, so a fraction is padded first."""


def _fraction_to_ms(digits: str | None) -> int:
    """Turn the part after the colon into milliseconds.

    ``.5``, ``.05`` and ``.050`` are all 500ms: the digits are scaled by their
    own length rather than assumed to be hundredths, because a three-digit
    fraction means milliseconds and a one-digit one means tenths.
    """
    if not digits:
        return 0
    padded = digits.ljust(MIN_FRACTION_DIGITS, "0")
    return int(padded) * (10 ** (3 - len(padded)))


def _time_to_ms(minutes: str, seconds: str, fraction: str | None) -> int:
    return (int(minutes) * 60 + int(seconds)) * 1000 + _fraction_to_ms(fraction)


@dataclass(frozen=True)
class LyricLine:
    """One line of a lyric, timed when the source had a timestamp."""

    text: str
    time_ms: int | None = None
    index: int = 0

    @property
    def timed(self) -> bool:
        return self.time_ms is not None

    @property
    def empty(self) -> bool:
        """A timestamp with no words, which is a gap rather than a line."""
        return not self.text.strip()


@dataclass(frozen=True)
class Lyrics:
    """A parsed lyric: the lines, plus whatever the header tags carried."""

    lines: tuple[LyricLine, ...] = ()
    title: str = ""
    artist: str = ""
    offset_ms: int = 0
    """The ``[offset:]`` lag in milliseconds, positive meaning the lyrics run late."""
    timed_ms: tuple[int, ...] = field(default=(), repr=False)
    """The sorted timestamps, kept for :meth:`index_at`."""

    @property
    def synchronized(self) -> bool:
        """Whether these lines can be followed in time."""
        return bool(self.timed_ms)

    @property
    def empty(self) -> bool:
        return not any(not line.empty for line in self.lines)

    def index_at(self, position_ms: int) -> int:
        """The line being sung at ``position_ms``, or ``-1`` before the first.

        The last timed line sticks: once the vocals have stopped, the reader
        still shows the final line instead of blanking the drawer, which is
        what a person reading along would do.  The comparison is
        ``bisect_right`` so a line is current from its own timestamp onwards,
        and the ``[offset:]`` lag is applied here so every caller gets the same
        answer.
        """
        if not self.timed_ms:
            return -1
        position = int(position_ms) - self.offset_ms
        found = bisect_right(self.timed_ms, position) - 1
        return found if found >= 0 else -1

    def line_at(self, position_ms: int) -> LyricLine | None:
        index = self.index_at(position_ms)
        if index < 0:
            return None
        for line in self.lines:
            if line.timed and line.time_ms == self.timed_ms[index]:
                return line
        return None

    def static_lines(self) -> tuple[str, ...]:
        """The text of every non-empty line, for a plain-text view."""
        return tuple(line.text.strip() for line in self.lines if not line.empty)


def parse_lrc(raw: str | None) -> Lyrics:
    """Parse an LRC payload, or a plain-text one, into :class:`Lyrics`.

    Every failure mode here is a reason to keep going rather than to raise: a
    lyric is decoration over a playing track, and a track must not stop because
    one of its lines has an odd number of brackets.
    """
    text = raw or ""
    if not text.strip():
        return Lyrics()

    title = ""
    artist = ""
    offset_ms = 0
    timed: list[tuple[int, str]] = []
    plain: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        meta = _METADATA.match(stripped)
        if meta is not None:
            key = meta.group(1).lower()
            value = meta.group(2).strip()
            if key == "ti":
                title = title or value
            elif key in ("ar", "au"):
                artist = artist or value
            elif key == "offset":
                # The tag is the lag of the lyrics behind the music: a positive
                # offset means a line stamped 10.000 is sung at 10.500.  The
                # value is kept as it stands and subtracted in
                # :meth:`Lyrics.index_at`, so the sign is applied in exactly one
                # place and cannot be inverted twice.
                try:
                    offset_ms = int(value)
                except (TypeError, ValueError):
                    offset_ms = 0
            continue
        if _TAG.match(stripped) and not _TIMESTAMP.search(stripped):
            # Some other tag we do not use ([length:…], [re:…], a [Chorus]
            # section header). Skipped rather than shown as a lyric line.
            continue

        stamps = list(_TIMESTAMP.finditer(stripped))
        if not stamps:
            plain.append(stripped)
            continue
        # Everything before the first timestamp is a label, not a lyric.
        body = stripped[stamps[-1].end() :].strip()
        for stamp in stamps:
            timed.append((_time_to_ms(stamp.group(1), stamp.group(2), stamp.group(3)), body))

    if not timed:
        return Lyrics(lines=tuple(LyricLine(text=line, index=index) for index, line in enumerate(plain)))

    timed.sort(key=lambda pair: (pair[0], pair[1]))
    lines: list[LyricLine] = []
    seen: set[tuple[int, str]] = set()
    for time_ms, body in timed:
        key = (time_ms, body)
        if key in seen:
            continue
        seen.add(key)
        lines.append(LyricLine(text=body, time_ms=time_ms, index=len(lines)))
    return Lyrics(
        lines=tuple(lines),
        title=title,
        artist=artist,
        offset_ms=offset_ms,
        timed_ms=tuple(line.time_ms or 0 for line in lines),
    )
