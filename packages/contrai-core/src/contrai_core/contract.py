# Contract class for the "contree" card game.
# This class represents a contract established during bidding.

from typing import Optional, TYPE_CHECKING

from .bid import ContractBid

if TYPE_CHECKING:
    from .player import BasePlayer as Player
    from .team import Team

class Contract:
    """
    Represents a contract established during bidding.

    Contains contract details like value, trump suit, contracting player/team,
    and handles double/redouble states with score calculations.
    """

    def __init__(self, contract_bid: ContractBid, double: bool = False, redouble: bool = False):
        """
        Initialize a contract from a ContractBid.

        Args:
            contract_bid: The winning ContractBid that established this contract
            double: Whether contract has been doubled
            redouble: Whether contract has been redoubled
        """
        self.contract_bid = contract_bid
        self.player = contract_bid.player
        self.team = contract_bid.player.team
        self.value = contract_bid.value
        self.suit = contract_bid.suit
        self.double = double
        self.redouble = redouble

    @classmethod
    def from_legacy(cls, player: 'Player', value: int or str, suit: str,
                   double: bool = False, redouble: bool = False):
        """
        Create a Contract from legacy parameters (for backwards compatibility).

        Args:
            player: Player who made the winning bid
            value: Contract value (points to make)
            suit: Trump suit for the contract
            double: Whether contract has been doubled
            redouble: Whether contract has been redoubled
        """
        contract_bid = ContractBid(player, value, suit)
        return cls(contract_bid, double, redouble)

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

    def is_made(self, team_points: int) -> bool:
        """
        Check if the contract was successfully made.

        Args:
            team_points: Points scored by the contracting team

        Returns:
            True if contract was made, False otherwise

        Note:
            For Solo Slam the team-points check is necessary but not
            sufficient — the contracting *player* must personally win
            every trick. That predicate requires per-player trick
            counts and lives in :class:`contrai_engine.Round`, not on
            ``Contract`` (which only sees team aggregates).
        """
        if self.value in ('Slam', 'SoloSlam'):
            # All-tricks contracts: team must win every trick (162 pts).
            return team_points >= 162
        else:
            return team_points >= self.value

    def get_attacking_team(self) -> 'Team':
        """
        Get the team that must make the contract.

        Returns:
            The contracting team
        """
        return self.team

    def get_defending_team(self) -> 'Team':
        """
        Get the team defending against the contract.

        Returns:
            The opposing team
        """
        # This requires access to game teams, but we can get it from player's game context
        # For now, return None - this should be handled at game level
        return None

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
