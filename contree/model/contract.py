# Contract class for the "contree" card game.
# This class represents a contract established during bidding.

from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .player import Player
    from .team import Team

class Contract:
    """
    Represents a contract established during bidding.

    Contains contract details like value, trump suit, contracting player/team,
    and handles double/redouble states with score calculations.
    """

    def __init__(self, player: 'Player', value: int, suit: str,
                 double: bool = False, redouble: bool = False):
        """
        Initialize a contract.

        Args:
            player: Player who made the winning bid
            value: Contract value (points to make)
            suit: Trump suit for the contract
            double: Whether contract has been doubled
            redouble: Whether contract has been redoubled
        """
        self.player = player
        self.team = player.team
        self.value = value
        self.suit = suit
        self.double = double
        self.redouble = redouble

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
            True if contract value was reached
        """
        return team_points >= self.value

    def get_attacking_team(self) -> 'Team':
        """Get the team that must make the contract."""
        return self.team

    def get_defending_team(self, all_teams: List['Team']) -> Optional['Team']:
        """
        Get the team defending against the contract.

        Args:
            all_teams: List of all teams in the game

        Returns:
            The defending team, or None if not found
        """
        for team in all_teams:
            if team != self.team:
                return team
        return None

    def is_capot(self) -> bool:
        """Check if this is a capot contract (all tricks must be won)."""
        return self.value == 'Capot'

    def get_base_points(self) -> int:
        """Get the base points value for this contract."""
        if self.is_capot():
            return 250  # Capot base value
        return self.value

    def calculate_success_points(self, team_points: int) -> int:
        """
        Calculate points awarded to attacking team if contract is successful.

        Args:
            team_points: Actual points scored by the contracting team

        Returns:
            Points to award to the attacking team
        """
        base_value = self.get_base_points()
        multiplier = self.get_multiplier()

        if self.is_capot():
            # For capot, award fixed points
            return base_value * multiplier
        elif self.double or self.redouble:
            # When doubled/redoubled and made, special scoring
            return 160 + base_value * multiplier
        else:
            # Normal contract: base value + actual points scored
            return base_value + team_points

    def calculate_failure_points(self) -> int:
        """
        Calculate points awarded to defending team if contract fails.

        Returns:
            Points to award to the defending team
        """
        base_value = self.get_base_points()
        multiplier = self.get_multiplier()

        # Defending team gets all possible points + contract value
        return (160 + base_value) * multiplier

    def to_dict(self) -> dict:
        """Convert contract to dictionary representation (for compatibility)."""
        return {
            'player': self.player,
            'team': self.team,
            'value': self.value,
            'suit': self.suit,
            'double': self.double,
            'redouble': self.redouble
        }

    # Dictionary-style access for backward compatibility
    def __getitem__(self, key: str):
        """Allow dictionary-style access for backward compatibility."""
        if key in ['player', 'team', 'value', 'suit', 'double', 'redouble']:
            return getattr(self, key)
        else:
            raise KeyError(f"'{key}' is not a valid contract attribute")

    def get(self, key: str, default=None):
        """Dictionary-style get method for backward compatibility."""
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        """Support 'in' operator for dictionary-style access."""
        return key in ['player', 'team', 'value', 'suit', 'double', 'redouble']

    def __str__(self) -> str:
        """String representation of the contract."""
        modifiers = []
        if self.redouble:
            modifiers.append("Redoubled")
        elif self.double:
            modifiers.append("Doubled")

        modifier_str = " (" + ", ".join(modifiers) + ")" if modifiers else ""

        if self.is_capot():
            return f"Capot in {self.suit}{modifier_str} by {self.player.name}"
        else:
            return f"{self.value} {self.suit}{modifier_str} by {self.player.name}"

    def __repr__(self) -> str:
        """Detailed string representation for debugging."""
        return (f"Contract(player={self.player.name}, value={self.value}, "
                f"suit={self.suit}, double={self.double}, redouble={self.redouble})")
