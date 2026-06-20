# Game class for the contrée card game.
# This class manages the game state, players, teams, deck, and game logic.

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from contrai_core.deck import Deck
from contrai_core.team import Team
from .player import Player
from contrai_core.trick import Trick
from contrai_core.contract import Contract
from .round import Round
from contrai_core.exceptions import InvalidPlayerCountError
import random

if TYPE_CHECKING:
    # Imported for type checking only — the Model must never import the View
    # at runtime (MVC layering). ``from __future__ import annotations`` makes
    # the annotation a lazy string, so this stays a static-analysis concern.
    from contrai_engine.view.rich_view import RichView


class RoundResult(TypedDict):
    """Structured result of a single round managed by :meth:`Game.manage_round`."""

    contract: Contract | None
    scores: dict[str, int]
    total_scores: dict[str, int]
    message: str

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

    def manage_round(self, view: RichView | None = None) -> RoundResult:
        """
        Manages a complete round: bidding, trick-taking, and scoring using Round class.

        Args:
            view: Optional view for human player interaction.

        Returns:
            RoundResult: Round outcome with the contract, per-round scores,
                cumulative total scores, and a human-readable message.
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

        # If no contract (all passed), handle failed contract
        if not contract:
            round_scores = self.current_round.handle_failed_contract()
            # Notify the view that the round will be redealt.
            if view is not None and hasattr(view, 'on_all_pass_redeal'):
                view.on_all_pass_redeal(self.current_round)
            return {
                'contract': None,
                'scores': round_scores,
                'total_scores': self.scores.copy(),
                'message': 'All players passed. Cards redistributed.'
            }

        # Play all tricks - delegate to Round
        self.current_round.play_all_tricks(view)

        # Calculate scores for the round - delegate to Round
        round_scores = self.current_round.calculate_round_scores()

        # Update total scores
        for team_name, points in round_scores.items():
            self.scores[team_name] += points

        return {
            'contract': contract,
            'scores': round_scores,
            'total_scores': self.scores.copy(),
            'message': 'Round completed'
        }

    def check_game_over(self, target_score=1500):
        """
        Checks if any team has reached the target score to end the game.

        Args:
            target_score: Score required to win the game

        Returns:
            dict: Game over status and winner information
        """
        max_score = max(self.scores.values())

        if max_score >= target_score:
            # Find winning team(s)
            winning_teams = [team.name for team in self.teams
                           if self.scores[team.name] == max_score]

            return {
                'game_over': True,
                'winner': winning_teams[0] if len(winning_teams) == 1 else None,
                'tied_teams': winning_teams if len(winning_teams) > 1 else None,
                'final_scores': self.scores.copy()
            }

        return {
            'game_over': False,
            'winner': None,
            'tied_teams': None,
            'final_scores': self.scores.copy()
        }

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