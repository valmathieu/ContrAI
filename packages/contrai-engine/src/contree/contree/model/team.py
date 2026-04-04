# Team class for "contree" game, representing a team of two players.

from contree.model.exceptions import InvalidPlayerCountError
from .player import Player

class Team:
    """
    Represents a team of two players in "contree" game.

    Attributes:
        name (str): The name of the team (e.g., "North-South", "East-West").
        players (list[Player]): List of two players that form the team.
        total_score (int): The cumulative score of the team across all rounds.
    """

    def __init__(self, name, players):
        """
        Initialize a team with a name and two players.

        Args:
            name (str): The name of the team
            players (list[Player]): List of exactly 2 players

        Raises:
            InvalidPlayerCountError: If the number of players is not exactly 2.
        """
        if len(players) != 2:
            raise InvalidPlayerCountError(2, len(players), "Creating team")

        self.name = name
        self.players = players  # list of Player
        self.total_score = 0

    def add_points(self, points: int):
        """
        Add points to the team's total score.

        Args:
            points (int): Points to add to the team's score
        """
        self.total_score += points

    def get_partner(self, player: Player) -> Player | None:
        """
        Get the partner of a given player within this team.

        Args:
            player (Player): The player whose partner to find

        Returns:
            Player: The partner player, or None if the player is not in this team
        """
        if player not in self.players:
            return None
        return self.players[0] if self.players[1] == player else self.players[1]

    def contains_player(self, player: Player) -> bool:
        """
        Check if a player belongs to this team.

        Args:
            player (Player): The player to check

        Returns:
            bool: True if the player is in this team, False otherwise
        """
        return player in self.players

    def __str__(self):
        """String representation of the team."""
        player_names = [player.name for player in self.players]
        return f"{self.name}: {' & '.join(player_names)} ({self.total_score} pts)"

    def __repr__(self):
        """Developer representation of the team."""
        return f"Team('{self.name}', {len(self.players)} players, {self.total_score} pts)"
