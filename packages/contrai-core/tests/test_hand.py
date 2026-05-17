"""Tests for the Hand class."""

import pytest

from contrai_core import Card, Hand, Rank, Suit


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def sample_cards() -> list[Card]:
    """A small mixed hand used across several tests (3 spades, 1 heart)."""
    return [
        Card(Suit.SPADES, Rank.ACE),
        Card(Suit.SPADES, Rank.KING),
        Card(Suit.SPADES, Rank.SEVEN),
        Card(Suit.HEARTS, Rank.JACK),
    ]


@pytest.fixture
def full_hand_cards() -> list[Card]:
    """An 8-card hand with all unique (suit, rank) pairs."""
    return [
        Card(Suit.SPADES, Rank.ACE),
        Card(Suit.SPADES, Rank.KING),
        Card(Suit.HEARTS, Rank.QUEEN),
        Card(Suit.HEARTS, Rank.JACK),
        Card(Suit.DIAMONDS, Rank.TEN),
        Card(Suit.DIAMONDS, Rank.NINE),
        Card(Suit.CLUBS, Rank.EIGHT),
        Card(Suit.CLUBS, Rank.SEVEN),
    ]


# ----------------------------------------------------------------------
# construction
# ----------------------------------------------------------------------


def test_hand_default_is_empty():
    """``Hand()`` with no args produces an empty hand."""
    h = Hand()
    assert len(h) == 0


def test_hand_from_iterable_preserves_order(sample_cards):
    """Constructing from a list keeps the cards in input order."""
    h = Hand(sample_cards)
    assert len(h) == 4
    assert list(h) == sample_cards


def test_hand_from_empty_iterable_is_empty():
    """``Hand([])`` is equivalent to ``Hand()``."""
    h = Hand([])
    assert len(h) == 0


def test_hand_from_generator():
    """The constructor accepts any iterable, not just a list."""
    cards_gen = (Card(Suit.SPADES, r) for r in (Rank.SEVEN, Rank.EIGHT))
    h = Hand(cards_gen)
    assert len(h) == 2


def test_two_hands_have_independent_storage():
    """Mutating one Hand never bleeds into another (no shared default)."""
    h1 = Hand()
    h2 = Hand()
    h1.append(Card(Suit.SPADES, Rank.ACE))
    assert len(h1) == 1
    assert len(h2) == 0


def test_hand_from_list_does_not_alias_input():
    """The constructor copies its input; mutating the source is safe."""
    source = [Card(Suit.SPADES, Rank.ACE)]
    h = Hand(source)
    source.append(Card(Suit.HEARTS, Rank.KING))
    assert len(h) == 1


# ----------------------------------------------------------------------
# list-compatible API
# ----------------------------------------------------------------------


def test_append_adds_card():
    h = Hand()
    card = Card(Suit.SPADES, Rank.ACE)
    h.append(card)
    assert len(h) == 1
    assert h[0] is card


def test_extend_adds_multiple_cards(sample_cards):
    h = Hand()
    h.extend(sample_cards)
    assert len(h) == len(sample_cards)
    assert list(h) == sample_cards


def test_remove_removes_first_occurrence():
    card_a = Card(Suit.SPADES, Rank.ACE)
    card_b = Card(Suit.HEARTS, Rank.KING)
    h = Hand([card_a, card_b])
    h.remove(card_a)
    assert len(h) == 1
    assert h[0] is card_b


def test_remove_missing_card_raises():
    h = Hand([Card(Suit.SPADES, Rank.ACE)])
    with pytest.raises(ValueError):
        h.remove(Card(Suit.HEARTS, Rank.KING))


def test_clear_empties_hand(sample_cards):
    h = Hand(sample_cards)
    h.clear()
    assert len(h) == 0


def test_contains_true_and_false(sample_cards):
    h = Hand(sample_cards)
    assert sample_cards[0] in h
    assert Card(Suit.CLUBS, Rank.NINE) not in h


def test_iter_yields_cards_in_order(sample_cards):
    h = Hand(sample_cards)
    collected = []
    for card in h:
        collected.append(card)
    assert collected == sample_cards


def test_len_matches_card_count(sample_cards):
    h = Hand(sample_cards)
    assert len(h) == 4
    h.append(Card(Suit.CLUBS, Rank.NINE))
    assert len(h) == 5


def test_getitem_int_returns_card(sample_cards):
    h = Hand(sample_cards)
    assert h[0] == sample_cards[0]
    assert h[-1] == sample_cards[-1]


