"""TeamSide: the two sides of the table as a strict enum.

Shared across all ContrAI packages. A contrée table has exactly two
sides, fixed by the seating: North/South face each other, so do
East/West. This enum is that identity — the stable token score
bookkeeping keys by, and the one a persisted game record or a training
label serializes.

It is deliberately *not* a display name: ``"North-South"`` and ``"N-S"``
are presentation strings owned by the view, and renaming one of them
must not be able to break a score lookup.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .position import Position


class TeamSide(Enum):
    """The two sides of a contrée table.

    Members:
        NS: The North/South side.
        EW: The East/West side.

    This is a plain :class:`~enum.Enum`, not a :class:`~enum.StrEnum`,
    for the same reason :class:`~contrai_core.position.Position` is:
    ``TeamSide.NS == "NS"`` is ``False``, and so is
    ``TeamSide.NS == "North-South"``. Score dictionaries are keyed by
    these members, so a leftover string key must miss loudly rather than
    resolve to the right bucket by accident — the point of typing the
    identity is precisely that the old free-form names stop comparing
    equal.

    Member values are the short serialization tokens ``"NS"`` / ``"EW"``,
    which :meth:`__str__` returns: the enum round-trips through
    ``TeamSide(record["side"])`` without ever being string-*equal* to
    that record's value.

    Enum members are singletons, so identity comparison (``is``) is
    always safe and is what every derivation here uses.
    """

    NS = "NS"
    EW = "EW"

    def __str__(self) -> str:
        """Render as the short side token, e.g. ``"NS"``.

        Keeps f-strings and log lines that embed a side reading as
        ``"NS"`` rather than the default ``Enum.__str__`` output
        ``"TeamSide.NS"``. Human-facing labels are the view's job — see
        the engine's ``TEAM_LABEL`` / ``TEAM_ABBR`` mappings.
        """

        return self.value

    @property
    def positions(self) -> tuple[Position, ...]:
        """The two seats on this side, in anticlockwise order.

        Derived from :class:`~contrai_core.position.Position`'s seating
        order rather than listed here, so there is exactly one place
        where "which seats pair up" is encoded.
        """

        from .position import Position

        return tuple(
            position for position in Position if position.team_side is self
        )

    @property
    def opponent(self) -> TeamSide:
        """The other side of the table.

        There are exactly two sides, so this is an involution:
        ``side.opponent.opponent is side``.
        """

        return TeamSide.EW if self is TeamSide.NS else TeamSide.NS
