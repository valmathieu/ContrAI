# Rule-based AI strategies — the expert table of SF-09/SF-10.
#
# These are the *first* concrete rung of the AI ladder (AI roadmap §6),
# registered as ``AI_LEVELS["expert"]`` (see :mod:`.levels`). They are the
# logic that used to live inline on ``AiPlayer``; injecting them behind the
# :mod:`.strategy` interfaces means future levels are new classes, not
# edits to ``AiPlayer``.

from contrai_core.auction import Auction
from contrai_core.bid import (
    Bid,
    PassBid,
    RedoubleBid,
    SlamLevel,
)
from contrai_core.types import CARD_SUITS, Rank, Suit

from .strategy import BiddingStrategy, CardPlayStrategy, _PlayerStrategy
from .wire import bid_to_wire, wire_to_bid

SUITS = CARD_SUITS


class RuleBasedBiddingStrategy(BiddingStrategy, _PlayerStrategy):
    """Expert bidding policy driven by the SF-09 bidding table.

    Bidding strategy:
    1. Evaluate hand according to bidding table (80-160 points + Slam / Solo Slam)
    2. If partner hasn't bid or bid lower, make initial bid if it's hand is strong enough
    3. If partner has bid, support with incremental bidding (+10 per external ace, +10 for trump complement)
    4. If multiple bid are possible : choose best suit based on strength, belote
    """

    # Internal numeric values used in BIDDING_TABLE for the all-tricks
    # bids. Sourced from the single source of truth on the core
    # :class:`SlamLevel` enum so the AI's ladder arithmetic and the
    # domain scoring never drift apart.
    SLAM_NUMERIC = SlamLevel.SLAM.base_value
    SOLO_SLAM_NUMERIC = SlamLevel.SOLO_SLAM.base_value

    # Bidding table. The ``contract`` column is stored numerically and
    # matches each contract's *base value* (what the bidder commits to,
    # used for auction precedence). The two all-tricks bids live at the
    # bottom of the table:
    #   - ``SLAM_NUMERIC``      (250) — team must win all 8 tricks.
    #   - ``SOLO_SLAM_NUMERIC`` (500) — bidder personally must win all 8.
    # Both rows are gated purely by the trick estimator (``tricks_min=8``)
    # in this first pass. The numeric values match
    # ``ContractBid.get_numeric_value`` / ``Contract.get_base_points`` in
    # ``contrai-core``; they're translated back to the ``SlamLevel``
    # members at the bid-return boundary (see ``_make_initial_bid`` /
    # ``_support_partner_bid``).
    BIDDING_TABLE = [
        # (contract, trump_expected, trump_min, aces, tricks_min, belote_required)
        (80, {'jack_or_nine': True, 'jack_and_nine': False}, 3, 1, 4, False),
        (90, {'jack_or_nine': False, 'jack_and_nine': True}, 3, 1, 4, False),
        (100, {'jack_or_nine': True, 'jack_and_nine': False}, 3, 2, 5, False),
        (110, {'jack_or_nine': False, 'jack_and_nine': True}, 3, 2, 5, False),
        (120, {'jack_or_nine': True, 'jack_and_nine': False}, 3, 3, 6, False),
        (130, {'jack_or_nine': False, 'jack_and_nine': True}, 3, 3, 6, False),
        (140, {'jack_or_nine': True, 'jack_and_nine': False}, 4, 3, 6, True),
        (150, {'jack_or_nine': False, 'jack_and_nine': True}, 4, 3, 6, True),
        (160, {'jack_or_nine': False, 'jack_and_nine': True, 'ace_required': True}, 5, 3, 7, True),
        (SLAM_NUMERIC, {}, 0, 0, 8, False),  # Slam — only the trick estimator gates it.
        # TODO: tune SoloSlam gate — currently shares Slam's gate. A
        # stricter rule (e.g. holds the 8 top trumps in trump-led play,
        # or all aces + trump master) would make this conservative.
        (SOLO_SLAM_NUMERIC, {}, 0, 0, 8, False),  # Solo Slam — same gate as Slam for now.
    ]

    # Suit preference order (Spades, Hearts, Diamonds, Clubs)
    SUIT_PREFERENCE = SUITS

    def choose_bid(self, auction: Auction) -> Bid:
        """Choose a :class:`Bid` for the current auction state.

        The expert bidding table still operates on the legacy wire
        format internally; this method adapts the :class:`Auction`
        boundary into wire-format inputs, delegates to
        :meth:`_choose_wire`, and lifts the result back to a
        :class:`Bid` for the engine to apply. The engine is
        responsible for validating legality — see
        :meth:`Auction.apply`.

        Args:
            auction: The current :class:`Auction` state.

        Returns:
            A :class:`Bid` instance the engine will validate.
        """

        # A standing Coinche (Double) freezes the auction: no further
        # numeric contract bids are legal — only Pass, or a Surcoinche
        # (Redouble) from the team that owns the contract (see
        # ``Auction._is_contract_value_legal`` / ``contree-domain.md
        # §5.3``). The expert bidding table below has no model of this
        # freeze and would happily try to raise — including raising its
        # *own* partner's contract — producing an illegal ContractBid.
        # Resolve the frozen states here before delegating.
        if auction.has_redouble:
            # Already surcoinched; nothing legal remains but to pass.
            return PassBid(self._player)
        if auction.has_double:
            return self._choose_under_double(auction)

        current_bids = [(b.player, bid_to_wire(b)) for b in auction.bids]
        wire_choice = self._choose_wire(current_bids)
        bid = wire_to_bid(self._player, wire_choice)

        # Safety net honouring the Auction design contract: callers must
        # only propose legal bids, there is no silent force-a-Pass in
        # ``Auction.apply`` (it raises ``IllegalBidError``). If the
        # expert table still produced an illegal bid in some unmodeled
        # edge case, fall back to the always-legal Pass rather than
        # crash the whole game mid-auction.
        if not auction.is_legal(bid):
            return PassBid(self._player)
        return bid

    def _choose_under_double(self, auction: Auction) -> Bid:
        """Pick a bid when a Coinche (Double) has frozen the auction.

        With a Double standing, the only legal actions are :class:`PassBid`
        and — for the side that owns the contract — a :class:`RedoubleBid`
        (Surcoinche). Numeric raises are illegal, so the expert bidding
        table must not run. We offer a Surcoinche only when we are on the
        contracting team and :meth:`_should_redouble` approves; otherwise
        we pass.

        Args:
            auction: The current (doubled) :class:`Auction` state.

        Returns:
            A :class:`RedoubleBid` when surcoinching is both legal and
            strategically chosen, else a :class:`PassBid`.
        """

        contract_bid = auction.last_contract_bid
        if contract_bid is not None and contract_bid.player.team is self.team:
            redouble = RedoubleBid(self._player)
            if auction.is_legal(redouble) and self._should_redouble():
                return redouble
        return PassBid(self._player)

    def _choose_wire(self, current_bids):
        """Strategy core: pick a wire-format bid for ``current_bids``.

        Args:
            current_bids: List of ``(player, wire_bid)`` tuples from
                the current bidding round in chronological order.

        Returns:
            ``'Pass'``, ``'Double'``, ``'Redouble'``, or
            ``(value, suit)``.
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
            return self._support_partner_bid(partner_bid, last_bid)

        return 'Pass'

    @classmethod
    def _bid_value_numeric(cls, value):
        """Coerce a contract value (numeric or :class:`SlamLevel`) to int.

        The wire format on ``current_bids`` carries the all-tricks bids
        as :class:`SlamLevel` members (see the wire-format bridge in
        :mod:`contrai_engine.model.player`), so the AI's ladder
        arithmetic must normalise them to their auction-precedence /
        base-point numeric: ``SlamLevel.SLAM`` → 250,
        ``SlamLevel.SOLO_SLAM`` → 500.
        """

        if isinstance(value, SlamLevel):
            return value.base_value
        return value

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
        value = self._bid_value_numeric(value)

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

        trump_cards = self.hand.cards_of_suit(suit)

        if not trump_cards:
            return {'contract': 0, 'strength': 0, 'has_belote': False}

        # Count trump strength
        has_jack = any(card.rank == Rank.JACK for card in trump_cards)
        has_nine = any(card.rank == Rank.NINE for card in trump_cards)
        has_ace = any(card.rank == Rank.ACE for card in trump_cards)
        has_king = any(card.rank == Rank.KING for card in trump_cards)
        has_queen = any(card.rank == Rank.QUEEN for card in trump_cards)

        trump_count = len(trump_cards)

        # Check for belote (King + Queen of trump)
        has_belote = has_king and has_queen

        # Count external aces
        external_aces = sum(1 for card in self.hand
                          if card.suit != suit and card.rank == Rank.ACE)

        # Estimate trick-taking potential
        estimated_tricks = self._estimate_tricks(suit)

        # Find the highest contract we can bid
        max_contract = 0

        for contract, trump_req, trump_min, aces_req, tricks_req, belote_req in self.BIDDING_TABLE:
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
                if card.rank == Rank.ACE:
                    tricks += 1
                if card.rank == Rank.TEN and self.hand.count_suit(card.suit) > 1:
                    tricks += 1
                if (card.rank == Rank.KING or card.rank == Rank.QUEEN) and self.hand.has_card(card.suit, Rank.ACE)\
                        and self.hand.has_card(card.suit, Rank.TEN):
                    tricks += 1

        return min(tricks, 8)  # Maximum 8 tricks in a round

    @classmethod
    def _can_overbid_partner(cls, partner_bid, suit_evaluations):
        """Check if we can make a higher bid than our partner."""

        partner_value, partner_suit = partner_bid
        partner_value = cls._bid_value_numeric(partner_value)

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
            last_value = self._bid_value_numeric(last_value)
            if max_contract <= last_value:
                return 'Pass'

        # Choose best suit among candidates
        chosen_suit = self._choose_best_suit(best_suits, suit_evaluations)

        # Translate the internal numeric sentinels back to the wire format.
        bid_value = self._numeric_to_wire(max_contract)
        return bid_value, chosen_suit

    def _support_partner_bid(self, partner_bid, last_bid):
        """Support partner's bid with incremental bidding."""

        _, partner_suit = partner_bid

        # Calculate our contribution to partner's suit
        contribution = 0

        # +10 per external ace
        for card in self.hand:
            if card.suit != partner_suit and card.rank == Rank.ACE:
                contribution += 10

        # +10 if we have trump complement (Jack or 9)
        trump_cards = self.hand.cards_of_suit(partner_suit)
        has_jack = any(card.rank == Rank.JACK for card in trump_cards)
        has_nine = any(card.rank == Rank.NINE for card in trump_cards)

        if has_jack or has_nine:
            contribution += 10

        # Calculate new bid value
        last_value, _ = last_bid
        last_value = self._bid_value_numeric(last_value)
        new_value = last_value + contribution

        # Cap at SoloSlam (the top of the table); don't try to raise past it.
        if new_value > self.SOLO_SLAM_NUMERIC or contribution == 0:
            return 'Pass'

        bid_value = self._numeric_to_wire(new_value)
        return bid_value, partner_suit

    @classmethod
    def _numeric_to_wire(cls, value):
        """Translate the bidding-table numeric back to the wire value.

        Numeric contracts (80–160) round-trip unchanged. The two
        all-tricks numerics become their :class:`SlamLevel` members:
        ``SLAM_NUMERIC`` → ``SlamLevel.SLAM``, ``SOLO_SLAM_NUMERIC`` →
        ``SlamLevel.SOLO_SLAM`` — so the wire ``(value, suit)`` tuple
        carries the same value a :class:`ContractBid` will hold.
        """

        if value == cls.SOLO_SLAM_NUMERIC:
            return SlamLevel.SOLO_SLAM
        if value == cls.SLAM_NUMERIC:
            return SlamLevel.SLAM
        return value

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

        # If still tied, use preference order: Spades, Hearts, Diamonds, Clubs
        for preferred_suit in self.SUIT_PREFERENCE:
            return preferred_suit

        return strongest_suits[0]  # Fallback

    def _evaluate_trump_tricks(self, suit):
        """Evaluate potential tricks won with trump suit."""

        trump_cards = self.hand.cards_of_suit(suit)
        expected_won_tricks = 0

        has_jack = False
        has_nine = False
        has_ace = False

        if len(trump_cards) > 0:
            has_jack = any(card.rank == Rank.JACK for card in trump_cards)
            has_nine = any(card.rank == Rank.NINE for card in trump_cards)
            has_ace = any(card.rank == Rank.ACE for card in trump_cards)

            if has_jack and has_nine:
                expected_won_tricks = 2  # Both Jack and 9
            elif has_jack:
                expected_won_tricks = 1 # Only Jack
            elif has_nine and len(trump_cards) > 1:
                expected_won_tricks = 1 # Only 9 but with support

            if len(trump_cards) >= 3:
                expected_won_tricks += len(trump_cards) - 3 + has_ace

        return expected_won_tricks


class RuleBasedCardPlayStrategy(CardPlayStrategy, _PlayerStrategy):
    """Expert card-play policy (SF-10).

    Owns the per-round card-tracking state (``_fallen_cards`` /
    ``_players_without_trump``), initialised at construction so the lazy
    ``hasattr`` guards below are harmless no-ops, and decides which card
    to play based on the trick state, the contract, and what has fallen.
    """

    def __init__(self, player):
        """Bind to the player and initialise card tracking.

        Args:
            player: The owning :class:`AiPlayer`.
        """

        super().__init__(player)
        self.initialize_card_tracking()

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
