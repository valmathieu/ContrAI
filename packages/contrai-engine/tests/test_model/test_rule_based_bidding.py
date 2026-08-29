"""Unit tests for the rule-based AI bidding strategy.

These tests exercise the expert bidding table now living on
``RuleBasedBiddingStrategy``. ``AiPlayer.choose_bid`` is a public
delegator, so high-level ``choose_bid(...)`` calls stay on the player,
while private helpers and constants are reached through
``ai_player.bidding.*`` (the injected strategy object).
"""

import itertools

import pytest
from contrai_engine.model.player import AiPlayer, BidDecision, Rationale
from contrai_core import (
    AllTrumpBelote,
    Auction,
    ContractBid,
    DoubleBid,
    Hand,
    PassBid,
    Position,
    RedoubleBid,
    RuleConfig,
    SlamLevel,
)
from contrai_core.card import Card
from contrai_core.rules import rules_for
from contrai_core.team import Team
from contrai_core.types import CONTRACT_SUITS, Suit, Rank, TrumpVariant


def _auction(bids=()):
    """Pack a sequence of :class:`Bid` objects into an :class:`Auction`.

    ``AiPlayer.choose_bid`` takes an Auction; test bodies build the
    chronological :class:`Bid` history directly (``ContractBid`` /
    ``PassBid`` / ``DoubleBid`` / ``RedoubleBid``) and hand it here.
    """
    return Auction(tuple(bids))


