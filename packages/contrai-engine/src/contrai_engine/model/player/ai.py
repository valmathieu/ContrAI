# AiPlayer — holds pluggable strategies and delegates to them.
#
# ``AiPlayer`` owns no strategic logic of its own. It holds a bidding
# strategy and (still inline, pending extraction) the card-play logic,
# and routes the engine's calls to them. The default bidding strategy is
# the expert rule-based one, so ``AiPlayer("Bot", "South")`` keeps
# producing today's bot.

from contrai_core.auction import Auction
from contrai_core.bid import Bid
from contrai_core.types import Rank, Suit

from .base import Player
from .rule_based import RuleBasedBiddingStrategy


class AiPlayer(Player):
    """AI player delegating bidding to an injected strategy.

    The bidding policy is supplied as a *factory* (``player -> strategy``,
    i.e. the strategy class itself) so the strategy can take a
    back-reference to this player while the player is still being built.
    Defaults reproduce today's expert bot.
    """

    def __init__(self, name, position, bidding=RuleBasedBiddingStrategy):
        """Build an AI player with an injected bidding strategy.

        Args:
            name: Display name.
            position: Seat position (``'North'`` / ``'South'`` / …).
            bidding: A factory ``player -> BiddingStrategy``. Defaults to
                :class:`RuleBasedBiddingStrategy` (the ``"expert"`` level).
        """

        super().__init__(name, position)
        self.bidding = bidding(self)

    def choose_bid(self, auction: Auction) -> Bid:
        """Delegate to the injected bidding strategy."""
        return self.bidding.choose_bid(auction)

    def choose_card(self, trick, contract, playable_cards):
        """
        Choose a card to play based on simple AI strategy.

        Args:
            trick: List of (player, card) tuples, current trick with cards played by players so far
            contract: Current contract (value, trump_suit, player) or None
            playable_cards: List of cards the AI can legally play

        Returns:
            Card: The chosen card to play
        """

        # Lazy-init card tracking. The engine never calls
        # initialize_card_tracking() explicitly, so without this guard
        # _is_master_card / _opponents_might_have_trump crash on the
        # first non-opening trick.
        if not hasattr(self, '_fallen_cards'):
            self.initialize_card_tracking()

        # Determine strategy based on position in trick
        # TODO: adapt the code using the game class to know the trick number
        # First to play - use fallback approach since we don't have game reference
        if len(trick) == 0:
            # Check if this is likely the very first card by checking if we have tracking data
            if not hasattr(self, '_fallen_cards') or all(len(cards) == 0 for cards in self._fallen_cards.values()):
                return self._play_opening_card(contract, playable_cards)
            else:
                return self._play_leading_card(contract, playable_cards)
        else:
            # Not first to play
            return self._play_following_card(trick, contract, playable_cards)

    def initialize_card_tracking(self):
        """Initialize tracking of fallen cards and trump distribution. Should be called by the game."""

        self._fallen_cards = {
            Suit.SPADES: set(),
            Suit.HEARTS: set(),
            Suit.DIAMONDS: set(),
            Suit.CLUBS: set()
        }
        self._players_without_trump = set()

    def update_card_tracking(self, card, player, led_suit, trump_suit):
        """
        Update tracking based on a card played by any player.
        Should be called by the game whenever a card is played.

        Args:
            card: The card that was played
            player: Player who played the card
            led_suit: The suit that was led this trick
            trump_suit: The current trump suit
        """

        if not hasattr(self, '_fallen_cards'):
            self.initialize_card_tracking()

        # Track fallen cards
        self._fallen_cards[card.suit].add(card.rank)

        # Track trump distribution - if player couldn't follow suit and didn't trump
        if (led_suit == trump_suit and card.suit != trump_suit) or (led_suit != trump_suit and
                (card.suit != led_suit and card.suit != trump_suit)):
            # Player couldn't follow suit and didn't trump - no trump
            self._players_without_trump.add(player)

    def _play_first_card(self, game, contract, playable_cards):
        """Strategy when AI is first to play in the trick."""

        # Check if this is the very first card of the round
        if game.current_trick_number == 0:
            return self._play_opening_card(contract, playable_cards)

        # Subsequent tricks when AI leads
        return self._play_leading_card(contract, playable_cards)

    def _play_opening_card(self, contract, playable_cards):
        """Play the very first card of the round."""

        trump_suit = contract.suit if contract else None

        if contract and contract.player.team == self.team:
            # Our team has the contract - play the strongest trump
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                sorted_trumps = sorted(trump_cards, key=lambda c: c.get_order(trump_suit), reverse = True)
                if sorted_trumps[0].rank == Rank.NINE and len(sorted_trumps) > 1:
                    # Avoid playing 9 first
                    return sorted_trumps[1]
                else:
                    return sorted_trumps[0]
        else:
            # Opponents have contract - play an ace if we have one
            aces = [c for c in playable_cards if c.rank == Rank.ACE]
            if aces:
                # Play ace from the shortest suit
                return min(aces, key=lambda c: self.hand.count_suit(c.suit))

        # Default: play the lowest value card (excluding trump unless only trumps available)
        non_trump_cards = [c for c in playable_cards if c.suit != trump_suit] if trump_suit else playable_cards

        if not non_trump_cards:
            # Only trump cards available, use all playable cards
            cards_to_consider = playable_cards
        else:
            # Use non-trump cards
            cards_to_consider = non_trump_cards

        # Find cards with minimum points value
        min_points = min(c.get_points(trump_suit) for c in cards_to_consider)
        lowest_value_cards = [c for c in cards_to_consider if c.get_points(trump_suit) == min_points]

        # If multiple cards with same lowest value, choose randomly
        return lowest_value_cards[0]

    def _play_leading_card(self, contract, playable_cards):
        """Play when leading subsequent tricks."""

        trump_suit = contract.suit if contract else None

        # If the team has the contract and opponents might still have trump, play the strongest trump
        if contract and contract.player.team == self.team and self._opponents_might_have_trump(trump_suit):
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                return max(trump_cards, key=lambda c: c.get_order(trump_suit))

        # TODO: exclude trump from logic if we know opponents have no trump left
        # No trump left with opponents - play ace from the longest suit
        aces = [c for c in playable_cards if c.rank == Rank.ACE]
        if aces:
            return max(aces, key=lambda c: self.hand.count_suit(c.suit))

        # Play master card from the longest suit
        master_cards = [c for c in playable_cards if self._is_master_card(c, trump_suit)]
        if master_cards:
            return max(master_cards, key=lambda c: self.hand.count_suit(c.suit))

        # Default: play the lowest value card (excluding trump unless only trumps available)
        non_trump_cards = [c for c in playable_cards if c.suit != trump_suit] if trump_suit else playable_cards

        if not non_trump_cards:
            # Only trump cards available, use all playable cards
            cards_to_consider = playable_cards
        else:
            # Use non-trump cards
            cards_to_consider = non_trump_cards

        # Find cards with minimum points value
        min_points = min(c.get_points(trump_suit) for c in cards_to_consider)
        lowest_value_cards = [c for c in cards_to_consider if c.get_points(trump_suit) == min_points]

        # If multiple cards with same lowest value, choose randomly
        return lowest_value_cards[0]

    def _play_following_card(self, trick, contract, playable_cards):
        """Strategy when not first to play."""

        team_winning = self._is_team_winning_trick(trick)

        if team_winning:
            return self._play_when_team_winning(trick, contract, playable_cards)
        else:
            return self._play_when_team_losing(trick, contract, playable_cards)

    def _play_when_team_winning(self, trick, contract, playable_cards):
        """Play when our team is currently winning the trick.

        Partner already secures the trick, so the goal is to add value
        (high-points cards) to the pile WITHOUT wasting trumps:

        1. Follow suit if able — pile the highest-points lead-suit card
           on partner's win.
        2. Cannot follow suit → discard a NON-TRUMP card. Don't dump
           trumps onto a trick the partner has already locked down.
           Prefer non-master cards (preserve cards that can still win
           their suit later); within the candidate set, pick the
           highest-points to maximize this trick's value.
        3. Hand has nothing but trumps → forced to play one. Use the
           lowest trump so we don't waste the Jack or 9.
        """
        trump_suit = contract.suit if contract else None
        led_suit = trick.get_led_suit()

        # 1. Follow suit if able.
        same_suit_cards = [c for c in playable_cards if c.suit == led_suit]
        if same_suit_cards:
            return max(same_suit_cards, key=lambda c: c.get_points(trump_suit))

        # 2. Discard a non-trump card.
        non_trump_cards = [
            c for c in playable_cards if c.suit != trump_suit
        ]
        if non_trump_cards:
            non_master_non_trump = [
                c for c in non_trump_cards
                if not self._is_master_card(c, trump_suit)
            ]
            candidates = non_master_non_trump or non_trump_cards
            return max(candidates, key=lambda c: c.get_points(trump_suit))

        # 3. Only trumps in hand — dump the lowest one.
        if playable_cards:
            return min(playable_cards, key=lambda c: c.get_order(trump_suit))
        return playable_cards[0]

    def _play_when_team_losing(self, trick, contract, playable_cards):
        """Play when opponents are currently winning the trick."""

        trump_suit = contract.suit if contract else None
        led_suit = trick.get_led_suit()
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
                # Can't beat - play the lowest card
                return min(same_suit_cards, key=lambda c: c.get_points(trump_suit))

        # Can't follow suit - try to trump
        if trump_suit and led_suit != trump_suit:
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                # Trump with the lowest trump that can win
                winning_trumps = [c for c in trump_cards
                                if self._can_trump_win(c, trick, trump_suit)]
                if winning_trumps:
                    return min(winning_trumps, key=lambda c: c.get_order(trump_suit))


        # Can't follow or trump - discard lowest from the shortest suit (excluding masters)
        non_master_cards = [c for c in playable_cards if not self._is_master_card(c, trump_suit)]
        if non_master_cards:
            return min(non_master_cards, key=lambda c: (
                self.hand.count_suit(c.suit),
                c.get_points(trump_suit)
            ))

        return playable_cards[0]

    def _opponents_might_have_trump(self, trump_suit):
        """Check if opponents might still have trump cards."""

        # TODO: upgrade to exclude partner if we can track their cards
        # Count trump cards we've seen fall
        trump_fallen = len(self._fallen_cards.get(trump_suit, set()))
        trump_in_hand = self.hand.count_suit(trump_suit)

        # Total trump cards is 8, if we've seen less than 8 - trump_in_hand, opponents might have some
        return trump_fallen < (8 - trump_in_hand)

    # TODO: replace trump_suit with a boolean is_trump parameter
    def _is_master_card(self, card, trump_suit):
        """Check if a card is currently the master (highest remaining) in its suit."""

        # Get fallen cards in this suit
        suit_fallen = self._fallen_cards.get(card.suit, set())

        # Get all ranks higher than this card's rank
        higher_ranks = self._get_higher_ranks(card.rank, card.suit, trump_suit)

        # Check if all higher cards have fallen
        return all(rank in suit_fallen for rank in higher_ranks)

    @staticmethod
    def _get_higher_ranks(rank, suit, trump_suit):
        """Get all ranks higher than the given rank in the suit."""

        if suit == trump_suit:
            # Trump order: 7, 8, Queen, King, 10, Ace, 9, Jack
            trump_order = [Rank.SEVEN, Rank.EIGHT, Rank.QUEEN, Rank.KING, Rank.TEN, Rank.ACE, Rank.NINE, Rank.JACK]
        else:
            # Normal order: 7, 8, 9, Jack, Queen, King, 10, Ace
            trump_order = [Rank.SEVEN, Rank.EIGHT, Rank.NINE, Rank.JACK, Rank.QUEEN, Rank.KING, Rank.TEN, Rank.ACE]

        try:
            rank_index = trump_order.index(rank)
            return trump_order[rank_index + 1:]
        except ValueError:
            return []

    def _is_team_winning_trick(self, trick, trump_suit=None):
        """Check if our team is currently winning the trick."""

        # TODO: check with trick number from game
        if len(trick) < 1:
            return False

        # Find partner's position
        partner_position = self._get_partner_position()

        # Check if partner played the strongest card so far
        strongest_position = self._get_strongest_card_position(trick, trump_suit)
        return strongest_position == partner_position

    def _get_partner_position(self):
        """Get partner's position."""

        position_map = {'North': 'South', 'South': 'North', 'East': 'West', 'West': 'East'}
        return position_map.get(self.position)

    def _get_strongest_card_position(self, trick, trump_suit):
        """Get the position of the player who played the strongest card."""

        if not trick:
            return None

        strongest_card = self._get_strongest_card_in_trick(trick, trump_suit)

        # Find which player played the strongest card
        for player, card in trick.get_plays():
            if card == strongest_card:
                return player.position

        return None

    @staticmethod
    def _get_strongest_card_in_trick(trick, trump_suit):
        """Get the strongest card played so far in the trick."""

        if not trick:
            return None

        led_suit = trick.get_led_suit()
        cards = trick.get_cards()

        # Trump cards beat non-trump (unless led suit is trump)
        if led_suit != trump_suit:
            trump_cards = [c for c in cards if c.suit == trump_suit]
            if trump_cards:
                return max(trump_cards, key=lambda c: c.get_order(trump_suit))

        # Among cards of led suit
        led_suit_cards = [c for c in cards if c.suit == led_suit]
        if led_suit_cards:
            return max(led_suit_cards, key=lambda c: c.get_order(trump_suit if led_suit == trump_suit else None))

        return cards[0]

    @staticmethod
    def _is_stronger_card(card, current_best, trump_suit):
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
