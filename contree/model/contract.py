# Contract class for the "contree" card game.
# This class represents a contract established during bidding.

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .player import Player
    from .team import Team
    from .bid import ContractBid

class Contract:
    """
    Represents a contract established during bidding.

    Contains contract details like value, trump suit, contracting player/team,
    and handles double/redouble states with score calculations.
    """

    def __init__(self, contract_bid: 'ContractBid', double: bool = False, redouble: bool = False):
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
        # Import here to avoid circular imports
        from .bid import ContractBid

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
        """
        if self.value == 'Capot':
            # For Capot, team must win all tricks (all 162 points)
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

    def is_capot(self) -> bool:
        """
        Check if this is a Capot contract.

        Returns:
            True if contract value is 'Capot', False otherwise
        """
        return self.value == 'Capot'

    def get_base_points(self) -> int:
        """
        Get the base point value of the contract.

        Returns:
            Base points for the contract (250 for Capot, actual value otherwise)
        """
        return 250 if self.value == 'Capot' else self.value

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