class TestAiPlayerBidding:
    """Test AI player bidding logic"""

    @pytest.fixture
    def ai_player(self):
        """Create an AI player for testing"""
        player = AiPlayer("TestBot", Position.NORTH)
        # Create a mock team
        partner = AiPlayer("Partner", Position.SOUTH)
        team = Team("North-South", [player, partner])
        player.team = team
        partner.team = team
        return player

    @pytest.fixture
    def ai_opponent_player(self):
        """Create an opponent AI player for testing"""
        opponent = AiPlayer("Opponent", Position.WEST)
        opponent_partner = AiPlayer("OpponentPartner", Position.EAST)
        opponent_team = Team("East-West", [opponent, opponent_partner])
        opponent.team = opponent_team
        opponent_partner.team = opponent_team

        return opponent

    @pytest.fixture
    def sample_cards_weak(self):
        """Create a weak hand for testing"""
        return Hand([
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT)
        ])

    @pytest.fixture
    def sample_cards_correct_hearts(self):
        """Create a middle hand for testing"""
        return Hand([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.KING),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.TEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.CLUBS, Rank.TEN)
        ])

    @pytest.fixture
    def sample_cards_strong_spades(self):
        """Create a strong spades hand for testing"""
        return Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.CLUBS, Rank.JACK)
        ])

    @pytest.fixture
    def sample_cards_belote_spades(self):
        """Create a hand with belote in spades"""
        return Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.SPADES, Rank.QUEEN),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.CLUBS, Rank.EIGHT)
        ])

    def test_evaluate_suits_weak_hand(self, ai_player, sample_cards_weak):
        """Test suit evaluation with a weak hand"""
        ai_player.hand = sample_cards_weak
        evaluations = ai_player.bidding._evaluate_suits()

        # All suits should have low or zero contract values
        for suit, eval_data in evaluations.items():
            assert eval_data['contract'] == 0
            assert eval_data['estimated_tricks'] == 0
            assert eval_data['has_belote'] is False

    def test_evaluate_suits_correct_hand(self, ai_player, sample_cards_correct_hearts):
        """Test suit evaluation with a correct hand"""
        ai_player.hand = sample_cards_correct_hearts
        evaluations = ai_player.bidding._evaluate_suits()

        hearts_eval = evaluations[Suit.HEARTS]
        assert hearts_eval['contract'] == 80  # Should be able to bid 130
        assert hearts_eval['trump_count'] == 3
        assert hearts_eval['estimated_tricks'] == 4
        assert hearts_eval['external_aces'] == 1

    def test_evaluate_suits_strong_spades(self, ai_player, sample_cards_strong_spades):
        """Test suit evaluation with a strong spades hand"""
        ai_player.hand = sample_cards_strong_spades
        evaluations = ai_player.bidding._evaluate_suits()

        spades_eval = evaluations[Suit.SPADES]
        assert spades_eval['contract'] == 130  # Should be able to bid 130
        assert spades_eval['trump_count'] == 4
        assert spades_eval['estimated_tricks'] == 7
        assert spades_eval['external_aces'] == 3

    def test_evaluate_suits_belote(self, ai_player, sample_cards_belote_spades):
        """Test suit evaluation with belote"""
        ai_player.hand = sample_cards_belote_spades
        evaluations = ai_player.bidding._evaluate_suits()

        spades_eval = evaluations[Suit.SPADES]
        assert spades_eval['has_belote'] is True
        assert spades_eval['contract'] == 140

    def test_estimate_tricks(self, ai_player, sample_cards_strong_spades):
        """Test trick estimation"""
        ai_player.hand = sample_cards_strong_spades
        tricks = ai_player.bidding._estimate_tricks(Suit.SPADES)

        # Strong spades hand with 3 external aces should estimate 7 tricks
        assert tricks == 7

    def test_trump_suit_tricks_split_into_ladder_plus_length(
        self, ai_player, sample_cards_strong_spades
    ):
        """The old ``_evaluate_trump_tricks`` total, now in its two halves.

        Jack + 9 are the top two of the trump ladder (2 tricks); the two
        further spades, one of them the ace, add 2 more by exhaustion.
        The 4 they used to sum to is unchanged — only the split is new,
        and only the ladder half is regime-dependent.
        """
        ai_player.hand = sample_cards_strong_spades
        rules = rules_for(Suit.SPADES)
        ladder = ai_player.bidding._top_card_tricks(rules, Suit.SPADES)
        assert ladder == 2
        # The length bonus is inlined in _estimate_tricks; reading it back
        # off the total for the spade suit alone keeps the old number pinned.
        held = ai_player.hand.cards_of_suit(Suit.SPADES)
        length_bonus = len(held) - 3 + any(
            c.rank == Rank.ACE for c in held
        )
        assert ladder + length_bonus == 4

    def test_get_partner_bid(self, ai_player, ai_opponent_player):
        """Test getting partner's bid"""
        ai_player_partner = ai_player.team.players[1]
        ai_opponent_player_partner = ai_opponent_player.team.players[1]

        bids = [
            PassBid(ai_opponent_player),
            ContractBid(ai_player_partner, 80, Suit.SPADES),
            ContractBid(ai_opponent_player_partner, 90, Suit.HEARTS),
        ]

        partner_bid = ai_player.bidding._get_partner_bid(bids)
        assert isinstance(partner_bid, ContractBid)
        assert partner_bid.value == 80
        assert partner_bid.suit == Suit.SPADES

    def test_choose_bid_pass_weak_hand(self, ai_player, sample_cards_weak):
        """Test that AI passes with weak hand"""
        ai_player.hand = sample_cards_weak
        bid = ai_player.choose_bid(_auction()).bid
        assert isinstance(bid, PassBid)

    def test_choose_bid_initial_bid_strong_hand(self, ai_player, sample_cards_strong_spades):
        """Test initial bid with strong hand"""
        ai_player.hand = sample_cards_strong_spades
        bid = ai_player.choose_bid(_auction()).bid

        assert isinstance(bid, ContractBid)
        assert bid.value == 130
        assert bid.suit == Suit.SPADES

    def test_choose_bid_overbid_opponent(self, ai_player, ai_opponent_player, sample_cards_strong_spades):
        """Test overbidding opponent"""
        ai_player.hand = sample_cards_strong_spades

        auction = _auction([ContractBid(ai_opponent_player, 90, Suit.HEARTS)])
        bid = ai_player.choose_bid(auction).bid

        assert isinstance(bid, ContractBid)
        assert bid.value > 90
        assert bid.suit == Suit.SPADES

    def test_choose_bid_support_partner(self, ai_player, ai_opponent_player):
        """Test supporting partner's bid"""
        # Give AI player some external aces to support partner
        ai_player.hand = Hand([
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.QUEEN),
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.SPADES, Rank.JACK),  # Trump complement
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.EIGHT)
        ])

        # Partner bids 80 in Spades
        partner = ai_player.team.players[1]
        auction = _auction([
            ContractBid(partner, 80, Suit.SPADES),
            PassBid(ai_opponent_player),
        ])
        bid = ai_player.choose_bid(auction).bid

        # Should support with higher bid due to 3 external aces + trump complement
        assert isinstance(bid, ContractBid)
        assert bid.value >= 100  # 80 + 20 (2 aces) + 10 (trump complement)
        assert bid.suit == Suit.SPADES

    def test_choose_bid_cant_overbid_partner(self, ai_player, ai_opponent_player, sample_cards_weak):
        """Test that AI doesn't overbid partner when it can't"""
        ai_player.hand = sample_cards_weak

        # Partner bids high
        partner = ai_player.team.players[1]
        auction = _auction([
            ContractBid(partner, 140, Suit.SPADES),
            PassBid(ai_opponent_player),
        ])
        bid = ai_player.choose_bid(auction).bid

        assert isinstance(bid, PassBid)

    # --- Bidding under a standing Coinche / Surcoinche --------------------
    # Regression coverage for the crash where the expert table, blind to a
    # Double freezing the auction, returned an illegal numeric raise (even
    # over its *own* partner) and ``Auction.apply`` aborted the game with
    # ``IllegalBidError``. A standing Double permits only Pass, or a
    # Surcoinche (Redouble) from the contracting team.

    def test_choose_bid_strong_hand_overbids_partner_without_double(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """Control case: with no Double, the strong AI *does* raise partner.

        Establishes that the Pass in
        :meth:`test_choose_bid_passes_when_opponent_doubled_partner` is
        caused by the freeze, not by the hand being too weak to raise.
        """
        ai_player.hand = sample_cards_strong_spades  # max contract 130
        partner = ai_player.team.players[1]
        auction = _auction([ContractBid(partner, 80, Suit.SPADES)])
        bid = ai_player.choose_bid(auction).bid
        assert isinstance(bid, ContractBid)
        assert bid.value == 130
        assert bid.suit == Suit.SPADES

    def test_choose_bid_passes_when_opponent_doubled_partner(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """AI must Pass — not raise — when an opponent Coinched partner.

        The exact reproduction of the reported crash: partner holds the
        contract, an opponent Doubles, and the AI's hand is strong enough
        that the open-auction path would raise to 130. The Double freezes
        the auction, so the only non-redouble action is Pass.
        """
        ai_player.hand = sample_cards_strong_spades
        partner = ai_player.team.players[1]
        auction = _auction([
            ContractBid(partner, 80, Suit.SPADES),
            DoubleBid(ai_opponent_player),
        ])
        bid = ai_player.choose_bid(auction).bid
        assert isinstance(bid, PassBid)

    def test_choose_bid_passes_when_own_team_doubled_opponent(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """AI on the *doubling* side may only Pass (no raise, no redouble).

        Here the opponents hold the contract and the AI's partner has
        already Coinched it. The contracting team is the opponents, so a
        Surcoinche is illegal for this seat and the strong hand must not
        tempt a numeric raise either.
        """
        ai_player.hand = sample_cards_strong_spades
        partner = ai_player.team.players[1]
        auction = _auction([
            ContractBid(ai_opponent_player, 120, Suit.HEARTS),
            DoubleBid(partner),
        ])
        bid = ai_player.choose_bid(auction).bid
        assert isinstance(bid, PassBid)

    def test_choose_bid_passes_after_redouble(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """Once the auction is Surcoinched, only Pass remains."""
        ai_player.hand = sample_cards_strong_spades
        partner = ai_player.team.players[1]
        auction = _auction([
            ContractBid(partner, 110, Suit.SPADES),
            DoubleBid(ai_opponent_player),
            RedoubleBid(partner),
        ])
        bid = ai_player.choose_bid(auction).bid
        assert isinstance(bid, PassBid)

    def test_choose_bid_surcoinches_when_strategy_approves(
        self, ai_player, ai_opponent_player, sample_cards_weak
    ):
        """Contracting team may Redouble when the strategy says so.

        ``_should_redouble`` is a stub returning ``False`` today, so we
        force it ``True`` to exercise the (legal) Surcoinche path and
        confirm the resulting :class:`RedoubleBid` is what the Auction
        would accept.
        """
        ai_player.hand = sample_cards_weak
        partner = ai_player.team.players[1]
        auction = _auction([
            ContractBid(partner, 100, Suit.SPADES),
            DoubleBid(ai_opponent_player),
        ])
        ai_player.bidding._should_redouble = lambda: True  # type: ignore[method-assign]
        bid = ai_player.choose_bid(auction).bid
        assert isinstance(bid, RedoubleBid)
        assert auction.is_legal(bid)

    def test_choose_bid_guard_converts_illegal_table_bid_to_pass(
        self, ai_player, ai_opponent_player, sample_cards_weak
    ):
        """The is_legal safety net turns an illegal expert-table bid into Pass.

        Independently of the freeze handling, ``choose_bid`` must never
        hand ``Auction.apply`` a bid it would reject. We force the expert
        table to emit an under-cutting raise (90 over a live 140) and
        assert the guard downgrades it to the always-legal Pass.
        """
        ai_player.hand = sample_cards_weak
        auction = _auction([ContractBid(ai_opponent_player, 140, Suit.SPADES)])
        ai_player.bidding._choose_open_bid = (  # type: ignore[method-assign]
            lambda _auction: BidDecision(
                ContractBid(ai_player, 90, Suit.SPADES),
                Rationale("forced", "an illegal raise, planted by the test"),
            )
        )
        decision = ai_player.choose_bid(auction)
        assert isinstance(decision.bid, PassBid)
        # The withdrawal is an explained decision, not a silent swallow.
        assert decision.rationale.rule == "withdraw an illegal bid"

    # --- Slam / Solo Slam bidding -----------------------------------------
    # _estimate_tricks is capped at 8 (`min(tricks, 8)`), so a hand holding
    # 5 trumps (J + 9 + A + K + Q) plus all three external aces triggers
    # the Slam-family rows in BIDDING_TABLE. Both Slam (500) and Solo Slam
    # (1000) share the same trick-estimator gate today (tricks_min=8), so
    # the table walks both and stops on the higher one.

    @pytest.fixture
    def sample_cards_slam_spades(self):
        """Five-trump Spades hand plus the three external aces."""
        return Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.SPADES, Rank.QUEEN),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
        ])

    def test_evaluate_suit_slam_family_qualifies(
        self, ai_player, sample_cards_slam_spades
    ):
        """A hand estimated at 8 tricks resolves to the top Slam-family row.

        With the current (deliberately permissive) Solo Slam gate that
        shares Slam's ``tricks_min=8``, the table walk lands on
        ``SOLO_SLAM_NUMERIC`` (1000). The Slam row (500) is still
        reachable via the AI when partner bids below that — see the
        sentinel-translation tests.
        """
        ai_player.hand = sample_cards_slam_spades
        evaluations = ai_player.bidding._evaluate_suits()
        assert evaluations[Suit.SPADES]['contract'] == ai_player.bidding.SOLO_SLAM_NUMERIC
        assert evaluations[Suit.SPADES]['estimated_tricks'] == 8

    def test_choose_bid_solo_slam_strong_hand(
        self, ai_player, sample_cards_slam_spades
    ):
        """choose_bid lifts the Solo Slam wire choice to a ContractBid."""
        ai_player.hand = sample_cards_slam_spades
        bid = ai_player.choose_bid(_auction()).bid
        assert isinstance(bid, ContractBid)
        assert bid.value is SlamLevel.SOLO_SLAM
        assert bid.suit == Suit.SPADES

    def test_should_double_handles_slam_value(self, ai_player, sample_cards_weak):
        """_should_double must not TypeError on a SlamLevel value.

        The heuristic itself (``strength > 162 - value``) is permissive
        against Slam-family bids because ``162 - 500`` (and -1000) is
        negative; we only assert the boolean contract here. Tuning the
        heuristic is a separate concern.
        """
        ai_player.hand = sample_cards_weak
        result = ai_player.bidding._should_double(
            ContractBid(ai_player, SlamLevel.SLAM, Suit.SPADES)
        )
        assert isinstance(result, bool)
        result = ai_player.bidding._should_double(
            ContractBid(ai_player, SlamLevel.SOLO_SLAM, Suit.SPADES)
        )
        assert isinstance(result, bool)

    def test_choose_bid_passes_when_partner_announced_slam(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """A strong-but-not-Slam AI passes cleanly when partner announces Slam."""
        ai_player.hand = sample_cards_strong_spades  # estimates 7 tricks, max 130
        partner = ai_player.team.players[1]
        auction = _auction([ContractBid(partner, SlamLevel.SLAM, Suit.SPADES)])
        # Must not TypeError on the 130-vs-Slam comparison.
        bid = ai_player.choose_bid(auction).bid
        assert isinstance(bid, PassBid)

    def test_choose_bid_passes_when_partner_announced_solo_slam(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """A strong-but-not-Slam AI passes when partner announces Solo Slam."""
        ai_player.hand = sample_cards_strong_spades
        partner = ai_player.team.players[1]
        auction = _auction([ContractBid(partner, SlamLevel.SOLO_SLAM, Suit.SPADES)])
        bid = ai_player.choose_bid(auction).bid
        assert isinstance(bid, PassBid)

    # --- Best-contract resolution ------------------------------------------
    # _find_best_contract folds the max-contract search and the suit
    # tie-break (belote first, then the fixed preference order) into a
    # single step, so the open-bid path handles one (contract, suit)
    # pair. Tie cases build the evaluation dicts directly — real hands
    # rarely produce exact contract ties on demand.

    def test_find_best_contract_weak_hand(self, ai_player, sample_cards_weak):
        """No suit meets the bidding table → (0, None)."""
        ai_player.hand = sample_cards_weak
        evaluations = ai_player.bidding._evaluate_suits()

        assert ai_player.bidding._find_best_contract(evaluations) == (0, None)

    def test_find_best_contract_single_best_suit(
        self, ai_player, sample_cards_strong_spades
    ):
        """A hand with one dominant suit resolves to that suit's contract."""
        ai_player.hand = sample_cards_strong_spades
        evaluations = ai_player.bidding._evaluate_suits()

        assert ai_player.bidding._find_best_contract(evaluations) == (130, Suit.SPADES)

    def test_find_best_contract_tie_preference_order(self, ai_player):
        """Tied suits without belote fall back to the preference order."""
        # Mirror-image Spades/Hearts holdings: both evaluate to 130.
        ai_player.hand = Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.NINE),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE)
        ])

        evaluations = ai_player.bidding._evaluate_suits()

        # Spades wins the tie: first in the preference order.
        assert ai_player.bidding._find_best_contract(evaluations) == (130, Suit.SPADES)

    def test_find_best_contract_tie_belote_preference(self, ai_player):
        """A belote suit wins the tie over an equal belote-less suit."""
        evaluations = {
            Suit.SPADES: {'contract': 100, 'has_belote': False},
            Suit.HEARTS: {'contract': 100, 'has_belote': True},
            Suit.DIAMONDS: {'contract': 0, 'has_belote': False},
            Suit.CLUBS: {'contract': 0, 'has_belote': False},
        }

        assert ai_player.bidding._find_best_contract(evaluations) == (100, Suit.HEARTS)

    def test_find_best_contract_tie_respects_candidates(self, ai_player):
        """The preference tie-break must pick among the *tied* suits only.

        Regression: the retired ``_choose_best_suit`` fallback loop
        returned ``SUIT_PREFERENCE[0]`` (Spades) unconditionally — even
        when Spades never met the bidding table.
        """
        evaluations = {
            Suit.SPADES: {'contract': 0, 'has_belote': False},
            Suit.HEARTS: {'contract': 100, 'has_belote': False},
            Suit.DIAMONDS: {'contract': 100, 'has_belote': False},
            Suit.CLUBS: {'contract': 0, 'has_belote': False},
        }

        assert ai_player.bidding._find_best_contract(evaluations) == (100, Suit.HEARTS)

    def test_find_best_contract_tie_multiple_belotes(self, ai_player):
        """Several belote suits: tie-break *within* the belote holders.

        Regression: two belote suits fell through to the raw preference
        order over all candidates, so a belote-less suit (here Spades)
        could win the tie it should have lost.
        """
        evaluations = {
            Suit.SPADES: {'contract': 100, 'has_belote': False},
            Suit.HEARTS: {'contract': 100, 'has_belote': True},
            Suit.DIAMONDS: {'contract': 100, 'has_belote': True},
            Suit.CLUBS: {'contract': 0, 'has_belote': False},
        }

        assert ai_player.bidding._find_best_contract(evaluations) == (100, Suit.HEARTS)


