"""Tests for the ``Card`` value object.

Card is pure identity — a suit and a rank. These tests cover
construction (the non-Suit rejection), equality / hashing, immutability,
and the string representations. Everything contextual a card used to be
asked about (trumpness, points, strength) belongs to the ``TrumpRules``
seam and is pinned in ``test_rules.py``.
"""

import dataclasses

import pytest

from contrai_core import Card, InvalidCardError, Rank, Suit, TrumpVariant


@pytest.fixture
def sample_cards():
    """
    Fixture that returns a set of sample cards for testing.
    """
    return {
        'spade_jack': Card(Suit.SPADES, Rank.JACK),
        'heart_ace': Card(Suit.HEARTS, Rank.ACE),
        'diamond_9': Card(Suit.DIAMONDS, Rank.NINE),
        'club_king': Card(Suit.CLUBS, Rank.KING),
    }


class TestCardConstruction:
    """A card must carry a suit a card can actually have."""

    @pytest.mark.parametrize(
        "variant", [TrumpVariant.NO_TRUMP, TrumpVariant.ALL_TRUMP]
    )
    def test_trump_variant_suit_raises(self, variant):
        # The live path this closes: Hand.has_card(suit, rank) is
        # `Card(suit, rank) in self`, and the round's belote detection
        # feeds it suits taken from the contract's rules. Without the
        # guard a suitless trump mints a card in a suit no deck holds and
        # quietly matches nothing.
        with pytest.raises(InvalidCardError, match="Invalid card suit"):
            Card(variant, Rank.ACE)

    def test_string_suit_raises(self):
        with pytest.raises(InvalidCardError, match="Invalid card suit"):
            Card("Spades", Rank.ACE)

    @pytest.mark.parametrize("suit", Suit)
    def test_every_real_suit_is_accepted(self, suit):
        assert Card(suit, Rank.ACE).suit is suit

    def test_carries_no_call_time_trump_api(self):
        # Contextual questions live on the TrumpRules seam, not the card:
        # a Card knows its identity and nothing about any contract.
        card = Card(Suit.SPADES, Rank.JACK)
        for retired in ("is_trump", "get_points", "get_order"):
            assert not hasattr(card, retired)


class TestCardStringRepresentations:
    """Test __str__ and __repr__ output formats."""

    def test_str_uses_suit_symbol(self, sample_cards):
        # Jack of Spades → "Jack ♠"
        assert str(sample_cards['spade_jack']) == "Jack ♠"
        assert str(sample_cards['heart_ace']) == "Ace ♥"
        assert str(sample_cards['diamond_9']) == "9 ♦"
        assert str(sample_cards['club_king']) == "King ♣"

    def test_str_uses_rank_display_value(self):
        # Rank.TEN.value is "10" — make sure str doesn't show "TEN".
        assert str(Card(Suit.HEARTS, Rank.TEN)) == "10 ♥"
        assert str(Card(Suit.SPADES, Rank.SEVEN)) == "7 ♠"

    def test_repr_is_debuggable(self, sample_cards):
        card = sample_cards['spade_jack']
        # __repr__ uses enum repr — assert the key identifying bits are
        # present rather than pinning the full enum repr format.
        text = repr(card)
        assert "Card(" in text
        assert "SPADES" in text
        assert "JACK" in text

    def test_suit_symbol_table_covers_all_physical_suits(self):
        for suit in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS):
            assert suit in Card.SUIT_SYMBOLS


class TestCardValueSemantics:
    """Card is an immutable value object: equality/hash by ``(suit, rank)``.

    Distinct instances of the same physical card must be interchangeable —
    this is what lets a Card survive a deep-copy / SQLite reload / replay and
    still compare equal to "the same" card, and live in a ``set``/``dict`` by
    value. There is no ``__lt__``: strength is contextual and belongs to the
    contract's ``TrumpRules`` object, not to direct card comparison.
    """

    def test_same_suit_and_rank_are_equal(self):
        # Two independently constructed 7♠ are the same value.
        assert Card(Suit.SPADES, Rank.SEVEN) == Card(Suit.SPADES, Rank.SEVEN)

    def test_equal_cards_have_equal_hash(self):
        a = Card(Suit.HEARTS, Rank.ACE)
        b = Card(Suit.HEARTS, Rank.ACE)
        assert hash(a) == hash(b)

    def test_different_rank_is_unequal(self):
        assert Card(Suit.SPADES, Rank.SEVEN) != Card(Suit.SPADES, Rank.EIGHT)

    def test_different_suit_is_unequal(self):
        assert Card(Suit.SPADES, Rank.SEVEN) != Card(Suit.HEARTS, Rank.SEVEN)

    def test_unequal_to_non_card(self):
        # No false positive against the raw (suit, rank) tuple it wraps.
        card = Card(Suit.SPADES, Rank.JACK)
        assert card != (Suit.SPADES, Rank.JACK)
        assert card != "Jack ♠"
        assert card is not None
        assert (card == 42) is False

    def test_membership_uses_value_equality(self):
        # A freshly built card is "in" a set containing an equal instance.
        assert Card(Suit.DIAMONDS, Rank.KING) in {Card(Suit.DIAMONDS, Rank.KING)}

    def test_set_dedupes_by_value(self):
        cards = [
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.SEVEN),  # duplicate value
            Card(Suit.HEARTS, Rank.SEVEN),
        ]
        assert set(cards) == {
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.SEVEN),
        }

    def test_usable_as_dict_key(self):
        prices = {Card(Suit.CLUBS, Rank.ACE): 11}
        # Lookup by an equal-but-distinct instance hits the same bucket.
        assert prices[Card(Suit.CLUBS, Rank.ACE)] == 11

    def test_is_immutable(self):
        card = Card(Suit.SPADES, Rank.JACK)
        with pytest.raises(dataclasses.FrozenInstanceError):
            card.suit = Suit.HEARTS
        with pytest.raises(dataclasses.FrozenInstanceError):
            card.rank = Rank.ACE

    def test_no_ordering_defined(self):
        # Strength is contextual (the rules object's job), so Card
        # defines no __lt__.
        with pytest.raises(TypeError):
            sorted([Card(Suit.SPADES, Rank.SEVEN), Card(Suit.HEARTS, Rank.ACE)])
