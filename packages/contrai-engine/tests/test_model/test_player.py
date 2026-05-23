# Unit tests for the Player classes (Player, HumanPlayer, AiPlayer)

import pytest
from contrai_engine.model.player import HumanPlayer, AiPlayer
from contrai_core import Hand, ContractBid, Contract
from contrai_core.card import Card
from contrai_core.team import Team
from contrai_core.types import Suit, Rank


def _contract(player, value, suit):
    """Build a real Contract for the AiPlayer trick-taking tests.

    The original tests passed a ``(player, value, suit)`` tuple, but the
    engine threads the actual ``Contract`` object from ``Round`` into
    ``AiPlayer.choose_card``. This helper keeps the test bodies readable
    while matching the production type.
    """
    return Contract(ContractBid(player, value, suit))


class TestPlayer:
    """Test the abstract Player class"""

    def test_player_creation(self):
        """Test creating a human player"""
        player = HumanPlayer("Alice", "North")
        assert player.name == "Alice"
        assert player.position == "North"
        assert len(player.hand) == 0
        assert player.team is None
        assert player.is_human is True

    def test_ai_player_creation(self):
        """Test creating an AI player"""
        player = AiPlayer("Bot", "South")
        assert player.name == "Bot"
        assert player.position == "South"
        assert len(player.hand) == 0
        assert player.team is None
        assert player.is_human is False


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
        evaluations = ai_player._evaluate_suits()

        # All suits should have low or zero contract values
        for suit, eval_data in evaluations.items():
            assert eval_data['contract'] == 0
            assert eval_data['estimated_tricks'] == 0
            assert eval_data['has_belote'] is False

    def test_evaluate_suits_correct_hand(self, ai_player, sample_cards_correct_hearts):
        """Test suit evaluation with a correct hand"""
        ai_player.hand = sample_cards_correct_hearts
        evaluations = ai_player._evaluate_suits()

        hearts_eval = evaluations[Suit.HEARTS]
        assert hearts_eval['contract'] == 80  # Should be able to bid 130
        assert hearts_eval['trump_count'] == 3
        assert hearts_eval['estimated_tricks'] == 4
        assert hearts_eval['external_aces'] == 1

    def test_evaluate_suits_strong_spades(self, ai_player, sample_cards_strong_spades):
        """Test suit evaluation with a strong spades hand"""
        ai_player.hand = sample_cards_strong_spades
        evaluations = ai_player._evaluate_suits()

        spades_eval = evaluations[Suit.SPADES]
        assert spades_eval['contract'] == 130  # Should be able to bid 130
        assert spades_eval['trump_count'] == 4
        assert spades_eval['estimated_tricks'] == 7
        assert spades_eval['external_aces'] == 3

    def test_evaluate_suits_belote(self, ai_player, sample_cards_belote_spades):
        """Test suit evaluation with belote"""
        ai_player.hand = sample_cards_belote_spades
        evaluations = ai_player._evaluate_suits()

        spades_eval = evaluations[Suit.SPADES]
        assert spades_eval['has_belote'] is True
        assert spades_eval['contract'] == 140

    def test_estimate_tricks(self, ai_player, sample_cards_strong_spades):
        """Test trick estimation"""
        ai_player.hand = sample_cards_strong_spades
        tricks = ai_player._estimate_tricks(Suit.SPADES)

        # Strong spades hand with 3 external aces should estimate 7 tricks
        assert tricks == 7

    def test_evaluate_trump_tricks(self, ai_player, sample_cards_strong_spades):
        """Test trump tricks evaluation"""
        ai_player.hand = sample_cards_strong_spades
        expected_tricks = ai_player._evaluate_trump_tricks(Suit.SPADES)

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

        last_bid = ai_player._get_last_bid(current_bids)
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

        partner_bid = ai_player._get_partner_bid(current_bids)
        assert partner_bid == (80, Suit.SPADES)

    def test_choose_bid_pass_weak_hand(self, ai_player, sample_cards_weak):
        """Test that AI passes with weak hand"""
        ai_player.hand = sample_cards_weak
        bid = ai_player.choose_bid([])
        assert bid == 'Pass'

    def test_choose_bid_initial_bid_strong_hand(self, ai_player, sample_cards_strong_spades):
        """Test initial bid with strong hand"""
        ai_player.hand = sample_cards_strong_spades
        bid = ai_player.choose_bid([])

        assert isinstance(bid, tuple)
        value, suit = bid
        assert value == 130
        assert suit == Suit.SPADES

    def test_choose_bid_overbid_opponent(self, ai_player, ai_opponent_player, sample_cards_strong_spades):
        """Test overbidding opponent"""
        ai_player.hand = sample_cards_strong_spades

        current_bids = [(ai_opponent_player, (90, Suit.HEARTS))]
        bid = ai_player.choose_bid(current_bids)

        assert isinstance(bid, tuple)
        value, suit = bid
        assert value > 90
        assert suit == Suit.SPADES

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
        current_bids = [(partner, (80, Suit.SPADES)), (ai_opponent_player,'Pass')]
        bid = ai_player.choose_bid(current_bids)

        # Should support with higher bid due to 3 external aces + trump complement
        assert isinstance(bid, tuple)
        value, suit = bid
        assert value >= 100  # 80 + 20 (2 aces) + 10 (trump complement)
        assert suit == Suit.SPADES

    def test_choose_bid_cant_overbid_partner(self, ai_player, ai_opponent_player, sample_cards_weak):
        """Test that AI doesn't overbid partner when it can't"""
        ai_player.hand = sample_cards_weak

        # Partner bids high
        partner = ai_player.team.players[1]
        current_bids = [(partner, (140, Suit.SPADES)), (ai_opponent_player,'Pass')]
        bid = ai_player.choose_bid(current_bids)

        assert bid == 'Pass'

    # --- Capot bidding -----------------------------------------------------
    # _estimate_tricks is capped at 8 (player.py: `min(tricks, 8)`), so a hand
    # holding 5 trumps (J + 9 + A + K + Q) plus all three external aces
    # triggers the Capot row in BIDDING_TABLE.

    @pytest.fixture
    def sample_cards_capot_spades(self):
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

    def test_evaluate_suit_capot_qualifies(self, ai_player, sample_cards_capot_spades):
        """A hand estimated at 8 tricks resolves to the Capot row (250)."""
        ai_player.hand = sample_cards_capot_spades
        evaluations = ai_player._evaluate_suits()
        assert evaluations[Suit.SPADES]['contract'] == 250
        assert evaluations[Suit.SPADES]['estimated_tricks'] == 8

    def test_choose_bid_capot_strong_hand(self, ai_player, sample_cards_capot_spades):
        """choose_bid returns ('Capot', suit) — the wire format expected by Round."""
        ai_player.hand = sample_cards_capot_spades
        bid = ai_player.choose_bid([])
        assert bid == ('Capot', Suit.SPADES)

    def test_can_overbid_partner_handles_capot_value(self, ai_player, sample_cards_weak):
        """Normalising 'Capot' → 250 in _can_overbid_partner avoids TypeError."""
        ai_player.hand = sample_cards_weak
        # Should not raise; nothing in our weak hand beats Capot.
        assert ai_player._can_overbid_partner(
            ('Capot', Suit.SPADES), ai_player._evaluate_suits()
        ) is False

    def test_should_double_handles_capot_value(self, ai_player, sample_cards_weak):
        """_should_double must not TypeError when value is 'Capot'.

        The heuristic itself (`strength > 162 - value`) is permissive against
        Capot because 162 - 250 is negative; we only assert the boolean
        contract here. Tuning that heuristic is a separate concern.
        """
        ai_player.hand = sample_cards_weak
        result = ai_player._should_double(('Capot', Suit.SPADES))
        assert isinstance(result, bool)

    def test_choose_bid_passes_when_partner_announced_capot(
        self, ai_player, ai_opponent_player, sample_cards_strong_spades
    ):
        """A strong-but-not-Capot AI passes cleanly when partner announces Capot."""
        ai_player.hand = sample_cards_strong_spades  # estimates 7 tricks, max 130
        partner = ai_player.team.players[1]
        current_bids = [(partner, ('Capot', Suit.SPADES))]
        # Must not TypeError on the 130-vs-'Capot' comparison.
        bid = ai_player.choose_bid(current_bids)
        assert bid == 'Pass'

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

        evaluations = ai_player._evaluate_suits()

        # Both Spades and Hearts should be good, but Spades should be preferred
        candidate_suits = [Suit.SPADES, Suit.HEARTS]
        chosen_suit = ai_player._choose_best_suit(candidate_suits, evaluations)
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

        evaluations = ai_player._evaluate_suits()

        # Hearts should be preferred due to belote
        candidate_suits = [Suit.SPADES, Suit.HEARTS]
        chosen_suit = ai_player._choose_best_suit(candidate_suits, evaluations)
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
        current_bids = [(opponent1, (120, Suit.SPADES))]
        bid = player.choose_bid(current_bids)

        assert bid == 'Double'

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
        current_bids = [(opponent1, (100, Suit.HEARTS))]
        bid = player.choose_bid(current_bids)

        assert bid == 'Pass'