class TestAiPlayerDoubling:
    """Test AI player doubling logic"""

    @pytest.fixture
    def ai_players_with_teams(self):
        """Create AI players with team setup"""
        player = AiPlayer("TestBot", Position.NORTH)
        partner = AiPlayer("Partner", Position.SOUTH)
        team = Team("North-South", [player, partner])
        player.team = team
        partner.team = team

        # Create opponent team
        opponent1 = AiPlayer("Opponent1", Position.WEST)
        opponent2 = AiPlayer("Opponent2", Position.EAST)
        opponent_team = Team("East-West", [opponent1, opponent2])
        opponent1.team = opponent_team
        opponent2.team = opponent_team

        return player, partner, opponent1, opponent2

    def test_should_double_with_external_strength(self, ai_players_with_teams):
        """Test doubling when having external strength"""
        player, _, opponent1, _ = ai_players_with_teams

        # Give player strong external cards
        player.hand = Hand([
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.TEN),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.TEN),
            Card(Suit.CLUBS, Rank.TEN),
            Card(Suit.CLUBS, Rank.JACK),
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.SPADES, Rank.SEVEN)
        ])

        # Opponent bids in Spades
        auction = _auction([ContractBid(opponent1, 120, Suit.SPADES)])
        bid = player.choose_bid(auction).bid

        assert isinstance(bid, DoubleBid)

    def test_should_not_double_weak_external(self, ai_players_with_teams):
        """Test not doubling when lacking external strength"""
        player, _, opponent1, _ = ai_players_with_teams

        # Give player weak external cards
        player.hand = Hand([
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.KING)
        ])

        # Opponent bids in Hearts
        auction = _auction([ContractBid(opponent1, 100, Suit.HEARTS)])
        bid = player.choose_bid(auction).bid

        assert isinstance(bid, PassBid)


