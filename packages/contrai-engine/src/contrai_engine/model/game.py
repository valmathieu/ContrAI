"""Game class for the contrée card game.

This class manages the game state, players, teams, deck, and game logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from contrai_core.deck import Deck
from contrai_core.team import Team
from .player import Player
from contrai_core.trick import Trick
from .round import Round
from contrai_core.exceptions import InvalidPlayerCountError
import random

if TYPE_CHECKING:
    from contrai_engine.view.rich_view import RichView


@dataclass(frozen=True)
class GameOverStatus:
    """Structured verdict returned by :meth:`Game.check_game_over`.

    Attributes:
        game_over: Whether a team strictly leads at or above the target score.
        winner: The winning team name — always set when ``game_over`` is True,
            ``None`` otherwise.
        tied_teams: The teams sharing the lead at or above the target — the
            sudden-death signal: the game continues with tiebreaker rounds
            until one team leads. ``None`` when no such tie exists.
        final_scores: Snapshot of every team's score at the moment of the check.
    """

    game_over: bool
    winner: str | None
    tied_teams: list[str] | None
    final_scores: dict[str, int]

class Game:
    """
    Represents a full game of contrée.

    Attributes:
        teams (list[Team]): The two teams playing the game.
        players (list[Player]): The four players (flattened from teams).
        deck (Deck): The deck of cards for the game.
        dealer (Player): The current dealer.
        players_order (list[Player]): The order of players for the current round.
        current_contract (Contract): The current contract object.
        current_round (Round): The current round object.
        round_number (int): The current round number.
        scores (dict): The current scores for each team.
    """
    def __init__(self, players):
        """
        Initialize a game with 4 players positioned North, East, South, West.
        Teams are automatically created: North-South vs East-West.

        Args:
            players (list[Player]): List of 4 players with positions North, East, South, West

        Raises:
            InvalidPlayerCountError: If the number of players is not exactly 4.
            ValueError: If players don't have the required positions.
        """
        # Validate players
        if len(players) != 4:
            raise InvalidPlayerCountError(4, len(players), "Initializing game")

        #TODO: Accept no position and assign positions automatically

        # Validate positions
        required_positions = {'North', 'West', 'South', 'East'}
        player_positions = {player.position for player in players}
        if player_positions != required_positions:
            raise ValueError(f"Players must have positions: {required_positions}")

        # Sort players by position (North, West, South, East)
        position_order = ['North', 'West', 'South', 'East']
        self.players = sorted(players, key=lambda p: position_order.index(p.position))

        # Create teams automatically: North-South vs East-West
        north_player = next(p for p in players if p.position == 'North')
        south_player = next(p for p in players if p.position == 'South')
        east_player = next(p for p in players if p.position == 'East')
        west_player = next(p for p in players if p.position == 'West')

        team_ns = Team("North-South", [north_player, south_player])
        team_ew = Team("East-West", [east_player, west_player])
        self.teams = [team_ns, team_ew]

        # Assign teams to players
        north_player.team = team_ns
        south_player.team = team_ns
        east_player.team = team_ew
        west_player.team = team_ew

        self.deck = Deck()  # Deck instance
        self.dealer = None
        self.players_order = []
        self.current_contract = None
        self.current_round = None
        self.round_number = 0
        self.scores = {team.name: 0 for team in self.teams}

    def start_new_round(self):
        """
        Starts a new round: shuffles or cuts, deals, resets contract and sets the next dealer.
        """
        # Reset contract and set next dealer
        self.current_contract = None
        self.next_dealer()

        # Shuffle if it's the first round and cut deck otherwise
        if self.round_number == 0:
            self.deck.shuffle()
        else:
            self.deck.cut()

        # Set players order for the round
        self.set_players_order()

        # Increment the round number
        self.round_number += 1

        # Create new Round object
        self.current_round = Round(self.players_order, self.dealer, self.deck, self.round_number)

        # Deal cards
        self.current_round.deal_cards()

    def manage_round(self, view: RichView | None = None) -> None:
        """
        Manages a complete round: bidding, trick-taking, and scoring using Round class.

        Mutates game state in place: sets ``current_contract`` (``None`` when every
        player passed) and folds the round's points into ``scores``. Returns nothing —
        callers read the outcome off the ``Game``/``Round`` they passed in.

        Args:
            view: Optional view for human player interaction.
        """
        # Start new round (deal cards, set dealer, etc.)
        self.start_new_round()

        # Notify the view that a fresh round has been dealt. Used by
        # interactive views to log the deal in the rolling event log.
        if view is not None and hasattr(view, 'on_round_dealt'):
            view.on_round_dealt(self.current_round)

        # Bidding phase - delegate to Round
        contract = self.current_round.manage_bidding(view)
        self.current_contract = contract

        # If no contract (all passed), handle failed contract (redistributes cards).
        if not contract:
            self.current_round.handle_failed_contract()
            # Notify the view that the round will be redealt. The hook is a
            # pure announcement — it carries no round payload.
            if view is not None and hasattr(view, 'on_all_pass_redeal'):
                view.on_all_pass_redeal()
            return

        # Play all tricks - delegate to Round
        self.current_round.play_all_tricks(view)

        # Calculate scores for the round - delegate to Round
        round_scores = self.current_round.calculate_round_scores()

        # Update total scores
        for team_name, points in round_scores.items():
            self.scores[team_name] += points

    def check_game_over(self, target_score: int = 1500) -> GameOverStatus:
        """
        Checks if a team strictly leads at the target score, ending the game.

        A tie at or above the target does not end the game: the teams are in
        sudden death and keep playing tiebreaker rounds until one of them
        leads. The tie is surfaced through ``tied_teams`` so callers (e.g.
        the view) can announce the tiebreaker.

        Args:
            target_score: Score required to win the game.

        Returns:
            GameOverStatus: Whether the game is over, the winner (always set
                when over), any teams tied at/above the target, and a
                snapshot of the final scores.
        """
        max_score = max(self.scores.values())

        if max_score >= target_score:
            # Find the team(s) sharing the top score.
            leading_teams = [team.name for team in self.teams
                             if self.scores[team.name] == max_score]

            if len(leading_teams) == 1:
                return GameOverStatus(
                    game_over=True,
                    winner=leading_teams[0],
                    tied_teams=None,
                    final_scores=self.scores.copy(),
                )

            # Sudden death: level at/above the target — play another round.
            return GameOverStatus(
                game_over=False,
                winner=None,
                tied_teams=leading_teams,
                final_scores=self.scores.copy(),
            )

        return GameOverStatus(
            game_over=False,
            winner=None,
            tied_teams=None,
            final_scores=self.scores.copy(),
        )

    def next_dealer(self):
        """
        Sets the next dealer for the next round (player to the left of current dealer, anticlockwise).
        """
        if self.dealer is None:
            self.dealer = random.choice(self.players)
        else:
            idx = self.players.index(self.dealer)
            self.dealer = self.players[(idx + 1) % 4]

    def set_players_order(self):
        """
        Sets the players order starting with the player after the dealer (anticlockwise order).
        """
        # Reset players order and start with next player after dealer (anticlockwise order)
        dealer_idx = self.players.index(self.dealer)
        self.players_order = []
        for i in range(4):
            player_idx = (dealer_idx + 1 + i) % 4
            self.players_order.append(self.players[player_idx])