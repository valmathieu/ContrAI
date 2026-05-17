import pytest
from contrai_core.card import Card
from contrai_core.types import Suit, Rank


@pytest.fixture
def sample_cards():
    """
    Fixture that returns a set of sample cards for testing.
    """
    return {
        'spade_jack': Card(Suit.SPADES, Rank.JACK),
        'heart_ace': Card(Suit.HEARTS, Rank.ACE),
        'diamond_9': Card(Suit.DIAMONDS, Rank.NINE),
        'club_king': Card(Suit.CLUBS, Rank.KING),
        'spade_7': Card(Suit.SPADES, Rank.SEVEN),
        'heart_10': Card(Suit.HEARTS, Rank.TEN),
    }


class TestCardGetPoints:
    """Test the get_points method of the Card class."""

    def test_get_points_normal_suit(self, sample_cards):
        """
        Test that get_points returns normal points when card is not trump.
        """
        # Jack of Spades should have 2 points when not trump
        assert sample_cards['spade_jack'].get_points() == 2
        assert sample_cards['spade_jack'].get_points(Suit.HEARTS) == 2

        # Ace of Hearts should have 11 points when not trump
        assert sample_cards['heart_ace'].get_points() == 11
        assert sample_cards['heart_ace'].get_points(Suit.SPADES) == 11

        # 9 of Diamonds should have 0 points when not trump
        assert sample_cards['diamond_9'].get_points() == 0
        assert sample_cards['diamond_9'].get_points(Suit.CLUBS) == 0

    def test_get_points_trump_suit(self, sample_cards):
        """
        Test that get_points returns trump points when card is trump.
        """
        # Jack of Spades should have 20 points when trump
        assert sample_cards['spade_jack'].get_points(Suit.SPADES) == 20

        # 9 of Diamonds should have 14 points when trump
        assert sample_cards['diamond_9'].get_points(Suit.DIAMONDS) == 14

        # Ace of Hearts should have 11 points when trump (same as normal)
        assert sample_cards['heart_ace'].get_points(Suit.HEARTS) == 11

        # King of Clubs should have 4 points when trump (same as normal)
        assert sample_cards['club_king'].get_points(Suit.CLUBS) == 4

    def test_get_points_all_ranks_normal(self):
        """
        Test get_points for all ranks in normal (non-trump) suit.
        """
        test_suit = Suit.SPADES
        expected_normal_points = {
            Rank.SEVEN: 0, Rank.EIGHT: 0, Rank.NINE: 0, Rank.JACK: 2,
            Rank.QUEEN: 3, Rank.KING: 4, Rank.TEN: 10, Rank.ACE: 11
        }

        for rank, expected_points in expected_normal_points.items():
            card = Card(test_suit, rank)
            assert card.get_points(Suit.HEARTS) == expected_points  # Different trump suit

    def test_get_points_all_ranks_trump(self):
        """
        Test get_points for all ranks when they are trump.
        """
        test_suit = Suit.SPADES
        expected_trump_points = {
            Rank.SEVEN: 0, Rank.EIGHT: 0, Rank.NINE: 14, Rank.JACK: 20,
            Rank.QUEEN: 3, Rank.KING: 4, Rank.TEN: 10, Rank.ACE: 11
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
        assert sample_cards['spade_jack'].get_order(Suit.HEARTS) == 3

        # Ace of Hearts should have order 7 when not trump
        assert sample_cards['heart_ace'].get_order() == 7
        assert sample_cards['heart_ace'].get_order(Suit.SPADES) == 7

        # 9 of Diamonds should have order 2 when not trump
        assert sample_cards['diamond_9'].get_order() == 2
        assert sample_cards['diamond_9'].get_order(Suit.CLUBS) == 2

    def test_get_order_trump_suit(self, sample_cards):
        """
        Test that get_order returns trump order when card is trump.
        """
        # Jack of Spades should have order 7 when trump (highest)
        assert sample_cards['spade_jack'].get_order(Suit.SPADES) == 7

        # 9 of Diamonds should have order 6 when trump (second highest)
        assert sample_cards['diamond_9'].get_order(Suit.DIAMONDS) == 6

        # Ace of Hearts should have order 5 when trump
        assert sample_cards['heart_ace'].get_order(Suit.HEARTS) == 5

        # King of Clubs should have order 3 when trump
        assert sample_cards['club_king'].get_order(Suit.CLUBS) == 3

    def test_get_order_all_ranks_normal(self):
        """
        Test get_order for all ranks in normal (non-trump) suit.
        """
        test_suit = Suit.SPADES
        expected_normal_order = {
            Rank.SEVEN: 0, Rank.EIGHT: 1, Rank.NINE: 2, Rank.JACK: 3,
            Rank.QUEEN: 4, Rank.KING: 5, Rank.TEN: 6, Rank.ACE: 7
        }

        for rank, expected_order in expected_normal_order.items():
            card = Card(test_suit, rank)
            assert card.get_order(Suit.HEARTS) == expected_order  # Different trump suit

    def test_get_order_all_ranks_trump(self):
        """
        Test get_order for all ranks when they are trump.
        """
        test_suit = Suit.SPADES
        expected_trump_order = {
            Rank.SEVEN: 0, Rank.EIGHT: 1, Rank.QUEEN: 2, Rank.KING: 3,
            Rank.TEN: 4, Rank.ACE: 5, Rank.NINE: 6, Rank.JACK: 7
        }

        for rank, expected_order in expected_trump_order.items():
            card = Card(test_suit, rank)
            assert card.get_order(test_suit) == expected_order

    def test_trump_order_hierarchy(self):
        """
        Test that trump cards are ordered correctly from lowest to highest.
        """
        trump_suit = Suit.HEARTS
        cards = [
            Card(trump_suit, Rank.SEVEN),    # order 0
            Card(trump_suit, Rank.EIGHT),    # order 1
            Card(trump_suit, Rank.QUEEN),    # order 2
            Card(trump_suit, Rank.KING),     # order 3
            Card(trump_suit, Rank.TEN),      # order 4
            Card(trump_suit, Rank.ACE),      # order 5
            Card(trump_suit, Rank.NINE),     # order 6
            Card(trump_suit, Rank.JACK),     # order 7
        ]

        for i in range(len(cards) - 1):
            assert cards[i].get_order(trump_suit) < cards[i + 1].get_order(trump_suit)

    def test_normal_order_hierarchy(self):
        """
        Test that normal cards are ordered correctly from lowest to highest.
        """
        normal_suit = Suit.HEARTS
        trump_suit = Suit.SPADES  # Different from normal suit
        cards = [
            Card(normal_suit, Rank.SEVEN),   # order 0
            Card(normal_suit, Rank.EIGHT),   # order 1
            Card(normal_suit, Rank.NINE),    # order 2
            Card(normal_suit, Rank.JACK),    # order 3
            Card(normal_suit, Rank.QUEEN),   # order 4
            Card(normal_suit, Rank.KING),    # order 5
            Card(normal_suit, Rank.TEN),     # order 6
            Card(normal_suit, Rank.ACE),     # order 7
        ]

        for i in range(len(cards) - 1):
            assert cards[i].get_order(trump_suit) < cards[i + 1].get_order(trump_suit)


class TestCardIntegration:
    """Integration tests for Card methods."""

    def test_trump_vs_normal_points_difference(self):
        """
        Test that certain cards have different points when trump vs normal.
        """
        jack = Card(Suit.SPADES, Rank.JACK)
        nine = Card(Suit.SPADES, Rank.NINE)
        ace = Card(Suit.SPADES, Rank.ACE)

        # Jack: 2 normal, 20 trump
        assert jack.get_points(Suit.HEARTS) == 2
        assert jack.get_points(Suit.SPADES) == 20

        # Nine: 0 normal, 14 trump
        assert nine.get_points(Suit.HEARTS) == 0
        assert nine.get_points(Suit.SPADES) == 14

        # Ace: 11 normal, 11 trump (same)
        assert ace.get_points(Suit.HEARTS) == 11
        assert ace.get_points(Suit.SPADES) == 11

    def test_trump_vs_normal_order_difference(self):
        """
        Test that certain cards have different order when trump vs normal.
        """
        jack = Card(Suit.SPADES, Rank.JACK)
        nine = Card(Suit.SPADES, Rank.NINE)
        queen = Card(Suit.SPADES, Rank.QUEEN)

        # Jack: 3 normal, 7 trump (becomes highest)
        assert jack.get_order(Suit.HEARTS) == 3
        assert jack.get_order(Suit.SPADES) == 7

        # Nine: 2 normal, 6 trump (becomes second highest)
        assert nine.get_order(Suit.HEARTS) == 2
        assert nine.get_order(Suit.SPADES) == 6

        # Queen: 4 normal, 2 trump (becomes lower)
        assert queen.get_order(Suit.HEARTS) == 4
        assert queen.get_order(Suit.SPADES) == 2


class TestCardStringRepresentations:
    """Test __str__ and __repr__ output formats."""

    def test_str_uses_suit_symbol(self, sample_cards):
        # Jack of Spades → "Jack♠"
        assert str(sample_cards['spade_jack']) == "Jack♠"
        assert str(sample_cards['heart_ace']) == "Ace♥"
        assert str(sample_cards['diamond_9']) == "9♦"
        assert str(sample_cards['club_king']) == "King♣"

    def test_str_uses_rank_display_value(self):
        # Rank.TEN.value is "10" — make sure str doesn't show "TEN".
        assert str(Card(Suit.HEARTS, Rank.TEN)) == "10♥"
        assert str(Card(Suit.SPADES, Rank.SEVEN)) == "7♠"

    def test_repr_is_debuggable(self, sample_cards):
        card = sample_cards['spade_jack']
        # __repr__ uses enum repr — assert the key identifying bits are
        # present rather than pinning the full enum repr format.
        text = repr(card)
        assert "Card(" in text
        assert "SPADES" in text
        assert "JACK" in text

    def test_suit_symbol_table_covers_all_physical_suits(self):
        for suit in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS):
            assert suit in Card.SUIT_SYMBOLS
