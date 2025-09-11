# Player, HumanPlayer, AiPlayer classes

from abc import ABC, abstractmethod
from contree.model.card import Card
SUITS = Card.SUITS

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
    2. If partner hasn't bid or bid lower, make initial bid if it's hand is strong enough
    3. If partner has bid, support with incremental bidding (+10 per external ace, +10 for trump complement)
    4. If multiple bid are possible : choose best suit based on strength, belote
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
    SUIT_PREFERENCE = SUITS

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

        for player, bid in reversed(current_bids):
            if player.team == self.team and bid != 'Pass':
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

        # Check for double (only if opponent team made the bid)
        if last_bidder.team != self.team:
            # Simple double strategy: double if we have strong hand in other suits
            if self._should_double(last_bid):
                return 'Double'

        # Check for redouble (only if the opposite team made the original bid it was a double)
        if last_bidder.team != self.team and last_bid == 'Double':
            if self._should_redouble():
                return 'Redouble'

        return None

    def _should_double(self, opponent_bid):
        """Determine if we should double opponent's bid."""

        value, suit = opponent_bid

        strength = self._estimate_tricks(suit) * 20  # Each expected trick worth 20 points

        # Double if we have significant external strength
        return strength > 162 - value

    @staticmethod
    def _should_redouble():
        """Determine if we should redouble after being doubled."""

        # TODO: Implement a redouble strategy
        return False

    def _evaluate_suits(self):
        """Evaluate each suit for potential trump contracts."""
        
        evaluations = {}

        for suit in SUITS:
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
        for other_suit in SUITS:
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
            'has_belote': has_belote,
            'trump_count': trump_count,
            'external_aces': external_aces,
            'estimated_tricks': estimated_tricks
        }

    def _estimate_tricks(self, trump_suit):
        """Estimate number of tricks we can take with this trump suit."""

        tricks = 0

        # Count our strength inside their trump suit
        tricks += self._evaluate_trump_tricks(trump_suit)

        # Count our strength outside their trump suit
        for card in self.hand:
            if card.suit != trump_suit:
                if card.rank == 'Ace':
                    tricks += 1
                if card.rank == '10' and self._count_cards_in_suit(card.suit) > 1:
                    tricks += 1
                if (card.rank == 'King' or card.rank == 'Queen') and self._suit_has_rank(card.suit, 'Ace')\
                        and self._suit_has_rank(card.suit, '10'):
                    tricks += 1

        return min(tricks, 8)  # Maximum 8 tricks in a round

    @staticmethod
    def _can_overbid_partner(partner_bid, suit_evaluations):
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

        return max_contract, chosen_suit

    def _support_partner_bid(self, partner_bid, suit_evaluations, last_bid):
        """Support partner's bid with incremental bidding."""

        _, partner_suit = partner_bid

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
        last_value, _ = last_bid
        new_value = last_value + contribution

        # Don't bid beyond 160
        if new_value > 160:
            return 'Pass'

        return new_value, partner_suit

    def _choose_best_suit(self, candidate_suits, suit_evaluations):
        """Choose the best suit from candidates."""

        if len(candidate_suits) == 1:
            return candidate_suits[0]

        strongest_suits = []

        # If tied, prefer suit with belote
        belote_suits = [suit for suit in candidate_suits
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

    def _evaluate_trump_tricks(self, suit):
        """Evaluate potential tricks won with trump suit."""

        trump_cards = [card for card in self.hand if card.suit == suit]
        expected_won_tricks = 0

        has_jack = False
        has_nine = False
        has_ace = False

        if len(trump_cards) > 0:
            has_jack = any(card.rank == 'Jack' for card in trump_cards)
            has_nine = any(card.rank == '9' for card in trump_cards)
            has_ace = any(card.rank == 'Ace' for card in trump_cards)

            if has_jack and has_nine:
                expected_won_tricks += 2  # Both Jack and 9
            elif has_jack or has_nine:
                expected_won_tricks += 1  # Either Jack or 9

        if len(trump_cards) >= 3:
            expected_won_tricks += len(trump_cards) - 3 + (not has_ace) - has_jack - has_nine

        return expected_won_tricks

    def choose_card(self, trick, contract, playable_cards):
        """
        Choose a card to play based on AI strategy from functional specifications.

        Args:
            trick: Current trick with cards played so far
            contract: Current contract (value, trump_suit) or None
            playable_cards: List of cards the AI can legally play

        Returns:
            Card: The chosen card to play
        """
        if not playable_cards:
            return None

        trump_suit = contract[1] if contract else None

        # Initialize card tracking if not done
        if not hasattr(self, '_fallen_cards'):
            self._initialize_card_tracking()

        # Update card tracking with current trick
        self._update_card_tracking(trick, trump_suit)

        # Determine strategy based on position in trick
        if len(trick.cards) == 0:
            # First to play
            return self._play_first_card(contract, playable_cards)
        else:
            # Not first to play
            return self._play_following_card(trick, contract, playable_cards)

    def _initialize_card_tracking(self):
        """Initialize tracking of fallen cards and trump distribution."""
        self._fallen_cards = {
            'Spades': set(),
            'Hearts': set(),
            'Diamonds': set(),
            'Clubs': set()
        }
        self._players_without_trump = set()

    def _update_card_tracking(self, trick, trump_suit):
        """Update tracking based on cards played in current trick and previous tricks."""
        if not hasattr(trick, 'cards') or not trick.cards:
            return

        led_suit = trick.cards[0].suit

        for i, card in enumerate(trick.cards):
            # Track fallen cards
            self._fallen_cards[card.suit].add(card.rank)

            # Track trump distribution - if player couldn't follow suit and didn't trump
            if trump_suit and led_suit != trump_suit and card.suit != led_suit and card.suit != trump_suit:
                # Player couldn't follow suit and didn't trump - no trump
                player_position = (trick.leader_position + i) % 4 if hasattr(trick, 'leader_position') else i
                self._players_without_trump.add(player_position)

    def _play_first_card(self, contract, playable_cards):
        """Strategy when AI is first to play in the trick."""
        trump_suit = contract[1] if contract else None

        # Check if this is the very first card of the round
        if self._is_first_card_of_round():
            return self._play_opening_card(contract, playable_cards)

        # Subsequent tricks when AI leads
        return self._play_leading_card(contract, playable_cards)

    def _is_first_card_of_round(self):
        """Check if no tricks have been played yet."""
        return all(len(cards) == 0 for cards in self._fallen_cards.values())

    def _play_opening_card(self, contract, playable_cards):
        """Play the very first card of the round."""
        trump_suit = contract[1] if contract else None

        if self._team_has_contract(contract):
            # Our team has the contract - play strongest trump
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                return max(trump_cards, key=lambda c: c.get_order(trump_suit))
        else:
            # Opponents have contract - play an ace if we have one
            aces = [c for c in playable_cards if c.rank == 'Ace']
            if aces:
                # Play ace from shortest suit
                return min(aces, key=lambda c: self._count_cards_in_suit(c.suit))

        # Fallback - play first available card
        return playable_cards[0]

    def _play_leading_card(self, contract, playable_cards):
        """Play when leading subsequent tricks."""
        trump_suit = contract[1] if contract else None

        # If opponents might still have trump, play strongest trump
        if trump_suit and self._opponents_might_have_trump(trump_suit):
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                return max(trump_cards, key=lambda c: c.get_order(trump_suit))

        # No trump left with opponents - play ace from longest suit
        aces = [c for c in playable_cards if c.rank == 'Ace']
        if aces:
            return max(aces, key=lambda c: self._count_cards_in_suit(c.suit))

        # Play master card from longest suit
        master_cards = [c for c in playable_cards if self._is_master_card(c, trump_suit)]
        if master_cards:
            return max(master_cards, key=lambda c: self._count_cards_in_suit(c.suit))

        return playable_cards[0]

    def _play_following_card(self, trick, contract, playable_cards):
        """Strategy when not first to play."""
        trump_suit = contract[1] if contract else None
        led_suit = trick.cards[0].suit

        team_winning = self._is_team_winning_trick(trick)

        if team_winning:
            return self._play_when_team_winning(trick, contract, playable_cards)
        else:
            return self._play_when_team_losing(trick, contract, playable_cards)

    def _play_when_team_winning(self, trick, contract, playable_cards):
        """Play when our team is currently winning the trick."""
        trump_suit = contract[1] if contract else None
        led_suit = trick.cards[0].suit

        # Try to follow suit with highest point card
        same_suit_cards = [c for c in playable_cards if c.suit == led_suit]
        if same_suit_cards:
            return max(same_suit_cards, key=lambda c: c.get_points(trump_suit))

        # Can't follow suit - play highest point card (excluding masters)
        non_master_cards = [c for c in playable_cards if not self._is_master_card(c, trump_suit)]
        if non_master_cards:
            return max(non_master_cards, key=lambda c: c.get_points(trump_suit))

        return playable_cards[0]

    def _play_when_team_losing(self, trick, contract, playable_cards):
        """Play when opponents are currently winning the trick."""
        trump_suit = contract[1] if contract else None
        led_suit = trick.cards[0].suit
        current_best = self._get_strongest_card_in_trick(trick, trump_suit)

        # Try to follow suit
        same_suit_cards = [c for c in playable_cards if c.suit == led_suit]
        if same_suit_cards:
            # Try to beat the current best card
            stronger_cards = [c for c in same_suit_cards
                             if self._is_stronger_card(c, current_best, trump_suit)]
            if stronger_cards:
                return max(stronger_cards, key=lambda c: c.get_points(trump_suit))
            else:
                # Can't beat - play lowest card
                return min(same_suit_cards, key=lambda c: c.get_points(trump_suit))

        # Can't follow suit - try to trump
        if trump_suit and led_suit != trump_suit:
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                # Trump with lowest trump that can win
                winning_trumps = [c for c in trump_cards
                                if self._can_trump_win(c, trick, trump_suit)]
                if winning_trumps:
                    return min(winning_trumps, key=lambda c: c.get_order(trump_suit))

        # Can't follow or trump - discard lowest from shortest suit (excluding masters)
        non_master_cards = [c for c in playable_cards if not self._is_master_card(c, trump_suit)]
        if non_master_cards:
            return min(non_master_cards, key=lambda c: (
                self._count_cards_in_suit(c.suit),
                c.get_points(trump_suit)
            ))

        return playable_cards[0]

    def _team_has_contract(self, contract):
        """Check if our team has the contract."""
        # This would need to be implemented based on game state
        # For now, we'll need to track this in the game state
        # Placeholder implementation
        return False

    def _count_cards_in_suit(self, suit):
        """Count how many cards we have in the given suit."""

        return sum(1 for card in self.hand if card.suit == suit)

    def _suit_has_rank(self, suit, rank):
        """
        Check if the player has a specific rank in a given suit.

        Args:
            suit: The suit to check ('Spades', 'Hearts', 'Diamonds', 'Clubs')
            rank: The rank to look for ('7', '8', '9', '10', 'Jack', 'Queen', 'King', 'Ace')

        Returns:
            bool: True if the player has the specified rank in the specified suit
        """

        return any(card.suit == suit and card.rank == rank for card in self.hand)

    def _opponents_might_have_trump(self, trump_suit):
        """Check if opponents might still have trump cards."""

        if not trump_suit:
            return False

        # Count trump cards we've seen fall
        trump_fallen = len(self._fallen_cards.get(trump_suit, set()))
        trump_in_hand = sum(1 for card in self.hand if card.suit == trump_suit)

        # Total trump cards is 8, if we've seen less than 8 - trump_in_hand, opponents might have some
        return trump_fallen < (8 - trump_in_hand)

    def _is_master_card(self, card, trump_suit):
        """Check if a card is currently the master (highest remaining) in its suit."""
        suit_fallen = self._fallen_cards.get(card.suit, set())

        # Get all ranks higher than this card's rank
        higher_ranks = self._get_higher_ranks(card.rank, card.suit, trump_suit)

        # Check if all higher cards have fallen
        return all(rank in suit_fallen for rank in higher_ranks)

    def _get_higher_ranks(self, rank, suit, trump_suit):
        """Get all ranks higher than the given rank in the suit."""
        if suit == trump_suit:
            # Trump order: 7, 8, Queen, King, 10, Ace, 9, Jack
            trump_order = ['7', '8', 'Queen', 'King', '10', 'Ace', '9', 'Jack']
        else:
            # Normal order: 7, 8, 9, Jack, Queen, King, 10, Ace
            trump_order = ['7', '8', '9', 'Jack', 'Queen', 'King', '10', 'Ace']

        try:
            rank_index = trump_order.index(rank)
            return trump_order[rank_index + 1:]
        except ValueError:
            return []

    def _is_team_winning_trick(self, trick):
        """Check if our team is currently winning the trick."""
        if len(trick.cards) < 1:
            return False

        # Find partner's position
        partner_position = self._get_partner_position()

        # Check if partner played the strongest card so far
        strongest_position = self._get_strongest_card_position(trick)
        return strongest_position == partner_position

    def _get_partner_position(self):
        """Get partner's position."""
        position_map = {'North': 'South', 'South': 'North', 'East': 'West', 'West': 'East'}
        return position_map.get(self.position)

    def _get_strongest_card_position(self, trick):
        """Get the position of the player who played the strongest card."""
        if not trick.cards:
            return None

        trump_suit = getattr(trick, 'trump_suit', None)
        strongest_card = self._get_strongest_card_in_trick(trick, trump_suit)

        for i, card in enumerate(trick.cards):
            if card == strongest_card:
                leader_pos = getattr(trick, 'leader_position', 0)
                return (leader_pos + i) % 4

        return None

    def _get_strongest_card_in_trick(self, trick, trump_suit):
        """Get the strongest card played so far in the trick."""
        if not trick.cards:
            return None

        led_suit = trick.cards[0].suit

        # Trump cards beat non-trump (unless led suit is trump)
        if trump_suit and led_suit != trump_suit:
            trump_cards = [c for c in trick.cards if c.suit == trump_suit]
            if trump_cards:
                return max(trump_cards, key=lambda c: c.get_order(trump_suit))

        # Among cards of led suit
        led_suit_cards = [c for c in trick.cards if c.suit == led_suit]
        if led_suit_cards:
            return max(led_suit_cards, key=lambda c: c.get_order(trump_suit if led_suit == trump_suit else None))

        return trick.cards[0]

    def _is_stronger_card(self, card, current_best, trump_suit):
        """Check if card is stronger than current_best."""
        if not current_best:
            return True

        # If current best is trump and our card isn't (and trump is not led suit)
        if current_best.suit == trump_suit and card.suit != trump_suit:
            return False

        # If our card is trump and current best isn't
        if card.suit == trump_suit and current_best.suit != trump_suit:
            return True

        # Both trump or both same suit
        if card.suit == current_best.suit:
            return card.get_order(trump_suit if card.suit == trump_suit else None) > current_best.get_order(trump_suit if current_best.suit == trump_suit else None)

        return False

    def _can_trump_win(self, trump_card, trick, trump_suit):
        """Check if playing this trump card would win the trick."""
        current_best = self._get_strongest_card_in_trick(trick, trump_suit)
        return self._is_stronger_card(trump_card, current_best, trump_suit)

