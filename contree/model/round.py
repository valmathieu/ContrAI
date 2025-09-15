# Round class for the "contree" card game.
# This class represents a complete round of the card game from dealing to scoring.

from typing import Optional, Dict, List, TYPE_CHECKING
from .trick import Trick
from .contract import Contract
from .bid import Bid, PassBid, ContractBid, DoubleBid, RedoubleBid, BidValidator

if TYPE_CHECKING:
    from .player import Player
    from .team import Team
    from .deck import Deck

class Round:
    """
    Represents a complete round of the card game from dealing to scoring.

    Manages the complete round lifecycle including bidding phase coordination,
    trick sequence management, and round score calculation.
    """

    def __init__(self, players_order: List['Player'], dealer: 'Player', deck: 'Deck', round_number: int):
        """
        Initialize a round with the given parameters.

        Args:
            players_order: List of players in playing order for this round
            dealer: The dealer for this round
            deck: The deck to use for dealing cards
            round_number: The current round number
        """
        self.players_order = players_order
        self.dealer = dealer
        self.deck = deck
        self.round_number = round_number

        # Round state
        self.contract: Optional[Contract] = None
        self.tricks: List[Trick] = []
        self.current_trick: Optional[Trick] = None
        self.last_trick_winner: Optional['Player'] = None
        self.team_tricks: Dict[str, List[Trick]] = {}
        self.round_scores: Dict[str, int] = {}

        # Initialize team tricks dictionary
        if players_order:
            teams = set(player.team for player in players_order)
            self.team_tricks = {team.name: [] for team in teams}

    def deal_cards(self):
        """
        Deal cards to all players in the proper order.
        Dealer gets cards last.
        """
        self.deck.deal(self.players_order)

    def manage_bidding(self, view=None) -> Optional[Contract]:
        """
        Handle complete bidding phase.

        Args:
            view: Optional view for human player interaction

        Returns:
            Contract: The established contract or None if all players passed
        """
        bid_objects = []  # List of Bid objects
        passes_count = 0

        while True:
            for player in self.players_order:
                # Get bid choice from player (returns string or tuple)
                if hasattr(player, 'choose_bid'):
                    # Convert bid objects to legacy format for compatibility
                    legacy_bids = [(bid.player, self._bid_to_legacy_format(bid)) for bid in bid_objects]
                    bid_choice = player.choose_bid(legacy_bids)
                else:
                    bid_choice = 'Pass'

                # If view is provided and player is human, use view for input
                if view and hasattr(player, 'is_human') and player.is_human:
                    # Convert bid objects to legacy format for view compatibility
                    legacy_bids = [(bid.player, self._bid_to_legacy_format(bid)) for bid in bid_objects]
                    bid_choice = view.request_bid_action(player, legacy_bids)

                # Create appropriate Bid object from player's choice
                bid_obj = self._create_bid_from_choice(player, bid_choice)

                # Validate the bid
                if BidValidator.is_bid_valid(bid_obj, bid_objects):
                    bid_objects.append(bid_obj)

                    # Reset pass count for non-pass bids
                    if not isinstance(bid_obj, PassBid):
                        passes_count = 0
                    else:
                        passes_count += 1
                else:
                    # Invalid bid - force a pass
                    bid_objects.append(PassBid(player))
                    passes_count += 1

                # Check for end conditions
                # End if 3 passes after a valid contract/double/redouble
                if passes_count >= 3 and len(bid_objects) > 3:
                    # Check if there's any non-pass bid
                    has_non_pass = any(not isinstance(bid, PassBid) for bid in bid_objects)
                    if has_non_pass:
                        break

            # Break outer loop if bidding should end
            if passes_count >= 3 and len(bid_objects) > 3:
                has_non_pass = any(not isinstance(bid, PassBid) for bid in bid_objects)
                if has_non_pass:
                    break

            # If all players passed in first round
            if len(bid_objects) >= 4 and all(isinstance(bid, PassBid) for bid in bid_objects[-4:]):
                break

        # Create Contract from final bid sequence
        contract_bid = BidValidator.get_last_contract(bid_objects)

        if contract_bid:
            # Check for double and redouble
            has_double = BidValidator.has_double(bid_objects)
            has_redouble = BidValidator.has_redouble(bid_objects)

            self.contract = Contract(contract_bid, double=has_double, redouble=has_redouble)
        else:
            self.contract = None

        return self.contract

    def play_trick(self, view=None) -> Optional['Player']:
        """
        Play a single trick and return winner.

        Args:
            view: Optional view for human player interaction

        Returns:
            Player: The winner of the trick or None if no winner
        """
        self.current_trick = Trick()
        trick_leader = self.players_order[0] if self.last_trick_winner is None else self.last_trick_winner

        # Determine the order for this trick (winner of last trick leads)
        leader_idx = self.players_order.index(trick_leader)
        trick_order = []
        for i in range(4):
            trick_order.append(self.players_order[(leader_idx + i) % 4])

        # Each player plays a card
        for player in trick_order:
            # Get the playable cards for this player
            playable_cards = self._get_playable_cards(player)

            if hasattr(player, 'choose_card'):
                # AI player or player with choose_card method
                # Pass playable cards to help AI make legal moves
                card = player.choose_card(self.current_trick, self.contract, playable_cards)
            else:
                # Simple fallback: play first playable card
                card = playable_cards[0] if playable_cards else None

            # If view provided and player is human, use view for input
            if view and hasattr(player, 'is_human') and player.is_human:
                card = view.request_card_action(player, self.current_trick, self.contract, playable_cards)

            # Validate that the chosen card is legal
            if card and card in playable_cards and card in player.hand:
                player.hand.remove(card)
                self.current_trick.add_play(player, card)
            elif card and playable_cards:
                # Card chosen is not legal - fallback to first playable card
                fallback_card = playable_cards[0]
                player.hand.remove(fallback_card)
                self.current_trick.add_play(player, fallback_card)

        # Determine trick winner
        winner = self._determine_trick_winner(self.current_trick)
        self.last_trick_winner = winner

        # Add trick to the tricks list and to winner's team
        if self.current_trick:
            self.tricks.append(self.current_trick)
            if winner and winner.team:
                self.team_tricks[winner.team.name].append(self.current_trick)

        # Add cards back to deck (last card played first, then reverse order)
        if self.current_trick and hasattr(self.current_trick, 'get_plays'):
            trick_cards = [card for _, card in self.current_trick.get_plays()]
            trick_cards.reverse()  # Last card played becomes first to be added back
            self.deck.add_cards(trick_cards)

        return winner

    def play_all_tricks(self, view=None) -> Dict[str, List[Trick]]:
        """
        Play all 8 tricks of the round.

        Args:
            view: Optional view for human player interaction

        Returns:
            Dict mapping team names to their tricks
        """
        # Initialize team tricks tracking
        self.last_trick_winner = None

        # Play 8 tricks
        for trick_num in range(8):
            winner = self.play_trick(view)

        return self.team_tricks

    def calculate_round_scores(self) -> Dict[str, int]:
        """
        Calculate scores for this round.

        Returns:
            Dict: Team scores for this round
        """
        if not self.contract:
            # No contract established, return zero scores
            teams = set(player.team for player in self.players_order)
            self.round_scores = {team.name: 0 for team in teams}
            return self.round_scores

        contract_team = self.contract.player.team
        contract_value = self.contract.value
        trump_suit = self.contract.suit
        is_doubled = self.contract.double
        is_redoubled = self.contract.redouble

        team_card_points = {team_name: 0 for team_name in self.team_tricks.keys()}
        team_scores = {team_name: 0 for team_name in self.team_tricks.keys()}

        # Count card points for each team and check for belote (King + Queen of trump)
        belote_teams = set()  # Teams that have belote
        for team_name, tricks in self.team_tricks.items():
            points = 0
            trump_cards_played = []

            for trick in tricks:
                # Handle Trick objects
                if hasattr(trick, 'get_plays'):
                    plays = trick.get_plays()
                    for player, card in plays:
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
        if self.last_trick_winner and self.last_trick_winner.team:
            last_trick_team = self.last_trick_winner.team.name
            team_card_points[last_trick_team] += 10

        contract_team_name = contract_team.name
        contract_team_points = team_card_points[contract_team_name]

        # Check if contract is made
        if contract_value == 'Capot':
            # For Capot, team must win all tricks (all 162 points)
            contract_made = contract_team_points >= 162
        else:
            contract_made = contract_team_points >= contract_value

        # Calculate multiplier for double/redouble
        multiplier = 1
        if is_redoubled:
            multiplier = 4
        elif is_doubled:
            multiplier = 2

        # Calculate final scores
        if contract_made:
            # Contract successful
            if is_doubled or is_redoubled:
                # When contract is made with double/redouble, attacking team gets
                # the same points that defending team would have gotten if contract failed
                base_value = 250 if contract_value == 'Capot' else contract_value
                team_scores[contract_team_name] = 160 + base_value * multiplier

                # Defending team gets their actual points (no multiplier)
                for team_name, points in team_card_points.items():
                    if team_name != contract_team_name:
                        team_scores[team_name] = points
            else:
                # Normal contract made without double/redouble
                base_value = 250 if contract_value == 'Capot' else contract_value
                team_scores[contract_team_name] = base_value + contract_team_points
                # Opposing team gets their points
                for team_name, points in team_card_points.items():
                    if team_name != contract_team_name:
                        team_scores[team_name] = points
        else:
            # Contract failed
            team_scores[contract_team_name] = 0  # Contract team gets 0
            # Opposing team gets all points + contract value
            base_value = 250 if contract_value == 'Capot' else contract_value
            for team_name in team_scores:
                if team_name != contract_team_name:
                    team_scores[team_name] = (160 + base_value) * multiplier

        self.round_scores = team_scores
        return team_scores

    def handle_failed_contract(self) -> Dict[str, int]:
        """
        Manage cards when all players pass.

        Returns:
            Dict: Zero scores for all teams
        """
        # Put all players' cards back in deck (8 cards per player)
        for player in self.players_order:
            self.deck.add_cards(player.hand)
            player.hand = []

        # Return zero scores
        teams = set(player.team for player in self.players_order)
        self.round_scores = {team.name: 0 for team in teams}
        return self.round_scores

    def _create_bid_from_choice(self, player: 'Player', choice) -> Bid:
        """
        Create a Bid object from a player's choice.

        Args:
            player: The player making the bid
            choice: The bid choice (string or tuple)

        Returns:
            Appropriate Bid object
        """
        if choice == 'Pass':
            return PassBid(player)
        elif choice == 'Double':
            return DoubleBid(player)
        elif choice == 'Redouble':
            return RedoubleBid(player)
        elif isinstance(choice, tuple) and len(choice) == 2:
            # Contract bid: (value, suit)
            value, suit = choice
            try:
                return ContractBid(player, value, suit)
            except ValueError:
                # Invalid contract parameters - return pass
                return PassBid(player)
        else:
            # Unknown bid format - return pass
            return PassBid(player)

    def _bid_to_legacy_format(self, bid: Bid):
        """
        Convert a Bid object to legacy format for compatibility.

        Args:
            bid: Bid object to convert

        Returns:
            Legacy format bid representation
        """
        if isinstance(bid, PassBid):
            return 'Pass'
        elif isinstance(bid, DoubleBid):
            return 'Double'
        elif isinstance(bid, RedoubleBid):
            return 'Redouble'
        elif isinstance(bid, ContractBid):
            return (bid.value, bid.suit)
        else:
            return 'Pass'

    def _get_playable_cards(self, player: 'Player'):
        """
        Determine which cards a player can legally play based on the current trick and contract rules.

        Args:
            player: The player whose playable cards we want to determine

        Returns:
            list: List of cards that can be legally played

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
        trump_suit = self.contract.suit if self.contract else None
        if not self.current_trick or not hasattr(self.current_trick, 'get_plays'):
            return player.hand.copy()

        plays = self.current_trick.get_plays()
        if not plays:
            return player.hand.copy()

        lead_suit = plays[0][1].suit  # First card played in trick

        # Cards of the lead suit in player's hand
        lead_suit_cards = [card for card in player.hand if card.suit == lead_suit]

        # If player has cards of the lead suit, must play one
        if lead_suit_cards:
            return lead_suit_cards

        # Player doesn't have lead suit, check if partner is leading
        trick_leader = plays[0][0]
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
        player_team = player.team

        for trick_player, card in plays:
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

    def _determine_trick_winner(self, trick: Trick) -> Optional['Player']:
        """
        Determines the winner of a trick based on the cards played.

        Args:
            trick: a Trick object containing the plays

        Returns:
            Player: The winner of the trick or None
        """
        trump_suit = self.contract.suit if self.contract else None
        if not trick or not hasattr(trick, 'get_plays'):
            return None

        plays = trick.get_plays()
        if not plays:
            return None

        lead_suit = plays[0][1].suit  # Suit of the first card played

        best_player = plays[0][0]
        best_card = plays[0][1]
        best_is_trump = trump_suit and best_card.suit == trump_suit

        for player, card in plays[1:]:
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
