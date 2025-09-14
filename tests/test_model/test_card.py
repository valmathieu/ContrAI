import pytest
from contree.model.card import Card


@pytest.fixture
def sample_cards():
    """
    Fixture that returns a set of sample cards for testing.
    """
    return {
        'spade_jack': Card('Spades', 'Jack'),
        'heart_ace': Card('Hearts', 'Ace'),
        'diamond_9': Card('Diamonds', '9'),
        'club_king': Card('Clubs', 'King'),
        'spade_7': Card('Spades', '7'),
        'heart_10': Card('Hearts', '10'),
    }


class TestCardGetPoints:
    """Test the get_points method of the Card class."""

    def test_get_points_normal_suit(self, sample_cards):
        """
        Test that get_points returns normal points when card is not trump.
        """
        # Jack of Spades should have 2 points when not trump
        assert sample_cards['spade_jack'].get_points() == 2
        assert sample_cards['spade_jack'].get_points('Hearts') == 2

        # Ace of Hearts should have 11 points when not trump
        assert sample_cards['heart_ace'].get_points() == 11
        assert sample_cards['heart_ace'].get_points('Spades') == 11

        # 9 of Diamonds should have 0 points when not trump
        assert sample_cards['diamond_9'].get_points() == 0
        assert sample_cards['diamond_9'].get_points('Clubs') == 0

    def test_get_points_trump_suit(self, sample_cards):
        """
        Test that get_points returns trump points when card is trump.
        """
        # Jack of Spades should have 20 points when trump
        assert sample_cards['spade_jack'].get_points('Spades') == 20

        # 9 of Diamonds should have 14 points when trump
        assert sample_cards['diamond_9'].get_points('Diamonds') == 14

        # Ace of Hearts should have 11 points when trump (same as normal)
        assert sample_cards['heart_ace'].get_points('Hearts') == 11

        # King of Clubs should have 4 points when trump (same as normal)
        assert sample_cards['club_king'].get_points('Clubs') == 4

    def test_get_points_all_ranks_normal(self):
        """
        Test get_points for all ranks in normal (non-trump) suit.
        """
        test_suit = 'Spades'
        expected_normal_points = {
            '7': 0, '8': 0, '9': 0, 'Jack': 2,
            'Queen': 3, 'King': 4, '10': 10, 'Ace': 11
        }

        for rank, expected_points in expected_normal_points.items():
            card = Card(test_suit, rank)
            assert card.get_points('Hearts') == expected_points  # Different trump suit

    def test_get_points_all_ranks_trump(self):
        """
        Test get_points for all ranks when they are trump.
        """
        test_suit = 'Spades'
        expected_trump_points = {
            '7': 0, '8': 0, '9': 14, 'Jack': 20,
            'Queen': 3, 'King': 4, '10': 10, 'Ace': 11
        }

        for rank, expected_points in expected_trump_points.items():
            card = Card(test_suit, rank)
            assert card.get_points(test_suit) == expected_points


