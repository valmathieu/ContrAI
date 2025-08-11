# Game class for the "contree" card game.
# This class manages the game state, players, teams, deck, and game logic.

from .card import Card
from .deck import Deck
from .team import Team
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
        current_contract (object): The current contract (to be defined).
        current_trick (list[Card]): The cards played in the current trick.
        last_trick(list[Card]): The cards played in the last trick.
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
        self.current_trick = []
        self.last_trick = []
        self.round_number = 0
        self.scores = {team.name: 0 for team in self.teams}

    def start_new_round(self):
        """
        Starts a new round: shuffles or cuts, deals, resets contract and trick, and sets the next dealer.
        - Resets the current contract and trick.
        - Sets the next dealer (player to the right of the previous dealer).
        - Increments the round number.
        - Shuffles and cuts the deck (if not the first round, cut before shuffling).
        - Deals cards to all players (3-2-3 distribution) with dealer getting cards last.
        """
        # Reset contract and trick
        self.current_trick = []
        self.current_contract = None
        # Set the next dealer (right of previous dealer or randomly if first round)
        self.next_dealer()
        # Shuffle and cut deck
        if self.round_number == 0:
            self.deck.shuffle()
        else:
            self.deck.cut()
        # Set players order for the round
        self.set_players_order()
        # Deal cards in proper order (dealer gets cards last)
        self.deck.deal(self.players_order)
        # Increments the round number
        self.round_number += 1

    def manage_bid(self, view=None):
        """
        Manages the bidding phase for the current round.

        Handles the bidding order, validates bids, manages pass/double/redouble logic,
        and sets self.current_contract to the winning contract or None if all pass.
        Optionally uses a view for human interaction.
        Bidding order follows the players' order set by the dealer and stored in self.players_order.
        """
        bids = []  # List of (player, bid) tuples
        passes = 0
        last_bid = None
        last_bidder = None
        contract = None
        double = False
        redouble = False

        while True:
            for player in self.players_order:
                # Ask player for bid
                if hasattr(player, 'choose_bid'):
                    bid = player.choose_bid(bids)
                else:
                    bid = 'Pass'
                # If view is provided and player is human, use view for input
                if view and player.is_human:
                    bid = view.request_bid_action(player, bids)
                bids.append((player, bid))
                if bid == 'Pass':
                    passes += 1
                elif isinstance(bid, tuple):
                    # bid is (value, suit)
                    value, suit = bid
                    # TODO: better bid management
                    if last_bid is None or value > last_bid[0] or value == 'Capot':
                        last_bid = (value, suit)
                        last_bidder = player
                        passes = 0
                        contract = (player, value, suit)
                    else:
                        # Invalid bid, force pass
                        passes += 1
                elif bid == 'Double' and last_bid is not None and player not in [last_bidder]:
                    # Only defending team (not the bidder's team) can double
                    if last_bidder.team != player.team:
                        double = True
                        passes = 0
                    else:
                        # Invalid double, force pass
                        passes += 1
                elif bid == 'Redouble' and double and last_bidder:
                    # Only attacking team (bidder's team) can redouble after a double
                    if last_bidder.team == player.team:
                        redouble = True
                        passes = 0
                    else:
                        # Invalid redouble, force pass
                        passes += 1
                else:
                    passes += 1
                # End if 3 passes after last bid
                if last_bid and passes >= 3:
                    break
            if last_bid and passes >= 3:
                break
            if all(b[1] == 'Pass' for b in bids):
                # All players passed
                break
        if contract:
            self.current_contract = {
                'player': contract[0],
                'team': contract[0].team,
                'value': contract[1],
                'suit': contract[2],
                'double': double,
                'redouble': redouble
            }
        else:
            self.current_contract = None
        return self.current_contract

    def manage_trick(self, view=None):
        """
        Manages the trick-taking phase for the current round.
        Should update self.current_trick and handle trick winner logic.

        Args:
            view: Optional view for human player interaction

        Returns:
            Player: The winner of the trick
        """
        self.current_trick = []
        trick_leader = self.players_order[0] if not hasattr(self, 'trick_winner') else self.trick_winner

        # Determine the order for this trick (winner of last trick leads)
        if hasattr(self, 'trick_winner') and self.trick_winner:
            leader_idx = self.players.index(self.trick_winner)
            trick_order = []
            for i in range(4):
                trick_order.append(self.players[(leader_idx + i) % 4])
        else:
            trick_order = self.players_order.copy()

        # Each player plays a card
        for player in trick_order:
            if hasattr(player, 'choose_card'):
                # AI player or player with choose_card method
                card = player.choose_card(self.current_trick, self.current_contract)
            else:
                # Simple fallback: play first available card
                card = player.hand[0] if player.hand else None

            # If view provided and player is human, use view for input
            if view and player.is_human and hasattr(player, 'is_human'):
                card = view.request_card_action(player, self.current_trick, self.current_contract)

            if card and card in player.hand:
                player.hand.remove(card)
                self.current_trick.append((player, card))

        # Determine trick winner
        winner = self._determine_trick_winner(self.current_trick)
        self.trick_winner = winner

        # Move current trick to last trick for display
        self.last_trick = self.current_trick.copy()

        return winner

    def _determine_trick_winner(self, trick):
        """
        Determines the winner of a trick based on the cards played.

        Args:
            trick: List of (player, card) tuples

        Returns:
            Player: The winner of the trick
        """
        if not trick:
            return None

        trump_suit = self.current_contract['suit'] if self.current_contract else None
        lead_suit = trick[0][1].suit  # Suit of the first card played

        best_player = trick[0][0]
        best_card = trick[0][1]
        best_is_trump = trump_suit and best_card.suit == trump_suit

        for player, card in trick[1:]:
            card_is_trump = trump_suit and card.suit == trump_suit

            if card_is_trump and not best_is_trump:
                # Trump beats non-trump
                best_player = player
                best_card = card
                best_is_trump = True
            elif card_is_trump and best_is_trump:
                # Compare trump cards
                if card.get_order(trump_suit) > best_card.get_order(trump_suit):
                    best_player = player
                    best_card = card
            elif not card_is_trump and not best_is_trump and card.suit == lead_suit:
                # Compare cards of the same suit (non-trump)
                if card.get_order() > best_card.get_order():
                    best_player = player
                    best_card = card

        return best_player

    def manage_round(self, view=None):
        """
        Manages a complete round: bidding, trick-taking, and scoring.

        Args:
            view: Optional view for human player interaction

        Returns:
            dict: Round results with contract and scores
        """
        # Start new round (deal cards, set dealer, etc.)
        self.start_new_round()

        # Bidding phase
        contract = self.manage_bid(view)

        # If no contract (all passed), redistribute cards
        if not contract:
            return {
                'contract': None,
                'scores': {team.name: 0 for team in self.teams},
                'message': 'All players passed. Cards redistributed.'
            }

        # Initialize trick tracking
        self.trick_winner = None
        team_tricks = {team.name: [] for team in self.teams}

        # Play 8 tricks
        for trick_num in range(8):
            winner = self.manage_trick(view)
            if winner:
                winner_team = winner.team
                team_tricks[winner_team.name].append(self.current_trick.copy())

        # Calculate scores for the round
        round_scores = self.calculate_scores(team_tricks)

        # Update total scores
        for team_name, points in round_scores.items():
            self.scores[team_name] += points

        return {
            'contract': contract,
            'scores': round_scores,
            'total_scores': self.scores.copy(),
            'message': 'Round completed'
        }

    def calculate_scores(self, team_tricks=None):
        """
        Calculates and updates the scores for each team at the end of a round.

        Args:
            team_tricks: Dict mapping team names to their tricks

        Returns:
            dict: Points earned by each team this round
        """
        if not self.current_contract:
            return {team.name: 0 for team in self.teams}

        contract_team = self.current_contract['team']
        contract_value = self.current_contract['value']
        trump_suit = self.current_contract['suit']
        is_doubled = self.current_contract.get('double', False)
        is_redoubled = self.current_contract.get('redouble', False)

        # Initialize team scores
        team_scores = {team.name: 0 for team in self.teams}
        team_card_points = {team.name: 0 for team in self.teams}

        # Count card points for each team
        if team_tricks:
            for team_name, tricks in team_tricks.items():
                points = 0
                for trick in tricks:
                    for player, card in trick:
                        points += card.get_points(trump_suit)
                team_card_points[team_name] = points

        # Add "dix de der" (10 points for last trick)
        if hasattr(self, 'trick_winner') and self.trick_winner:
            last_trick_team = self.trick_winner.team.name
            team_card_points[last_trick_team] += 10

        contract_team_name = contract_team.name
        contract_team_points = team_card_points[contract_team_name]

        # Check if contract is made
        contract_made = contract_team_points >= contract_value

        # Calculate multiplier for double/redouble
        multiplier = 1
        if is_redoubled:
            multiplier = 4
        elif is_doubled:
            multiplier = 2

        if contract_made:
            # Contract successful
            team_scores[contract_team_name] = (contract_value + contract_team_points) * multiplier
            # Opposing team gets their points
            for team_name, points in team_card_points.items():
                if team_name != contract_team_name:
                    team_scores[team_name] = points
        else:
            # Contract failed
            team_scores[contract_team_name] = 0  # Contract team gets 0
            # Opposing team gets all points + contract value
            opposing_team_points = sum(points for name, points in team_card_points.items()
                                     if name != contract_team_name)
            for team_name in team_scores:
                if team_name != contract_team_name:
                    team_scores[team_name] = (162 + contract_value) * multiplier

        return team_scores

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
            'final_scores': None
        }

    def set_players_order(self):
        """
        Sets the order of players for the current round based on the dealer.
        The player to the right of the dealer starts first.
        """
        if self.dealer is None:
            raise ValueError("Dealer must be set before setting players order.")
        dealer_idx = self.players.index(self.dealer)
        # Reset players order and start with next player after dealer (anticlockwise order)
        self.players_order = []
        for i in range(4):
            player_idx = (dealer_idx + 1 + i) % 4
            self.players_order.append(self.players[player_idx])
