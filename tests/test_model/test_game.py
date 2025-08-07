import pytest
from contree.model.game import Deck
from contree.model.card import Card

@pytest.fixture
def deck():
    return Deck()

class DummyPlayer:
    def __init__(self):
        self.hand = []

def test_cut_changes_order(deck):
    original_order = deck.cards.copy()
    deck.cut()
    # The order should be different after cut
    assert original_order != deck.cards
    # The deck should still have the same cards (no loss or duplication)
    assert sorted(str(card) for card in original_order) == sorted(str(card) for card in deck.cards)

def test_deal_gives_each_player_8_cards(deck):
    players = [DummyPlayer() for _ in range(4)]
    deck.deal(players)
    for player in players:
        assert len(player.hand) == 8
    # Ensure all cards are unique and no card is missing
    all_dealt_cards = [str(card) for player in players for card in player.hand]
    assert len(all_dealt_cards) == len(set(all_dealt_cards))
    assert sorted(all_dealt_cards) == sorted(str(card) for card in deck.cards + [card for player in players for card in player.hand])

def test_deal_raises_with_wrong_number_of_players(deck):
    players = [DummyPlayer() for _ in range(3)]
    with pytest.raises(ValueError):
        deck.deal(players)
