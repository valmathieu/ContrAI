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
from contrai_core.types import CARD_SUITS, Rank

from .strategy import BiddingStrategy, _PlayerStrategy
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
