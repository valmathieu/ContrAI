"""Tests for Suit / TrumpVariant / Rank and the trump predicates.

The split between :class:`Suit` (what a card can be) and
:class:`TrumpVariant` (what a contract can name that a card cannot be) is
what makes :func:`is_trump` the only place the two meet, so the boundary
is pinned here rather than trusted to review.
"""

import pytest

from contrai_core import (
    CONTRACT_SUITS,
    ContractSuit,
    Rank,
    Suit,
    TrumpVariant,
    is_trump,
    trump_suits,
)


class TestSuit:
    def test_expected_members(self):
        # Exactly the four card-bearing suits. NO_TRUMP / ALL_TRUMP are
        # contract trump options and live on TrumpVariant.
        names = {s.name for s in Suit}
        assert names == {"SPADES", "HEARTS", "DIAMONDS", "CLUBS"}

    def test_length_is_four(self):
        assert len(Suit) == 4

    @pytest.mark.parametrize("name", ["NO_TRUMP", "ALL_TRUMP"])
    def test_suitless_trump_options_are_not_members(self, name):
        # Pinned rather than assumed: the whole refactor rests on Card.suit
        # being unable to hold one of these, and a re-added member would
        # silently restore the hole without breaking anything else.
        assert not hasattr(Suit, name)

    def test_iteration_order_is_the_display_preference(self):
        # Spades > Hearts > Diamonds > Clubs. Display sorting and the AI's
        # suit search both read `Suit` directly, so the order is load-bearing.
        assert tuple(Suit) == (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)

    def test_values_preserve_display_strings(self):
        assert Suit.SPADES.value == "Spades"
        assert Suit.HEARTS.value == "Hearts"
        assert Suit.DIAMONDS.value == "Diamonds"
        assert Suit.CLUBS.value == "Clubs"

    def test_str_is_the_display_name(self):
        # Pinned against literals, never against str(Suit.X): the default
        # Enum.__str__ would render "Suit.SPADES", which is what leaked into
        # Contract.__str__ before this override existed.
        assert str(Suit.SPADES) == "Spades"
        assert str(Suit.CLUBS) == "Clubs"

    def test_format_delegates_to_str(self):
        # __format__ falls through to __str__, which is what every f-string
        # embedding a suit depends on.
        assert f"{Suit.HEARTS}" == "Hearts"
        assert f"100 {Suit.CLUBS}" == "100 Clubs"

    def test_never_equals_a_bare_string(self):
        # A plain Enum, not a StrEnum — same pin as Position. A stray string
        # comparison yields a silent False rather than an exception, which is
        # exactly why it is worth a test instead of trusting code review.
        assert Suit.SPADES != "Spades"
        assert Suit.SPADES != "SPADES"


class TestTrumpVariant:
    def test_expected_members(self):
        names = {v.name for v in TrumpVariant}
        assert names == {"NO_TRUMP", "ALL_TRUMP"}

    def test_values_preserve_display_strings(self):
        assert TrumpVariant.NO_TRUMP.value == "NoTrump"
        assert TrumpVariant.ALL_TRUMP.value == "AllTrump"

    def test_str_is_the_display_name(self):
        # These flow through the same f-strings as the card suits, so they
        # need the same override.
        assert str(TrumpVariant.NO_TRUMP) == "NoTrump"
        assert f"100 {TrumpVariant.NO_TRUMP}" == "100 NoTrump"

    def test_is_not_a_suit(self):
        assert not isinstance(TrumpVariant.NO_TRUMP, Suit)
        assert TrumpVariant.NO_TRUMP not in tuple(Suit)

    def test_never_equals_a_bare_string(self):
        assert TrumpVariant.NO_TRUMP != "NoTrump"


class TestContractSuit:
    """The union, and the reason it is spelled as a plain assignment."""

    def test_isinstance_narrowing_works(self):
        # ContractSuit is `Suit | TrumpVariant`, NOT a PEP 695 `type` alias:
        # the alias form raises TypeError from isinstance, and isinstance
        # narrowing is the only guardrail in a workspace with no type checker.
        assert isinstance(Suit.SPADES, ContractSuit)
        assert isinstance(TrumpVariant.NO_TRUMP, ContractSuit)
        assert not isinstance("Spades", ContractSuit)

    def test_contract_suits_is_card_suits_then_variants(self):
        assert CONTRACT_SUITS == (*Suit, *TrumpVariant)

    def test_contract_suits_has_six_members(self):
        assert len(CONTRACT_SUITS) == 6

    def test_every_contract_suit_is_a_contract_suit(self):
        assert all(isinstance(suit, ContractSuit) for suit in CONTRACT_SUITS)


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
        # str(card) relies on these values.
        assert Rank.SEVEN.value == "7"
        assert Rank.TEN.value == "10"
        assert Rank.JACK.value == "Jack"
        assert Rank.ACE.value == "Ace"


#: Every (card suit, contract suit) pair naming two *different* suits — the
#: off-diagonal of the truth table below, where the answer must be False.
MISMATCHED_SUIT_PAIRS = [
    (card_suit, contract_suit)
    for card_suit in Suit
    for contract_suit in Suit
    if card_suit is not contract_suit
]


class TestIsTrump:
    """The full truth table: every card suit against every trump option."""

    @pytest.mark.parametrize("card_suit", Suit)
    def test_own_suit_is_trump(self, card_suit):
        assert is_trump(card_suit, card_suit) is True

    @pytest.mark.parametrize("card_suit,contract_suit", MISMATCHED_SUIT_PAIRS)
    def test_other_suit_is_not_trump(self, card_suit, contract_suit):
        assert is_trump(card_suit, contract_suit) is False

    @pytest.mark.parametrize("card_suit", Suit)
    def test_no_trump_makes_nothing_trump(self, card_suit):
        assert is_trump(card_suit, TrumpVariant.NO_TRUMP) is False

    @pytest.mark.parametrize("card_suit", Suit)
    def test_none_makes_nothing_trump(self, card_suit):
        # No contract established yet — the same answer as no-trump.
        assert is_trump(card_suit, None) is False

    @pytest.mark.parametrize("card_suit", Suit)
    def test_all_trump_raises(self, card_suit):
        # Not implemented, and deliberately loud about it: the inline
        # comparison this predicate replaced answered False here, which
        # played an all-trump contract as if it were no-trump.
        with pytest.raises(NotImplementedError, match="All-trump"):
            is_trump(card_suit, TrumpVariant.ALL_TRUMP)


class TestTrumpSuits:
    @pytest.mark.parametrize("contract_suit", Suit)
    def test_suit_contract_yields_exactly_that_suit(self, contract_suit):
        assert trump_suits(contract_suit) == (contract_suit,)

    def test_no_trump_yields_nothing(self):
        assert trump_suits(TrumpVariant.NO_TRUMP) == ()

    def test_none_yields_nothing(self):
        assert trump_suits(None) == ()

    def test_all_trump_raises(self):
        with pytest.raises(NotImplementedError, match="All-trump"):
            trump_suits(TrumpVariant.ALL_TRUMP)

    def test_always_returns_card_suits(self):
        # Callers key the fallen / void maps by the result, so it must never
        # hand back something a card cannot be.
        assert all(isinstance(suit, Suit) for suit in trump_suits(Suit.HEARTS))

    def test_emptiness_is_the_no_trump_test(self):
        # This is what callers use it for: an enum member is always truthy,
        # so `if not contract_suit` cannot distinguish NO_TRUMP from a suit,
        # while `if not trump_suits(contract_suit)` can.
        assert not trump_suits(TrumpVariant.NO_TRUMP)
        assert trump_suits(Suit.SPADES)
