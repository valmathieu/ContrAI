"""Expert rule-based bidding strategy (see the package docstring)."""

from contrai_core.auction import Auction
from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
    SlamLevel,
)
from contrai_core.types import CARD_SUITS, Rank, Suit

from ..strategy import BiddingStrategy, PlayerStateMixin

SUITS = CARD_SUITS


class RuleBasedBiddingStrategy(BiddingStrategy, PlayerStateMixin):
    """Expert bidding policy driven by a bidding table.

    Bidding strategy:
    1. Evaluate hand according to bidding table (80-160 points + Slam / Solo Slam)
    2. If partner hasn't bid or bid lower, make initial bid if it's hand is strong enough
    3. If partner has bid, support once with our complement (+10 per external ace,
       +10 for trump complement), capped at partner's opening bid + that complement —
       the team ceiling that stops partners from alternately re-raising each other
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

        The expert bidding table reads the :class:`Auction` history
        directly: :meth:`_choose_open_bid` walks ``auction.bids`` and
        returns a concrete :class:`Bid` (``PassBid`` /
        ``ContractBid`` / ``DoubleBid``). The engine is responsible for
        validating legality — see :meth:`Auction.apply`.

        Args:
            auction: The current :class:`Auction` state.

        Returns:
            A :class:`Bid` instance the engine will validate.
        """

        # A standing Double freezes the auction: no further
        # numeric contract bids are legal — only Pass, or a Redouble
        #  from the team that owns the contract (see
        #  ``Auction._is_contract_value_legal``). The expert bidding
        # table below has no model of this freeze and would happily try
        # to raise — including raising its *own* partner's contract —
        # producing an illegal ContractBid. Resolve the frozen states
        # here before delegating.
        if auction.has_redouble:
            # Already redoubled; nothing legal remains but to pass.
            return PassBid(self._player)
        if auction.has_double:
            return self._choose_under_double(auction)

        bid = self._choose_open_bid(auction)

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
        """Pick a bid when a Double has frozen the auction.

        With a Double standing, the only legal actions are :class:`PassBid`
        and — for the side that owns the contract — a :class:`RedoubleBid`
        (Redouble). Numeric raises are illegal, so the expert bidding
        table must not run. We offer a Redouble only when we are on the
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

    def _choose_open_bid(self, auction: Auction) -> Bid:
        """Strategy core: pick a :class:`Bid` for an open (unfrozen) auction.

        Reads the chronological :class:`Bid` history off ``auction``
        directly — no wire-format projection. Called by
        :meth:`choose_bid` only once the Double/Redouble freeze states
        have been ruled out, so the full expert table (raise, support,
        Coinche) is in play here.

        Args:
            auction: The current :class:`Auction` state.

        Returns:
            A :class:`PassBid`, :class:`ContractBid`, or
            :class:`DoubleBid`. Legality is re-checked by the caller.
        """

        bids = auction.bids

        # Get current game state
        last_bid = auction.last_contract_bid
        partner_bid = self._get_partner_bid(bids)

        # Check if we can Double the opponents' standing contract
        double_action = self._check_double(last_bid)
        if double_action is not None:
            return double_action

        # Evaluate our hand once and resolve it to the single best
        # (contract, suit) pair — ties already broken (belote first,
        # then the fixed preference order).
        best_contract, best_suit = self._find_best_contract(self._evaluate_suits())

        # Determine bidding strategy
        if partner_bid is None or (
            isinstance(partner_bid, ContractBid)
            and best_contract > partner_bid.get_numeric_value()
        ):
            # Make initial bid or overbid partner
            return self._make_initial_bid(best_contract, best_suit, last_bid)
        if isinstance(partner_bid, ContractBid):
            # Support partner's bid
            return self._support_partner_bid(partner_bid, last_bid, bids)

        return PassBid(self._player)

    def _get_partner_bid(self, bids):
        """Return our side's most recent non-pass :class:`Bid`, or ``None``.

        Matches any bid made by a player on our team (including our own
        earlier bid this round); only :class:`PassBid` is skipped.
        """

        for bid in reversed(bids):
            if not isinstance(bid, PassBid) and bid.player.team is self.team:
                return bid
        return None

    def _check_double(self, last_bid):
        """Return a :class:`DoubleBid` if we should Double, else ``None``.

        Only the Double decision lives here — the Redouble
        is a defence of our *own* contract and is handled on
        the frozen-auction path in :meth:`_choose_under_double`.

        Args:
            last_bid: The standing :class:`ContractBid`, or ``None``.
        """

        if last_bid is None:
            return None

        # Double only if the standing contract belongs to the opponents
        # and we hold enough external strength to threaten it.
        if last_bid.player.team is not self.team and self._should_double(last_bid):
            return DoubleBid(self._player)

        return None

    def _should_double(self, opponent_bid):
        """Determine if we should double opponent's bid.

        Args:
            opponent_bid: The opposing :class:`ContractBid` in play.
        """

        value = opponent_bid.get_numeric_value()
        suit = opponent_bid.suit

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

        # Held trump ranks — queried against the table's honour
        # requirements (Jack / 9 / Ace) by the table scan.
        trump_ranks = {card.rank for card in trump_cards}

        # Check for belote (King + Queen of trump)
        has_belote = Rank.KING in trump_ranks and Rank.QUEEN in trump_ranks

        # Count external aces
        external_aces = sum(1 for card in self.hand
                          if card.suit != suit and card.rank == Rank.ACE)

        # Estimate trick-taking potential
        estimated_tricks = self._estimate_tricks(suit)

        # Find the highest contract we can bid
        max_contract = self._max_table_contract(
            trump_ranks, len(trump_cards), external_aces, estimated_tricks, has_belote
        )

        return {
            'contract': max_contract,
            'has_belote': has_belote,
            'trump_count': len(trump_cards),
            'external_aces': external_aces,
            'estimated_tricks': estimated_tricks
        }

    def _max_table_contract(
        self, trump_ranks, trump_count, external_aces, estimated_tricks, has_belote
    ):
        """Return the highest ``BIDDING_TABLE`` contract the hand satisfies.

        Scans the table top to bottom and keeps the last row whose gates
        all pass, so the returned contract is the strongest one reachable.

        Args:
            trump_ranks: Ranks held in the candidate trump suit.
            trump_count: Number of cards held in that suit.
            external_aces: Aces held outside that suit.
            estimated_tricks: Trick estimate from :meth:`_estimate_tricks`.
            has_belote: Whether the suit carries King + Queen.

        Returns:
            The numeric contract value, or 0 when no row matches.
        """

        max_contract = 0

        for contract, trump_req, trump_min, aces_req, tricks_req, belote_req in self.BIDDING_TABLE:
            # Check trump requirements
            trump_ok = trump_count >= trump_min

            if trump_req.get('jack_and_nine', False):
                trump_ok = trump_ok and Rank.JACK in trump_ranks and Rank.NINE in trump_ranks
            elif trump_req.get('jack_or_nine', False):
                trump_ok = trump_ok and (Rank.JACK in trump_ranks or Rank.NINE in trump_ranks)

            if trump_req.get('ace_required', False):
                trump_ok = trump_ok and Rank.ACE in trump_ranks

            # Check other requirements
            if (trump_ok and
                external_aces >= aces_req and
                estimated_tricks >= tricks_req and
                (not belote_req or has_belote)):
                max_contract = contract

        return max_contract

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
                if (
                    card.rank in (Rank.KING, Rank.QUEEN)
                    and self.hand.has_card(card.suit, Rank.ACE)
                    and self.hand.has_card(card.suit, Rank.TEN)
                ):
                    tricks += 1

        return min(tricks, 8)  # Maximum 8 tricks in a round

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

    def _find_best_contract(self, suit_evaluations: dict) -> tuple[int, Suit | None]:
        """Resolve the suit evaluations to the single best (contract, suit).

        Folds the two questions the open-bid path used to answer
        separately — "what is the highest contract I can reach?" and
        "in which suit?" — into one pass. Ties on the contract value
        are broken here as well: suits carrying a Belote (King + Queen
        of trump) win first, then the fixed preference order
        (Spades, Hearts, Diamonds, Clubs) decides among the rest.

        Args:
            suit_evaluations: Per-suit evaluation dicts from
                :meth:`_evaluate_suits`.

        Returns:
            The highest reachable bidding-table contract and the single
            suit chosen for it, or ``(0, None)`` when no suit supports
            any contract.
        """

        max_contract = max(
            evaluation['contract'] for evaluation in suit_evaluations.values()
        )
        if max_contract == 0:
            return 0, None

        # Suits tied on the best contract value.
        candidates = [
            suit for suit, evaluation in suit_evaluations.items()
            if evaluation['contract'] == max_contract
        ]

        # Prefer belote-carrying suits; narrow the field when any exist.
        belote_suits = [
            suit for suit in candidates if suit_evaluations[suit]['has_belote']
        ]
        if belote_suits:
            candidates = belote_suits

        # Break the remaining tie with the fixed preference order. The
        # order covers every suit, so the first hit always exists.
        chosen_suit = next(
            suit for suit in self.SUIT_PREFERENCE if suit in candidates
        )
        return max_contract, chosen_suit

    def _make_initial_bid(self, best_contract, best_suit, last_bid):
        """Make an initial bid or overbid.

        Args:
            best_contract: Our highest reachable bidding-table contract
                (from :meth:`_find_best_contract`).
            best_suit: The single suit resolved for it, or ``None``.
            last_bid: The standing :class:`ContractBid`, or ``None``.

        Returns:
            A :class:`ContractBid` for the chosen suit, or a
            :class:`PassBid` when nothing legal improves the auction.
        """

        if best_contract == 0 or best_suit is None:
            return PassBid(self._player)

        # Check if we can overbid the last bid
        if last_bid is not None and best_contract <= last_bid.get_numeric_value():
            return PassBid(self._player)

        return self._contract_bid(best_contract, best_suit)

    def _team_opening_bid(self, bids, suit):
        """Return our team's first :class:`ContractBid` in ``suit``, or ``None``.

        That opening bid anchors the support ceiling: the expert table
        always opens at its full evaluation of the suit (there is no
        slow walk-up), so everything our side may legitimately add on
        top of it is the *other* seat's complement — announced once.

        Args:
            bids: Chronological bid history.
            suit: The trump suit whose team opening we want.
        """

        for bid in bids:
            if (
                isinstance(bid, ContractBid)
                and bid.suit == suit
                and bid.player.team is self.team
            ):
                return bid
        return None

    def _support_partner_bid(self, partner_bid, last_bid, bids):
        """Support partner's suit up to a fixed team ceiling.

        The ceiling is partner's *opening* bid in the suit (their full
        table evaluation) plus our own contribution (+10 per external
        ace, +10 for the trump complement). Anchoring the raise on the
        opening bid — never on the standing contract — is what breaks
        the partner-support loop: a hand is static during the auction,
        so re-adding the same contribution on top of a value that
        already contains it would count the same cards on every lap and
        ratchet the contract far past what the two hands can make.

        Two Pass conditions fall out of the same invariant: we opened
        the suit ourselves (our cards are already priced into the
        anchor, so there is nothing of ours left to announce), or the
        standing contract already reaches the ceiling (our complement
        is spent, whether by our earlier raise or an opponent overbid).

        Args:
            partner_bid: Our side's most recent standing :class:`ContractBid`.
            last_bid: The standing :class:`ContractBid`, or ``None``.
            bids: Chronological bid history, to locate the anchor.

        Returns:
            A :class:`ContractBid` raising partner's suit to the team
            ceiling, or a :class:`PassBid` when we add nothing, opened
            the suit ourselves, or the ceiling is already reached.
        """

        partner_suit = partner_bid.suit

        # Anchor on our team's opening bid of the suit. If *we* opened
        # it, `partner_bid` is our own bid echoed back by
        # `_get_partner_bid` — supporting it would double-count the
        # very cards that priced it.
        anchor = self._team_opening_bid(bids, partner_suit)
        if anchor is None or anchor.player is self._player:
            return PassBid(self._player)

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

        if contribution == 0:
            return PassBid(self._player)

        # The team ceiling: partner's evaluation + our complement. Once
        # the standing contract reaches it, our support is spent.
        ceiling = anchor.get_numeric_value() + contribution
        if ceiling <= last_bid.get_numeric_value():
            return PassBid(self._player)

        # An off-ladder ceiling (e.g. overshooting a partner's Slam)
        # falls back to Pass inside _contract_bid.
        return self._contract_bid(ceiling, partner_suit)

    def _contract_bid(self, numeric, suit) -> Bid:
        """Build a :class:`ContractBid` from a bidding-table numeric + suit.

        The expert table stores the all-tricks bids as their base-value
        numerics (``SLAM_NUMERIC`` = 250, ``SOLO_SLAM_NUMERIC`` = 500);
        those map to the corresponding :class:`SlamLevel` members here,
        so the constructed :class:`ContractBid` carries a value the
        domain accepts. Numeric steps (80–180) pass through unchanged.

        A numeric that lands off the contract ladder — e.g. the support
        ceiling overshooting a partner's Slam (250 + 40 = 290) —
        is not a constructible contract; we fall back to a
        :class:`PassBid` rather than raise. (``choose_bid``'s
        ``is_legal`` net then leaves that Pass untouched.)
        """

        if numeric == self.SOLO_SLAM_NUMERIC:
            value: int | SlamLevel = SlamLevel.SOLO_SLAM
        elif numeric == self.SLAM_NUMERIC:
            value = SlamLevel.SLAM
        elif numeric in ContractBid.VALID_VALUES:
            value = numeric
        else:
            return PassBid(self._player)
        return ContractBid(self._player, value, suit)