class TestSupportCeiling:
    """Partner-support raises are anchored to the team's opening bid.

    Regression suite for the support escalation loop: each supporting
    turn used to re-add the seat's full (static) contribution on top of
    the *standing* contract, so two partners alternately raised each
    other — the same aces re-counted on every lap — until the value
    walked off the ladder (typically at 180). Support is now capped at
    a team ceiling: partner's opening bid in the suit (their full table
    evaluation) plus our own contribution, announced exactly once. A
    seat never supports a suit it opened itself.
    """

    @pytest.fixture
    def four_ai_players(self):
        """Four AI players seated N/E/S/W with N-S and E-W teams."""
        north = AiPlayer("North", Position.NORTH)
        east = AiPlayer("East", Position.EAST)
        south = AiPlayer("South", Position.SOUTH)
        west = AiPlayer("West", Position.WEST)

        ns_team = Team("North-South", [north, south])
        ew_team = Team("East-West", [east, west])
        north.team = ns_team
        south.team = ns_team
        east.team = ew_team
        west.team = ew_team

        return north, east, south, west

    @pytest.fixture
    def opener_80_spades(self):
        """A hand the bidding table resolves to exactly 80 in Spades.

        4 trumps with the Jack (no 9), 1 external ace, 4 estimated
        tricks. Its own support contribution to Spades is +20
        (1 external ace + trump complement).
        """
        return Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.EIGHT),
        ])

    @pytest.fixture
    def supporter_30_spades(self):
        """A hand worth no table contract but a +30 support of Spades.

        2 external aces (+20) and the 9 of trump (+10); disjoint from
        :meth:`opener_80_spades` so the pair can be dealt together in
        the full-auction test.
        """
        return Hand([
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.NINE),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.SEVEN),
        ])

    @pytest.fixture
    def strong_spades_130(self):
        """The 130-in-Spades hand (3 external aces, J+9 of trump)."""
        return Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.CLUBS, Rank.JACK),
        ])

    def test_support_raises_to_partner_opening_plus_contribution(
        self, four_ai_players, supporter_30_spades
    ):
        """The ceiling is partner's opening bid + our contribution.

        With an opponent overbid wedged between partner's 80 and our
        turn, the raise must land on 80 + 30 = 110 — not on
        standing-90 + 30 = 120, which would silently inflate the team
        estimate.
        """
        north, east, south, _ = four_ai_players
        north.hand = supporter_30_spades

        auction = _auction([
            ContractBid(south, 80, Suit.SPADES),
            ContractBid(east, 90, Suit.DIAMONDS),
        ])
        bid = north.choose_bid(auction).bid

        assert isinstance(bid, ContractBid)
        assert bid.value == 110
        assert bid.suit == Suit.SPADES

    def test_support_passes_when_ceiling_already_beaten(
        self, four_ai_players, supporter_30_spades
    ):
        """No raise once the standing contract exceeds the team ceiling.

        Partner opened 80, our complement is +30, and an opponent
        already stands at 120 > 110: supporting would commit the team
        past its own combined estimate, so the only sane action is Pass.
        """
        north, east, south, _ = four_ai_players
        north.hand = supporter_30_spades

        auction = _auction([
            ContractBid(south, 80, Suit.SPADES),
            ContractBid(east, 120, Suit.DIAMONDS),
        ])
        bid = north.choose_bid(auction).bid

        assert isinstance(bid, PassBid)

    def test_opener_does_not_reraise_after_partner_support(
        self, four_ai_players, strong_spades_130
    ):
        """The suit opener passes on partner's support raise.

        The opening bid already carries the opener's full table
        evaluation; re-adding the same aces on top of partner's raise
        is the first lap of the escalation loop.
        """
        north, east, south, west = four_ai_players
        north.hand = strong_spades_130

        auction = _auction([
            ContractBid(north, 130, Suit.SPADES),
            PassBid(east),
            ContractBid(south, 140, Suit.SPADES),
            PassBid(west),
        ])
        bid = north.choose_bid(auction).bid

        assert isinstance(bid, PassBid)

    def test_opener_does_not_support_own_bid_after_opponent_overbid(
        self, four_ai_players, opener_80_spades
    ):
        """A seat never 'supports' its own opening bid.

        With partner silent, ``_get_partner_bid`` hands back the seat's
        own contract; piling the support contribution on top would
        re-count the very cards that priced the opening 80.
        """
        north, east, south, west = four_ai_players
        north.hand = opener_80_spades

        auction = _auction([
            ContractBid(north, 80, Suit.SPADES),
            ContractBid(east, 90, Suit.HEARTS),
            PassBid(south),
            PassBid(west),
        ])
        bid = north.choose_bid(auction).bid

        assert isinstance(bid, PassBid)

    def test_full_auction_settles_at_team_ceiling(
        self, four_ai_players, opener_80_spades, supporter_30_spades
    ):
        """End-to-end: a full 4-AI auction stops at opener + complement.

        The user-visible symptom of the loop: an 80 opening with a +30
        partner complement ratcheted 80 → 110 → 130 → 160, at which
        point the double heuristic (`strength > 162 - value`) armed
        itself on the inflated value and an opponent Coinched — the
        80-hand team ended up committed to a doubled 160. The auction
        must now settle at 110, un-doubled.
        """
        north, east, south, west = four_ai_players
        north.hand = opener_80_spades
        south.hand = supporter_30_spades
        # Weak opposing hands (disjoint from N/S): no table row is met
        # and the double heuristic stays quiet at every standing value.
        east.hand = Hand([
            Card(Suit.SPADES, Rank.QUEEN),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.QUEEN),
            Card(Suit.HEARTS, Rank.TEN),
            Card(Suit.DIAMONDS, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.NINE),
            Card(Suit.CLUBS, Rank.JACK),
            Card(Suit.CLUBS, Rank.NINE),
        ])
        west.hand = Hand([
            Card(Suit.SPADES, Rank.TEN),
            Card(Suit.HEARTS, Rank.KING),
            Card(Suit.DIAMONDS, Rank.QUEEN),
            Card(Suit.DIAMONDS, Rank.KING),
            Card(Suit.DIAMONDS, Rank.TEN),
            Card(Suit.CLUBS, Rank.QUEEN),
            Card(Suit.CLUBS, Rank.KING),
            Card(Suit.CLUBS, Rank.TEN),
        ])

        auction = Auction()
        for player in itertools.cycle([north, east, south, west]):
            if auction.is_terminal():
                break
            auction = auction.apply(player.choose_bid(auction).bid)
            # A converging auction is short; a long one means the
            # escalation loop is back.
            assert len(auction.bids) <= 24, "auction failed to converge"

        final = auction.last_contract_bid
        assert final is not None
        assert final.value == 110
        assert final.suit == Suit.SPADES
        # A sane 110 stays below the opponents' double threshold.
        assert not auction.has_double


