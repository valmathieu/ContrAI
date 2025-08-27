# Player, HumanPlayer, AiPlayer classes

from abc import ABC, abstractmethod

class Player(ABC):
    def __init__(self, name, position):
        self.name = name
        self.position = position  # 'North', 'South', 'East', 'West'
        self.hand = []  # list of Card
        self.team = None  # Will be set by Game when teams are created

    @property
    def is_human(self):
        """Returns True if this is a human player."""
        return isinstance(self, HumanPlayer)

    @abstractmethod
    def choose_bid(self, current_bids):
        pass

    @abstractmethod
    def choose_card(self, trick, contract, playable_cards):
        pass

class HumanPlayer(Player):
    def choose_bid(self, current_bids):
        # This method should be called by the controller via the view
        # Example: return ('Pass') or (value, suit) or 'Double' or 'Redouble'
        return None  # To be implemented in controller/view

    def choose_card(self, trick, contract, playable_cards):
        # This method should be called by the controller via the view
        return None  # To be implemented in controller/view

class AiPlayer(Player):
    """
    AI Player with sophisticated bidding strategy based on functional specifications.

    Bidding strategy:
    1. Evaluate hand according to bidding table (80-160 points + Capot)
    2. If partner hasn't bid or bid lower, make initial bid
    3. If partner has bid, support with incremental bidding (+10 per external ace, +10 for trump complement)
    4. Choose best suit based on strength, belote, and preference order
    """

    # Bidding table
    BIDDING_TABLE = [
        # (contract, trump_expected, trump_min, aces, tens_non_dry, tricks_min, belote_required)
        (80, {'jack_or_nine': True, 'jack_and_nine': False}, 3, 1, 0, 4, False),
        (90, {'jack_or_nine': False, 'jack_and_nine': True}, 3, 1, 0, 4, False),
        (100, {'jack_or_nine': True, 'jack_and_nine': False}, 3, 2, 0, 5, False),
        (110, {'jack_or_nine': False, 'jack_and_nine': True}, 3, 2, 0, 5, False),
        (120, {'jack_or_nine': True, 'jack_and_nine': False}, 3, 3, 0, 6, False),
        (130, {'jack_or_nine': False, 'jack_and_nine': True}, 3, 3, 0, 6, False),
        (140, {'jack_or_nine': True, 'jack_and_nine': False}, 4, 3, 1, 6, True),
        (150, {'jack_or_nine': False, 'jack_and_nine': True}, 4, 3, 1, 6, True),
        (160, {'jack_or_nine': False, 'jack_and_nine': True, 'ace_required': True}, 5, 3, 2, 7, True),
    ]

    # Suit preference order (Spades, Hearts, Diamonds, Clubs)
    SUIT_PREFERENCE = ['Spades', 'Hearts', 'Diamonds', 'Clubs']

    def choose_bid(self, current_bids):
        """
        Choose a bid based on simple AI strategy.

        Args:
            current_bids: List of (player, bid) tuples from the current bidding round

        Returns:
            str or tuple: 'Pass', 'Double', 'Redouble', or (value, suit)
        """

        # Get current game state
        last_bid = self._get_last_bid(current_bids)
        partner_bid = self._get_partner_bid(current_bids)

        # Check if we can double or redouble
        double_action = self._check_double_redouble(current_bids, last_bid)
        if double_action:
            return double_action

        # Evaluate our hand for each suit
        suit_evaluations = self._evaluate_suits()

        # Determine bidding strategy
        if partner_bid is None or (isinstance(partner_bid, tuple) and self._can_overbid_partner(partner_bid, suit_evaluations)):
            # Make initial bid or overbid partner
            return self._make_initial_bid(suit_evaluations, last_bid)
        elif isinstance(partner_bid, tuple):
            # Support partner's bid
            return self._support_partner_bid(partner_bid, suit_evaluations, last_bid)

        return 'Pass'

    @staticmethod
    def _get_last_bid(current_bids):
        """Get the last non-pass bid."""
        for player, bid in reversed(current_bids):
            if bid != 'Pass' and isinstance(bid, tuple):
                return bid
        return None

    def _get_partner_bid(self, current_bids):
        """Get partner's last bid."""
        if not self.team:
            return None

        for player, bid in reversed(current_bids):
            if player.team == self.team and player != self and bid != 'Pass':
                return bid
        return None

    def _check_double_redouble(self, current_bids, last_bid):
        """Check if we should double or redouble."""
        if not last_bid or not isinstance(last_bid, tuple):
            return None

        # Find who made the last bid
        last_bidder = None
        for player, bid in reversed(current_bids):
            if bid == last_bid:
                last_bidder = player
                break

        if not last_bidder:
            return None

        # Check for double (only if opponent team made the bid)
        if last_bidder.team != self.team:
            # Simple double strategy: double if we have strong hand in other suits
            if self._should_double(last_bid):
                return 'Double'

        # Check for redouble (only if our team made the original bid and it was doubled)
        elif last_bidder.team == self.team:
            # Check if the contract was doubled
            for player, bid in reversed(current_bids):
                if bid == 'Double':
                    if self._should_redouble(last_bid):
                        return 'Redouble'
                    break

        return None

    def _should_double(self, opponent_bid):
        """Determine if we should double opponent's bid."""
        value, suit = opponent_bid

        # Count our strength outside their trump suit
        external_strength = 0

        for card in self.hand:
            if card.suit != suit:
                if card.rank == 'Ace':
                    external_strength += 11
                elif card.rank == '10':
                    external_strength += 10
                elif card.rank == 'King':
                    external_strength += 4

        # Double if we have significant external strength
        return external_strength >= 25

    def _should_redouble(self, our_bid):
        """Determine if we should redouble after being doubled."""
        value, suit = our_bid

        # Redouble if we have very strong trump suit
        trump_strength = self._evaluate_trump_strength(suit)
        return trump_strength >= value * 0.8  # Conservative redouble

    def _evaluate_suits(self):
        """Evaluate each suit for potential trump contracts."""
        evaluations = {}

        for suit in ['Spades', 'Hearts', 'Diamonds', 'Clubs']:
            evaluations[suit] = self._evaluate_suit_as_trump(suit)

        return evaluations

    def _evaluate_suit_as_trump(self, suit):
        """Evaluate a specific suit as potential trump."""
        trump_cards = [card for card in self.hand if card.suit == suit]

        if not trump_cards:
            return {'contract': 0, 'strength': 0, 'has_belote': False}

        # Count trump strength
        has_jack = any(card.rank == 'Jack' for card in trump_cards)
        has_nine = any(card.rank == '9' for card in trump_cards)
        has_ace = any(card.rank == 'Ace' for card in trump_cards)
        has_king = any(card.rank == 'King' for card in trump_cards)
        has_queen = any(card.rank == 'Queen' for card in trump_cards)

        trump_count = len(trump_cards)

        # Check for belote (King + Queen of trump)
        has_belote = has_king and has_queen

        # Count external aces
        external_aces = sum(1 for card in self.hand
                          if card.suit != suit and card.rank == 'Ace')

        # Count non-dry tens (tens with at least one other card in the suit)
        non_dry_tens = 0
        for other_suit in ['Spades', 'Hearts', 'Diamonds', 'Clubs']:
            if other_suit != suit:
                suit_cards = [card for card in self.hand if card.suit == other_suit]
                has_ten = any(card.rank == '10' for card in suit_cards)
                if has_ten and len(suit_cards) > 1:
                    non_dry_tens += 1

        # Estimate trick-taking potential
        estimated_tricks = self._estimate_tricks(suit)

        # Find highest contract we can bid
        max_contract = 0

        for contract, trump_req, trump_min, aces_req, tens_req, tricks_req, belote_req in self.BIDDING_TABLE:
            # Check trump requirements
            trump_ok = trump_count >= trump_min

            if trump_req.get('jack_and_nine', False):
                trump_ok = trump_ok and has_jack and has_nine
            elif trump_req.get('jack_or_nine', False):
                trump_ok = trump_ok and (has_jack or has_nine)

            if trump_req.get('ace_required', False):
                trump_ok = trump_ok and has_ace

            # Check other requirements
            if (trump_ok and
                external_aces >= aces_req and
                non_dry_tens >= tens_req and
                estimated_tricks >= tricks_req and
                (not belote_req or has_belote)):
                max_contract = contract

        return {
            'contract': max_contract,
            'strength': trump_count * 10 + external_aces * 5,
            'has_belote': has_belote,
            'trump_count': trump_count,
            'external_aces': external_aces,
            'estimated_tricks': estimated_tricks
        }

    def _estimate_tricks(self, trump_suit):
        """Estimate number of tricks we can take with this trump suit."""
        tricks = 0

        # Count trump tricks
        trump_cards = [card for card in self.hand if card.suit == trump_suit]
        trump_strength = 0

        for card in trump_cards:
            if card.rank == 'Jack':
                trump_strength += 20
            elif card.rank == '9':
                trump_strength += 14
            elif card.rank == 'Ace':
                trump_strength += 11
            elif card.rank == '10':
                trump_strength += 10
            elif card.rank == 'King':
                trump_strength += 4
            elif card.rank == 'Queen':
                trump_strength += 3

        # Rough estimation: strong trumps can take multiple tricks
        if trump_strength >= 40:
            tricks += 3
        elif trump_strength >= 25:
            tricks += 2
        elif trump_strength >= 15:
            tricks += 1

        # Count external aces as potential tricks
        for suit in ['Spades', 'Hearts', 'Diamonds', 'Clubs']:
            if suit != trump_suit:
                suit_cards = [card for card in self.hand if card.suit == suit]
                if any(card.rank == 'Ace' for card in suit_cards):
                    tricks += 1

        return min(tricks, 8)  # Maximum 8 tricks in a round

    def _can_overbid_partner(self, partner_bid, suit_evaluations):
        """Check if we can make a higher bid than our partner."""
        partner_value, partner_suit = partner_bid

        # Find our best contract
        best_contract = 0
        for suit_eval in suit_evaluations.values():
            best_contract = max(best_contract, suit_eval['contract'])

        return best_contract > partner_value

    def _make_initial_bid(self, suit_evaluations, last_bid):
        """Make an initial bid or overbid."""
        # Find the best suit to bid
        best_suits = []
        max_contract = 0

        for suit, evaluation in suit_evaluations.items():
            if evaluation['contract'] > max_contract:
                max_contract = evaluation['contract']
                best_suits = [suit]
            elif evaluation['contract'] == max_contract and max_contract > 0:
                best_suits.append(suit)

        if max_contract == 0:
            return 'Pass'

        # Check if we can overbid the last bid
        if last_bid:
            last_value, _ = last_bid
            if max_contract <= last_value:
                return 'Pass'

        # Choose best suit among candidates
        chosen_suit = self._choose_best_suit(best_suits, suit_evaluations)

        return (max_contract, chosen_suit)

    def _support_partner_bid(self, partner_bid, suit_evaluations, last_bid):
        """Support partner's bid with incremental bidding."""
        partner_value, partner_suit = partner_bid

        # Calculate our contribution to partner's suit
        contribution = 0

        # +10 per external ace
        for card in self.hand:
            if card.suit != partner_suit and card.rank == 'Ace':
                contribution += 10

        # +10 if we have trump complement (Jack or 9)
        trump_cards = [card for card in self.hand if card.suit == partner_suit]
        has_jack = any(card.rank == 'Jack' for card in trump_cards)
        has_nine = any(card.rank == '9' for card in trump_cards)

        if has_jack or has_nine:
            contribution += 10

        # Calculate new bid value
        new_value = partner_value + contribution

        # Round to nearest 10
        new_value = ((new_value + 5) // 10) * 10

        # Check if we can make this bid
        if last_bid:
            last_value, _ = last_bid
            if new_value <= last_value:
                return 'Pass'

        # Don't bid beyond 160
        if new_value > 160:
            return 'Pass'

        return (new_value, partner_suit)

    def _choose_best_suit(self, candidate_suits, suit_evaluations):
        """Choose the best suit from candidates based on functional specs."""
        if len(candidate_suits) == 1:
            return candidate_suits[0]

        # First, choose suit where we can bid the most
        max_contract = max(suit_evaluations[suit]['contract'] for suit in candidate_suits)
        strongest_suits = [suit for suit in candidate_suits
                          if suit_evaluations[suit]['contract'] == max_contract]

        if len(strongest_suits) == 1:
            return strongest_suits[0]

        # If tied, prefer suit with belote
        belote_suits = [suit for suit in strongest_suits
                       if suit_evaluations[suit]['has_belote']]

        if belote_suits:
            if len(belote_suits) == 1:
                return belote_suits[0]
            strongest_suits = belote_suits

        # If still tied, use preference order: Spades, Hearts, Diamonds, Clubs
        for preferred_suit in self.SUIT_PREFERENCE:
            if preferred_suit in strongest_suits:
                return preferred_suit

        return strongest_suits[0]  # Fallback

    def _evaluate_trump_strength(self, suit):
        """Evaluate total strength of trump suit."""
        trump_cards = [card for card in self.hand if card.suit == suit]
        strength = 0

        for card in trump_cards:
            strength += card.get_points(suit)

        return strength

    def choose_card(self, trick, contract, playable_cards):
        """
        Choose a card to play based on AI strategy.
        This is a placeholder - the full card playing logic will be implemented separately.
        """
        if not playable_cards:
            return None

        # Simple strategy: play first playable card
        return playable_cards[0]
