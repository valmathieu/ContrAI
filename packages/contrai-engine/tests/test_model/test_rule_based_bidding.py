# Unit tests for the rule-based AI bidding strategy.
#
# These tests exercise the expert bidding table now living on
# ``RuleBasedBiddingStrategy``. ``AiPlayer.choose_bid`` is a public
# delegator, so high-level ``choose_bid(...)`` calls stay on the player,
# while private helpers and constants are reached through
# ``ai_player.bidding.*`` (the injected strategy object).

import pytest
from contrai_engine.model.player import AiPlayer, wire_to_bid
from contrai_core import (
    Auction,
    ContractBid,
    DoubleBid,
    Hand,
    PassBid,
    RedoubleBid,
    SlamLevel,
)
from contrai_core.card import Card
from contrai_core.team import Team
from contrai_core.types import Suit, Rank


def _auction(bids_with_players=()):
    """Build an :class:`Auction` from a list of ``(player, wire_bid)`` tuples.

    ``AiPlayer.choose_bid`` takes an Auction; the original tests were
    written when it took the legacy ``[(player, wire), …]`` list. This
    helper lifts each (player, wire) entry into the matching :class:`Bid`
    and packs the lot into an Auction so the test bodies stay close to
    their original shape.
    """
    bids = tuple(wire_to_bid(p, w) for p, w in bids_with_players)
    return Auction(bids)