class TestEstimateTricksPerMode:
    """``_estimate_tricks`` must read the regime's own ladder.

    The estimator asks one question per suit — *what tops this suit, and
    is the second card backed?* — and used to answer it with rank
    literals: Jack and 9 for the named trump suit, Ace and 10 for the
    rest. That is correct for exactly one regime. At all trump every
    suit ranks ``J 9 A 10 K Q 8 7``, so an ace is only the third card of
    its ladder and an ace-heavy hand is a trap, not a lock.
    """

    @pytest.fixture
    def strategy(self):
        player = AiPlayer("TestBot", Position.NORTH)
        partner = AiPlayer("Partner", Position.SOUTH)
        team = Team("North-South", [player, partner])
        player.team = team
        partner.team = team
        return player.bidding

    @pytest.fixture
    def four_aces_four_tens(self):
        """Every ace and every ten — eight certain tricks at no trump."""
        return Hand([
            Card(suit, rank)
            for suit in Suit
            for rank in (Rank.ACE, Rank.TEN)
        ])

    @pytest.fixture
    def four_jacks_four_nines(self):
        """Every Jack and every 9 — eight certain tricks at all trump."""
        return Hand([
            Card(suit, rank)
            for suit in Suit
            for rank in (Rank.JACK, Rank.NINE)
        ])

    def test_all_aces_and_tens_sweep_at_no_trump(
        self, strategy, four_aces_four_tens
    ):
        strategy._player.hand = four_aces_four_tens
        assert strategy._estimate_tricks(TrumpVariant.NO_TRUMP) == 8

    def test_all_aces_and_tens_are_a_trap_at_all_trump(
        self, strategy, four_aces_four_tens
    ):
        """The sharpest failure: each ace is beaten by its own Jack and 9."""
        strategy._player.hand = four_aces_four_tens
        assert strategy._estimate_tricks(TrumpVariant.ALL_TRUMP) < 8

    def test_all_jacks_and_nines_sweep_at_all_trump(
        self, strategy, four_jacks_four_nines
    ):
        strategy._player.hand = four_jacks_four_nines
        assert strategy._estimate_tricks(TrumpVariant.ALL_TRUMP) == 8

    def test_all_jacks_and_nines_are_worth_little_at_no_trump(
        self, strategy, four_jacks_four_nines
    ):
        """A Jack tops nothing on the plain ladder — Ace, 10, K and Q beat it."""
        strategy._player.hand = four_jacks_four_nines
        assert strategy._estimate_tricks(TrumpVariant.NO_TRUMP) == 0

    def test_the_same_hand_reads_differently_in_each_regime(
        self, strategy, four_aces_four_tens
    ):
        """One hand, three regimes, three answers — the point of the change."""
        strategy._player.hand = four_aces_four_tens
        no_trump = strategy._estimate_tricks(TrumpVariant.NO_TRUMP)
        all_trump = strategy._estimate_tricks(TrumpVariant.ALL_TRUMP)
        assert no_trump > all_trump

    def test_a_suit_contract_estimate_is_unchanged(self, strategy):
        """The regression guard: today's suit numbers must not move.

        The strong-spades hand (J + 9 + A + K of trump, three external
        aces) estimated 7 before the ladder rewrite and must still.
        """
        strategy._player.hand = Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.CLUBS, Rank.JACK),
        ])
        assert strategy._estimate_tricks(Suit.SPADES) == 7

    def test_a_weak_hand_estimates_nothing_in_any_regime(self, strategy):
        strategy._player.hand = Hand([
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT),
        ])
        for mode in (
            Suit.SPADES, TrumpVariant.NO_TRUMP, TrumpVariant.ALL_TRUMP
        ):
            assert strategy._estimate_tricks(mode) == 0

    def test_the_estimate_is_capped_at_eight(
        self, strategy, four_jacks_four_nines
    ):
        """Eight tricks exist in a round, however good the hand reads."""
        strategy._player.hand = four_jacks_four_nines
        for mode in (
            Suit.SPADES, TrumpVariant.NO_TRUMP, TrumpVariant.ALL_TRUMP
        ):
            assert strategy._estimate_tricks(mode) <= 8


