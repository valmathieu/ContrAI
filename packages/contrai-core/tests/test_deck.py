"""Tests for the ``Deck`` class.

Covers the 32-card initial composition, shuffling and cutting, dealing
(eight unique cards per player, player-count and card-count guards),
card return via ``add_cards``, and the string representations.
"""

import copy
from collections import Counter

import pytest

from contrai_core import (
    Card,
    Deck,
    Hand,
    InvalidCardCountError,
    InvalidPlayerCountError,
    Rank,
    Suit,
)

@pytest.fixture
def deck():
    """
    Fixture that returns a new Deck instance for each test.
    """
    return Deck()

class DummyPlayer:
    """Minimal player stand-in: just the ``Hand`` ``deal`` fills."""

    def __init__(self):
        self.hand = Hand()

def test_deck_initialization(deck):
    """
    Test that a deck is correctly initialized with 32 cards.
    """
    assert len(deck.cards) == 32
    assert not deck.is_empty()

def test_deck_has_all_card_combinations():
    """
    Test that the deck contains all expected card combinations.

    ``Card`` is a value object (equality by ``(suit, rank)``), so the deck's
    cards can be compared directly against the full 32-card set built from
    the suit × rank product — no ``str()`` projection needed.
    """
    deck = Deck()
    expected_cards = {Card(suit, rank) for suit in Suit for rank in Rank}
    assert set(deck.cards) == expected_cards

def test_shuffle_changes_order(deck):
    """
    Test that shuffling changes the order of cards.
    """
    original_order = deck.cards.copy()
    deck.shuffle()
    # Note: There's a tiny chance this could fail if shuffle returns same order
    # but with 32 cards, this is extremely unlikely
    assert deck.cards != original_order
    # Ensure all cards are still present
    assert len(deck.cards) == 32
    assert Counter(deck.cards) == Counter(original_order)

def test_cut_changes_order(deck):
    """
    Test that the cut method changes the order of the deck but does not lose or duplicate any cards.
    """
    original_order = deck.cards.copy()
    deck.cut()
    # The order should be different after cut
    assert original_order != deck.cards
    # The deck should still have the same cards (no loss or duplication)
    assert Counter(original_order) == Counter(deck.cards)

def test_deal_gives_each_player_8_unique_cards(deck):
    """
    Test that the deal method gives exactly 8 cards to each of 4 players and that all cards are unique.
    """
    players = [DummyPlayer() for _ in range(4)]
    deck_copy = copy.deepcopy(deck)  # Keep a copy of the original deck
    deck.deal(players)

    # Each player should have exactly 8 cards
    for player in players:
        assert len(player.hand) == 8

    # Ensure all cards are unique and no card is missing
    all_dealt_cards = [card for player in players for card in player.hand]
    assert Counter(all_dealt_cards) == Counter(deck_copy.cards)

    # Deck should be empty after dealing
    assert deck.is_empty()

def test_deal_raises_with_wrong_number_of_players(deck):
    """
    Test that the deal method raises a InvalidPlayerCountError if the number of players is not 4.
    """
    # Test with too few players
    players = [DummyPlayer() for _ in range(3)]
    with pytest.raises(InvalidPlayerCountError, match="Dealing cards: Expected 4 players, got 3"):
        deck.deal(players)

    # Test with too many players
    players = [DummyPlayer() for _ in range(5)]
    with pytest.raises(InvalidPlayerCountError, match="Dealing cards: Expected 4 players, got 5"):
        deck.deal(players)

def test_deal_raises_with_insufficient_cards():
    """
    Test that the deal method raises a InvalidCardCountError if there are not enough cards.
    """
    deck = Deck()
    deck.cards = deck.cards[:20]  # Remove some cards
    players = [DummyPlayer() for _ in range(4)]

    with pytest.raises(InvalidCardCountError, match="Dealing cards: Expected 32 cards, got 20"):
        deck.deal(players)

