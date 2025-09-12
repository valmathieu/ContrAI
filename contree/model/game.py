# Game class for the "contree" card game.
# This class manages the game state, players, teams, deck, and game logic.

from .card import Card
from .deck import Deck
from .team import Team
from .player import Player
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
        last_trick_winner(Player): The player that won the last trick.
        round_number (int): The current round number.
        current_trick_number (int): The current trick number within the round.
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
        self.last_trick_winner = None
        self.round_number = 0
        self.current_trick_number = 0
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
        self.current_trick_number = 0  # Reset trick counter for new round
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
        trick_leader = self.players_order[0] if self.last_trick_winner is None else self.last_trick_winner

        # Determine the order for this trick (winner of last trick leads)
        leader_idx = self.players.index(trick_leader)
        trick_order = []
        for i in range(4):
            trick_order.append(self.players[(leader_idx + i) % 4])

        # Each player plays a card
        for player in trick_order:
            # Get the playable cards for this player
            playable_cards = self.get_playable_cards(player)

            card = None
            if hasattr(player, 'choose_card'):
                # AI player or player with choose_card method
                # Pass playable cards to help AI make legal moves
                card = player.choose_card(self.current_trick, self.current_contract, playable_cards)
            else:
                # Simple fallback: play first playable card
                card = playable_cards[0] if playable_cards else None

            # If view provided and player is human, use view for input
            if view and hasattr(player, 'is_human') and player.is_human:
                card = view.request_card_action(player, self.current_trick, self.current_contract, playable_cards)

            # Validate that the chosen card is legal
            if card and card in playable_cards and card in player.hand:
                player.hand.remove(card)
                self.current_trick.append((player, card))
            elif card:
                # Card chosen is not legal - fallback to first playable card
                fallback_card = playable_cards[0]
                player.hand.remove(fallback_card)
                self.current_trick.append((player, fallback_card))

        # Determine trick winner
        winner = self._determine_trick_winner(self.current_trick)
        self.last_trick_winner = winner

        # Move current trick to last trick for display
        self.last_trick = self.current_trick.copy()

        # Add cards back to deck (last card played first, then reverse order)
        trick_cards = [card for _, card in self.current_trick]
        trick_cards.reverse()  # Last card played becomes first to be added back
        self.deck.add_cards(trick_cards)

        return winner

    def _determine_trick_winner(self, trick) -> Player:
        """
        Determines the winner of a trick based on the cards played.

        Args:
            trick: List of (player, card) tuples

        Returns:
            Player: The winner of the trick
        """
        # TODO : Raise an error if trick is empty or not existing
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
            # Put all players' cards back in deck (8 cards per player)
            for player in self.players:
                self.deck.add_cards(player.hand.copy())
                player.hand.clear()

            return {
                'contract': None,
                'scores': {team.name: 0 for team in self.teams},
                'message': 'All players passed. Cards redistributed.'
            }

        # Initialize trick tracking
        self.last_trick_winner = None
        team_tricks = {team.name: [] for team in self.teams}

        # Play 8 tricks
        for trick_num in range(8):
            winner = self.manage_trick(view)
            self.current_trick_number = trick_num + 1
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

        # Count card points for each team and check for belote (King + Queen of trump)
        belote_teams = set()  # Teams that have belote
        if team_tricks:
            for team_name, tricks in team_tricks.items():
                points = 0
                trump_cards_played = []

                for trick in tricks:
                    for player, card in trick:
                        points += card.get_points(trump_suit)
                        # Track trump cards played by this team
                        if trump_suit and card.suit == trump_suit:
                            trump_cards_played.append(card.rank)

                # Check for belote (King and Queen of trump suit in same round)
                if trump_suit and 'King' in trump_cards_played and 'Queen' in trump_cards_played:
                    points += 20  # Belote bonus
                    belote_teams.add(team_name)

                team_card_points[team_name] = points

        # Add "dix de der" (10 points for last trick)
        if hasattr(self, 'last_trick_winner') and self.last_trick_winner:
            last_trick_team = self.last_trick_winner.team.name
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
        # TODO : Handle "Capot" (all tricks won by one team)
        if contract_made:
            # Contract successful
            if is_doubled or is_redoubled:
                # When contract is made with double/redouble, attacking team gets
                # the same points that defending team would have gotten if contract failed
                defending_team_points = sum(points for name, points in team_card_points.items()
                                          if name != contract_team_name)
                team_scores[contract_team_name] = 160 + contract_value * multiplier

                # Defending team gets their actual points (no multiplier)
                for team_name, points in team_card_points.items():
                    if team_name != contract_team_name:
                        team_scores[team_name] = points
            else:
                # Normal contract made without double/redouble
                team_scores[contract_team_name] = contract_value + contract_team_points
                # Opposing team gets their points
                for team_name, points in team_card_points.items():
                    if team_name != contract_team_name:
                        team_scores[team_name] = points
        else:
            # Contract failed
            team_scores[contract_team_name] = 0  # Contract team gets 0
            # Opposing team gets all points + contract value
            for team_name in team_scores:
                if team_name != contract_team_name:
                    team_scores[team_name] = (160 + contract_value) * multiplier

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

    def get_playable_cards(self, player):
        """
        Determine which cards a player can legally play based on the current trick and contract rules.

        Args:
            player (Player): The player whose playable cards we want to determine

        Returns:
            list[Card]: List of cards that the player can legally play

        Rules:
        1. Must follow suit if possible
        2. If can't follow suit and partner is not leading, must trump if possible
        3. If opponent already trumped, must play higher trump if possible
        4. If can't follow suit or trump, can play any card (discard)
        5. If partner is leading the trick, no obligation to trump when can't follow suit
        """
        if not player.hand:
            return []

        # If no cards played yet in trick, any card is playable
        if not self.current_trick:
            return player.hand.copy()

        trump_suit = self.current_contract['suit'] if self.current_contract else None
        lead_suit = self.current_trick[0][1].suit  # First card played in trick

        # Cards of the lead suit in player's hand
        lead_suit_cards = [card for card in player.hand if card.suit == lead_suit]

        # If player has cards of the lead suit, must play one
        if lead_suit_cards:
            return lead_suit_cards

        # Player doesn't have lead suit, check if partner is leading
        trick_leader = self.current_trick[0][0]
        player_team = player.team
        partner_is_leading = trick_leader.team == player_team

        # If partner is leading, no obligation to trump - can play any card
        if partner_is_leading:
            return player.hand.copy()

        # Partner is not leading, check trump obligations
        if not trump_suit or lead_suit == trump_suit:
            # No trump suit or lead suit is trump, can play any card
            return player.hand.copy()

        # Check if opponent has already played trump
        trump_cards = [card for card in player.hand if card.suit == trump_suit]

        # Get highest trump played so far by opponents
        highest_opponent_trump = None

        for trick_player, card in self.current_trick:
            if (card.suit == trump_suit and
                trick_player.team != player_team):
                if (highest_opponent_trump is None or
                    card.get_order(trump_suit) > highest_opponent_trump.get_order(trump_suit)):
                    highest_opponent_trump = card

        if highest_opponent_trump:
            # Opponent has trumped, must play higher trump if possible
            higher_trumps = [card for card in trump_cards
                           if card.get_order(trump_suit) > highest_opponent_trump.get_order(trump_suit)]
            if higher_trumps:
                return higher_trumps
            elif trump_cards:
                # Must trump even if can't go higher
                return trump_cards
            else:
                # No trump cards, can discard any card
                return player.hand.copy()
        else:
            # No opponent trump yet
            if trump_cards:
                # Must trump if has trump cards
                return trump_cards
            else:
                # No trump cards, can discard any card
                return player.hand.copy()