class TestTopCardTricks:
    """The per-suit ladder rule the estimator is built from.

    *Top of the ladder is a trick; the second is a trick when the hand
    holds another card of the suit to back it.* One rule, read off
    ``rules.higher_ranks`` — which is the same question
    :meth:`_honours` asks, so the bid value and the trick floor gating
    it can never disagree about what a top card is.
    """

    @pytest.fixture
    def strategy(self):
        player = AiPlayer("TestBot", Position.NORTH)
        partner = AiPlayer("Partner", Position.SOUTH)
        team = Team("North-South", [player, partner])
        player.team = team
        partner.team = team
        return player.bidding

    def test_trump_jack_and_nine_are_two_tricks(self, strategy):
        strategy._player.hand = Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
        ])
        assert strategy._top_card_tricks(
            rules_for(Suit.SPADES), Suit.SPADES
        ) == 2

    def test_trump_jack_alone_is_one_trick(self, strategy):
        strategy._player.hand = Hand([Card(Suit.SPADES, Rank.JACK)])
        assert strategy._top_card_tricks(
            rules_for(Suit.SPADES), Suit.SPADES
        ) == 1

    def test_the_second_card_needs_backing(self, strategy):
        """A bare 9 takes nothing; a 9 with an escort takes a trick."""
        rules = rules_for(Suit.SPADES)
        strategy._player.hand = Hand([Card(Suit.SPADES, Rank.NINE)])
        assert strategy._top_card_tricks(rules, Suit.SPADES) == 0
        strategy._player.hand = Hand([
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.SEVEN),
        ])
        assert strategy._top_card_tricks(rules, Suit.SPADES) == 1

    def test_the_same_rule_reads_the_plain_ladder(self, strategy):
        """Ace and 10 lead a plain suit exactly as Jack and 9 lead trump."""
        rules = rules_for(Suit.SPADES)
        strategy._player.hand = Hand([
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.TEN),
        ])
        assert strategy._top_card_tricks(rules, Suit.HEARTS) == 2

    def test_the_same_rule_reads_the_all_trump_ladder(self, strategy):
        rules = rules_for(TrumpVariant.ALL_TRUMP)
        strategy._player.hand = Hand([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.NINE),
        ])
        assert strategy._top_card_tricks(rules, Suit.HEARTS) == 2
        strategy._player.hand = Hand([
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.TEN),
        ])
        assert strategy._top_card_tricks(rules, Suit.HEARTS) == 0

    def test_the_same_rule_reads_the_no_trump_ladder(self, strategy):
        rules = rules_for(TrumpVariant.NO_TRUMP)
        strategy._player.hand = Hand([
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.TEN),
        ])
        assert strategy._top_card_tricks(rules, Suit.HEARTS) == 2
        strategy._player.hand = Hand([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.NINE),
        ])
        assert strategy._top_card_tricks(rules, Suit.HEARTS) == 0

    def test_an_empty_suit_is_no_tricks(self, strategy):
        strategy._player.hand = Hand([Card(Suit.HEARTS, Rank.ACE)])
        assert strategy._top_card_tricks(
            rules_for(Suit.SPADES), Suit.SPADES
        ) == 0


# ---------------------------------------------------------------------------
# The honours table: one table serving both suitless modes
# ---------------------------------------------------------------------------


def _extended(bids=()):
    """An auction at a table that has switched the extended modes on."""
    return Auction(tuple(bids), rules=RuleConfig(extended_trump_choices=True))


def _seated_player():
    """A North player wired into a North-South team."""
    player = AiPlayer("TestBot", Position.NORTH)
    partner = AiPlayer("Partner", Position.SOUTH)
    team = Team("North-South", [player, partner])
    player.team = team
    partner.team = team
    return player


def _hand(*cards):
    return Hand(list(cards))


def _masters(rank, suits):
    """One card of ``rank`` in each of ``suits``."""
    return [Card(suit, rank) for suit in suits]


class TestExtendedModesAreOnlyEvaluatedWhenBookable:
    """A classic table offers four suits and nothing else."""

    def test_the_default_table_evaluates_exactly_the_four_suits(self):
        player = _seated_player()
        player.hand = _hand(*_masters(Rank.JACK, list(Suit)))
        evaluations = player.bidding._evaluate_modes(RuleConfig())
        assert set(evaluations) == set(Suit)

    def test_an_extended_table_evaluates_all_six_trump_choices(self):
        player = _seated_player()
        player.hand = _hand(*_masters(Rank.JACK, list(Suit)))
        evaluations = player.bidding._evaluate_modes(
            RuleConfig(extended_trump_choices=True)
        )
        assert set(evaluations) == set(CONTRACT_SUITS)

    def test_the_default_table_never_names_a_suitless_mode(self):
        """The regression guard behind every pre-existing bidding test."""
        player = _seated_player()
        player.hand = _hand(*_masters(Rank.JACK, list(Suit)))
        bid = player.choose_bid(Auction()).bid
        if isinstance(bid, ContractBid):
            assert bid.suit in set(Suit)


