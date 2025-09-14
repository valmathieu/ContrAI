# Unit tests for the Player classes (Player, HumanPlayer, AiPlayer)

import pytest
from contree.model.player import HumanPlayer, AiPlayer
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
    def sample_cards_correct_hearts(self):
        """Create a middle hand for testing"""

        return [
            Card('Hearts', 'Jack'),
            Card('Hearts', 'King'),
            Card('Hearts', '7'),
            Card('Spades', '8'),
            Card('Diamonds', '10'),
            Card('Diamonds', '8'),
            Card('Clubs', 'Ace'),
            Card('Clubs', '10')
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
            Card('Clubs', 'Jack')
        ]

    @pytest.fixture
    def sample_cards_belote_spades(self):
        """Create a hand with belote in spades"""

        return [
            Card('Spades', 'Jack'),
            Card('Spades', 'Ace'),
            Card('Spades', 'King'),
            Card('Spades', 'Queen'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Ace'),
            Card('Clubs', 'Ace'),
            Card('Clubs', '8')
        ]

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

        hearts_eval = evaluations['Hearts']
        assert hearts_eval['contract'] == 80  # Should be able to bid 130
        assert hearts_eval['trump_count'] == 3
        assert hearts_eval['estimated_tricks'] == 4
        assert hearts_eval['external_aces'] == 1

    def test_evaluate_suits_strong_spades(self, ai_player, sample_cards_strong_spades):
        """Test suit evaluation with a strong spades hand"""

        ai_player.hand = sample_cards_strong_spades
        evaluations = ai_player._evaluate_suits()

        spades_eval = evaluations['Spades']
        assert spades_eval['contract'] == 130  # Should be able to bid 130
        assert spades_eval['trump_count'] == 4
        assert spades_eval['estimated_tricks'] == 7
        assert spades_eval['external_aces'] == 3

    def test_evaluate_suits_belote(self, ai_player, sample_cards_belote_spades):
        """Test suit evaluation with belote"""

        ai_player.hand = sample_cards_belote_spades
        evaluations = ai_player._evaluate_suits()

        spades_eval = evaluations['Spades']
        assert spades_eval['has_belote'] is True
        assert spades_eval['contract'] == 140

    def test_estimate_tricks(self, ai_player, sample_cards_strong_spades):
        """Test trick estimation"""

        ai_player.hand = sample_cards_strong_spades
        tricks = ai_player._estimate_tricks('Spades')

        # Strong spades hand with 3 external aces should estimate 7 tricks
        assert tricks == 7

    def test_evaluate_trump_tricks(self, ai_player, sample_cards_strong_spades):
        """Test trump tricks evaluation"""

        ai_player.hand = sample_cards_strong_spades
        expected_tricks = ai_player._evaluate_trump_tricks('Spades')

        # Strong spades hand with Jack + 9 + Ace + King should expect good trick count
        # Jack + 9 = 2 tricks, plus additional tricks from trump length
        assert expected_tricks == 4

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
        assert suit == 'Spades'

    def test_choose_bid_overbid_opponent(self, ai_player, ai_opponent_player, sample_cards_strong_spades):
        """Test overbidding opponent"""

        ai_player.hand = sample_cards_strong_spades

        current_bids = [(ai_opponent_player, (90, 'Hearts'))]
        bid = ai_player.choose_bid(current_bids)

        assert isinstance(bid, tuple)
        value, suit = bid
        assert value > 90
        assert suit == 'Spades'

    def test_choose_bid_support_partner(self, ai_player, ai_opponent_player):
        """Test supporting partner's bid"""

        # Give AI player some external aces to support partner
        ai_player.hand = [
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Queen'),
            Card('Clubs', 'Ace'),
            Card('Spades', 'Jack'),  # Trump complement
            Card('Spades', '8'),
            Card('Hearts', '8'),
            Card('Diamonds', '8'),
            Card('Clubs', '8')
        ]

        # Partner bids 80 in Spades
        partner = ai_player.team.players[1]
        current_bids = [(partner, (80, 'Spades')), (ai_opponent_player,'Pass')]
        bid = ai_player.choose_bid(current_bids)

        # Should support with higher bid due to 3 external aces + trump complement
        assert isinstance(bid, tuple)
        value, suit = bid
        assert value >= 100  # 80 + 20 (2 aces) + 10 (trump complement)
        assert suit == 'Spades'

    def test_choose_bid_cant_overbid_partner(self, ai_player, ai_opponent_player, sample_cards_weak):
        """Test that AI doesn't overbid partner when it can't"""

        ai_player.hand = sample_cards_weak

        # Partner bids high
        partner = ai_player.team.players[1]
        current_bids = [(partner, (140, 'Spades')), (ai_opponent_player,'Pass')]
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
    def mock_trick(self):
        """Create a mock trick object"""
        class MockTrick:
            def __init__(self):
                self.cards = []
                self.leader_position = 0
                self.trump_suit = None
        return MockTrick()

    @pytest.fixture
    def sample_hand_mixed(self):
        """Create a mixed hand for testing"""
        return [
            Card('Spades', 'Jack'),
            Card('Spades', 'Ace'),
            Card('Hearts', 'King'),
            Card('Hearts', '10'),
            Card('Diamonds', 'Ace'),
            Card('Diamonds', '8'),
            Card('Clubs', 'Queen'),
            Card('Clubs', '7')
        ]

    def test_choose_card_empty_playable_cards(self, ai_player_with_tracking, mock_trick):
        """Test that None is returned when no cards are playable"""
        result = ai_player_with_tracking.choose_card(mock_trick, (100, 'Spades'), [])
        assert result is None

    def test_play_first_card_opening_round(self, ai_player_with_tracking, mock_trick, sample_hand_mixed):
        """Test playing the very first card of the round"""
        ai_player_with_tracking.hand = sample_hand_mixed
        contract = (100, 'Spades')

        # Mock team has contract
        ai_player_with_tracking._team_has_contract = lambda c: True

        # Should play strongest trump (Jack of Spades)
        result = ai_player_with_tracking.choose_card(mock_trick, contract, sample_hand_mixed)
        assert result.suit == 'Spades'
        assert result.rank == 'Jack'

    def test_play_first_card_opponents_contract(self, ai_player_with_tracking, mock_trick, sample_hand_mixed):
        """Test playing first card when opponents have contract"""
        ai_player_with_tracking.hand = sample_hand_mixed
        contract = (100, 'Hearts')

        # Mock team doesn't have contract
        ai_player_with_tracking._team_has_contract = lambda c: False

        # Should play ace from shortest suit (Diamonds or Spades)
        result = ai_player_with_tracking.choose_card(mock_trick, contract, sample_hand_mixed)
        assert result.rank == 'Ace'
        assert result.suit in ['Spades', 'Diamonds']

    def test_play_leading_card_with_trump_remaining(self, ai_player_with_tracking, mock_trick):
        """Test leading subsequent tricks when opponents might have trump"""
        ai_player_with_tracking.hand = [
            Card('Spades', 'Jack'),
            Card('Spades', '9'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', '8')
        ]
        contract = (100, 'Spades')

        # Mark some cards as fallen to simulate non-opening trick
        ai_player_with_tracking._fallen_cards['Hearts'].add('King')

        # Mock opponents might have trump
        ai_player_with_tracking._opponents_might_have_trump = lambda s: True

        result = ai_player_with_tracking.choose_card(mock_trick, contract, ai_player_with_tracking.hand)
        assert result.suit == 'Spades'  # Should play trump

    def test_play_leading_card_no_trump_remaining(self, ai_player_with_tracking, mock_trick):
        """Test leading when opponents have no trump left"""
        ai_player_with_tracking.hand = [
            Card('Spades', '8'),
            Card('Hearts', 'Ace'),
            Card('Diamonds', 'Ace'),
            Card('Clubs', '7')
        ]
        contract = (100, 'Spades')

        # Mark some cards as fallen
        ai_player_with_tracking._fallen_cards['Hearts'].add('King')

        # Mock opponents have no trump
        ai_player_with_tracking._opponents_might_have_trump = lambda s: False

        result = ai_player_with_tracking.choose_card(mock_trick, contract, ai_player_with_tracking.hand)
        assert result.rank == 'Ace'  # Should play ace

    def test_follow_suit_when_team_winning(self, ai_player_with_tracking, mock_trick):
        """Test following suit when team is winning"""
        ai_player_with_tracking.hand = [
            Card('Hearts', 'King'),
            Card('Hearts', '10'),
            Card('Hearts', '8'),
            Card('Spades', 'Ace')
        ]

        # Set up trick where partner is winning
        mock_trick.cards = [Card('Hearts', 'Queen')]
        mock_trick.trump_suit = 'Spades'

        # Mock team is winning
        ai_player_with_tracking._is_team_winning_trick = lambda t: True

        playable_cards = [Card('Hearts', 'King'), Card('Hearts', '10'), Card('Hearts', '8')]
        result = ai_player_with_tracking.choose_card(mock_trick, (100, 'Spades'), playable_cards)

        # Should play highest point card (King or 10)
        assert result.suit == 'Hearts'
        assert result.rank in ['King', '10']

    def test_follow_suit_when_team_losing_can_beat(self, ai_player_with_tracking, mock_trick):
        """Test following suit when team is losing but can beat current card"""
        ai_player_with_tracking.hand = [
            Card('Hearts', 'Ace'),
            Card('Hearts', '8'),
            Card('Spades', 'Jack')
        ]

        # Set up trick where opponent is winning with King
        mock_trick.cards = [Card('Hearts', 'King')]
        mock_trick.trump_suit = 'Spades'

        # Mock team is losing
        ai_player_with_tracking._is_team_winning_trick = lambda t: False

        playable_cards = [Card('Hearts', 'Ace'), Card('Hearts', '8')]
        result = ai_player_with_tracking.choose_card(mock_trick, (100, 'Spades'), playable_cards)

        # Should play Ace to beat King
        assert result.rank == 'Ace'
        assert result.suit == 'Hearts'

    def test_follow_suit_when_team_losing_cannot_beat(self, ai_player_with_tracking, mock_trick):
        """Test following suit when team is losing and cannot beat"""
        ai_player_with_tracking.hand = [
            Card('Hearts', '9'),
            Card('Hearts', '8'),
            Card('Spades', 'Jack')
        ]

        # Set up trick where opponent is winning with Ace
        mock_trick.cards = [Card('Hearts', 'Ace')]
        mock_trick.trump_suit = 'Spades'

        # Mock team is losing
        ai_player_with_tracking._is_team_winning_trick = lambda t: False

        playable_cards = [Card('Hearts', '9'), Card('Hearts', '8')]
        result = ai_player_with_tracking.choose_card(mock_trick, (100, 'Spades'), playable_cards)

        # Should play lowest card (8)
        assert result.rank == '8'
        assert result.suit == 'Hearts'

    def test_trump_when_cannot_follow_suit(self, ai_player_with_tracking, mock_trick):
        """Test trumping when cannot follow suit and team is losing"""
        ai_player_with_tracking.hand = [
            Card('Spades', 'Jack'),
            Card('Spades', '9'),
            Card('Diamonds', '8')
        ]

        # Set up trick with Hearts led
        mock_trick.cards = [Card('Hearts', 'King')]
        mock_trick.trump_suit = 'Spades'

        # Mock team is losing and can trump win
        ai_player_with_tracking._is_team_winning_trick = lambda t: False
        ai_player_with_tracking._can_trump_win = lambda card, trick, trump: card.rank == 'Jack'

        playable_cards = [Card('Spades', 'Jack'), Card('Spades', '9'), Card('Diamonds', '8')]
        result = ai_player_with_tracking.choose_card(mock_trick, (100, 'Spades'), playable_cards)

        # Should trump with Jack (lowest winning trump)
        assert result.suit == 'Spades'
        assert result.rank == 'Jack'

    def test_discard_when_cannot_follow_or_trump(self, ai_player_with_tracking, mock_trick):
        """Test discarding when cannot follow suit or trump effectively"""
        ai_player_with_tracking.hand = [
            Card('Diamonds', '8'),
            Card('Diamonds', '7'),
            Card('Clubs', 'Queen'),
            Card('Clubs', '10')
        ]

        # Set up trick with Hearts led and Spades trump
        mock_trick.cards = [Card('Hearts', 'King')]
        mock_trick.trump_suit = 'Spades'

        # Mock team is losing, no trump cards, all cards are not masters
        ai_player_with_tracking._is_team_winning_trick = lambda t: False
        ai_player_with_tracking._is_master_card = lambda card, trump: False

        playable_cards = [Card('Diamonds', '8'), Card('Diamonds', '7'), Card('Clubs', 'Queen'), Card('Clubs', '10')]
        result = ai_player_with_tracking.choose_card(mock_trick, (100, 'Spades'), playable_cards)

        # Should discard lowest from shortest suit
        assert result.rank == '7'  # Lowest point card
        assert result.suit == 'Diamonds'  # From shorter suit

    def test_card_tracking_initialization(self, ai_player_with_tracking):
        """Test that card tracking is properly initialized"""
        assert hasattr(ai_player_with_tracking, '_fallen_cards')
        assert hasattr(ai_player_with_tracking, '_players_without_trump')
        assert len(ai_player_with_tracking._fallen_cards) == 4
        for suit_cards in ai_player_with_tracking._fallen_cards.values():
            assert isinstance(suit_cards, set)

    def test_update_card_tracking(self, ai_player_with_tracking):
        """Test updating card tracking with played cards"""
        # Test the update_card_tracking method directly
        card = Card('Hearts', 'King')
        ai_player_with_tracking.update_card_tracking(card, 'East', 'Hearts', 'Spades')
        ai_player_with_tracking.hand = [
        # Check that card is tracked as fallen
        ]

        # Test trump tracking - player couldn't follow suit and didn't trump
        card2 = Card('Diamonds', '8')
        ai_player_with_tracking.update_card_tracking(card2, 'West', 'Hearts', 'Spades')

        # West should be marked as having no trump (couldn't follow Hearts, didn't trump)
        assert 'West' in ai_player_with_tracking._players_without_trump
        ai_player_with_tracking._fallen_cards['Spades'] = {'King', 'Queen'}

        # With 2 trump in hand and 2 fallen, opponents might have 4 remaining
        result = ai_player_with_tracking._opponents_might_have_trump('Spades')
        assert result is True

        # Mark more trump cards as fallen
        ai_player_with_tracking._fallen_cards['Spades'] = {'King', 'Queen', 'Ace', '10', '8', '7'}

        # With 2 trump in hand and 6 fallen, opponents have 0 remaining
        result = ai_player_with_tracking._opponents_might_have_trump('Spades')
        assert result is False

    def test_is_master_card_detection(self, ai_player_with_tracking):
        """Test detection of master cards"""
        # Set up fallen cards
        ai_player_with_tracking._fallen_cards['Hearts'] = {'King', 'Queen', 'Jack'}

        # Ace should be master now
        ace_hearts = Card('Hearts', 'Ace')
        result = ai_player_with_tracking._is_master_card(ace_hearts, 'Spades')
        assert result is True

        # 10 should not be master (Ace still out)
        ten_hearts = Card('Hearts', '10')
        result = ai_player_with_tracking._is_master_card(ten_hearts, 'Spades')
        assert result is False

    def test_trump_order_vs_normal_order(self, ai_player_with_tracking):
        """Test that trump and normal card orders are handled correctly"""
        # Normal order: 7, 8, 9, Jack, Queen, King, 10, Ace
        normal_higher = ai_player_with_tracking._get_higher_ranks('9', 'Hearts', 'Spades')
        assert 'Jack' in normal_higher
        assert 'Ace' in normal_higher

        # Trump order: 7, 8, Queen, King, 10, Ace, 9, Jack
        trump_higher = ai_player_with_tracking._get_higher_ranks('9', 'Spades', 'Spades')
        assert 'Jack' in trump_higher
        assert 'Ace' not in trump_higher  # 9 is higher than Ace in trump

    def test_team_winning_trick_detection(self, ai_player_with_tracking, mock_trick):
        """Test detection of whether team is winning current trick"""
        # Set up mock trick with partner winning
        mock_trick.cards = [Card('Hearts', 'King'), Card('Hearts', 'Ace')]
        mock_trick.leader_position = 0

        # Mock partner position and strongest card detection
        ai_player_with_tracking._get_partner_position = lambda: 1  # Partner at position 1
        ai_player_with_tracking._get_strongest_card_position = lambda t: 1  # Position 1 winning

        result = ai_player_with_tracking._is_team_winning_trick(mock_trick)
        assert result is True

        # Change winning position to opponent
        ai_player_with_tracking._get_strongest_card_position = lambda t: 2  # Position 2 winning

        result = ai_player_with_tracking._is_team_winning_trick(mock_trick)
        assert result is False

    def test_strongest_card_in_trick_with_trump(self, ai_player_with_tracking, mock_trick):
        """Test finding strongest card when trump is involved"""
        mock_trick.cards = [
            Card('Hearts', 'Ace'),    # Led suit
            Card('Hearts', 'King'),   # Following suit
            Card('Spades', '8')       # Trump beats all
        ]

        result = ai_player_with_tracking._get_strongest_card_in_trick(mock_trick, 'Spades')
        assert result.suit == 'Spades'
        assert result.rank == '8'

    def test_strongest_card_in_trick_no_trump(self, ai_player_with_tracking, mock_trick):
        """Test finding strongest card when no trump is played"""
        mock_trick.cards = [
            Card('Hearts', 'King'),   # Led suit
            Card('Hearts', 'Ace'),    # Higher in led suit
            Card('Diamonds', 'Ace')   # Different suit, doesn't matter
        ]

        result = ai_player_with_tracking._get_strongest_card_in_trick(mock_trick, 'Spades')
        assert result.suit == 'Hearts'
        assert result.rank == 'Ace'

    def test_can_trump_win_logic(self, ai_player_with_tracking, mock_trick):
        """Test logic for determining if a trump card can win"""
        mock_trick.cards = [
            Card('Hearts', 'Ace'),
            Card('Spades', '8')  # Current trump winning
        ]

        # Jack of trump should beat 8 of trump
        trump_jack = Card('Spades', 'Jack')
        result = ai_player_with_tracking._can_trump_win(trump_jack, mock_trick, 'Spades')
        assert result is True

        # 7 of trump should not beat 8 of trump
        trump_seven = Card('Spades', '7')
        result = ai_player_with_tracking._can_trump_win(trump_seven, mock_trick, 'Spades')
        assert result is False

    def test_is_stronger_card_comparison(self, ai_player_with_tracking):
        """Test card strength comparison logic"""
        # Trump vs non-trump
        trump_card = Card('Spades', '7')
        non_trump = Card('Hearts', 'Ace')
        result = ai_player_with_tracking._is_stronger_card(trump_card, non_trump, 'Spades')
        assert result is True

        # Same suit comparison
        higher_card = Card('Hearts', 'Ace')
        lower_card = Card('Hearts', 'King')
        result = ai_player_with_tracking._is_stronger_card(higher_card, lower_card, 'Spades')
        assert result is True

        # Trump vs trump
        trump_jack = Card('Spades', 'Jack')
        trump_nine = Card('Spades', '9')
        result = ai_player_with_tracking._is_stronger_card(trump_jack, trump_nine, 'Spades')
        assert result is True
