"""Bid hierarchy — pure value carriers for the bidding phase.

Each :class:`Bid` is a frozen dataclass attached to the player who made
it. The four concrete variants are:

- :class:`PassBid` — the player declines to act.
- :class:`ContractBid` — a numeric contract or *Slam* / *Solo Slam*
  announcement with an associated trump suit.
- :class:`DoubleBid` — *contre*.
- :class:`RedoubleBid` — *surcontre*.

Knowledge about which bids are *legal at which auction state* used to
live on ``Bid.is_valid_after`` and the ``BidValidator`` utility class.
That logic now lives on :class:`contrai_core.Auction`, which owns the
chronological history and the rules in one place. Bids themselves are
intentionally dumb data carriers — they answer "what was announced",
not "is it legal now".

The variants are deliberately a sum type: any concrete ``Bid`` is one
of the four classes above, every subclass adds at most a couple of
payload fields, and there is no behaviour to override. This is the
shape pattern-matching consumers (Auction's rule helpers, the engine's
bid-to-wire bridge, future MCTS / RL agents) actually want.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from .exceptions import InvalidContractError
from .types import Suit

if TYPE_CHECKING:
    from .player import BasePlayer


class SlamLevel(Enum):
    """The two all-tricks contracts, ranked above every numeric bid.

    A Slam-family contract's *identity* is the kind of declaration — not
    the number of points it is worth. Each member therefore owns its
    :attr:`base_value` as data: this is the single source of truth for
    the 250 / 500 that used to be re-derived from string sentinels all
    over the codebase. The base value drives auction precedence (both
    members outrank the 180 numeric ceiling) and doubles as the
    slam-family scoring substitute — see
    :meth:`ContractBid.get_numeric_value`,
    :meth:`contrai_core.Contract.get_base_points`, and
    :meth:`contrai_core.Contract.get_slam_card_substitute`.

    This is a plain :class:`~enum.Enum`, not an :class:`~enum.IntEnum`:
    keeping the type distinct from ``int`` is the whole point — it stops
    a Slam's value from being silently mistaken for card points in
    scoring arithmetic.

    Attributes:
        base_value: Points the contract commits to (250 / 500).
        label: Human-facing name used for display (``str(level)``).
    """

    SLAM = (250, "Slam")          # contracting team must win all 8 tricks
    SOLO_SLAM = (500, "Solo Slam")  # bidder personally must win all 8

    def __init__(self, base_value: int, label: str) -> None:
        self.base_value = base_value
        self.label = label

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True, slots=True)
class Bid:
    """Common base class for all bid variants.

    Holds the player who made the bid. Concrete subclasses add their
    own payload fields (a numeric value + suit for :class:`ContractBid`,
    nothing for the other three).

    Equality on bids is *type + payload*, not player identity. Two
    ``PassBid`` instances from different players still compare equal —
    a bid identifies *what was announced*, not *who announced it*. The
    ``player`` field is therefore excluded from the auto-generated
    ``__eq__`` / ``__hash__`` via :func:`dataclasses.field`.

    Attributes:
        player: The player who made the bid.
    """

    player: "BasePlayer" = field(compare=False)


@dataclass(frozen=True, slots=True)
class PassBid(Bid):
    """The player declines to bid this turn.

    Always a legal action in any :class:`contrai_core.Auction` state.
    """

    def __str__(self) -> str:
        return "Pass"


@dataclass(frozen=True, slots=True)
class ContractBid(Bid):
    """A numeric contract or *Slam* / *Solo Slam* announcement.

    Validated at construction via ``__post_init__``: the value must be
    one of the table-defined steps and the suit must be a known
    :class:`Suit`.

    The two all-tricks contracts are the :class:`SlamLevel` enum members:

    - :attr:`SlamLevel.SLAM` — the contracting team must win all 8
      tricks. Outranks every numeric bid (80–180).
    - :attr:`SlamLevel.SOLO_SLAM` — the contracting **player
      personally** must win all 8 tricks (their partner may not win
      any). Outranks Slam in raw numeric value, but is asymmetrically
      blocked once a Slam has been announced (see
      :class:`contrai_core.Auction`).

    Attributes:
        value: A numeric step (80, 90, 100, …, 180), or a
            :class:`SlamLevel` member for the all-tricks contracts.
        suit: The trump suit — any :class:`Suit`, including
            ``Suit.NO_TRUMP``.
    """

    VALID_VALUES: ClassVar[list] = [
        80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180,
        SlamLevel.SLAM, SlamLevel.SOLO_SLAM,
    ]
    VALID_SUITS: ClassVar[list] = list(Suit)

    value: int | SlamLevel
    suit: Suit

    def __post_init__(self) -> None:
        """Reject unknown values / suits at construction time.

        Raises:
            InvalidContractError: If ``value`` is not on
                :attr:`VALID_VALUES` or ``suit`` is not a :class:`Suit`
                member.
        """

        if self.value not in self.VALID_VALUES:
            raise InvalidContractError(
                f"Invalid contract value: {self.value}. "
                f"Must be one of {self.VALID_VALUES}"
            )
        if self.suit not in self.VALID_SUITS:
            raise InvalidContractError(
                f"Invalid trump suit: {self.suit}. "
                f"Must be one of {self.VALID_SUITS}"
            )

    def get_numeric_value(self) -> int:
        """Numeric value for comparison purposes.

        :class:`SlamLevel` members resolve to their
        :attr:`~SlamLevel.base_value` — i.e. the amount the bidder
        commits to, used both for auction precedence and as one of the
        two halves of the Slam-family scoring formula.
        ``SlamLevel.SLAM`` → 250, ``SlamLevel.SOLO_SLAM`` → 500. (Both
        still outrank the numeric ceiling of 180.)

        The final at-risk amount on a Slam-family round is
        ``(base + substitute) × multiplier`` where ``substitute``
        equals the base — see :meth:`contrai_core.Contract.get_base_points`
        and :meth:`contrai_core.Contract.get_slam_card_substitute`.
        """

        if isinstance(self.value, SlamLevel):
            return self.value.base_value
        return self.value

    def __gt__(self, other) -> bool:
        """Strict numeric ordering against another :class:`ContractBid`.

        Comparisons against any other type return ``False`` — the
        bidding flow only orders contract bids against contract bids.
        """

        if not isinstance(other, ContractBid):
            return False
        return self.get_numeric_value() > other.get_numeric_value()

    def __str__(self) -> str:
        return f"{self.value} {self.suit}"


@dataclass(frozen=True, slots=True)
class DoubleBid(Bid):
    """A *contre* — doubles the contract's stake (×2)."""

    def __str__(self) -> str:
        return "Double"


@dataclass(frozen=True, slots=True)
class RedoubleBid(Bid):
    """A *surcontre* — quadruples the contract's stake (×4)."""

    def __str__(self) -> str:
        return "Redouble"