class TestHonoursTableAnchors:
    """The house convention's three anchors, in both suitless modes.

    80 = two masters, 90 = three, 100 = four — two Jacks at all trump,
    two aces at no trump. One table serves both because the difference
    between them is carried entirely by the ladder ``rules_for(mode)``
    hands back.
    """

    #: Filler that tops no ladder in either regime: 8s and 7s are the
    #: bottom two rungs of both the plain and the trump ladder.
    _FILLER = (Rank.EIGHT, Rank.SEVEN)

    def _hand_with(self, master_rank, master_suits, filler_suits):
        cards = _masters(master_rank, master_suits)
        for suit in filler_suits:
            for rank in self._FILLER:
                cards.append(Card(suit, rank))
        return Hand(cards[:8])

    @pytest.mark.parametrize(
        "mode, master_rank, expected",
        [
            (TrumpVariant.ALL_TRUMP, Rank.JACK, 80),
            (TrumpVariant.NO_TRUMP, Rank.ACE, 80),
        ],
    )
    def test_two_masters_reach_eighty(self, mode, master_rank, expected):
        player = _seated_player()
        player.hand = self._hand_with(
            master_rank,
            [Suit.SPADES, Suit.HEARTS],
            [Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES],
        )
        evaluation = player.bidding._evaluate_mode(
            mode, RuleConfig(extended_trump_choices=True)
        )
        assert evaluation["contract"] == expected

    @pytest.mark.parametrize(
        "mode, master_rank",
        [
            (TrumpVariant.ALL_TRUMP, Rank.JACK),
            (TrumpVariant.NO_TRUMP, Rank.ACE),
        ],
    )
    def test_three_masters_reach_ninety(self, mode, master_rank):
        player = _seated_player()
        player.hand = self._hand_with(
            master_rank,
            [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS],
            [Suit.CLUBS, Suit.SPADES, Suit.HEARTS],
        )
        evaluation = player.bidding._evaluate_mode(
            mode, RuleConfig(extended_trump_choices=True)
        )
        assert evaluation["contract"] == 90

    @pytest.mark.parametrize(
        "mode, master_rank",
        [
            (TrumpVariant.ALL_TRUMP, Rank.JACK),
            (TrumpVariant.NO_TRUMP, Rank.ACE),
        ],
    )
    def test_four_masters_reach_a_hundred(self, mode, master_rank):
        player = _seated_player()
        player.hand = self._hand_with(
            master_rank, list(Suit), [Suit.SPADES, Suit.HEARTS]
        )
        evaluation = player.bidding._evaluate_mode(
            mode, RuleConfig(extended_trump_choices=True)
        )
        assert evaluation["contract"] == 100


class TestComplementsClimb:
    """A complement is the card whose only superior is in the same hand.

    The 9 under its own Jack at all trump, the 10 under its own ace at no
    trump. Both are certain tricks the moment the hand holds them, which
    is why the ladder prices them the same as a master.
    """

    @pytest.mark.parametrize(
        "mode, master_rank, complement_rank",
        [
            (TrumpVariant.ALL_TRUMP, Rank.JACK, Rank.NINE),
            (TrumpVariant.NO_TRUMP, Rank.ACE, Rank.TEN),
        ],
    )
    def test_two_masters_plus_their_two_complements_reach_a_hundred(
        self, mode, master_rank, complement_rank
    ):
        player = _seated_player()
        player.hand = Hand([
            Card(Suit.SPADES, master_rank),
            Card(Suit.SPADES, complement_rank),
            Card(Suit.HEARTS, master_rank),
            Card(Suit.HEARTS, complement_rank),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
        ])
        evaluation = player.bidding._evaluate_mode(
            mode, RuleConfig(extended_trump_choices=True)
        )
        assert evaluation["contract"] == 100

    @pytest.mark.parametrize(
        "mode, master_rank, complement_rank",
        [
            (TrumpVariant.ALL_TRUMP, Rank.JACK, Rank.NINE),
            (TrumpVariant.NO_TRUMP, Rank.ACE, Rank.TEN),
        ],
    )
    def test_an_unbacked_second_card_is_not_a_complement(
        self, mode, master_rank, complement_rank
    ):
        """The masters are elsewhere, so the second cards can be beaten."""
        player = _seated_player()
        player.hand = Hand([
            Card(Suit.SPADES, master_rank),
            Card(Suit.HEARTS, master_rank),
            Card(Suit.DIAMONDS, complement_rank),
            Card(Suit.CLUBS, complement_rank),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
        ])
        masters, complements = player.bidding._honours(rules_for(mode))
        assert (masters, complements) == (2, 0)


class TestTheOpeningFloor:
    """A hand cannot open on complements alone: ``masters >= 2``."""

    @pytest.mark.parametrize(
        "mode, master_rank, complement_rank",
        [
            (TrumpVariant.ALL_TRUMP, Rank.JACK, Rank.NINE),
            (TrumpVariant.NO_TRUMP, Rank.ACE, Rank.TEN),
        ],
    )
    def test_one_master_and_its_complement_is_no_bid(
        self, mode, master_rank, complement_rank
    ):
        """Two honours, one master — below the rung two Jacks reserve."""
        player = _seated_player()
        player.hand = Hand([
            Card(Suit.SPADES, master_rank),
            Card(Suit.SPADES, complement_rank),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
        ])
        evaluation = player.bidding._evaluate_mode(
            mode, RuleConfig(extended_trump_choices=True)
        )
        assert evaluation["contract"] == 0


class TestTheAceTrapAtAllTrump:
    """The sharpest test of the task: eight cards, two opposite readings."""

    def test_four_aces_and_four_tens_name_no_trump_not_all_trump(self):
        """At no trump 4 masters + 4 complements; at all trump neither."""
        player = _seated_player()
        player.hand = Hand(
            _masters(Rank.ACE, list(Suit)) + _masters(Rank.TEN, list(Suit))
        )
        bid = player.choose_bid(_extended()).bid
        assert isinstance(bid, ContractBid)
        assert bid.suit is TrumpVariant.NO_TRUMP

    def test_the_same_hand_counts_no_honours_at_all_trump(self):
        player = _seated_player()
        player.hand = Hand(
            _masters(Rank.ACE, list(Suit)) + _masters(Rank.TEN, list(Suit))
        )
        assert player.bidding._honours(
            rules_for(TrumpVariant.ALL_TRUMP)
        ) == (0, 0)

    def test_and_eight_honours_at_no_trump(self):
        player = _seated_player()
        player.hand = Hand(
            _masters(Rank.ACE, list(Suit)) + _masters(Rank.TEN, list(Suit))
        )
        assert player.bidding._honours(
            rules_for(TrumpVariant.NO_TRUMP)
        ) == (4, 4)

    def test_the_mirror_hand_names_all_trump(self):
        """Four Jacks and four 9s: 8 honours at all trump, 0 at no trump."""
        player = _seated_player()
        player.hand = Hand(
            _masters(Rank.JACK, list(Suit)) + _masters(Rank.NINE, list(Suit))
        )
        bid = player.choose_bid(_extended()).bid
        assert isinstance(bid, ContractBid)
        assert bid.suit is TrumpVariant.ALL_TRUMP


