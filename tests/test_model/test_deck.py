import pytest
import copy
from contree.model.deck import Deck
from contree.model.card import Card
from contree.model.exceptions import InvalidPlayerCountError, InvalidCardCountError

@pytest.fixture
def deck():
    """
    Fixture that returns a new Deck instance for each test.
    """
    return Deck()

class DummyPlayer:
    def __init__(self):
        self.hand = []

def test_deck_initialization(deck):
    """
    Test that a deck is correctly initialized with 32 cards.
    """
    assert len(deck.cards) == 32
    assert not deck.is_empty()

def test_deck_has_all_card_combinations():
    """
    Test that the deck contains all expected card combinations.
    """
    deck = Deck()
    expected_cards = set()
    suit_symbols = {
        'Spades': '♠',
        'Hearts': '♥',
        'Diamonds': '♦',
        'Clubs': '♣'
    }
    for suit in Card.SUITS:
        for rank in Card.RANKS:
            expected_cards.add(f"{rank}{suit_symbols[suit]}")

    actual_cards = {str(card) for card in deck.cards}
    assert actual_cards == expected_cards

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
    assert sorted(str(card) for card in deck.cards) == sorted(str(card) for card in original_order)

def test_cut_changes_order(deck):
    """
    Test that the cut method changes the order of the deck but does not lose or duplicate any cards.
    """
    original_order = deck.cards.copy()
    deck.cut()
    # The order should be different after cut
    assert original_order != deck.cards
    # The deck should still have the same cards (no loss or duplication)
    assert sorted(str(card) for card in original_order) == sorted(str(card) for card in deck.cards)

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
    all_dealt_cards = [str(card) for player in players for card in player.hand]
    assert sorted(all_dealt_cards) == sorted([str(card) for card in deck_copy.cards])

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
