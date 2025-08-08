# Game class for La Contrée

from .card import Card
from .deck import Deck
from .team import Team
import random

class Game:
    """
    Represents a full game of La Contrée.

    Attributes:
        teams (list[Team]): The two teams playing the game.
        players (list[Player]): The four players (flattened from teams).
        deck (Deck): The deck of cards for the game.
        dealer (Player): The current dealer.
        current_contract (object): The current contract (to be defined).
        current_trick (list[Card]): The cards played in the current trick.
        round_number (int): The current round number.
        scores (dict): The current scores for each team.
    """
    def __init__(self, players):
        """
        Initialize a game with 4 players positioned North, East, South, West.
        Teams are automatically created: North-South vs East-West.

        Args:
            players (list[Player]): List of 4 players with positions North, East, South, West
        """
        # Validate players
        if len(players) != 4:
            raise ValueError("Game requires exactly 4 players")

        #TODO: Accept no position and assign positions automatically

        # Validate positions
        required_positions = {'North', 'East', 'South', 'West'}
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

        self.deck = Deck()  # Deck instance
        self.dealer = None  # Player instance
        self.current_contract = None
        self.current_trick = []
        self.round_number = 0
        self.scores = {team.name: 0 for team in self.teams}

    def start_new_round(self):
        """
        Starts a new round: shuffles, deals, resets contract and trick, and sets the next dealer.
        - Increments the round number.
        - Sets the next dealer (player to the right of the previous dealer).
        - Shuffles and cuts the deck (if not the first round, cut before shuffling).
        - Deals cards to all players (3-2-3 distribution) with dealer getting cards last.
        - Resets the current contract and trick.
        """
        self.round_number += 1
        # Set the next dealer (right of previous dealer or randomly if first round)
        self.next_dealer()
        # Shuffle and cut deck
        if self.round_number == 1:
            self.deck.shuffle()
        else:
            self.deck.cut()
        # Clear all players' hands
        for player in self.players:
            player.hand.clear()
        # Create dealing order: dealer should be last
        dealer_idx = self.players.index(self.dealer)
        dealing_order = []
        # Start with next player after dealer (anticlockwise order), dealer gets cards last
        for i in range(4):
            player_idx = (dealer_idx + 1 + i) % 4
            dealing_order.append(self.players[player_idx])
        # Deal cards in proper order (dealer gets cards last)
        self.deck.deal(dealing_order)
        # Reset contract and trick
        self.current_trick = []
        self.current_contract = None

    def manage_bid(self, view=None):
        """
        Manages the bidding phase for the current round.
        Handles the bidding order, validates bids, manages pass/contrer/surcontrer,
        and sets self.current_contract to the winning contract or None if all pass.
        Optionally uses a view for human interaction.
        """
        # Bidding order: starts with player to the right of dealer
        num_players = len(self.players)
        dealer_idx = self.players.index(self.dealer)
        order = [(dealer_idx - i - 1) % num_players for i in range(num_players)]
        bids = []  # List of (player, bid) tuples
        passes = 0
        last_bid = None
        last_bidder = None
        contract = None
        contrer = False
        surcontrer = False
        bid_history = []

        while True:
            for idx in order:
                player = self.players[idx]
                # Ask player for bid
                if hasattr(player, 'choose_bid'):
                    bid = player.choose_bid(last_bid)
                else:
                    bid = 'Pass'
                # If view is provided and player is human, use view for input
                if view and hasattr(player, 'is_human') and player.is_human:
                    bid = view.request_bid_action(player, last_bid)
                bid_history.append((player, bid))
                if bid == 'Pass':
                    passes += 1
                elif isinstance(bid, tuple):
                    # bid is (value, suit)
                    value, suit = bid
                    if last_bid is None or (value > last_bid[0] or (value == 'Capot' and last_bid[0] != 'Capot')):
                        last_bid = (value, suit)
                        last_bidder = player
                        passes = 0
                        contract = (player, value, suit)
                    else:
                        # Invalid bid, force pass
                        passes += 1
                elif bid == 'Contre' and last_bid is not None and player not in [last_bidder]:
                    contrer = True
                    passes = 0
                elif bid == 'Surcontre' and contrer and player in [last_bidder]:
                    surcontrer = True
                    passes = 0
                else:
                    passes += 1
                # End if 3 passes after last bid
                if last_bid and passes >= 3:
                    break
            if last_bid and passes >= 3:
                break
            if all(b[1] == 'Pass' for b in bid_history[-num_players:]):
                # All players passed
                break
        if contract:
            self.current_contract = {
                'player': contract[0],
                'value': contract[1],
                'suit': contract[2],
                'contrer': contrer,
                'surcontrer': surcontrer
            }
        else:
            self.current_contract = None
        return self.current_contract

    def manage_trick(self):
        """
        Manages the trick-taking phase for the current round.
        Should update self.current_trick and handle trick winner logic.
        """
        pass  # To be implemented

    def next_dealer(self):
        """
        Sets the next dealer for the next round (player to the left of current dealer, anticlockwise).
        """
        if self.dealer is None:
            self.dealer = random.choice(self.players)
        else:
            idx = self.players.index(self.dealer)
            self.dealer = self.players[(idx + 1) % 4]

    def calculate_scores(self):
        """
        Calculates and updates the scores for each team at the end of a round.
        """
        # Placeholder: implement score calculation logic
        pass

    def check_game_over(self, target_score=1500):
        """
        Checks if any team has reached the target score to end the game.
        """
        return any(score >= target_score for score in self.scores.values())


