"""Tests for the Suit / Rank enums and CARD_SUITS tuple."""

from contrai_core.types import CARD_SUITS, Rank, Suit


class TestSuit:
    def test_expected_members(self):
        names = {s.name for s in Suit}
        assert names == {"SPADES", "HEARTS", "DIAMONDS", "CLUBS", "NO_TRUMP"}

    def test_values_preserve_display_strings(self):
        assert Suit.SPADES.value == "Spades"
        assert Suit.HEARTS.value == "Hearts"
        assert Suit.DIAMONDS.value == "Diamonds"
        assert Suit.CLUBS.value == "Clubs"
        assert Suit.NO_TRUMP.value == "NoTrump"


class TestRank:
    def test_expected_members(self):
        names = {r.name for r in Rank}
        assert names == {
            "SEVEN",
            "EIGHT",
            "NINE",
            "TEN",
            "JACK",
            "QUEEN",
            "KING",
            "ACE",
        }

    def test_values_preserve_display_strings(self):
        # str(card) relies on these values — see card.py:90.
        assert Rank.SEVEN.value == "7"
        assert Rank.TEN.value == "10"
        assert Rank.JACK.value == "Jack"
        assert Rank.ACE.value == "Ace"


class TestCardSuits:
    def test_excludes_no_trump(self):
        assert Suit.NO_TRUMP not in CARD_SUITS

    def test_order_matches_documented_preference(self):
        # Spades > Hearts > Diamonds > Clubs (see contree-domain.md §11.1).
        assert CARD_SUITS == (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)

    def test_length_is_four(self):
        assert len(CARD_SUITS) == 4
