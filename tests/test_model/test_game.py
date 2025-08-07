import pytest
from contree.model.game import Deck
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

def test_deal_gives_each_player_8_cards(deck):
    """
    Test that the deal method gives exactly 8 cards to each of 4 players and that all cards are unique.
    """
    players = [DummyPlayer() for _ in range(4)]
    deck.deal(players)
    for player in players:
        assert len(player.hand) == 8
    # Ensure all cards are unique and no card is missing
    all_dealt_cards = [str(card) for player in players for card in player.hand]
    assert sorted(all_dealt_cards) == sorted([str(card) for card in deck.cards])

def test_deal_raises_with_wrong_number_of_players(deck):
    """
    Test that the deal method raises a ValueError if the number of players is not 4.
    """
    players = [DummyPlayer() for _ in range(3)]
    with pytest.raises(ValueError):
        deck.deal(players)