class TestCardGetOrder:
    """Test the get_order method of the Card class."""

    def test_get_order_normal_suit(self, sample_cards):
        """
        Test that get_order returns normal order when card is not trump.
        """
        # Jack of Spades should have order 3 when not trump
        assert sample_cards['spade_jack'].get_order() == 3
        assert sample_cards['spade_jack'].get_order('Hearts') == 3

        # Ace of Hearts should have order 7 when not trump
        assert sample_cards['heart_ace'].get_order() == 7
        assert sample_cards['heart_ace'].get_order('Spades') == 7

        # 9 of Diamonds should have order 2 when not trump
        assert sample_cards['diamond_9'].get_order() == 2
        assert sample_cards['diamond_9'].get_order('Clubs') == 2

    def test_get_order_trump_suit(self, sample_cards):
        """
        Test that get_order returns trump order when card is trump.
        """
        # Jack of Spades should have order 7 when trump (highest)
        assert sample_cards['spade_jack'].get_order('Spades') == 7

        # 9 of Diamonds should have order 6 when trump (second highest)
        assert sample_cards['diamond_9'].get_order('Diamonds') == 6

        # Ace of Hearts should have order 5 when trump
        assert sample_cards['heart_ace'].get_order('Hearts') == 5

        # King of Clubs should have order 3 when trump
        assert sample_cards['club_king'].get_order('Clubs') == 3

    def test_get_order_all_ranks_normal(self):
        """
        Test get_order for all ranks in normal (non-trump) suit.
        """
        test_suit = 'Spades'
        expected_normal_order = {
            '7': 0, '8': 1, '9': 2, 'Jack': 3,
            'Queen': 4, 'King': 5, '10': 6, 'Ace': 7
        }

        for rank, expected_order in expected_normal_order.items():
            card = Card(test_suit, rank)
            assert card.get_order('Hearts') == expected_order  # Different trump suit

    def test_get_order_all_ranks_trump(self):
        """
        Test get_order for all ranks when they are trump.
        """
        test_suit = 'Spades'
        expected_trump_order = {
            '7': 0, '8': 1, 'Queen': 2, 'King': 3,
            '10': 4, 'Ace': 5, '9': 6, 'Jack': 7
        }

        for rank, expected_order in expected_trump_order.items():
            card = Card(test_suit, rank)
            assert card.get_order(test_suit) == expected_order

    def test_trump_order_hierarchy(self):
        """
        Test that trump cards are ordered correctly from lowest to highest.
        """
        trump_suit = 'Hearts'
        cards = [
            Card(trump_suit, '7'),    # order 0
            Card(trump_suit, '8'),    # order 1
            Card(trump_suit, 'Queen'), # order 2
            Card(trump_suit, 'King'),  # order 3
            Card(trump_suit, '10'),    # order 4
            Card(trump_suit, 'Ace'),   # order 5
            Card(trump_suit, '9'),     # order 6
            Card(trump_suit, 'Jack'),  # order 7
        ]

        for i in range(len(cards) - 1):
            assert cards[i].get_order(trump_suit) < cards[i + 1].get_order(trump_suit)

    def test_normal_order_hierarchy(self):
        """
        Test that normal cards are ordered correctly from lowest to highest.
        """
        normal_suit = 'Hearts'
        trump_suit = 'Spades'  # Different from normal suit
        cards = [
            Card(normal_suit, '7'),    # order 0
            Card(normal_suit, '8'),    # order 1
            Card(normal_suit, '9'),    # order 2
            Card(normal_suit, 'Jack'), # order 3
            Card(normal_suit, 'Queen'),# order 4
            Card(normal_suit, 'King'), # order 5
            Card(normal_suit, '10'),   # order 6
            Card(normal_suit, 'Ace'),  # order 7
        ]

        for i in range(len(cards) - 1):
            assert cards[i].get_order(trump_suit) < cards[i + 1].get_order(trump_suit)


class TestCardIntegration:
    """Integration tests for Card methods."""

    def test_trump_vs_normal_points_difference(self):
        """
        Test that certain cards have different points when trump vs normal.
        """
        jack = Card('Spades', 'Jack')
        nine = Card('Spades', '9')
        ace = Card('Spades', 'Ace')

        # Jack: 2 normal, 20 trump
        assert jack.get_points('Hearts') == 2
        assert jack.get_points('Spades') == 20

        # Nine: 0 normal, 14 trump
        assert nine.get_points('Hearts') == 0
        assert nine.get_points('Spades') == 14

        # Ace: 11 normal, 11 trump (same)
        assert ace.get_points('Hearts') == 11
        assert ace.get_points('Spades') == 11

    def test_trump_vs_normal_order_difference(self):
        """
        Test that certain cards have different order when trump vs normal.
        """
        jack = Card('Spades', 'Jack')
        nine = Card('Spades', '9')
        queen = Card('Spades', 'Queen')

        # Jack: 3 normal, 7 trump (becomes highest)
        assert jack.get_order('Hearts') == 3
        assert jack.get_order('Spades') == 7

        # Nine: 2 normal, 6 trump (becomes second highest)
        assert nine.get_order('Hearts') == 2
        assert nine.get_order('Spades') == 6

        # Queen: 4 normal, 2 trump (becomes lower)
        assert queen.get_order('Hearts') == 4
        assert queen.get_order('Spades') == 2