class TestNoModeIsBidPastItsLadderTop:
    """``ladder_top`` is the ceiling, never re-derived."""

    @pytest.mark.parametrize(
        "belote, ceiling",
        [
            (AllTrumpBelote.NONE, 160),
            (AllTrumpBelote.SINGLE, 180),
            (AllTrumpBelote.FOUR, 240),
        ],
    )
    def test_all_trump_stops_at_its_regimes_ceiling(self, belote, ceiling):
        player = _seated_player()
        rules = RuleConfig(
            extended_trump_choices=True, all_trump_belote=belote
        )
        # The strongest all-trump hand there is, K + Q pairs included.
        player.hand = Hand([
            Card(suit, rank)
            for suit in Suit
            for rank in (Rank.JACK, Rank.NINE)
        ])
        evaluation = player.bidding._evaluate_mode(
            TrumpVariant.ALL_TRUMP, rules
        )
        assert evaluation["contract"] <= ceiling

    def test_a_belote_rich_hand_stops_at_the_ceiling_too(self):
        player = _seated_player()
        rules = RuleConfig(
            extended_trump_choices=True,
            all_trump_belote=AllTrumpBelote.FOUR,
        )
        player.hand = Hand([
            Card(suit, rank)
            for suit in Suit
            for rank in (Rank.KING, Rank.QUEEN)
        ])
        evaluation = player.bidding._evaluate_mode(
            TrumpVariant.ALL_TRUMP, rules
        )
        assert evaluation["contract"] <= 240

    def test_no_trump_stops_at_one_sixty(self):
        player = _seated_player()
        player.hand = Hand(
            _masters(Rank.ACE, list(Suit)) + _masters(Rank.TEN, list(Suit))
        )
        evaluation = player.bidding._evaluate_mode(
            TrumpVariant.NO_TRUMP, RuleConfig(extended_trump_choices=True)
        )
        assert evaluation["contract"] <= 160


class TestBeloteLiftsAllTrump:
    """The only thing that can lift a bid past the honours ladder's 140."""

    def _kq_rich(self):
        """Two masters, plus a K + Q pair in every suit."""
        return Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.SPADES, Rank.QUEEN),
            Card(Suit.HEARTS, Rank.KING),
            Card(Suit.HEARTS, Rank.QUEEN),
            Card(Suit.DIAMONDS, Rank.KING),
            Card(Suit.DIAMONDS, Rank.QUEEN),
        ])

    def _contract_under(self, belote):
        player = _seated_player()
        player.hand = self._kq_rich()
        return player.bidding._evaluate_mode(
            TrumpVariant.ALL_TRUMP,
            RuleConfig(
                extended_trump_choices=True, all_trump_belote=belote
            ),
        )["contract"]

    def test_four_reaches_higher_than_single(self):
        assert self._contract_under(
            AllTrumpBelote.FOUR
        ) > self._contract_under(AllTrumpBelote.SINGLE)

    def test_single_adds_no_more_than_twenty_over_none(self):
        single = self._contract_under(AllTrumpBelote.SINGLE)
        none = self._contract_under(AllTrumpBelote.NONE)
        assert 0 <= single - none <= 20

    def test_the_add_on_is_inert_at_no_trump(self):
        """``NoTrumpRules.belote_suits`` is empty — nothing to credit."""
        player = _seated_player()
        player.hand = self._kq_rich()
        contracts = {
            belote: player.bidding._evaluate_mode(
                TrumpVariant.NO_TRUMP,
                RuleConfig(
                    extended_trump_choices=True, all_trump_belote=belote
                ),
            )["contract"]
            for belote in AllTrumpBelote
        }
        assert len(set(contracts.values())) == 1


class TestSupportComplementIsModeAware:
    """``_support_partner_bid``'s +10 reads the mode's own ladder."""

    def test_a_jack_supports_a_suit_contract(self):
        player = _seated_player()
        partner = player.team.players[1]
        opponent = AiPlayer("Opponent", Position.WEST)
        opponent.team = Team("East-West", [opponent, AiPlayer("OP", Position.EAST)])
        player.hand = Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT),
            Card(Suit.SPADES, Rank.SEVEN),
        ])
        auction = Auction((
            ContractBid(partner, 80, Suit.SPADES),
            PassBid(opponent),
        ))
        bid = player.choose_bid(auction).bid
        assert isinstance(bid, ContractBid)
        assert bid.get_numeric_value() == 90

    def test_an_all_trump_honour_supports_an_all_trump_contract(self):
        """The Jack of any suit is an honour once every suit is trump."""
        player = _seated_player()
        partner = player.team.players[1]
        opponent = AiPlayer("Opponent", Position.WEST)
        opponent.team = Team("East-West", [opponent, AiPlayer("OP", Position.EAST)])
        player.hand = Hand([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.SEVEN),
        ])
        auction = _extended((
            ContractBid(partner, 80, TrumpVariant.ALL_TRUMP),
            PassBid(opponent),
        ))
        bid = player.choose_bid(auction).bid
        assert isinstance(bid, ContractBid)
        assert bid.get_numeric_value() == 90

    def test_a_plain_jack_is_no_honour_at_no_trump(self):
        """A Jack tops nothing on the plain ladder — nothing to add."""
        player = _seated_player()
        partner = player.team.players[1]
        opponent = AiPlayer("Opponent", Position.WEST)
        opponent.team = Team("East-West", [opponent, AiPlayer("OP", Position.EAST)])
        player.hand = Hand([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.SEVEN),
        ])
        auction = _extended((
            ContractBid(partner, 80, TrumpVariant.NO_TRUMP),
            PassBid(opponent),
        ))
        bid = player.choose_bid(auction).bid
        assert isinstance(bid, PassBid)


class TestTheBidRationaleCitesTheTable:
    """A bid says which modes were on the table and what capped it."""

    def test_an_extended_open_cites_the_knobs_it_consulted(self):
        player = _seated_player()
        player.hand = Hand(
            _masters(Rank.JACK, list(Suit)) + _masters(Rank.NINE, list(Suit))
        )
        rationale = player.choose_bid(_extended()).rationale
        knobs = {c.knob for c in rationale.citations}
        assert "extended_trump_choices" in knobs
        assert "all_trump_belote" in knobs

    def test_the_runners_up_are_listed(self):
        """A hand that scores in two modes names the one it did not bid.

        ♠ A K Q J 10 plus the other three aces reaches 140 in spades on
        the suit table and 110 at no trump on the honours table (four ace
        masters and the ♠10 backed by its own ace).
        """
        player = _seated_player()
        player.hand = Hand([
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.SPADES, Rank.QUEEN),
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.TEN),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
        ])
        decision = player.choose_bid(_extended())
        assert decision.bid.suit is Suit.SPADES
        assert any(
            str(TrumpVariant.NO_TRUMP) in entry
            for entry in decision.rationale.considered
        )