def test_getitem_slice_returns_list(sample_cards):
    h = Hand(sample_cards)
    result = h[1:3]
    assert isinstance(result, list)
    assert result == sample_cards[1:3]


# ----------------------------------------------------------------------
# query helpers
# ----------------------------------------------------------------------


def test_count_suit(sample_cards):
    h = Hand(sample_cards)
    assert h.count_suit(Suit.SPADES) == 3
    assert h.count_suit(Suit.HEARTS) == 1
    assert h.count_suit(Suit.DIAMONDS) == 0
    assert h.count_suit(Suit.CLUBS) == 0


def test_count_rank(sample_cards):
    h = Hand(sample_cards)
    assert h.count_rank(Rank.ACE) == 1
    assert h.count_rank(Rank.KING) == 1
    assert h.count_rank(Rank.JACK) == 1
    assert h.count_rank(Rank.QUEEN) == 0


def test_has_card_hit_and_miss(sample_cards):
    h = Hand(sample_cards)
    assert h.has_card(Suit.SPADES, Rank.ACE) is True
    assert h.has_card(Suit.HEARTS, Rank.JACK) is True
    assert h.has_card(Suit.CLUBS, Rank.SEVEN) is False
    assert h.has_card(Suit.SPADES, Rank.JACK) is False  # right suit, wrong rank


def test_cards_of_suit_returns_matching_cards_in_order(sample_cards):
    h = Hand(sample_cards)
    spades = h.cards_of_suit(Suit.SPADES)
    assert len(spades) == 3
    assert all(c.suit == Suit.SPADES for c in spades)
    # Preserves hand order
    assert [c.rank for c in spades] == [Rank.ACE, Rank.KING, Rank.SEVEN]


def test_cards_of_suit_empty_when_no_match():
    h = Hand([Card(Suit.SPADES, Rank.ACE)])
    assert h.cards_of_suit(Suit.HEARTS) == []


def test_cards_of_suit_returns_independent_list(sample_cards):
    """Mutating the returned list doesn't affect the hand."""
    h = Hand(sample_cards)
    spades = h.cards_of_suit(Suit.SPADES)
    spades.clear()
    assert h.count_suit(Suit.SPADES) == 3


# ----------------------------------------------------------------------
# is_complete
# ----------------------------------------------------------------------


def test_is_complete_true_for_8_unique_cards(full_hand_cards):
    h = Hand(full_hand_cards)
    assert h.is_complete() is True


def test_is_complete_false_when_empty():
    assert Hand().is_complete() is False


def test_is_complete_false_for_7_cards(full_hand_cards):
    h = Hand(full_hand_cards[:-1])
    assert h.is_complete() is False


def test_is_complete_false_for_9_cards(full_hand_cards):
    h = Hand([*full_hand_cards, Card(Suit.CLUBS, Rank.NINE)])
    assert h.is_complete() is False


def test_is_complete_false_when_8_cards_but_duplicate():
    """Eight cards with a duplicate (suit, rank) is not a valid full hand."""
    dup = Card(Suit.SPADES, Rank.ACE)
    cards = [
        dup,
        Card(Suit.SPADES, Rank.ACE),  # same suit+rank as dup
        Card(Suit.HEARTS, Rank.QUEEN),
        Card(Suit.HEARTS, Rank.JACK),
        Card(Suit.DIAMONDS, Rank.TEN),
        Card(Suit.DIAMONDS, Rank.NINE),
        Card(Suit.CLUBS, Rank.EIGHT),
        Card(Suit.CLUBS, Rank.SEVEN),
    ]
    assert Hand(cards).is_complete() is False


# ----------------------------------------------------------------------
# copy
# ----------------------------------------------------------------------


def test_copy_returns_list_of_same_cards(sample_cards):
    h = Hand(sample_cards)
    snapshot = h.copy()
    assert isinstance(snapshot, list)
    assert snapshot == list(sample_cards)


def test_copy_is_independent_of_hand(sample_cards):
    """Mutating the copy must not affect the hand or vice versa."""
    h = Hand(sample_cards)
    snapshot = h.copy()
    snapshot.pop()
    assert len(h) == len(sample_cards)
    h.append(Card(Suit.CLUBS, Rank.NINE))
    assert len(snapshot) == len(sample_cards) - 1


# ----------------------------------------------------------------------
# repr
# ----------------------------------------------------------------------


def test_repr_mentions_cards():
    card = Card(Suit.SPADES, Rank.ACE)
    h = Hand([card])
    assert "Hand(" in repr(h)
    assert repr(card) in repr(h)
