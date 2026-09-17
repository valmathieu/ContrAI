"""When the scraper may watch, on the operator's own clock.

A schedule is a list of daily ranges in one named timezone. The box itself may
run on UTC; the ranges are what a person would write ("08:00-23:30") and are
read in the zone the profile names.

Only one question is hard, and it is the one a shift asks: *when does this
next change?* Building "today at 23:30" as a local datetime and subtracting
is the classic daylight-saving bug — on the spring-forward day 02:30 does not
exist, on the fall-back day 02:30 happens twice, and Python subtracts two
datetimes sharing a ``tzinfo`` on the wall clock rather than in real time. So
this module never builds a local time. It walks **UTC** minutes forward and
asks, for each real instant, whether that instant falls inside a range. Two
days of minutes is under three thousand conversions: slow enough to be
correct, fast enough to be free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .exceptions import ProfileError

#: ``HH:MM-HH:MM``, zero-padded.
_RANGE: Final = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")

#: Minutes in a day, which is also the only legal spelling of an end at midnight.
DAY_MINUTES: Final[int] = 24 * 60

#: How far ahead a change is looked for. Two days and a minute is enough for
#: any window that closes at all; one that never does answers ``None``.
_HORIZON_MINUTES: Final[int] = 2 * DAY_MINUTES + 1


@dataclass(frozen=True, slots=True)
class ActiveRange:
    """One daily range, as minutes after local midnight."""

    start: int
    """First minute inside the range, 0 to 1439."""

    end: int
    """First minute outside it, 1 to 1440. ``end <= start`` crosses midnight."""

    def covers(self, minute: int) -> bool:
        """Whether a local minute of the day falls inside the range.

        Args:
            minute: Minutes after local midnight.

        Returns:
            Whether it is inside.
        """

        if self.start < self.end:
            return self.start <= minute < self.end
        return minute >= self.start or minute < self.end


@dataclass(frozen=True, slots=True)
class Schedule:
    """The profile's ``[schedule]``: when to watch, and how to stop."""

    timezone: ZoneInfo
    """The zone the ranges are written in."""

    active: tuple[ActiveRange, ...]
    """The daily ranges; any one of them is enough to be open."""

    finish_current_game: bool
    """Whether a range closing mid-game lets that game finish."""

    max_overrun_minutes: int
    """How long past a close the game in hand may run on."""

    idle_poll_minutes: int
    """How often a closed schedule is asked again."""

    def __post_init__(self) -> None:
        if not self.active:
            raise ProfileError("[schedule].active must name at least one range")
        if self.idle_poll_minutes < 1:
            raise ProfileError("[schedule].idle_poll_minutes must be at least 1")
        if self.max_overrun_minutes < 0:
            raise ProfileError("[schedule].max_overrun_minutes may not be negative")

    def is_active(self, now: datetime) -> bool:
        """Whether an instant falls inside any range.

        Args:
            now: An aware instant, in any zone.

        Returns:
            Whether the scraper may watch at that instant.
        """

        local = now.astimezone(self.timezone)
        minute = local.hour * 60 + local.minute
        return any(window.covers(minute) for window in self.active)

    def next_change(self, now: datetime) -> datetime | None:
        """The first minute at which :meth:`is_active` flips.

        Args:
            now: An aware instant.

        Returns:
            That minute in UTC — the next opening when closed, the close when
            open — or ``None`` when nothing changes within two days, which is
            what an always-open schedule looks like.
        """

        state = self.is_active(now)
        # Seconds are dropped rather than rounded: a shift sleeps until this
        # instant, and a stray half-minute would wake it after the close.
        start = now.astimezone(UTC).replace(second=0, microsecond=0)
        for step in range(1, _HORIZON_MINUTES + 1):
            instant = start + timedelta(minutes=step)
            if self.is_active(instant) != state:
                return instant
        return None


def parse_range(text: str) -> ActiveRange:
    """Read one ``HH:MM-HH:MM`` range.

    Args:
        text: The range as the profile spells it.

    Returns:
        The range, in minutes after local midnight.

    Raises:
        ProfileError: The text is malformed, a time is out of range, or the
            range is empty (start equal to end — write ``00:00-24:00`` for
            always).
    """

    match = _RANGE.match(text)
    if match is None:
        raise ProfileError(f"[schedule].active holds {text!r}, not HH:MM-HH:MM")
    start_h, start_m, end_h, end_m = (int(group) for group in match.groups())
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    if start_m > 59 or end_m > 59 or start >= DAY_MINUTES or end > DAY_MINUTES:
        raise ProfileError(
            f"[schedule].active holds {text!r}, which is not a time of day"
        )
    # 24:00 is only ever an end (start >= DAY_MINUTES was refused above), so
    # equal minutes can only mean an empty range such as 08:00-08:00.
    if start == end:
        raise ProfileError(f"[schedule].active holds {text!r}, which is empty")
    return ActiveRange(start=start, end=end)


def timezone_named(name: str) -> ZoneInfo:
    """Resolve an IANA zone name.

    Args:
        name: The zone, as ``[schedule].timezone`` spells it.

    Returns:
        The zone.

    Raises:
        ProfileError: The zone is unknown (or, on Windows, ``tzdata`` is
            missing, which is why the package depends on it).
    """

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ProfileError(
            f"[schedule].timezone names {name!r}, which is not a known zone"
        ) from error
