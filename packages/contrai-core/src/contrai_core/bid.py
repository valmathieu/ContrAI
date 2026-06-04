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
from typing import TYPE_CHECKING, ClassVar

from .exceptions import InvalidContractError
from .types import Suit

if TYPE_CHECKING:
    from .player import BasePlayer


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

    Two string sentinels represent the all-tricks contracts:

    - ``"Slam"`` — the contracting team must win all 8 tricks. Outranks
      every numeric bid (80–180).
    - ``"SoloSlam"`` — the contracting **player personally** must win
      all 8 tricks (their partner may not win any). Outranks Slam in
      raw numeric value, but is asymmetrically blocked once a Slam has
      been announced (see :class:`contrai_core.Auction`).

    Attributes:
        value: A numeric step (80, 90, 100, …, 180), or one of the
            literal strings ``"Slam"`` / ``"SoloSlam"``.
        suit: The trump suit — any :class:`Suit`, including
            ``Suit.NO_TRUMP``.
    """

    VALID_VALUES: ClassVar[list] = [
        80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180,
        "Slam", "SoloSlam",
    ]
    VALID_SUITS: ClassVar[list] = list(Suit)

    value: int | str
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

        Sentinels map to the contract's *base value* — i.e. the amount
        the bidder commits to, used both for auction precedence and as
        one of the two halves of the Slam-family scoring formula.
        ``"Slam"`` → 250, ``"SoloSlam"`` → 500. (Both still outrank the
        numeric ceiling of 180.)

        The final at-risk amount on a Slam-family round is
        ``(base + substitute) × multiplier`` where ``substitute``
        equals the base — see :meth:`contrai_core.Contract.get_base_points`
        and :meth:`contrai_core.Contract.get_slam_card_substitute`.
        """

        if self.value == "Slam":
            return 250
        if self.value == "SoloSlam":
            return 500
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
