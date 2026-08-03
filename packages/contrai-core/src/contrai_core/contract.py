"""Contract class for the contrée card game.

Two views of the same established contract live here:

- :class:`Contract` — the authoritative one, holding the live players
  who declared, doubled and redoubled plus the declaring :class:`Team`.
  This is what the engine's ``Round`` scores from.
- :class:`ObservedContract` — the sealed projection, naming those same
  people by :class:`~contrai_core.Position` alone. This is what a
  :class:`~contrai_core.PlayObservation` carries, so a card-play
  strategy reading the contract cannot reach a live player and, through
  them, another seat's hand.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from .bid import ContractBid, SlamLevel
from .exceptions import InvalidContractError

if TYPE_CHECKING:
    from .player import BasePlayer as Player
    from .position import Position
    from .types import ContractSuit

class Contract:
    """
    Represents a contract established during bidding.

    Contains contract details like value, trump suit, contracting player/team,
    and handles double/redouble states with score calculations.
    """

    def __init__(self, contract_bid: ContractBid,
                 double_player: Optional[Player] = None,
                 redouble_player: Optional[Player] = None):
        """
        Initialize a contract from a ContractBid.

        The doubled / redoubled state is *derived* from whether a caller
        is recorded (see :attr:`double` / :attr:`redouble`) — there is no
        separate boolean flag to keep in sync, so an "anonymous double"
        (doubled with no known doubler) is unrepresentable by design.

        Args:
            contract_bid: The winning ContractBid that established this contract
            double_player: The player who doubled (coincheur), if any.
                Its presence is what marks the contract as doubled.
            redouble_player: The player who redoubled (surcoincheur), if any.
                Its presence is what marks the contract as redoubled.

        Raises:
            InvalidContractError: If a ``redouble_player`` is given
                without a ``double_player`` — a surcoinche can only stand
                on top of a coinche.
        """
        if redouble_player is not None and double_player is None:
            raise InvalidContractError(
                "A contract cannot be redoubled without first being "
                "doubled: redouble_player was given but double_player is None."
            )
        self.contract_bid = contract_bid
        self.player = contract_bid.player
        self.team = contract_bid.player.team
        self.value = contract_bid.value
        self.suit: ContractSuit = contract_bid.suit
        self.double_player = double_player
        self.redouble_player = redouble_player

    @property
    def double(self) -> bool:
        """Whether the contract has been doubled (coinche).

        Derived from :attr:`double_player`: a contract is doubled iff a
        doubling player is recorded.
        """
        return self.double_player is not None

    @property
    def redouble(self) -> bool:
        """Whether the contract has been redoubled (surcoinche).

        Derived from :attr:`redouble_player`. The constructor guarantees
        a redouble can only exist on top of a double.
        """
        return self.redouble_player is not None

    def get_multiplier(self) -> int:
        """
        Get the score multiplier based on double/redouble state.

        Returns:
            4 for redoubled, 2 for doubled, 1 for normal
        """
        if self.redouble:
            return 4
        elif self.double:
            return 2
        return 1

    def is_slam(self) -> bool:
        """
        Check if this is a Slam contract (team must win all 8 tricks).

        Returns:
            True if contract value is ``SlamLevel.SLAM``, False otherwise.
        """
        return self.value is SlamLevel.SLAM

    def is_solo_slam(self) -> bool:
        """
        Check if this is a Solo Slam contract.

        In a Solo Slam the bidder *personally* must win every one of
        the 8 tricks — their partner is forbidden from winning any.

        Returns:
            True if contract value is ``SlamLevel.SOLO_SLAM``, False
            otherwise.
        """
        return self.value is SlamLevel.SOLO_SLAM

    def is_slam_family(self) -> bool:
        """Whether this contract is a Slam or Solo Slam."""
        return isinstance(self.value, SlamLevel)

    def get_base_points(self) -> int:
        """
        Get the base point value of the contract — what the bidder
        committed to and what shows up in the auction's precedence
        ordering.

        Returns:
            250 for Slam, 500 for Solo Slam, the numeric value
            otherwise.

        Note:
            For Slam-family contracts this is only *half* of the
            at-risk amount — the actual card pile (normally up to
            162) is replaced by a flat substitute equal to the base.
            See :meth:`get_slam_card_substitute`. The full at-risk
            amount is ``(base + substitute) × multiplier`` and is
            awarded to whichever side wins the contract (attacker
            if made, defender if failed).
        """
        return self.contract_bid.get_numeric_value()

    def get_slam_card_substitute(self) -> int:
        """
        Return the flat amount that replaces the 162 of trick-card
        points on a Slam-family round.

        For Slam the substitute is 250; for Solo Slam it is 500.
        For numeric (80-180) contracts there is no substitute —
        teams actually count the cards they took — and this method
        returns 0.

        The Slam-family at-risk amount is
        ``(get_base_points() + get_slam_card_substitute()) × get_multiplier()``,
        i.e. ``500 / 1000 / 2000`` for Slam at normal / doubled /
        redoubled and ``1000 / 2000 / 4000`` for Solo Slam.

        Returns:
            250 for Slam, 500 for Solo Slam, 0 otherwise.
        """
        if isinstance(self.value, SlamLevel):
            return self.value.base_value
        return 0

    def __str__(self) -> str:
        """String representation of the contract."""
        multiplier_str = ""
        if self.redouble:
            multiplier_str = " (Redoubled)"
        elif self.double:
            multiplier_str = " (Doubled)"

        return f"{self.value} {self.suit} by {self.player.name}{multiplier_str}"

    def __eq__(self, other) -> bool:
        """Equality comparison between contracts."""
        return (isinstance(other, Contract) and
                self.contract_bid == other.contract_bid and
                self.double == other.double and
                self.redouble == other.redouble)

    def observed(self) -> "ObservedContract":
        """Project this contract down to seat identifiers.

        The contract half of the observation trust boundary — see
        :class:`ObservedContract` for what the projection keeps and why.

        Returns:
            The same contract terms with every person named by seat.
        """
        return ObservedContract(
            declarer=self.player.position,
            value=self.value,
            suit=self.suit,
            doubled_by=(
                self.double_player.position
                if self.double_player is not None
                else None
            ),
            redoubled_by=(
                self.redouble_player.position
                if self.redouble_player is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ObservedContract:
    """The established contract as an observation reports it.

    The observation-facing counterpart of :class:`Contract`. Where a
    ``Contract`` holds live :class:`~contrai_core.BasePlayer` references
    — ``player``, ``double_player``, ``redouble_player``, and a
    :class:`~contrai_core.Team` whose ``players`` list reaches both its
    members' hands — an ``ObservedContract`` names each of them by seat
    alone. That is exactly the public knowledge a player at the table
    has: *West declared 120 in hearts and North doubled it.*

    It carries the contract terms a card-play strategy actually reasons
    from, under the same names :class:`Contract` uses so a reader moving
    between the two is not relearning an API. The scoring helpers
    (``get_base_points`` / ``get_slam_card_substitute``) are deliberately
    **not** mirrored: scoring reads the authoritative ``Contract`` off
    the round, never an observation.

    The declaring *team* is not a field either. A seat's side is
    derivable — ``position.is_teammate(contract.declarer)`` — so
    carrying a second representation of the same pairing would just be
    something else to keep consistent.

    Attributes:
        declarer: The seat that won the auction and owns the contract.
        value: The contracted amount — a numeric step (80–180) or a
            :class:`~contrai_core.SlamLevel` member.
        suit: The trump the contract names, which may be
            ``TrumpVariant.NO_TRUMP``.
        doubled_by: The seat that called *coinche*, or ``None``. Its
            presence is what marks the contract doubled.
        redoubled_by: The seat that called *surcoinche*, or ``None``.
    """

    declarer: Position
    value: int | SlamLevel
    suit: ContractSuit
    doubled_by: Optional[Position] = None
    redoubled_by: Optional[Position] = None

    @property
    def double(self) -> bool:
        """Whether the contract has been doubled (coinche)."""
        return self.doubled_by is not None

    @property
    def redouble(self) -> bool:
        """Whether the contract has been redoubled (surcoinche)."""
        return self.redoubled_by is not None

    def get_multiplier(self) -> int:
        """Get the score multiplier based on double/redouble state.

        Returns:
            4 for redoubled, 2 for doubled, 1 for normal.
        """
        if self.redouble:
            return 4
        if self.double:
            return 2
        return 1

    def is_slam(self) -> bool:
        """Whether this is a Slam contract (the team must win all 8 tricks)."""
        return self.value is SlamLevel.SLAM

    def is_solo_slam(self) -> bool:
        """Whether this is a Solo Slam (the declarer personally wins all 8)."""
        return self.value is SlamLevel.SOLO_SLAM

    def is_slam_family(self) -> bool:
        """Whether this contract is a Slam or Solo Slam."""
        return isinstance(self.value, SlamLevel)

    def __str__(self) -> str:
        """String representation of the observed contract."""
        multiplier_str = ""
        if self.redouble:
            multiplier_str = " (Redoubled)"
        elif self.double:
            multiplier_str = " (Doubled)"

        return f"{self.value} {self.suit} by {self.declarer}{multiplier_str}"
