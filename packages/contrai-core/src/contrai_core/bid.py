"""Bid hierarchy — pure value carriers for the bidding phase.

Each :class:`Bid` is a frozen dataclass attached to whoever made it —
a live :class:`~contrai_core.BasePlayer` in an auction, a bare
:class:`~contrai_core.Position` once projected into an observation. The
hierarchy is generic over that slot (:data:`ActorT`) rather than
duplicated, so both sides speak the same four variants:

- :class:`PassBid` — the player declines to act.
- :class:`ContractBid` — a numeric contract or *Slam* / *Solo Slam*
  bid with an associated trump suit.
- :class:`DoubleBid` — *contre*.
- :class:`RedoubleBid` — *surcontre*.

Knowledge about which bids are *legal at which auction state* used to
live on ``Bid.is_valid_after`` and the ``BidValidator`` utility class.
That logic now lives on :class:`contrai_core.Auction`, which owns the
chronological history and the rules in one place. Bids themselves are
intentionally dumb data carriers — they answer "what was bid",
not "is it legal now".

The variants are deliberately a sum type: any concrete ``Bid`` is one
of the four classes above, every subclass adds at most a couple of
payload fields, and there is no behaviour to override. This is the
shape pattern-matching consumers (Auction's rule helpers, the engine's
bid-to-wire bridge, future MCTS / RL agents) actually want.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from .exceptions import InvalidContractError
from .rule_config import AllTrumpBelote, RuleConfig
from .types import CONTRACT_SUITS, ContractSuit, Suit, TrumpVariant

if TYPE_CHECKING:
    from .player import BasePlayer
    from .position import Position

#: The type naming *who* made a bid. The bid itself is unchanged
#: whoever holds the slot, so the hierarchy is generic over it rather
#: than duplicated per actor kind: auction-side code speaks
#: ``Bid[BasePlayer]`` (the live player, whose team and hand the engine
#: needs), while the sealed observation surface speaks ``Bid[Position]``
#: (a bare seat token, through which no hand is reachable). See
#: :meth:`contrai_core.PlayState.observe`.
ActorT = TypeVar("ActorT", "BasePlayer", "Position")


class SlamLevel(Enum):
    """The two all-tricks contracts, ranked above every numeric bid.

    A Slam-family contract's *identity* is the kind of declaration — not
    the number of points it is worth. Each member therefore owns its
    :attr:`base_value` as data: one source of truth for the 250 / 500,
    rather than a constant each caller re-derives for itself. The base
    value drives auction precedence (both members outrank the 180
    numeric ceiling) and doubles as the slam-family scoring
    substitute — see
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
class Bid(Generic[ActorT]):
    """Common base class for all bid variants.

    Holds whoever made the bid. Concrete subclasses add their own
    payload fields (a numeric value + suit for :class:`ContractBid`,
    nothing for the other three).

    Generic over :data:`ActorT`, the type filling the ``player`` slot.
    An auction holds ``Bid[BasePlayer]``; the imperfect-information
    :class:`contrai_core.PlayObservation` holds ``Bid[Position]``,
    projected seat by seat so a strategy reading the auction history
    cannot reach a live player — and through it another seat's hand.
    Parameterizing beats duplicating the four variants into a parallel
    "observed bid" hierarchy: the bid is identical either way,
    and every ``isinstance`` / ``match`` over the sum type keeps working
    on both sides of the projection.

    Equality on bids is *type + payload*, not actor identity. Two
    ``PassBid`` instances from different players still compare equal —
    a bid identifies *what was bid*, not *who bid it*. The
    ``player`` field is therefore excluded from the auto-generated
    ``__eq__`` / ``__hash__`` via :func:`dataclasses.field`. That also
    makes a bid equal to its own sealed projection, since sealing
    rewrites only the excluded field.

    Attributes:
        player: Whoever made the bid — a live ``BasePlayer`` in an
            auction, a bare ``Position`` in an observation.
    """

    player: ActorT = field(compare=False)


@dataclass(frozen=True, slots=True)
class PassBid(Bid[ActorT]):
    """The player declines to bid this turn.

    Always a legal action in any :class:`contrai_core.Auction` state.
    """

    def __str__(self) -> str:
        return "Pass"


