"""Tests for the shared card-collection queries.

Each function is exercised over the three container shapes real callers
pass: the ``list`` a :class:`Hand` wraps, the frozen ``tuple`` the
card-play path carries (``PlayState.hands`` / ``PlayObservation.hand``),
and a ``Hand`` itself — the whole point of the module is that one
implementation serves all three.
"""

import pytest

from contrai_core import (
    Card,
    Hand,
    Rank,
    Suit,
    cards_of_suit,
    count_suit,
    has_card,
    has_suit,
)


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def sample_cards() -> list[Card]:
    """A small mixed collection (3 spades, 1 heart, no diamond/club)."""
    return [
        Card(Suit.SPADES, Rank.ACE),
        Card(Suit.SPADES, Rank.KING),
        Card(Suit.SPADES, Rank.SEVEN),
        Card(Suit.HEARTS, Rank.JACK),
    ]


@pytest.fixture(params=["list", "tuple", "hand"])
def collection(request, sample_cards):
    """The same cards in each container shape a caller might pass."""
    shapes = {
        "list": lambda cards: list(cards),
        "tuple": lambda cards: tuple(cards),
        "hand": lambda cards: Hand(cards),
    }
    return shapes[request.param](sample_cards)


@pytest.fixture(params=["list", "tuple", "hand"])
def empty_collection(request):
    """An empty collection in each container shape."""
    return {"list": [], "tuple": (), "hand": Hand()}[request.param]


# ----------------------------------------------------------------------
# count_suit
# ----------------------------------------------------------------------


def test_count_suit_counts_present_and_absent_suits(collection):
    assert count_suit(collection, Suit.SPADES) == 3
    assert count_suit(collection, Suit.HEARTS) == 1
    assert count_suit(collection, Suit.DIAMONDS) == 0
    assert count_suit(collection, Suit.CLUBS) == 0


def test_count_suit_of_empty_is_zero(empty_collection):
    assert count_suit(empty_collection, Suit.SPADES) == 0


# ----------------------------------------------------------------------
# cards_of_suit
# ----------------------------------------------------------------------


def test_cards_of_suit_returns_matches_in_order(collection):
    spades = cards_of_suit(collection, Suit.SPADES)
    assert [card.rank for card in spades] == [Rank.ACE, Rank.KING, Rank.SEVEN]


def test_cards_of_suit_empty_when_no_match(collection):
    assert cards_of_suit(collection, Suit.CLUBS) == []


def test_cards_of_suit_of_empty_is_empty(empty_collection):
    assert cards_of_suit(empty_collection, Suit.SPADES) == []


def test_cards_of_suit_returns_a_list_for_every_shape(collection):
    """Even given a tuple or a Hand, the result is a plain list."""
    assert isinstance(cards_of_suit(collection, Suit.SPADES), list)


def test_cards_of_suit_result_is_independent(collection):
    """Mutating the returned list must not touch the source collection."""
    spades = cards_of_suit(collection, Suit.SPADES)
    spades.clear()
    assert count_suit(collection, Suit.SPADES) == 3


# ----------------------------------------------------------------------
# has_suit
# ----------------------------------------------------------------------


def test_has_suit_present_and_absent(collection):
    assert has_suit(collection, Suit.SPADES) is True
    assert has_suit(collection, Suit.HEARTS) is True
    assert has_suit(collection, Suit.DIAMONDS) is False
    assert has_suit(collection, Suit.CLUBS) is False


def test_has_suit_of_empty_is_false(empty_collection):
    assert has_suit(empty_collection, Suit.SPADES) is False


def test_has_suit_agrees_with_count_suit(collection):
    """Presence and count can never disagree about a suit."""
    for suit in Suit:
        assert has_suit(collection, suit) == (count_suit(collection, suit) > 0)


# ----------------------------------------------------------------------
# has_card
# ----------------------------------------------------------------------


def test_has_card_hit(collection):
    assert has_card(collection, Suit.SPADES, Rank.ACE) is True
    assert has_card(collection, Suit.HEARTS, Rank.JACK) is True


def test_has_card_miss(collection):
    assert has_card(collection, Suit.CLUBS, Rank.SEVEN) is False


def test_has_card_needs_both_suit_and_rank(collection):
    """A right-suit/wrong-rank and wrong-suit/right-rank pair both miss."""
    assert has_card(collection, Suit.SPADES, Rank.JACK) is False
    assert has_card(collection, Suit.HEARTS, Rank.ACE) is False


def test_has_card_of_empty_is_false(empty_collection):
    assert has_card(empty_collection, Suit.SPADES, Rank.ACE) is False


def test_has_card_matches_membership(collection):
    """Card value-equality is the single source of truth for the lookup."""
    hit = Card(Suit.SPADES, Rank.ACE)
    miss = Card(Suit.CLUBS, Rank.SEVEN)
    assert has_card(collection, hit.suit, hit.rank) == (hit in collection)
    assert has_card(collection, miss.suit, miss.rank) == (miss in collection)


# ----------------------------------------------------------------------
# the Hand facade delegates rather than re-implementing
# ----------------------------------------------------------------------


def test_hand_methods_agree_with_the_free_functions(sample_cards):
    """``Hand``'s query methods answer exactly what the functions do."""
    hand = Hand(sample_cards)
    for suit in Suit:
        assert hand.count_suit(suit) == count_suit(sample_cards, suit)
        assert hand.cards_of_suit(suit) == cards_of_suit(sample_cards, suit)
        assert hand.has_suit(suit) == has_suit(sample_cards, suit)
        for rank in Rank:
            assert hand.has_card(suit, rank) == has_card(
                sample_cards, suit, rank
            )
