"""Tests for the Suit / Rank enums, CARD_SUITS, and the trump predicates."""

import pytest

from contrai_core import CARD_SUITS, Rank, Suit, is_trump, trump_suits


class TestSuit:
    def test_expected_members(self):
        names = {s.name for s in Suit}
        assert names == {"SPADES", "HEARTS", "DIAMONDS", "CLUBS", "NO_TRUMP", "ALL_TRUMP"}

    def test_values_preserve_display_strings(self):
        assert Suit.SPADES.value == "Spades"
        assert Suit.HEARTS.value == "Hearts"
        assert Suit.DIAMONDS.value == "Diamonds"
        assert Suit.CLUBS.value == "Clubs"
        assert Suit.NO_TRUMP.value == "NoTrump"
        assert Suit.ALL_TRUMP.value == "AllTrump"

    def test_str_is_the_display_name(self):
        # Pinned against literals, never against str(Suit.X): the default
        # Enum.__str__ would render "Suit.SPADES", which is what leaked into
        # Contract.__str__ before this override existed.
        assert str(Suit.SPADES) == "Spades"
        assert str(Suit.NO_TRUMP) == "NoTrump"

    def test_format_delegates_to_str(self):
        # __format__ falls through to __str__, which is what every f-string
        # embedding a suit depends on.
        assert f"{Suit.HEARTS}" == "Hearts"
        assert f"100 {Suit.CLUBS}" == "100 Clubs"


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
        # Spades > Hearts > Diamonds > Clubs.
        assert CARD_SUITS == (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)

    def test_length_is_four(self):
        assert len(CARD_SUITS) == 4


#: Every (card suit, contract suit) pair naming two *different* suits — the
#: off-diagonal of the truth table below, where the answer must be False.
MISMATCHED_SUIT_PAIRS = [
    (card_suit, contract_suit)
    for card_suit in CARD_SUITS
    for contract_suit in CARD_SUITS
    if card_suit is not contract_suit
]


class TestIsTrump:
    """The full truth table: every card suit against every trump option."""

    @pytest.mark.parametrize("card_suit", CARD_SUITS)
    def test_own_suit_is_trump(self, card_suit):
        assert is_trump(card_suit, card_suit) is True

    @pytest.mark.parametrize("card_suit,contract_suit", MISMATCHED_SUIT_PAIRS)
    def test_other_suit_is_not_trump(self, card_suit, contract_suit):
        assert is_trump(card_suit, contract_suit) is False

    @pytest.mark.parametrize("card_suit", CARD_SUITS)
    def test_no_trump_makes_nothing_trump(self, card_suit):
        assert is_trump(card_suit, Suit.NO_TRUMP) is False

    @pytest.mark.parametrize("card_suit", CARD_SUITS)
    def test_none_makes_nothing_trump(self, card_suit):
        # No contract established yet — the same answer as no-trump.
        assert is_trump(card_suit, None) is False

    @pytest.mark.parametrize("card_suit", CARD_SUITS)
    def test_all_trump_raises(self, card_suit):
        # Not implemented, and deliberately loud about it: the inline
        # comparison this predicate replaced answered False here, which
        # played an all-trump contract as if it were no-trump.
        with pytest.raises(NotImplementedError, match="All-trump"):
            is_trump(card_suit, Suit.ALL_TRUMP)


class TestTrumpSuits:
    @pytest.mark.parametrize("contract_suit", CARD_SUITS)
    def test_suit_contract_yields_exactly_that_suit(self, contract_suit):
        assert trump_suits(contract_suit) == (contract_suit,)

    def test_no_trump_yields_nothing(self):
        assert trump_suits(Suit.NO_TRUMP) == ()

    def test_none_yields_nothing(self):
        assert trump_suits(None) == ()

    def test_all_trump_raises(self):
        with pytest.raises(NotImplementedError, match="All-trump"):
            trump_suits(Suit.ALL_TRUMP)

    def test_emptiness_is_the_no_trump_test(self):
        # This is what callers use it for: an enum member is always truthy,
        # so `if not contract_suit` cannot distinguish NO_TRUMP from a suit,
        # while `if not trump_suits(contract_suit)` can.
        assert not trump_suits(Suit.NO_TRUMP)
        assert trump_suits(Suit.SPADES)