@dataclass(frozen=True, slots=True)
class ContractBid(Bid[ActorT]):
    """A numeric contract or *Slam* / *Solo Slam* bid.

    Validated at construction via ``__post_init__``, and only that far:
    the value must be one of the table-defined steps and the suit one of
    the six contract trumps. That is *well-formedness*, not legality —
    whether this table offers that trump choice at all, and how high its
    numeric ladder goes, are rules of the auction, answered by
    :func:`bookable_suits` and :func:`ladder_top` from the round's
    :class:`~contrai_core.RuleConfig`. Keeping the split here is what
    lets a caller build a bid from user input and *then* ask whether it
    is legal, with an explanation, instead of catching an exception.

    The two all-tricks contracts are the :class:`SlamLevel` enum members:

    - :attr:`SlamLevel.SLAM` — the contracting team must win all 8
      tricks. Outranks every numeric bid (80–240).
    - :attr:`SlamLevel.SOLO_SLAM` — the contracting **player
      personally** must win all 8 tricks (their partner may not win
      any). Outranks Slam in raw numeric value, but is asymmetrically
      blocked once a Slam has been bid (see
      :class:`contrai_core.Auction`).

    Attributes:
        value: A numeric step (80, 90, 100, …, 240), or a
            :class:`SlamLevel` member for the all-tricks contracts. The
            steps past 180 exist only for the all-trump ``four`` belote
            regime; :func:`ladder_top` is what keeps them out of the
            other modes.
        suit: The trump — one of the four :class:`Suit` members or
            either :class:`TrumpVariant`. See :attr:`VALID_SUITS`.
    """

    #: Every numeric step a well-formed bid may name, plus the two
    #: all-tricks contracts. The ladder runs to 240 because the all-trump
    #: ``four`` regime can mark 162 on cards plus four Belotes; where a
    #: given mode actually stops is :func:`ladder_top`'s answer.
    VALID_VALUES: ClassVar[list] = [
        80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180,
        190, 200, 210, 220, 230, 240,
        SlamLevel.SLAM, SlamLevel.SOLO_SLAM,
    ]
    #: Every contract trump a well-formed bid may name. Which of the six a
    #: *table* actually offers is a rule of the auction, not of the bid —
    #: see :func:`bookable_suits`, which
    #: :meth:`contrai_core.Auction.legal_actions` iterates instead.
    VALID_SUITS: ClassVar[list] = list(CONTRACT_SUITS)

    value: int | SlamLevel
    suit: ContractSuit

    def __post_init__(self) -> None:
        """Reject malformed values / suits at construction time.

        Well-formedness only: a step on the 80–240 ladder (or a
        :class:`SlamLevel`) naming one of the six contract trumps. A bid
        that passes here may still be illegal at the table it was made
        at — :meth:`contrai_core.Auction.is_legal` is the authority on
        that.

        Raises:
            InvalidContractError: If ``value`` is not on
                :attr:`VALID_VALUES`, or ``suit`` is not on
                :attr:`VALID_SUITS`.
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
        ``substitute + base × multiplier`` where ``substitute``
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
class DoubleBid(Bid[ActorT]):
    """A *contre* — doubles the contract's stake (×2)."""

    def __str__(self) -> str:
        return "Double"


@dataclass(frozen=True, slots=True)
class RedoubleBid(Bid[ActorT]):
    """A *surcontre* — quadruples the contract's stake (×4)."""

    def __str__(self) -> str:
        return "Redouble"


def seal_bid(bid: Bid[BasePlayer]) -> Bid[Position]:
    """Project a bid onto its bidder's seat, dropping the live player.

    The bid-side half of the observation trust boundary: a live
    :class:`~contrai_core.BasePlayer` in the ``player`` slot is an
    object path to ``player.hand`` and, via ``player.team``, to the
    partner's hand as well. Replacing it with the bare
    :class:`~contrai_core.Position` leaves exactly the public fact —
    *this seat bid this* — that a player at the table has.

    Rebuilt with :func:`dataclasses.replace`, so the concrete variant
    survives: a :class:`ContractBid` seals to a ``ContractBid``, and
    every ``isinstance`` / ``match`` over the sum type reads the same on
    both sides. Since ``player`` is ``compare=False``, the sealed bid
    also compares equal to the one it came from.

    Args:
        bid: The auction-side bid to project.

    Returns:
        The same bid by the same variant, its ``player`` slot
        holding the bidder's seat.
    """

    return replace(bid, player=bid.player.position)


#: The all-trump numeric ceiling per belote regime (contree-domain.md
#: §5.2). Cards plus the last-trick bonus cap at 162, so a mode with no
#: Belote stops at 160; each Belote the regime allows lifts the ceiling by
#: 20 and the last 10-point step with it.
_ALL_TRUMP_LADDER_TOP: Mapping[AllTrumpBelote, int] = MappingProxyType({
    AllTrumpBelote.NONE: 160,
    AllTrumpBelote.SINGLE: 180,
    AllTrumpBelote.FOUR: 240,
})


def bookable_suits(rules: RuleConfig) -> tuple[ContractSuit, ...]:
    """The trump choices a table under ``rules`` accepts (§5.2, §9.2).

    Args:
        rules: The table ruleset.

    Returns:
        The four card suits, plus ``NO_TRUMP`` and ``ALL_TRUMP`` when
        ``rules.extended_trump_choices`` is on. Card suits first, so the
        enumeration order of :meth:`contrai_core.Auction.legal_actions`
        is stable across both settings.
    """

    return CONTRACT_SUITS if rules.extended_trump_choices else tuple(Suit)


def ladder_top(contract_suit: ContractSuit, rules: RuleConfig) -> int:
    """The highest numeric bid legal under ``contract_suit`` (§5.2).

    The ladder top is the last 10-point step at or below what the mode can
    actually take: 162 on cards and the last-trick bonus, plus 20 for each
    Belote the mode admits. A suit contract admits one (180), no trump
    admits none (160), and all trump follows the table's
    ``all_trump_belote`` regime (160 / 180 / 240).

    Bidding a step only a Belote can reach is legal without holding it —
    the auction does not check hands — but commits the bidder to a
    contract that will fail on cards alone.

    Args:
        contract_suit: The trump choice the bid names.
        rules: The table ruleset.

    Returns:
        The highest legal numeric value. :class:`SlamLevel` bids are not
        ladder-capped: the Slam family is available under every trump
        choice at its own base value.
    """

    if contract_suit is TrumpVariant.NO_TRUMP:
        return 160
    if contract_suit is TrumpVariant.ALL_TRUMP:
        return _ALL_TRUMP_LADDER_TOP[rules.all_trump_belote]
    return 180
