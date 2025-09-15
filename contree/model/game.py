# Game class for the "contree" card game.
# This class manages the game state, players, teams, deck, and game logic.

from .deck import Deck
from .team import Team
from .player import Player
from .trick import Trick
from .contract import Contract
from .round import Round
from .exceptions import InvalidPlayerCountError
import random

class Game:
    """
    Represents a full game of "contree".

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

        # Shuffle and cut deck
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

    def manage_round(self, view=None):
        """
        Manages a complete round: bidding, trick-taking, and scoring using Round class.

        Args:
            view: Optional view for human player interaction

        Returns:
            dict: Round results with contract and scores
        """
        # Start new round (deal cards, set dealer, etc.)
        self.start_new_round()

        # Bidding phase - delegate to Round
        contract = self.current_round.manage_bidding(view)
        self.current_contract = contract

        # If no contract (all passed), handle failed contract
        if not contract:
            round_scores = self.current_round.handle_failed_contract()
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

    # Legacy methods for backward compatibility (deprecated - use Round class directly)
    def manage_bid(self, view=None):
        """
        Legacy method - use current_round.manage_bidding() instead.

        Returns:
            Contract: The established contract or None if all players passed
        """
        if self.current_round:
            return self.current_round.manage_bidding(view)
        return None

    def manage_trick(self, view=None):
        """
        Legacy method - use current_round.play_trick() instead.

        Returns:
            Player: The winner of the trick
        """
        if self.current_round:
            return self.current_round.play_trick(view)
        return None

    def calculate_scores(self, team_tricks=None):
        """
        Legacy method - use current_round.calculate_round_scores() instead.

        Returns:
            dict: Team scores for this round
        """
        if self.current_round:
            return self.current_round.calculate_round_scores()
        return {team.name: 0 for team in self.teams}

    def get_playable_cards(self, player):
        """
        Legacy method - use current_round._get_playable_cards() instead.

        Returns:
            list: List of cards that can be legally played
        """
        if self.current_round:
            return self.current_round._get_playable_cards(player)
        return player.hand.copy() if player.hand else []

    # Compatibility properties for legacy test support
    @property
    def current_trick(self):
        """
        Legacy property - access current_round.current_trick instead.

        Returns:
            Current trick object or empty Trick for compatibility
        """
        if self.current_round and hasattr(self.current_round, 'current_trick') and self.current_round.current_trick:
            return self.current_round.current_trick
        # Return empty Trick for compatibility with tests that expect len()
        return Trick()

    @property
    def last_trick(self):
        """
        Legacy property - access current_round.tricks[-1] instead.

        Returns:
            Last completed trick or empty Trick for compatibility
        """
        if self.current_round and self.current_round.tricks:
            return self.current_round.tricks[-1]
        # Return empty Trick for compatibility with tests that expect len()
        return Trick()

    @property
    def last_trick_winner(self):
        """
        Legacy property - access current_round.last_trick_winner instead.

        Returns:
            Winner of the last trick or None
        """
        if self.current_round and hasattr(self.current_round, 'last_trick_winner'):
            return self.current_round.last_trick_winner
        return None

    @property
    def current_trick_number(self):
        """
        Legacy property - calculated from current_round.tricks length.

        Returns:
            Number of completed tricks in current round
        """
        if self.current_round and hasattr(self.current_round, 'tricks'):
            return len(self.current_round.tricks)
        return 0
