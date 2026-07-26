"""Position: the four table seats as a strict enum.

Shared across all ContrAI packages. Typing the seat as an enum keeps seat
arithmetic (who's next, who's the partner, who's an opponent) centralized
here instead of re-derived with ad-hoc string comparisons at every call
site.
"""

from __future__ import annotations

from enum import Enum


class Position(Enum):
    """The four seats around a contrée table.

    Definition order IS the anticlockwise turn order that bidding and
    card play both speak (N -> W -> S -> E -> N again): ``list(Position)``
    is therefore the canonical seating, and :attr:`next`, :attr:`partner`,
    and :attr:`opponents` all derive from that single ordering rather than
    each re-encoding the seat arithmetic separately.

    This is a plain :class:`~enum.Enum`, not a :class:`~enum.StrEnum`, on
    purpose: ``Position.NORTH == "North"`` is ``False`` — a ``Position``
    member never compares equal to a bare string, so every comparison
    must be against a ``Position`` member (``player.position ==
    Position.NORTH``, never a string literal). A stray string comparison
    yields a silent ``False`` rather than an exception, which is exactly
    why it is worth pinning with a dedicated test instead of trusting
    code review to catch it.

    Enum members are singletons, so identity comparison (``is``) is always
    safe and is what every derivation below uses.
    """

    NORTH = "North"
    WEST = "West"
    SOUTH = "South"
    EAST = "East"

    def __str__(self) -> str:
        """Render as the plain display name, e.g. ``"North"``.

        Load-bearing for every f-string and error-context message that
        embeds a position directly, e.g. ``f"{position} card play"``
        reads ``"North card play"`` (``__format__`` delegates to
        ``__str__`` by default). Without this override, the default
        ``Enum.__str__`` would print ``"Position.NORTH"`` instead.
        """

        return self.value

    @property
    def next(self) -> Position:
        """The next seat anticlockwise — the turn-order successor.

        Cycles through all four seats: applying ``.next`` four times in a
        row returns to the seat it started from.
        """

        seats = list(Position)
        return seats[(seats.index(self) + 1) % len(seats)]

    @property
    def partner(self) -> Position:
        """The seat directly across the table.

        Two steps ahead in turn order (``next.next``): the four seats
        alternate between the two teams, so stepping over one opponent
        lands on the partner.
        """

        return self.next.next

    @property
    def opponents(self) -> tuple[Position, Position]:
        """The two seats on the other team, in anticlockwise order.

        Seats alternate teams around the table — self, an opponent,
        partner, the other opponent — so the opponents are exactly the
        seat right after ``self`` and the seat right after :attr:`partner`.
        """

        return (self.next, self.partner.next)

    @property
    def french_name(self) -> str:
        """The lowercase French seat name used by the scraper's DOM ids.

        ``app.belote-rebelote.fr`` identifies seats via the element ids
        ``#nord`` / ``#ouest`` / ``#sud`` / ``#est``; this is this
        position's entry in that vocabulary.
        """

        return _TO_FRENCH[self]

    @classmethod
    def from_french(cls, name: str) -> Position:
        """Parse a French seat name into a :class:`Position`.

        The strict counterpart to :attr:`french_name`, for the scraper
        turning a DOM id back into a seat.

        Args:
            name: One of ``"nord"``, ``"ouest"``, ``"sud"``, ``"est"``
                (lowercase, matching the scraper's DOM ids exactly).

        Returns:
            The matching :class:`Position` member.

        Raises:
            ValueError: If ``name`` is not one of the four French seat
                names.
        """

        try:
            return _FROM_FRENCH[name]
        except KeyError:
            raise ValueError(
                f"Unknown French seat name: {name!r}. "
                f"Must be one of {sorted(_FROM_FRENCH)}."
            ) from None


# Position -> French seat name, keyed to the DOM ids that
# app.belote-rebelote.fr polls (#nord/#ouest/#sud/#est). This lookup table
# lives at module level, after the class body, rather than as a dict-valued
# class attribute: Enum treats every class-body assignment as a candidate
# member, so a dict literal there would itself become a (nonsensical) fifth
# member instead of a plain lookup table.
_TO_FRENCH: dict[Position, str] = {
    Position.NORTH: "nord",
    Position.WEST: "ouest",
    Position.SOUTH: "sud",
    Position.EAST: "est",
}
_FROM_FRENCH: dict[str, Position] = {
    name: position for position, name in _TO_FRENCH.items()
}
