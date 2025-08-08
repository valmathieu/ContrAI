import pytest
import copy
from contree.model.deck import Deck
from contree.model.card import Card

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
    assert deck.cards_remaining() == 32
    assert not deck.is_empty()

def test_deck_has_all_card_combinations():
    """
    Test that the deck contains all expected card combinations.
    """
    deck = Deck()
    expected_cards = set()
    for suit in Card.SUITS:
        for rank in Card.RANKS:
            expected_cards.add(f"{rank} of {suit}")
    
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

def test_cut_with_insufficient_cards():
    """
    Test that cut works properly when there are fewer than 8 cards.
    """
    deck = Deck()
    # Remove most cards to simulate a deck with few cards
    deck.cards = deck.cards[:6]
    original_cards = deck.cards.copy()
    deck.cut()  # Should not crash and should not change anything
    assert deck.cards == original_cards

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
    assert deck.cards_remaining() == 0

def test_deal_3_2_3_distribution(deck):
    """
    Test that the deal method follows the 3-2-3 distribution pattern.
    This test verifies the specific dealing pattern used in La Contrée.
    """
    players = [DummyPlayer() for _ in range(4)]
    original_cards = deck.cards.copy()
    deck.deal(players)
    
    # Reconstruct the dealing order to verify 3-2-3 pattern
    # First 3 cards to each player (indices 0-11)
    first_round_cards = []
    for i in range(4):
        first_round_cards.extend(original_cards[i * 3:(i * 3) + 3])
    
    # Next 2 cards to each player (indices 12-19)
    second_round_cards = []
    for i in range(4):
        second_round_cards.extend(original_cards[(i * 2) + 12:(i * 2) + 14])
    
    # Last 3 cards to each player (indices 20-31)
    third_round_cards = []
    for i in range(4):
        third_round_cards.extend(original_cards[(i * 3) + 20:(i * 3) + 23])
    
    # Verify each player received cards in the correct pattern
    for i, player in enumerate(players):
        expected_cards = []
        # First round: 3 cards
        expected_cards.extend(original_cards[i * 3:(i * 3) + 3])
        # Second round: 2 cards
        expected_cards.extend(original_cards[(i * 2) + 12:(i * 2) + 14])
        # Third round: 3 cards
        expected_cards.extend(original_cards[(i * 3) + 20:(i * 3) + 23])
        
        assert len(player.hand) == 8
        assert player.hand == expected_cards

def test_deal_raises_with_wrong_number_of_players(deck):
    """
    Test that the deal method raises a ValueError if the number of players is not 4.
    """
    # Test with too few players
    players = [DummyPlayer() for _ in range(3)]
    with pytest.raises(ValueError, match="The number of players has to be exactly 4"):
        deck.deal(players)
    
    # Test with too many players
    players = [DummyPlayer() for _ in range(5)]
    with pytest.raises(ValueError, match="The number of players has to be exactly 4"):
        deck.deal(players)

def test_deal_raises_with_insufficient_cards():
    """
    Test that the deal method raises a ValueError if there are not enough cards.
    """
    deck = Deck()
    deck.cards = deck.cards[:20]  # Remove some cards
    players = [DummyPlayer() for _ in range(4)]
    
    with pytest.raises(ValueError, match="Not enough cards in deck to deal"):
        deck.deal(players)

def test_reset_restores_full_deck():
    """
    Test that reset method restores the deck to 32 cards.
    """
    deck = Deck()
    players = [DummyPlayer() for _ in range(4)]
    deck.deal(players)  # This should empty the deck
    
    assert deck.is_empty()
    
    deck.reset()
    assert len(deck.cards) == 32
    assert not deck.is_empty()

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
    assert str(deck) == "Deck with 0 cards"
    assert repr(deck) == "Deck(0 cards)"
