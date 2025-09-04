# Unit tests for the Player classes (Player, HumanPlayer, AiPlayer)

import pytest
from contree.model.player import Player, HumanPlayer, AiPlayer
from contree.model.card import Card
from contree.model.team import Team


class TestPlayer:
    """Test the abstract Player class"""

    def test_player_creation(self):
        """Test creating a human player"""
        player = HumanPlayer("Alice", "North")
        assert player.name == "Alice"
        assert player.position == "North"
        assert player.hand == []
        assert player.team is None
        assert player.is_human is True

    def test_ai_player_creation(self):
        """Test creating an AI player"""
        player = AiPlayer("Bot", "South")
        assert player.name == "Bot"
        assert player.position == "South"
        assert player.hand == []
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
    def sample_cards_weak(self):
        """Create a weak hand for testing"""
        return [
            Card('Spades', '7'),
            Card('Spades', '8'),
            Card('Hearts', '7'),
            Card('Hearts', '8'),
            Card('Diamonds', '7'),
            Card('Diamonds', '8'),
            Card('Clubs', '7'),
            Card('Clubs', '8')
        ]

    @pytest.fixture
    def sample_cards_strong_spades(self):
        """Create a strong spades hand for testing"""
        return [
            Card('Spades', 'Jack'),
            Card('Spades', '9'),
            Card('Spades', 'Ace'),
            Card('Spades', 'King'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Ace'),
            Card('Clubs', 'Ace'),
            Card('Clubs', '10')
        ]

    @pytest.fixture
    def sample_cards_belote_spades(self):
        """Create a hand with belote in spades"""
        return [
            Card('Spades', 'Jack'),
            Card('Spades', 'King'),
            Card('Spades', 'Queen'),
            Card('Spades', '9'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Ace'),
            Card('Clubs', '10'),
            Card('Clubs', '8')
        ]

    def test_evaluate_suits_weak_hand(self, ai_player, sample_cards_weak):
        """Test suit evaluation with a weak hand"""
        ai_player.hand = sample_cards_weak
        evaluations = ai_player._evaluate_suits()

        # All suits should have low or zero contract values
        for suit, eval_data in evaluations.items():
            assert eval_data['contract'] == 0
            assert eval_data['has_belote'] is False

    def test_evaluate_suits_strong_spades(self, ai_player, sample_cards_strong_spades):
        """Test suit evaluation with a strong spades hand"""
        ai_player.hand = sample_cards_strong_spades
        evaluations = ai_player._evaluate_suits()

        spades_eval = evaluations['Spades']
        assert spades_eval['contract'] >= 120  # Should be able to bid at least 120
        assert spades_eval['trump_count'] == 4
        assert spades_eval['external_aces'] == 3

    def test_evaluate_suits_belote(self, ai_player, sample_cards_belote_spades):
        """Test suit evaluation with belote"""
        ai_player.hand = sample_cards_belote_spades
        evaluations = ai_player._evaluate_suits()

        spades_eval = evaluations['Spades']
        assert spades_eval['has_belote'] is True
        assert spades_eval['contract'] >= 140  # Belote should enable higher contracts

    def test_estimate_tricks(self, ai_player, sample_cards_strong_spades):
        """Test trick estimation"""
        ai_player.hand = sample_cards_strong_spades
        tricks = ai_player._estimate_tricks('Spades')

        # Strong spades hand with 3 external aces should estimate good tricks
        assert tricks >= 4
        assert tricks <= 8

    def test_evaluate_trump_strength(self, ai_player, sample_cards_strong_spades):
        """Test trump strength evaluation"""
        ai_player.hand = sample_cards_strong_spades
        strength = ai_player._evaluate_trump_strength('Spades')

        # Jack (20) + 9 (14) + Ace (11) + King (4) = 49 points
        assert strength == 49

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
        assert value >= 120
        assert suit == 'Spades'

    def test_choose_bid_overbid_opponent(self, ai_player, sample_cards_strong_spades):
        """Test overbidding opponent"""
        ai_player.hand = sample_cards_strong_spades

        # Create opponent player not in same team
        opponent = AiPlayer("Opponent", "East")
        opponent_team = Team("East-West", [opponent, AiPlayer("OpponentPartner", "West")])
        opponent.team = opponent_team

        current_bids = [(opponent, (110, 'Hearts'))]
        bid = ai_player.choose_bid(current_bids)

        assert isinstance(bid, tuple)
        value, suit = bid
        assert value > 110
        assert suit == 'Spades'

    def test_choose_bid_support_partner(self, ai_player):
        """Test supporting partner's bid"""
        # Give AI player some external aces to support partner
        ai_player.hand = [
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Ace'),
            Card('Clubs', 'Ace'),
            Card('Spades', 'Jack'),  # Trump complement
            Card('Spades', '8'),
            Card('Hearts', '8'),
            Card('Diamonds', '8'),
            Card('Clubs', '8')
        ]

        # Partner bids 80 in Spades
        partner = ai_player.team.players[1]
        current_bids = [(partner, (80, 'Spades'))]
        bid = ai_player.choose_bid(current_bids)

        # Should support with higher bid due to 3 external aces + trump complement
        assert isinstance(bid, tuple)
        value, suit = bid
        assert value >= 110  # 80 + 30 (3 aces) + 10 (trump complement)
        assert suit == 'Spades'

    def test_choose_bid_cant_overbid_partner(self, ai_player, sample_cards_weak):
        """Test that AI doesn't overbid partner when it can't"""
        ai_player.hand = sample_cards_weak

        # Partner bids high
        partner = ai_player.team.players[1]
        current_bids = [(partner, (140, 'Spades'))]
        bid = ai_player.choose_bid(current_bids)

        assert bid == 'Pass'

    def test_choose_best_suit_preference_order(self, ai_player):
        """Test suit preference order when multiple suits are equal"""
        # Create hand with equal strength in multiple suits
        ai_player.hand = [
            Card('Spades', 'Jack'),
            Card('Spades', '9'),
            Card('Spades', 'Ace'),
            Card('Hearts', 'Jack'),
            Card('Hearts', '9'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Ace'),
            Card('Clubs', 'Ace')
        ]

        evaluations = ai_player._evaluate_suits()

        # Both Spades and Hearts should be good, but Spades should be preferred
        candidate_suits = ['Spades', 'Hearts']
        chosen_suit = ai_player._choose_best_suit(candidate_suits, evaluations)
        assert chosen_suit == 'Spades'

    def test_choose_best_suit_belote_preference(self, ai_player):
        """Test that belote is preferred when contract values are equal"""
        ai_player.hand = [
            Card('Spades', 'Jack'),
            Card('Spades', '9'),
            Card('Spades', 'Ace'),
            Card('Hearts', 'Jack'),
            Card('Hearts', 'King'),
            Card('Hearts', 'Queen'),  # Belote in Hearts
            Card('Diamonds', 'Ace'),
            Card('Clubs', 'Ace')
        ]

        evaluations = ai_player._evaluate_suits()

        # Hearts should be preferred due to belote
        candidate_suits = ['Spades', 'Hearts']
        chosen_suit = ai_player._choose_best_suit(candidate_suits, evaluations)
        assert chosen_suit == 'Hearts'


class TestAiPlayerDoubling:
    """Test AI player doubling logic"""

    @pytest.fixture
    def ai_player_with_team(self):
        """Create AI player with team setup"""
        player = AiPlayer("TestBot", "North")
        partner = AiPlayer("Partner", "South")
        team = Team("North-South", [player, partner])
        player.team = team
        partner.team = team

        # Create opponent team
        opponent1 = AiPlayer("Opponent1", "East")
        opponent2 = AiPlayer("Opponent2", "West")
        opponent_team = Team("East-West", [opponent1, opponent2])
        opponent1.team = opponent_team
        opponent2.team = opponent_team

        return player, partner, opponent1, opponent2

    def test_should_double_with_external_strength(self, ai_player_with_team):
        """Test doubling when having external strength"""
        player, partner, opponent1, opponent2 = ai_player_with_team

        # Give player strong external cards
        player.hand = [
            Card('Hearts', 'Ace'),
            Card('Hearts', '10'),
            Card('Diamonds', 'Ace'),
            Card('Diamonds', '10'),
            Card('Clubs', 'Ace'),
            Card('Clubs', '10'),
            Card('Spades', '8'),
            Card('Spades', '7')
        ]

        # Opponent bids in Spades
        current_bids = [(opponent1, (100, 'Spades'))]
        bid = player.choose_bid(current_bids)

        assert bid == 'Double'

    def test_should_not_double_weak_external(self, ai_player_with_team):
        """Test not doubling when lacking external strength"""
        player, partner, opponent1, opponent2 = ai_player_with_team

        # Give player weak external cards
        player.hand = [
            Card('Hearts', '8'),
            Card('Hearts', '7'),
            Card('Diamonds', '8'),
            Card('Diamonds', '7'),
            Card('Clubs', '8'),
            Card('Clubs', '7'),
            Card('Spades', 'Ace'),
            Card('Spades', 'King')
        ]

        # Opponent bids in Hearts
        current_bids = [(opponent1, (100, 'Hearts'))]
        bid = player.choose_bid(current_bids)

        assert bid == 'Pass'

    def test_should_redouble_strong_trump(self, ai_player_with_team):
        """Test redoubling when having very strong trump"""
        player, partner, opponent1, opponent2 = ai_player_with_team

        # Give player very strong spades
        player.hand = [
            Card('Spades', 'Jack'),
            Card('Spades', '9'),
            Card('Spades', 'Ace'),
            Card('Spades', 'King'),
            Card('Spades', 'Queen'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', '8'),
            Card('Clubs', '7')
        ]

        # Partner bids, then opponent doubles
        current_bids = [
            (partner, (120, 'Spades')),
            (opponent1, 'Double')
        ]
        bid = player.choose_bid(current_bids)

        assert bid == 'Redouble'

    def test_get_last_bid(self, ai_player_with_team):
        """Test getting the last contract bid"""
        player, partner, opponent1, opponent2 = ai_player_with_team

        current_bids = [
            (partner, (80, 'Spades')),
            (opponent1, 'Pass'),
            (opponent2, (90, 'Hearts')),
            (player, 'Pass')
        ]

        last_bid = player._get_last_bid(current_bids)
        assert last_bid == (90, 'Hearts')

    def test_get_partner_bid(self, ai_player_with_team):
        """Test getting partner's bid"""
        player, partner, opponent1, opponent2 = ai_player_with_team

        current_bids = [
            (partner, (80, 'Spades')),
            (opponent1, (90, 'Hearts')),
            (opponent2, 'Pass')
        ]

        partner_bid = player._get_partner_bid(current_bids)
        assert partner_bid == (80, 'Spades')


class TestAiPlayerCardPlay:
    """Test AI player card playing logic"""

    @pytest.fixture
    def ai_player(self):
        """Create an AI player for testing"""
        return AiPlayer("TestBot", "North")

    def test_choose_card_simple(self, ai_player):
        """Test simple card selection"""
        playable_cards = [
            Card('Spades', 'Ace'),
            Card('Hearts', 'King')
        ]

        chosen_card = ai_player.choose_card([], None, playable_cards)
        assert chosen_card in playable_cards

    def test_choose_card_no_playable(self, ai_player):
        """Test card selection when no cards are playable"""
        chosen_card = ai_player.choose_card([], None, [])
        assert chosen_card is None


class TestBiddingTableLogic:
    """Test the bidding table logic specifically"""

    @pytest.fixture
    def ai_player(self):
        """Create an AI player for testing"""
        return AiPlayer("TestBot", "North")

    def test_bidding_table_80_contract(self, ai_player):
        """Test 80 contract requirements"""
        # Hand that meets 80 contract: 3+ trump with Jack or 9, 1+ ace
        ai_player.hand = [
            Card('Spades', 'Jack'),
            Card('Spades', '8'),
            Card('Spades', '7'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', '8'),
            Card('Clubs', '8'),
            Card('Clubs', '7'),
            Card('Hearts', '7')
        ]

        evaluation = ai_player._evaluate_suit_as_trump('Spades')
        assert evaluation['contract'] >= 80

    def test_bidding_table_140_contract(self, ai_player):
        """Test 140 contract requirements"""
        # Hand that meets 140: 4+ trump, 3+ aces, 1+ non-dry ten, belote
        ai_player.hand = [
            Card('Spades', 'Jack'),
            Card('Spades', 'King'),
            Card('Spades', 'Queen'),  # Belote
            Card('Spades', '9'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Ace'),
            Card('Clubs', 'Ace'),
            Card('Clubs', '10')  # Non-dry ten (with other clubs)
        ]

        evaluation = ai_player._evaluate_suit_as_trump('Spades')
        assert evaluation['contract'] >= 140
        assert evaluation['has_belote'] is True

    def test_bidding_table_capot_requirements(self, ai_player):
        """Test very high contract requirements"""
        # Exceptional hand for 160
        ai_player.hand = [
            Card('Spades', 'Jack'),
            Card('Spades', '9'),
            Card('Spades', 'Ace'),
            Card('Spades', 'King'),
            Card('Spades', 'Queen'),  # Belote + strong trump
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Ace'),
            Card('Clubs', 'Ace')  # Many external aces
        ]

        evaluation = ai_player._evaluate_suit_as_trump('Spades')
        assert evaluation['contract'] == 160
        assert evaluation['trump_count'] == 5
        assert evaluation['external_aces'] == 3