class TestAiPlayerTrickTaking:
    """Test AI player trick taking strategy"""

    @pytest.fixture
    def ai_player_with_tracking(self):
        """Create an AI player with initialized card tracking"""
        player = AiPlayer("TestBot", "North")
        # Create a mock team
        partner = AiPlayer("Partner", "South")
        team = Team("North-South", [player, partner])
        player.team = team
        partner.team = team
        player.initialize_card_tracking()
        return player

    @pytest.fixture
    def ai_player_opponent(self):
        """Create an opponent AI player and team"""
        opponent1 = AiPlayer("Opponent1", "West")
        opponent2 = AiPlayer("Opponent2", "East")
        opponent_team = Team("East-West", [opponent1, opponent2])
        opponent1.team = opponent_team
        opponent2.team = opponent_team
        return opponent1

    @pytest.fixture
    def mock_trick(self):
        """Create a mock trick object.

        Mirrors the subset of the real Trick API that AiPlayer consumes:
        ``__len__`` (so empty-check works), ``get_led_suit`` and
        ``get_cards`` (cards-only convenience for tests that don't care
        about players), and ``get_plays`` (used by code paths that need
        player identity — synthetic plays pair each card with ``None``,
        which is sufficient because the only test exercising that path
        mocks the methods that look at players).
        """
        class MockTrick:
            def __init__(self):
                self.cards = []
                self.leader_position = 0
                self.trump_suit = None

            def __len__(self):
                return len(self.cards)

            def get_cards(self):
                return list(self.cards)

            def get_led_suit(self):
                return self.cards[0].suit if self.cards else None

            def get_plays(self):
                return [(None, card) for card in self.cards]
        return MockTrick()

    @pytest.fixture
    def sample_hand_mixed(self):
        """Create a mixed hand for testing"""
        return Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.KING),
            Card(Suit.HEARTS, Rank.TEN),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.EIGHT),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.QUEEN),
        ])

    def test_play_first_card_opening_round(self, ai_player_with_tracking, mock_trick, sample_hand_mixed):
        """Test playing the very first card of the round"""
        ai_player_with_tracking.hand = sample_hand_mixed
        contract = _contract(ai_player_with_tracking, 80, Suit.SPADES)

        # Should play the strongest trump (Jack of Spades)
        result = ai_player_with_tracking.choose_card(mock_trick, contract, sample_hand_mixed)
        assert result.suit == Suit.SPADES
        assert result.rank == Rank.JACK

    def test_play_first_card_opponents_contract(self, ai_player_with_tracking, ai_player_opponent, mock_trick, sample_hand_mixed):
        """Test playing first card when opponents have contract"""
        ai_player_with_tracking.hand = sample_hand_mixed
        contract = _contract(ai_player_opponent, 100, Suit.HEARTS)

        # Should play ace from the shortest suit (Diamonds or Spades)
        result = ai_player_with_tracking.choose_card(mock_trick, contract, sample_hand_mixed)
        assert result.rank == Rank.ACE
        assert result.suit == Suit.SPADES

    def test_play_leading_card_with_trump_remaining(self, ai_player_with_tracking, mock_trick):
        """Test leading subsequent tricks when opponents might have trump"""
        ai_player_with_tracking.hand = Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.EIGHT)
        ])
        contract = _contract(ai_player_with_tracking, 100, Suit.SPADES)

        # Mark some cards as fallen to simulate non-opening trick
        ai_player_with_tracking._fallen_cards[Suit.HEARTS].add(Rank.KING)

        # Mock opponents might have trump
        ai_player_with_tracking._opponents_might_have_trump = lambda s: True

        result = ai_player_with_tracking.choose_card(mock_trick, contract, ai_player_with_tracking.hand)
        assert result.suit == Suit.SPADES  # Should play trump

    def test_play_leading_card_no_trump_remaining(self, ai_player_with_tracking, mock_trick):
        """Test leading when opponents have no trump left"""
        ai_player_with_tracking.hand = Hand([
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.SEVEN)
        ])
        contract = _contract(ai_player_with_tracking, 100, Suit.SPADES)

        # Mark some cards as fallen
        ai_player_with_tracking._fallen_cards[Suit.HEARTS].add(Rank.KING)

        # Mock opponents have no trump
        ai_player_with_tracking._opponents_might_have_trump = lambda s: False

        result = ai_player_with_tracking.choose_card(mock_trick, contract, ai_player_with_tracking.hand)
        assert result.rank == Rank.ACE  # Should play ace

    def test_follow_suit_when_team_winning(self, ai_player_with_tracking, mock_trick):
        """Test following suit when team is winning"""
        ai_player_with_tracking.hand = Hand([
            Card(Suit.HEARTS, Rank.KING),
            Card(Suit.HEARTS, Rank.TEN),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.SPADES, Rank.ACE)
        ])

        # Set up trick where partner is winning
        mock_trick.cards = [Card(Suit.HEARTS, Rank.QUEEN), Card(Suit.HEARTS, Rank.SEVEN)]
        mock_trick.trump_suit = Suit.SPADES

        # Mock team is winning
        ai_player_with_tracking._is_team_winning_trick = lambda t: True

        playable_cards = [Card(Suit.HEARTS, Rank.KING), Card(Suit.HEARTS, Rank.TEN), Card(Suit.HEARTS, Rank.EIGHT)]
        result = ai_player_with_tracking.choose_card(mock_trick, _contract(ai_player_with_tracking, 100, Suit.SPADES), playable_cards)

        # Should play the highest point card (King or 10)
        assert result.suit == Suit.HEARTS
        assert result.rank == Rank.TEN

    def test_follow_suit_when_team_losing_can_beat(self, ai_player_with_tracking, mock_trick):
        """Test following suit when team is losing but can beat current card"""
        ai_player_with_tracking.hand = Hand([
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.SPADES, Rank.JACK)
        ])

        # Set up trick where opponent is winning with King
        mock_trick.cards = [Card(Suit.HEARTS, Rank.KING)]
        mock_trick.trump_suit = Suit.SPADES

        # Mock team is losing
        ai_player_with_tracking._is_team_winning_trick = lambda t: False

        playable_cards = [Card(Suit.HEARTS, Rank.ACE), Card(Suit.HEARTS, Rank.EIGHT)]
        result = ai_player_with_tracking.choose_card(mock_trick, _contract(ai_player_with_tracking, 100, Suit.SPADES), playable_cards)

        # Should play Ace to beat King
        assert result.rank == Rank.ACE
        assert result.suit == Suit.HEARTS

    def test_follow_suit_when_team_losing_cannot_beat(self, ai_player_with_tracking, mock_trick):
        """Test following suit when team is losing and cannot beat"""
        ai_player_with_tracking.hand = Hand([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.SPADES, Rank.JACK)
        ])

        # Set up trick where opponent is winning with Ace
        mock_trick.cards = [Card(Suit.HEARTS, Rank.ACE)]
        mock_trick.trump_suit = Suit.SPADES

        # Mock team is losing
        ai_player_with_tracking._is_team_winning_trick = lambda t: False

        playable_cards = [Card(Suit.HEARTS, Rank.JACK), Card(Suit.HEARTS, Rank.EIGHT)]
        result = ai_player_with_tracking.choose_card(mock_trick, _contract(ai_player_with_tracking, 100, Suit.SPADES), playable_cards)

        # Should play the lowest card (8)
        assert result.rank == Rank.EIGHT
        assert result.suit == Suit.HEARTS

    def test_trump_when_cannot_follow_suit(self, ai_player_with_tracking, mock_trick):
        """Test trumping when cannot follow suit and team is losing"""
        ai_player_with_tracking.hand = Hand([
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.DIAMONDS, Rank.EIGHT)
        ])

        # Set up trick with Hearts led
        mock_trick.cards = [Card(Suit.HEARTS, Rank.KING)]
        mock_trick.trump_suit = Suit.SPADES

        # Mock team is losing and can trump win
        ai_player_with_tracking._is_team_winning_trick = lambda t: False
        ai_player_with_tracking._can_trump_win = lambda card, trick, trump: card.rank == Rank.JACK

        playable_cards = [Card(Suit.SPADES, Rank.JACK), Card(Suit.SPADES, Rank.NINE), Card(Suit.DIAMONDS, Rank.EIGHT)]
        result = ai_player_with_tracking.choose_card(mock_trick, _contract(ai_player_with_tracking, 100, Suit.SPADES), playable_cards)

        # Should trump with Jack (lowest winning trump)
        assert result.suit == Suit.SPADES
        assert result.rank == Rank.JACK

    def test_discard_when_cannot_follow_or_trump(self, ai_player_with_tracking, mock_trick):
        """Test discarding when cannot follow suit or trump effectively"""
        ai_player_with_tracking.hand = Hand([
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.QUEEN),
            Card(Suit.CLUBS, Rank.JACK),
            Card(Suit.CLUBS, Rank.TEN)
        ])

        # Set up trick with Hearts led and Spades trump
        mock_trick.cards = [Card(Suit.HEARTS, Rank.KING)]
        mock_trick.trump_suit = Suit.SPADES

        # Mock team is losing, no trump cards, all cards are not masters
        ai_player_with_tracking._is_team_winning_trick = lambda t: False
        ai_player_with_tracking._is_master_card = lambda card, trump: False

        playable_cards = [Card(Suit.DIAMONDS, Rank.SEVEN), Card(Suit.CLUBS, Rank.QUEEN), Card(Suit.CLUBS, Rank.JACK), Card(Suit.CLUBS, Rank.TEN)]
        result = ai_player_with_tracking.choose_card(mock_trick, _contract(ai_player_with_tracking, 100, Suit.SPADES), playable_cards)

        # Should discard lowest from the shortest suit
        assert result.rank == Rank.SEVEN  # Lowest point card
        assert result.suit == Suit.DIAMONDS  # From shorter suit

    def test_card_tracking_initialization(self, ai_player_with_tracking):
        """Test that card tracking is properly initialized"""
        assert hasattr(ai_player_with_tracking, '_fallen_cards')
        assert hasattr(ai_player_with_tracking, '_players_without_trump')
        assert len(ai_player_with_tracking._fallen_cards) == 4
        for suit_cards in ai_player_with_tracking._fallen_cards.values():
            assert isinstance(suit_cards, set)


    def test_update_card_tracking(self, ai_player_with_tracking, ai_player_opponent):
        """Test updating card tracking with played cards"""
        # Test the update_card_tracking method directly
        card = Card(Suit.HEARTS, Rank.KING)
        ai_player_with_tracking.update_card_tracking(card, ai_player_opponent, Suit.HEARTS, Suit.SPADES)

        # Players list without trump should be empty
        assert ai_player_with_tracking._players_without_trump == set()
        assert Rank.KING in ai_player_with_tracking._fallen_cards[Suit.HEARTS]

        # Test trump tracking - player couldn't follow suit and didn't trump
        card2 = Card(Suit.DIAMONDS, Rank.EIGHT)
        ai_player_with_tracking.update_card_tracking(card2, ai_player_opponent.team.players[1], Suit.HEARTS, Suit.SPADES)

        # West should be marked as having no trump (couldn't follow Hearts, didn't trump)
        assert ai_player_opponent.team.players[1] in ai_player_with_tracking._players_without_trump

        # With 2 trump in hand and 2 fallen, opponents might have 4 remaining
        ai_player_with_tracking.hand = Hand([Card(Suit.SPADES, Rank.JACK), Card(Suit.SPADES, Rank.NINE)])
        ai_player_with_tracking._fallen_cards[Suit.SPADES] = {Rank.KING, Rank.QUEEN}
        result = ai_player_with_tracking._opponents_might_have_trump(Suit.SPADES)
        assert result is True

        # Mark more trump cards as fallen
        ai_player_with_tracking._fallen_cards[Suit.SPADES] = {Rank.KING, Rank.QUEEN, Rank.ACE, Rank.TEN, Rank.EIGHT, Rank.SEVEN}

        # With 2 trump in hand and 6 fallen, opponents have 0 remaining
        result = ai_player_with_tracking._opponents_might_have_trump(Suit.SPADES)
        assert result is False

    def test_is_master_card_detection(self, ai_player_with_tracking):
        """Test detection of master cards"""
        # Set up fallen cards
        ai_player_with_tracking._fallen_cards[Suit.HEARTS] = {Rank.ACE, Rank.QUEEN, Rank.EIGHT}

        # Ace should be master now
        ace_hearts = Card(Suit.HEARTS, Rank.TEN)
        result = ai_player_with_tracking._is_master_card(ace_hearts, Suit.SPADES)
        assert result is True

        # 10 should not be master (Ace still out)
        ten_hearts = Card(Suit.HEARTS, Rank.KING)
        result = ai_player_with_tracking._is_master_card(ten_hearts, Suit.SPADES)
        assert result is False

    def test_trump_order_vs_normal_order(self, ai_player_with_tracking):
        """Test that trump and normal card orders are handled correctly"""
        # Normal order: 7, 8, 9, Jack, Queen, King, 10, Ace
        normal_higher = ai_player_with_tracking._get_higher_ranks(Rank.NINE, Suit.HEARTS, Suit.SPADES)
        assert Rank.JACK in normal_higher
        assert Rank.ACE in normal_higher

        # Trump order: 7, 8, Queen, King, 10, Ace, 9, Jack
        trump_higher = ai_player_with_tracking._get_higher_ranks(Rank.NINE, Suit.SPADES, Suit.SPADES)
        assert Rank.JACK in trump_higher
        assert Rank.ACE not in trump_higher  # 9 is higher than Ace in trump

    def test_team_winning_trick_detection(self, ai_player_with_tracking, mock_trick):
        """Test detection of whether team is winning current trick"""
        # Set up mock trick with partner winning
        mock_trick.cards = [Card(Suit.HEARTS, Rank.KING), Card(Suit.HEARTS, Rank.ACE)]
        mock_trick.leader_position = 0

        # Mock partner position and strongest card detection
        ai_player_with_tracking._get_partner_position = lambda: 1  # Partner at position 1
        ai_player_with_tracking._get_strongest_card_position = lambda t, ts: 1  # Position 1 winning

        result = ai_player_with_tracking._is_team_winning_trick(mock_trick)
        assert result is True

        # Change winning position to opponent
        ai_player_with_tracking._get_strongest_card_position = lambda t, ts: 2  # Position 2 winning

        result = ai_player_with_tracking._is_team_winning_trick(mock_trick)
        assert result is False

    def test_strongest_card_in_trick_with_trump(self, ai_player_with_tracking, mock_trick):
        """Test finding strongest card when trump is involved"""
        mock_trick.cards = [
            Card(Suit.HEARTS, Rank.ACE),    # Led suit
            Card(Suit.HEARTS, Rank.KING),   # Following suit
            Card(Suit.SPADES, Rank.EIGHT)       # Trump beats all
        ]

        result = ai_player_with_tracking._get_strongest_card_in_trick(mock_trick, Suit.SPADES)
        assert result.suit == Suit.SPADES
        assert result.rank == Rank.EIGHT

    def test_strongest_card_in_trick_no_trump(self, ai_player_with_tracking, mock_trick):
        """Test finding strongest card when no trump is played"""
        mock_trick.cards = [
            Card(Suit.HEARTS, Rank.KING),   # Led suit
            Card(Suit.HEARTS, Rank.ACE),    # Higher in led suit
            Card(Suit.DIAMONDS, Rank.ACE)   # Different suit, doesn't matter
        ]

        result = ai_player_with_tracking._get_strongest_card_in_trick(mock_trick, Suit.SPADES)
        assert result.suit == Suit.HEARTS
        assert result.rank == Rank.ACE

    def test_can_trump_win_logic(self, ai_player_with_tracking, mock_trick):
        """Test logic for determining if a trump card can win"""
        mock_trick.cards = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.SPADES, Rank.EIGHT)  # Current trump winning
        ]

        # Jack of trump should beat 8 of trump
        trump_jack = Card(Suit.SPADES, Rank.JACK)
        result = ai_player_with_tracking._can_trump_win(trump_jack, mock_trick, Suit.SPADES)
        assert result is True

        # 7 of trump should not beat 8 of trump
        trump_seven = Card(Suit.SPADES, Rank.SEVEN)
        result = ai_player_with_tracking._can_trump_win(trump_seven, mock_trick, Suit.SPADES)
        assert result is False

    def test_is_stronger_card_comparison(self, ai_player_with_tracking):
        """Test card strength comparison logic"""
        # Trump vs non-trump
        trump_card = Card(Suit.SPADES, Rank.SEVEN)
        non_trump = Card(Suit.HEARTS, Rank.ACE)
        result = ai_player_with_tracking._is_stronger_card(trump_card, non_trump, Suit.SPADES)
        assert result is True

        # Same suit comparison
        higher_card = Card(Suit.HEARTS, Rank.ACE)
        lower_card = Card(Suit.HEARTS, Rank.KING)
        result = ai_player_with_tracking._is_stronger_card(higher_card, lower_card, Suit.SPADES)
        assert result is True

        # Trump vs trump
        trump_jack = Card(Suit.SPADES, Rank.JACK)
        trump_nine = Card(Suit.SPADES, Rank.NINE)
        result = ai_player_with_tracking._is_stronger_card(trump_jack, trump_nine, Suit.SPADES)
        assert result is True