class TestAiPlayerBidding:
    """Test AI player bidding logic"""

    @pytest.fixture
    def ai_player(self):
        """Create an AI player for testing"""
        player = AiPlayer("TestBot", "North")
        # Create a mock team
        partner = AiPlayer("Partner", "South")
        team = Team("North-South", [player, partner])
        player.team = team
        partner.team = team
        return player

    @pytest.fixture
    def ai_opponent_player(self):
        """Create an opponent AI player for testing"""
        opponent = AiPlayer("Opponent", "West")
        opponent_partner = AiPlayer("OpponentPartner", "East")
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

    def test_evaluate_trump_tricks(self, ai_player, sample_cards_strong_spades):
        """Test trump tricks evaluation"""
        ai_player.hand = sample_cards_strong_spades
        expected_tricks = ai_player.bidding._evaluate_trump_tricks(Suit.SPADES)

        # Strong spades hand with Jack + 9 + Ace + King should expect good trick count
        # Jack + 9 = 2 tricks, plus additional tricks from trump length
        assert expected_tricks == 4

    def test_get_last_bid(self, ai_player, ai_opponent_player):
        """Test getting the last contract bid"""
        ai_player_partner = ai_player.team.players[1]
        ai_opponent_player_partner = ai_opponent_player.team.players[1]

        current_bids = [
            (ai_opponent_player, 'Pass'),
            (ai_player_partner, (80, Suit.SPADES)),
            (ai_opponent_player_partner, (90, Suit.HEARTS)),
        ]

        last_bid = ai_player.bidding._get_last_bid(current_bids)
        assert last_bid == (90, Suit.HEARTS)

    def test_get_partner_bid(self, ai_player, ai_opponent_player):
        """Test getting partner's bid"""
        ai_player_partner = ai_player.team.players[1]
        ai_opponent_player_partner = ai_opponent_player.team.players[1]

        current_bids = [
            (ai_opponent_player, 'Pass'),
            (ai_player_partner, (80, Suit.SPADES)),
            (ai_opponent_player_partner, (90, Suit.HEARTS)),
        ]

        partner_bid = ai_player.bidding._get_partner_bid(current_bids)
        assert partner_bid == (80, Suit.SPADES)

    def test_choose_bid_pass_weak_hand(self, ai_player, sample_cards_weak):
        """Test that AI passes with weak hand"""
        ai_player.hand = sample_cards_weak
        bid = ai_player.choose_bid(_auction())
        assert isinstance(bid, PassBid)

    def test_choose_bid_initial_bid_strong_hand(self, ai_player, sample_cards_strong_spades):
        """Test initial bid with strong hand"""
        ai_player.hand = sample_cards_strong_spades
        bid = ai_player.choose_bid(_auction())

        assert isinstance(bid, ContractBid)
        assert bid.value == 130
        assert bid.suit == Suit.SPADES

    def test_choose_bid_overbid_opponent(self, ai_player, ai_opponent_player, sample_cards_strong_spades):
        """Test overbidding opponent"""
        ai_player.hand = sample_cards_strong_spades

        auction = _auction([(ai_opponent_player, (90, Suit.HEARTS))])
        bid = ai_player.choose_bid(auction)

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
            (partner, (80, Suit.SPADES)),
            (ai_opponent_player, 'Pass'),
        ])
        bid = ai_player.choose_bid(auction)

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
            (partner, (140, Suit.SPADES)),
            (ai_opponent_player, 'Pass'),
        ])
        bid = ai_player.choose_bid(auction)

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
        auction = _auction([(partner, (80, Suit.SPADES))])
        bid = ai_player.choose_bid(auction)
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
            (partner, (80, Suit.SPADES)),
            (ai_opponent_player, 'Double'),
        ])
        bid = ai_player.choose_bid(auction)
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
            (ai_opponent_player, (120, Suit.HEARTS)),
            (partner, 'Double'),
        ])
        bid = ai_player.choose_bid(auction)
        assert isinstance(bid, PassBid)

    def test_choose_bid_passes_after_redouble(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """Once the auction is Surcoinched, only Pass remains."""
        ai_player.hand = sample_cards_strong_spades
        partner = ai_player.team.players[1]
        auction = _auction([
            (partner, (110, Suit.SPADES)),
            (ai_opponent_player, 'Double'),
            (partner, 'Redouble'),
        ])
        bid = ai_player.choose_bid(auction)
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
            (partner, (100, Suit.SPADES)),
            (ai_opponent_player, 'Double'),
        ])
        ai_player.bidding._should_redouble = lambda: True  # type: ignore[method-assign]
        bid = ai_player.choose_bid(auction)
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
        auction = _auction([(ai_opponent_player, (140, Suit.SPADES))])
        ai_player.bidding._choose_wire = lambda current_bids: (90, Suit.SPADES)  # type: ignore[method-assign]
        bid = ai_player.choose_bid(auction)
        assert isinstance(bid, PassBid)

    # --- Slam / Solo Slam bidding -----------------------------------------
    # _estimate_tricks is capped at 8 (`min(tricks, 8)`), so a hand holding
    # 5 trumps (J + 9 + A + K + Q) plus all three external aces triggers
    # the Slam-family rows in BIDDING_TABLE. Both Slam (500) and Solo Slam
    # (1000) share the same trick-estimator gate today (tricks_min=8), so
    # the table walks both and stops on the higher one.

    @pytest.fixture
    def sample_cards_slam_spades(self):
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
        bid = ai_player.choose_bid(_auction())
        assert isinstance(bid, ContractBid)
        assert bid.value is SlamLevel.SOLO_SLAM
        assert bid.suit == Suit.SPADES

    def test_can_overbid_partner_handles_slam_value(
        self, ai_player, sample_cards_weak
    ):
        """Normalising SlamLevel.SLAM → 500 in _can_overbid_partner avoids TypeError."""
        ai_player.hand = sample_cards_weak
        # Should not raise; nothing in our weak hand beats Slam.
        assert ai_player.bidding._can_overbid_partner(
            (SlamLevel.SLAM, Suit.SPADES), ai_player.bidding._evaluate_suits()
        ) is False

    def test_can_overbid_partner_handles_solo_slam_value(
        self, ai_player, sample_cards_weak
    ):
        """Normalising SlamLevel.SOLO_SLAM → 1000 in _can_overbid_partner avoids TypeError."""
        ai_player.hand = sample_cards_weak
        assert ai_player.bidding._can_overbid_partner(
            (SlamLevel.SOLO_SLAM, Suit.SPADES), ai_player.bidding._evaluate_suits()
        ) is False

    def test_should_double_handles_slam_value(self, ai_player, sample_cards_weak):
        """_should_double must not TypeError on a SlamLevel value.

        The heuristic itself (``strength > 162 - value``) is permissive
        against Slam-family bids because ``162 - 500`` (and -1000) is
        negative; we only assert the boolean contract here. Tuning the
        heuristic is a separate concern.
        """
        ai_player.hand = sample_cards_weak
        result = ai_player.bidding._should_double((SlamLevel.SLAM, Suit.SPADES))
        assert isinstance(result, bool)
        result = ai_player.bidding._should_double((SlamLevel.SOLO_SLAM, Suit.SPADES))
        assert isinstance(result, bool)

    def test_choose_bid_passes_when_partner_announced_slam(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """A strong-but-not-Slam AI passes cleanly when partner announces Slam."""
        ai_player.hand = sample_cards_strong_spades  # estimates 7 tricks, max 130
        partner = ai_player.team.players[1]
        auction = _auction([(partner, (SlamLevel.SLAM, Suit.SPADES))])
        # Must not TypeError on the 130-vs-Slam comparison.
        bid = ai_player.choose_bid(auction)
        assert isinstance(bid, PassBid)

    def test_choose_bid_passes_when_partner_announced_solo_slam(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """A strong-but-not-Slam AI passes when partner announces Solo Slam."""
        ai_player.hand = sample_cards_strong_spades
        partner = ai_player.team.players[1]
        auction = _auction([(partner, (SlamLevel.SOLO_SLAM, Suit.SPADES))])
        bid = ai_player.choose_bid(auction)
        assert isinstance(bid, PassBid)

    def test_choose_best_suit_preference_order(self, ai_player):
        """Test suit preference order when multiple suits are equal"""
        # Create hand with equal strength in multiple suits
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

        # Both Spades and Hearts should be good, but Spades should be preferred
        candidate_suits = [Suit.SPADES, Suit.HEARTS]
        chosen_suit = ai_player.bidding._choose_best_suit(candidate_suits, evaluations)
        assert chosen_suit == Suit.SPADES

    def test_choose_best_suit_belote_preference(self, ai_player):
        """Test that belote is preferred when contract values are equal"""
        ai_player.hand = Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.KING),
            Card(Suit.HEARTS, Rank.QUEEN),  # Belote in Hearts
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE)
        ])

        evaluations = ai_player.bidding._evaluate_suits()

        # Hearts should be preferred due to belote
        candidate_suits = [Suit.SPADES, Suit.HEARTS]
        chosen_suit = ai_player.bidding._choose_best_suit(candidate_suits, evaluations)
        assert chosen_suit == Suit.HEARTS


class TestAiPlayerDoubling:
    """Test AI player doubling logic"""

    @pytest.fixture
    def ai_players_with_teams(self):
        """Create AI players with team setup"""
        player = AiPlayer("TestBot", "North")
        partner = AiPlayer("Partner", "South")
        team = Team("North-South", [player, partner])
        player.team = team
        partner.team = team

        # Create opponent team
        opponent1 = AiPlayer("Opponent1", "West")
        opponent2 = AiPlayer("Opponent2", "East")
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
        auction = _auction([(opponent1, (120, Suit.SPADES))])
        bid = player.choose_bid(auction)

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
        auction = _auction([(opponent1, (100, Suit.HEARTS))])
        bid = player.choose_bid(auction)

        assert isinstance(bid, PassBid)