def test_deck_string_representations():
    """
    Test that string representations work correctly.
    """
    deck = Deck()
    assert str(deck) == "Deck with 32 cards"
    assert repr(deck) == "Deck(32 cards)"

    # Test after dealing
    players = [DummyPlayer() for _ in range(4)]
    deck.deal(players)
    assert str(deck) == "Empty deck"
    assert repr(deck) == "Deck(0 cards)"

def test_deck_add_cards_method(deck):
    """
    Test the add_cards method of the Deck class.
    """
    initial_size = len(deck.cards)
    assert initial_size == 32

    # Remove some cards to simulate dealing
    removed_cards = deck.cards[:4]
    deck.cards = deck.cards[4:]
    assert len(deck.cards) == 28

    # Add cards back
    deck.add_cards(removed_cards)

    # Check that cards are added back
    assert len(deck.cards) == 32


class TestStacked:
    """``Deck.stacked`` — the inverse of ``deal``'s 3-2-3 layout.

    The property that matters is the round trip: stacking a chosen deal
    and then dealing it must hand every seat exactly the cards it was
    given, in the order it was given them. Everything else is the guard
    rails around a malformed deal.
    """

    @staticmethod
    def _four_hands() -> list[list[Card]]:
        """Four distinct 8-card hands, one whole suit per seat."""

        return [
            [Card(suit, rank) for rank in Rank]
            for suit in Suit
        ]

    def test_dealing_a_stacked_deck_reproduces_the_hands(self):
        hands = self._four_hands()
        players = [DummyPlayer() for _ in range(4)]

        Deck.stacked(hands).deal(players)

        for player, hand in zip(players, hands):
            assert list(player.hand.cards) == hand

    def test_the_order_within_a_hand_is_preserved(self):
        # The 3-2-3 batches are three disjoint slices, so a hand that
        # came back re-ordered would mean the inversion mismatched them.
        hands = self._four_hands()
        hands[0] = list(reversed(hands[0]))
        players = [DummyPlayer() for _ in range(4)]

        Deck.stacked(hands).deal(players)

        assert list(players[0].hand.cards) == hands[0]

    def test_a_stacked_deck_holds_the_whole_pack(self):
        deck = Deck.stacked(self._four_hands())

        assert len(deck.cards) == 32
        assert len(set(deck.cards)) == 32

    def test_a_fresh_deck_stacks_onto_itself(self):
        # Stacking the deal a plain deck produces must give that deal
        # back — the inverse of an identity is an identity.
        players = [DummyPlayer() for _ in range(4)]
        Deck().deal(players)
        dealt = [list(player.hand.cards) for player in players]

        again = [DummyPlayer() for _ in range(4)]
        Deck.stacked(dealt).deal(again)

        assert [list(p.hand.cards) for p in again] == dealt

    def test_too_few_hands_is_refused(self):
        with pytest.raises(InvalidPlayerCountError) as excinfo:
            Deck.stacked(self._four_hands()[:3])

        assert excinfo.value.expected_count == 4
        assert excinfo.value.actual_count == 3

    def test_too_many_hands_is_refused(self):
        hands = self._four_hands()
        with pytest.raises(InvalidPlayerCountError):
            Deck.stacked(hands + [hands[0]])

    def test_a_short_hand_is_refused(self):
        hands = self._four_hands()
        hands[2] = hands[2][:7]

        with pytest.raises(InvalidCardCountError) as excinfo:
            Deck.stacked(hands)

        assert excinfo.value.expected_count == 8
        assert excinfo.value.actual_count == 7
        assert "hand 2" in str(excinfo.value)

    def test_a_repeated_card_is_refused(self):
        hands = self._four_hands()
        # Seat 1 is handed one of seat 0's cards.
        hands[1][0] = hands[0][0]

        with pytest.raises(InvalidCardCountError) as excinfo:
            Deck.stacked(hands)

        assert excinfo.value.expected_count == 32
        assert excinfo.value.actual_count == 31
