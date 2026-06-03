# Contract class for the "contree" card game.
# This class represents a contract established during bidding.

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from .bid import ContractBid

if TYPE_CHECKING:
    from .player import BasePlayer as Player

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
            ValueError: If a ``redouble_player`` is given without a
                ``double_player`` — a surcoinche can only stand on top of
                a coinche.
        """
        if redouble_player is not None and double_player is None:
            raise ValueError(
                "A contract cannot be redoubled without first being "
                "doubled: redouble_player was given but double_player is None."
            )
        self.contract_bid = contract_bid
        self.player = contract_bid.player
        self.team = contract_bid.player.team
        self.value = contract_bid.value
        self.suit = contract_bid.suit
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
            True if contract value is 'Slam', False otherwise.
        """
        return self.value == 'Slam'

    def is_solo_slam(self) -> bool:
        """
        Check if this is a Solo Slam contract.

        In a Solo Slam the bidder *personally* must win every one of
        the 8 tricks — their partner is forbidden from winning any.

        Returns:
            True if contract value is 'SoloSlam', False otherwise.
        """
        return self.value == 'SoloSlam'

    def is_slam_family(self) -> bool:
        """Whether this contract is a Slam or Solo Slam."""
        return self.value in ('Slam', 'SoloSlam')

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
        if self.value == 'Slam':
            return 250
        if self.value == 'SoloSlam':
            return 500
        return self.value

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
        if self.value == 'Slam':
            return 250
        if self.value == 'SoloSlam':
            return 500
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
