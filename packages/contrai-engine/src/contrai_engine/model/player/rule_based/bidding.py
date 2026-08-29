"""Expert rule-based bidding strategy (see the package docstring)."""

from contrai_core.auction import Auction
from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
    SlamLevel,
    bookable_suits,
    ladder_top,
)
from contrai_core.rule_config import AllTrumpBelote, RuleConfig
from contrai_core.rules import TrumpRules, rules_for
from contrai_core.types import CONTRACT_SUITS, ContractSuit, Rank, Suit

from ..rationale import BidDecision, Rationale, RuleCitation
from ..strategy import BiddingStrategy, PlayerStateMixin

#: The card suits every per-suit sweep walks. Every Suit member is a real
#: card suit, so no filtering is needed.
SUITS = tuple(Suit)

#: Points a single honour is worth on the honours table, over a base of 60.
#: The house convention names 2 / 3 / 4 masters as 80 / 90 / 100 and then
#: climbs by complements, which is one +10 step per honour throughout.
HONOUR_STEP = 10

#: What the honours table counts up from. ``60 + 10 x honours`` reproduces
#: all three of the convention's stated anchors exactly.
HONOURS_BASE = 60

#: Marked points a Belote the table will actually score is worth to an
#: all-trump evaluation (contree-domain.md §6.6).
BELOTE_POINTS = 20


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
    # --- The suit table -------------------------------------------------
    # Columns: (contract, trump_expected, trump_min, aces, tricks_min,
    # belote_required). ``aces`` counts aces held *outside* the candidate
    # trump suit; ``trump_expected`` names ranks on the trump ladder.
    SUIT_TABLE = [
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

    #: Backwards-readable alias: the suit table is what ``BIDDING_TABLE``
    #: always was, now one of two.
    BIDDING_TABLE = SUIT_TABLE

    # --- The honours table ----------------------------------------------
    # One table serves **both** suitless modes, because the difference
    # between them is carried entirely by the ladder ``rules_for(mode)``
    # hands back: a master is the Ace at no trump and the Jack at all
    # trump, a complement the 10 under its own Ace and the 9 under its own
    # Jack. Read at all trump, row 1 is "two Jacks", row 3 "four Jacks",
    # and every 9 sitting under its own Jack adds a rung. Read at no
    # trump, the same rows with Ace for Jack and 10 for 9.
    #
    # Columns: (contract, honours, tricks_min). ``honours`` is
    # masters + complements — note this is a *different* question from the
    # suit table's ``aces`` column, which counts aces held outside the
    # trump suit. ``tricks_min`` is the same sanity floor the suit table
    # uses, fed by the regime-correct ``_estimate_tricks(mode)``.
    #
    # ``tricks_min`` tracks the honour count exactly, and that is a
    # deliberate, measured choice rather than an oversight. Every honour
    # is a *certain* trick — a master is unbeatable in its suit, and a
    # complement's only superior sits in the same hand — so
    # ``_estimate_tricks`` provably returns at least ``honours``, both
    # helpers reading position off the same ladder. Floors set any higher
    # gated the convention's own anchors shut: two Jacks and junk is 2
    # certain tricks and cannot be filled to 4 without adding honours,
    # which would then be a different row. The floor therefore earns its
    # place as an *agreement invariant* — the day ``_honours`` and
    # ``_top_card_tricks`` stop answering the same question about what
    # tops a ladder, these rows fall out and the tests say so.
    #
    # The seven rows are starting values to be tuned; the honours are the
    # intended driver.
    HONOURS_TABLE = [
        # (contract, honours, tricks_min)
        (80, 2, 2),
        (90, 3, 3),
        (100, 4, 4),
        (110, 5, 5),
        (120, 6, 6),
        (130, 7, 7),
        (140, 8, 8),
    ]

    #: A hand cannot open on complements alone. Structurally
    #: ``complements <= masters``, so this one floor below every row is
    #: what keeps a lone Jack + 9 — two honours, one master — off the 80
    #: rung the convention reserves for two Jacks.
    MASTERS_FLOOR = 2

    #: Mode preference order, breaking a tie on value and belote. Reuses
    #: the core enumeration (card suits, then NO_TRUMP, then ALL_TRUMP)
    #: rather than restating an order of its own.
    MODE_PREFERENCE = CONTRACT_SUITS

    #: The old name, kept for the suit-only sweeps that still read it.
    SUIT_PREFERENCE = SUITS

    def _decide(
        self,
        bid: Bid,
        rule: str,
        detail: str,
        *,
        considered: tuple[str, ...] = (),
        citations: tuple[RuleCitation, ...] = (),
    ) -> BidDecision:
        """Pair a chosen bid with the rule that produced it.

        Every ``return`` in the bidding ladder goes through here, so the
        trace is written where the decision is taken rather than
        reconstructed afterwards by a reader of the code.

        Args:
            bid: The bid the table settled on.
            rule: The rule that fired, named as this class's docstrings
                name it.
            detail: One sentence on what that meant for this hand.
            considered: The alternatives weighed, already rendered.
            citations: The table knobs this branch consulted.

        Returns:
            The :class:`BidDecision` to hand back to the engine.
        """

        return BidDecision(
            bid, Rationale(rule, detail, considered, citations)
        )

    def choose_bid(self, auction: Auction) -> BidDecision:
        """Choose a :class:`Bid` for the current auction state.

        The expert bidding table reads the :class:`Auction` history
        directly: :meth:`_choose_open_bid` walks ``auction.bids`` and
        returns a concrete :class:`Bid` (``PassBid`` /
        ``ContractBid`` / ``DoubleBid``). The engine is responsible for
        validating legality — see :meth:`Auction.apply`.

        Args:
            auction: The current :class:`Auction` state.

        Returns:
            A :class:`BidDecision` — the bid the engine will validate,
            and the :class:`~..rationale.Rationale` naming the rule that
            chose it.
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
            return self._decide(
                PassBid(self._player),
                "the auction is frozen",
                "a Redouble stands — nothing but a Pass is legal.",
            )
        if auction.has_double:
            return self._choose_under_double(auction)

        decision = self._choose_open_bid(auction)

        # Safety net honouring the Auction design contract: callers must
        # only propose legal bids, there is no silent force-a-Pass in
        # ``Auction.apply`` (it raises ``IllegalBidError``). If the
        # expert table still produced an illegal bid in some unmodeled
        # edge case, fall back to the always-legal Pass rather than
        # crash the whole game mid-auction.
        if not auction.is_legal(decision.bid):
            return self._decide(
                PassBid(self._player),
                "withdraw an illegal bid",
                f"the table proposed {decision.bid} but the auction "
                f"refuses it — passed instead.",
                considered=(str(decision.bid),),
            )
        return decision

    def _choose_under_double(self, auction: Auction) -> BidDecision:
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
                return self._decide(
                    redouble,
                    "redouble our own contract",
                    "our contract was doubled and the hand backs it — "
                    "redoubled.",
                )
        return self._decide(
            PassBid(self._player),
            "the auction is frozen",
            "a Double stands: only a Pass, or a Redouble from the "
            "contracting side, is legal.",
        )

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
            A :class:`BidDecision` wrapping a :class:`PassBid`,
            :class:`ContractBid`, or :class:`DoubleBid`. Legality is
            re-checked by the caller.
        """

        bids = auction.bids

        # Get current game state
        last_bid = auction.last_contract_bid
        partner_bid = self._get_partner_bid(bids)

        # Check if we can Double the opponents' standing contract
        double_action = self._check_double(last_bid)
        if double_action is not None:
            return double_action

        # Evaluate our hand once, across every trump the *table* offers,
        # and resolve it to the single best (contract, mode) pair — ties
        # already broken (belote first, then the fixed mode order).
        evaluations = self._evaluate_modes(auction.rules)
        best_contract, best_suit = self._find_best_contract(evaluations)

        # Determine bidding strategy
        if partner_bid is None or (
            isinstance(partner_bid, ContractBid)
            and best_contract > partner_bid.get_numeric_value()
        ):
            # Make initial bid or overbid partner
            return self._make_initial_bid(
                best_contract, best_suit, last_bid, auction.rules, evaluations
            )
        if isinstance(partner_bid, ContractBid):
            # Support partner's bid
            return self._support_partner_bid(
                partner_bid, last_bid, bids, auction.rules
            )

        return self._decide(
            PassBid(self._player),
            "nothing to add",
            "our side already spoke and the hand adds no contract of its "
            "own — passed.",
        )

    def _get_partner_bid(self, bids):
        """Return our side's most recent non-pass :class:`Bid`, or ``None``.

        Matches any bid made by a player on our team (including our own
        earlier bid this round); only :class:`PassBid` is skipped.
        """

        for bid in reversed(bids):
            if not isinstance(bid, PassBid) and bid.player.team is self.team:
                return bid
        return None

    def _check_double(self, last_bid) -> BidDecision | None:
        """Return a Double :class:`BidDecision` if we should Double.

        Only the Double decision lives here — the Redouble
        is a defence of our *own* contract and is handled on
        the frozen-auction path in :meth:`_choose_under_double`.

        Args:
            last_bid: The standing :class:`ContractBid`, or ``None``.

        Returns:
            The Double decision, or ``None`` when the standing contract
            is ours or the hand does not threaten it.
        """

        if last_bid is None:
            return None

        # Double only if the standing contract belongs to the opponents
        # and we hold enough external strength to threaten it.
        if last_bid.player.team is not self.team and self._should_double(last_bid):
            return self._decide(
                DoubleBid(self._player),
                "double the opponents",
                f"we expect to hold {last_bid.suit} "
                f"{last_bid.get_numeric_value()} under its contract — "
                f"doubled.",
            )

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
        """Evaluate each card suit as a potential trump.

        The suit-only sweep, kept because several helpers and tests read
        exactly the four suits. :meth:`_evaluate_modes` is the general
        form the auction path uses.

        Returns:
            One evaluation dict per :class:`~contrai_core.Suit`.
        """

        return {
            suit: self._evaluate_mode(suit, RuleConfig()) for suit in SUITS
        }

    def _evaluate_modes(self, rules: RuleConfig) -> dict:
        """Evaluate every trump choice the table actually offers.

        Args:
            rules: The table ruleset. ``extended_trump_choices`` decides
                whether no trump and all trump are on the table at all —
                asked through :func:`~contrai_core.bookable_suits` rather
                than re-derived, so the AI can never evaluate a mode the
                auction would refuse.

        Returns:
            One evaluation dict per bookable trump choice.
        """

        return {
            mode: self._evaluate_mode(mode, rules)
            for mode in bookable_suits(rules)
        }

    def _evaluate_mode(self, mode: ContractSuit, rules: RuleConfig) -> dict:
        """Evaluate one trump choice for this hand.

        Dispatches on the *shape* of the mode, not on a chain of
        ``if mode is NO_TRUMP``: a card suit is priced by the suit table
        (trump length, external aces, the trump ladder's own honours),
        while the two suitless modes share the honours table, since what
        separates them is entirely carried by the ladder
        ``rules_for(mode)`` returns.

        Args:
            mode: The trump choice to price.
            rules: The table ruleset, supplying the belote regime and the
                ladder cap.

        Returns:
            An evaluation dict with at least ``contract`` (the highest
            reachable value, 0 for none) and ``has_belote``.
        """

        if isinstance(mode, Suit):
            return self._evaluate_suit_as_trump(mode)
        return self._evaluate_suitless_mode(mode, rules)

    def _evaluate_suit_as_trump(self, suit):
        """Evaluate a specific card suit as potential trump."""

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

    def _evaluate_suitless_mode(
        self, mode: ContractSuit, rules: RuleConfig
    ) -> dict:
        """Price no trump or all trump off the shared honours table.

        Three things stack, in order:

        1. **The honours ladder.** ``60 + 10 x (masters + complements)``,
           expressed as :attr:`HONOURS_TABLE` so the trick floors sit
           beside the values they gate. Below every row is the
           :attr:`MASTERS_FLOOR`: a hand cannot open on complements alone.
        2. **The belote add-on.** Each K + Q pair the table will actually
           mark is +20 (§6.6). One per pair under ``four``, at most one
           under ``single``, none under ``none``. At no trump this is
           structurally zero — ``NoTrumpRules.belote_suits`` is empty —
           so the knob is inert there whatever it says. This is the only
           thing that lifts an all-trump bid past the honours ladder's own
           140 ceiling.
        3. **The ladder cap.** :func:`~contrai_core.ladder_top` already
           encodes the three all-trump ceilings (160 / 180 / 240) and no
           trump's 160. Never re-derive one.

        The ``single`` credit is deliberately optimistic: only the first
        pair *announced in play* marks, and an opponent may announce
        first, so crediting a full +20 for a pair held is a tunable
        over-estimate.

        Args:
            mode: ``NO_TRUMP`` or ``ALL_TRUMP``.
            rules: The table ruleset.

        Returns:
            The evaluation dict, shaped like the suit one.
        """

        mode_rules = rules_for(mode)
        masters, complements = self._honours(mode_rules)
        estimated_tricks = self._estimate_tricks(mode)
        belote_suits = self._markable_belote_suits(mode_rules, rules)

        contract = self._max_honours_contract(
            masters, complements, estimated_tricks
        )
        if contract:
            contract = min(
                contract + BELOTE_POINTS * len(belote_suits),
                ladder_top(mode, rules),
            )

        return {
            'contract': contract,
            'has_belote': bool(belote_suits),
            'masters': masters,
            'complements': complements,
            'belote_count': len(belote_suits),
            'estimated_tricks': estimated_tricks,
        }

    def _honours(self, rules: TrumpRules) -> tuple[int, int]:
        """Masters and complements the hand holds under ``rules``.

        A *master* is a card nothing outranks in its own suit — the Ace
        at no trump, the Jack at all trump. A *complement* is the card
        directly below one, held alongside the very master that would
        otherwise beat it: the 10 under its own Ace, the 9 under its own
        Jack. Both are certain tricks the moment the hand holds them,
        which is why the ladder prices them the same.

        Asking the ladder rather than naming ranks is what lets one
        counter serve both regimes — and it is the same question
        :meth:`_top_card_tricks` asks, so the bid and the trick estimate
        can never disagree about what a top card is.

        Args:
            rules: The regime's rules, supplying the in-suit ladders.

        Returns:
            A ``(masters, complements)`` pair.
        """

        masters = complements = 0
        for card in self.hand:
            higher = rules.higher_ranks(card.rank, card.suit)
            if not higher:
                masters += 1
            elif len(higher) == 1 and self.hand.has_card(card.suit, higher[0]):
                complements += 1
        return masters, complements

    def _markable_belote_suits(
        self, mode_rules: TrumpRules, rules: RuleConfig
    ) -> tuple[Suit, ...]:
        """The K + Q pairs this table would actually mark under ``mode``.

        The rules object answers where a belote *can* live — every suit at
        all trump, none at no trump — and the table's ``all_trump_belote``
        regime then says how many of the pairs held actually score
        (§6.6, §9.2).

        Args:
            mode_rules: The mode's own rules object.
            rules: The table ruleset, carrying the belote regime.

        Returns:
            The suits whose held pair would mark, at most one under
            ``single`` and none under ``none``.
        """

        held = tuple(
            suit
            for suit in mode_rules.belote_suits
            if self.hand.has_card(suit, Rank.KING)
            and self.hand.has_card(suit, Rank.QUEEN)
        )
        if len(mode_rules.belote_suits) <= 1:
            # A suit contract's single trump suit, or no trump's empty
            # tuple: the all-trump regime does not apply.
            return held
        if rules.all_trump_belote is AllTrumpBelote.NONE:
            return ()
        if rules.all_trump_belote is AllTrumpBelote.SINGLE:
            return held[:1]
        return held

    def _max_honours_contract(
        self, masters: int, complements: int, estimated_tricks: int
    ) -> int:
        """Return the highest :attr:`HONOURS_TABLE` row the hand satisfies.

        Args:
            masters: Cards nothing outranks in their own suit.
            complements: Cards whose only superior is in this same hand.
            estimated_tricks: Estimate from :meth:`_estimate_tricks`, run
                under the mode being priced.

        Returns:
            The numeric contract value, or 0 when no row matches — which
            includes every hand below the :attr:`MASTERS_FLOOR`.
        """

        if masters < self.MASTERS_FLOOR:
            return 0

        honours = masters + complements
        max_contract = 0
        for contract, honours_req, tricks_req in self.HONOURS_TABLE:
            if honours >= honours_req and estimated_tricks >= tricks_req:
                max_contract = contract
        return max_contract

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

    def _estimate_tricks(self, mode) -> int:
        """Estimate the tricks this hand takes under ``mode``.

        Resolves the regime's :class:`~contrai_core.rules.TrumpRules`
        once, then sums per suit: the ladder's own top cards
        (:meth:`_top_card_tricks`) everywhere, plus the length bonus in
        the suits the regime treats as trump — long trump wins tricks by
        exhaustion, which a plain suit cannot, since anyone may cut it.

        The sum collapses to today's arithmetic at a suit contract (one
        trump ladder plus three plain ones), to a pure plain sweep at no
        trump, and to a four-suit trump-ladder sweep at all trump — with
        no branch on the mode anywhere in it.

        Args:
            mode: The trump choice being evaluated — a card
                :class:`~contrai_core.Suit`, ``NO_TRUMP`` or
                ``ALL_TRUMP``.

        Returns:
            The estimate, capped at the 8 tricks a round holds.
        """

        rules = rules_for(mode)
        tricks = 0

        for suit in SUITS:
            held = self.hand.cards_of_suit(suit)
            if not held:
                continue

            tricks += self._top_card_tricks(rules, suit)

            if rules.is_trump(suit):
                # Trump length wins tricks by exhaustion once the top
                # cards have drawn the suit out.
                if len(held) >= 3:
                    has_ace = any(card.rank == Rank.ACE for card in held)
                    tricks += len(held) - 3 + has_ace
            else:
                # A plain honour behind its suit's own top two: a King or
                # Queen escorted by both the ace and the ten is expected
                # to survive to a trick of its own.
                tricks += sum(
                    1
                    for card in held
                    if card.rank in (Rank.KING, Rank.QUEEN)
                    and self.hand.has_card(suit, Rank.ACE)
                    and self.hand.has_card(suit, Rank.TEN)
                )

        return min(tricks, 8)  # Maximum 8 tricks in a round

    def _top_card_tricks(self, rules: TrumpRules, suit: Suit) -> int:
        """Tricks the top of ``suit``'s own ladder is expected to take.

        The ladder is the regime's, not a rank list: the Jack and 9 lead
        a trump suit and every all-trump suit, the ace and 10 lead a
        plain one (§3.1–§3.4). Naming ranks here would have been correct
        for exactly one regime — which is why an ace-heavy hand reads as
        unbeatable at no trump and as a trap at all trump, where every
        ace sits under its own Jack and 9.

        The rule is what the trump-only version always said, read off
        the ladder instead of spelled out: *the top card is a trick; the
        second is a trick when the hand holds another card of the suit
        to back it* — a bare second card falls to the top one the first
        time the suit is led.

        This is the same question :meth:`_honours` asks, in the same
        terms, so the bid value and the trick floor gating it can never
        disagree about what a top card is.

        Args:
            rules: The regime's rules, supplying the in-suit ladder.
            suit: The suit to read.

        Returns:
            0, 1 or 2 expected tricks.
        """

        held = self.hand.cards_of_suit(suit)
        if not held:
            return 0

        ranks = {card.rank for card in held}
        # Read position off the ladder: a rank is "the top" when nothing
        # outranks it, "the second" when exactly one rank does.
        has_top = any(not rules.higher_ranks(r, suit) for r in ranks)
        has_second = any(len(rules.higher_ranks(r, suit)) == 1 for r in ranks)

        if has_top and has_second:
            return 2
        if has_top:
            return 1
        if has_second and len(held) > 1:
            return 1
        return 0

    def _find_best_contract(
        self, mode_evaluations: dict
    ) -> tuple[int, ContractSuit | None]:
        """Resolve the mode evaluations to the single best (contract, mode).

        Folds the two questions the open-bid path used to answer
        separately — "what is the highest contract I can reach?" and
        "under which trump?" — into one pass. Ties on the contract value
        are broken here as well: modes carrying a Belote win first, then
        :attr:`MODE_PREFERENCE` — the core enumeration, card suits then
        no trump then all trump — decides among the rest.

        Args:
            mode_evaluations: Per-mode evaluation dicts from
                :meth:`_evaluate_modes`.

        Returns:
            The highest reachable contract and the single trump chosen
            for it, or ``(0, None)`` when no mode supports any contract.
        """

        max_contract = max(
            evaluation['contract'] for evaluation in mode_evaluations.values()
        )
        if max_contract == 0:
            return 0, None

        # Modes tied on the best contract value.
        candidates = [
            mode for mode, evaluation in mode_evaluations.items()
            if evaluation['contract'] == max_contract
        ]

        # Prefer belote-carrying modes; narrow the field when any exist.
        belote_suits = [
            mode for mode in candidates if mode_evaluations[mode]['has_belote']
        ]
        if belote_suits:
            candidates = belote_suits

        # Break the remaining tie with the fixed preference order. The
        # order covers every contract trump, so the first hit always
        # exists whether the table offers four modes or six.
        chosen_suit = next(
            suit for suit in self.MODE_PREFERENCE if suit in candidates
        )
        return max_contract, chosen_suit

    def _table_citations(
        self, rules: RuleConfig, mode: ContractSuit
    ) -> tuple[RuleCitation, ...]:
        """The table knobs that shaped which modes were on offer.

        Every open bid names them, because they are what decides the
        search space and its ceiling: which trumps were biddable at all,
        what capped the mode chosen, and whether the Solo Slam was on the
        ladder.

        Args:
            rules: The table ruleset the auction runs under.
            mode: The trump chosen, whose ladder top is being cited.

        Returns:
            Three citations, in evaluation order.
        """

        offered = ", ".join(str(m) for m in bookable_suits(rules))
        return (
            RuleCitation(
                "extended_trump_choices",
                str(rules.extended_trump_choices),
                f"modes evaluated: {offered}",
            ),
            RuleCitation(
                "all_trump_belote",
                str(rules.all_trump_belote),
                f"capped {mode} at {ladder_top(mode, rules)}",
            ),
            RuleCitation(
                "solo_slam_available",
                str(rules.solo_slam_available),
                "Solo Slam on the ladder"
                if rules.solo_slam_available
                else "Solo Slam withdrawn from the ladder",
            ),
        )

    @staticmethod
    def _runners_up(evaluations: dict, chosen: ContractSuit) -> tuple[str, ...]:
        """The modes weighed and rejected, strongest first.

        Args:
            evaluations: Per-mode evaluation dicts.
            chosen: The mode actually bid, excluded from the list.

        Returns:
            ``"<mode> <value>"`` per rejected mode that reached any
            contract at all, highest value first.
        """

        rejected = [
            (mode, evaluation["contract"])
            for mode, evaluation in evaluations.items()
            if mode != chosen and evaluation["contract"]
        ]
        rejected.sort(key=lambda pair: pair[1], reverse=True)
        return tuple(f"{mode} {value}" for mode, value in rejected)

    def _make_initial_bid(
        self, best_contract, best_suit, last_bid, rules=None, evaluations=None
    ):
        """Make an initial bid or overbid.

        Args:
            best_contract: Our highest reachable bidding-table contract
                (from :meth:`_find_best_contract`).
            best_suit: The single trump resolved for it, or ``None``.
            last_bid: The standing :class:`ContractBid`, or ``None``.
            rules: The table ruleset, for the rationale's citations.
                ``None`` falls back to the §9 defaults.
            evaluations: The per-mode evaluations, for the rationale's
                runner-up list. ``None`` lists nothing.

        Returns:
            A :class:`BidDecision` carrying a :class:`ContractBid` for
            the chosen trump, or a :class:`PassBid` when nothing legal
            improves the auction.
        """

        rules = rules if rules is not None else RuleConfig()

        if best_contract == 0 or best_suit is None:
            return self._decide(
                PassBid(self._player),
                "no contract in hand",
                "no trump choice clears the bidding table's opening "
                "row — passed.",
                considered=self._runners_up(evaluations or {}, None),
            )

        # Check if we can overbid the last bid
        if last_bid is not None and best_contract <= last_bid.get_numeric_value():
            return self._decide(
                PassBid(self._player),
                "the standing bid is out of reach",
                f"the hand is worth {best_contract} in {best_suit}, which "
                f"does not raise the standing "
                f"{last_bid.get_numeric_value()} — passed.",
                considered=(f"{best_suit} {best_contract}",),
            )

        return self._decide(
            self._contract_bid(best_contract, best_suit),
            "open on the bidding table",
            f"the hand reaches {best_contract} in {best_suit} on the "
            f"bidding table — bid it.",
            considered=self._runners_up(evaluations or {}, best_suit),
            citations=self._table_citations(rules, best_suit),
        )

    def _team_opening_bid(self, bids, suit):
        """Return our team's first :class:`ContractBid` in ``suit``, or ``None``.

        That opening bid anchors the support ceiling: the expert table
        always opens at its full evaluation of the suit (there is no
        slow walk-up), so everything our side may legitimately add on
        top of it is the *other* seat's complement — bid once.

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

    def _support_partner_bid(self, partner_bid, last_bid, bids, rules=None):
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
        anchor, so there is nothing of ours left to bid), or the
        standing contract already reaches the ceiling (our complement
        is spent, whether by our earlier raise or an opponent overbid).

        Our contribution is priced on the *mode's own* ladder. The
        trump complement is a Jack or 9 at a suit contract; under the two
        suitless modes it is an honour — a master, or a complement backed
        by its own master (:meth:`_honours`). Naming the Jack and the 9
        there would credit a hand for cards no ladder tops: a plain Jack
        wins nothing at no trump, where the Ace, 10, King and Queen all
        beat it.

        Args:
            partner_bid: Our side's most recent standing :class:`ContractBid`.
            last_bid: The standing :class:`ContractBid`, or ``None``.
            bids: Chronological bid history, to locate the anchor.
            rules: The table ruleset. ``None`` falls back to the §9
                defaults, which offer only the four suits.

        Returns:
            A :class:`BidDecision` carrying a :class:`ContractBid`
            raising partner's suit to the team ceiling, or a
            :class:`PassBid` when we add nothing, opened the suit
            ourselves, or the ceiling is already reached.
        """

        partner_suit = partner_bid.suit

        # Anchor on our team's opening bid of the suit. If *we* opened
        # it, `partner_bid` is our own bid echoed back by
        # `_get_partner_bid` — supporting it would double-count the
        # very cards that priced it.
        anchor = self._team_opening_bid(bids, partner_suit)
        if anchor is None or anchor.player is self._player:
            return self._decide(
                PassBid(self._player),
                "our cards are already priced in",
                f"we opened {partner_suit} ourselves, so the standing bid "
                f"already values this hand — passed.",
            )

        # Calculate our contribution to partner's suit
        contribution = 0
        mode_rules = rules_for(partner_suit)

        if isinstance(partner_suit, Suit):
            # +10 per ace held outside the trump suit. Only meaningful
            # where "outside the trump suit" names something: at all
            # trump every suit is trump, and at no trump the aces are
            # already counted as the honours below.
            for card in self.hand:
                if card.suit != partner_suit and card.rank == Rank.ACE:
                    contribution += 10

            # +10 for the trump complement (Jack or 9 of trump).
            trump_cards = self.hand.cards_of_suit(partner_suit)
            if any(
                card.rank in (Rank.JACK, Rank.NINE) for card in trump_cards
            ):
                contribution += HONOUR_STEP
        else:
            # A suitless mode has no "outside" to count aces in and no
            # single complement to hold: what this hand adds is whatever
            # its honours are worth on the mode's own ladder.
            masters, complements = self._honours(mode_rules)
            contribution += HONOUR_STEP * (masters + complements)

        if contribution == 0:
            return self._decide(
                PassBid(self._player),
                "nothing to add to partner's bid",
                f"no external ace and no {partner_suit} complement — the "
                f"hand adds nothing to partner's contract.",
            )

        # The team ceiling: partner's evaluation + our complement. Once
        # the standing contract reaches it, our support is spent.
        ceiling = anchor.get_numeric_value() + contribution
        if ceiling <= last_bid.get_numeric_value():
            return self._decide(
                PassBid(self._player),
                "our support is spent",
                f"the team ceiling in {partner_suit} is {ceiling} and the "
                f"standing bid already reaches it — passed.",
                considered=(f"{ceiling} {partner_suit}",),
            )

        # An off-ladder ceiling (e.g. overshooting a partner's Slam)
        # falls back to Pass inside _contract_bid.
        return self._decide(
            self._contract_bid(ceiling, partner_suit),
            "support partner",
            f"partner opened {partner_suit} and this hand adds "
            f"{contribution} — raised to the team ceiling of {ceiling}.",
        )

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
