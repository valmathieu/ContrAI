"""Tests for the Trick class.

Covers add_play (incl. completeness guard), get_plays copy semantics,
get_led_suit, the size/__len__/is_empty/is_complete invariants, and
get_winner across the lead-suit, trump-beats-non-trump, and
trump-over-trump scenarios from contree-domain.md §6.4.
"""

import pytest

from contrai_core.card import Card
from contrai_core.player import BasePlayer
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit


@pytest.fixture
def north():
    return BasePlayer("North", "North")


@pytest.fixture
def east():
    return BasePlayer("East", "East")


@pytest.fixture
def south():
    return BasePlayer("South", "South")


@pytest.fixture
def west():
    return BasePlayer("West", "West")


# ---------------------------------------------------------------------------
# Construction & basic queries
# ---------------------------------------------------------------------------


class TestTrickConstruction:
    def test_default_construction(self):
        trick = Trick()
        assert trick.plays == []
        assert trick.trump_suit is None
        assert trick.is_empty() is True
        assert trick.is_complete() is False
        assert len(trick) == 0
        assert trick.size() == 0

    def test_construction_with_trump_suit(self):
        trick = Trick(trump_suit=Suit.SPADES)
        assert trick.trump_suit is Suit.SPADES


# ---------------------------------------------------------------------------
# add_play and completion guards
# ---------------------------------------------------------------------------


class TestTrickAddPlay:
    def test_add_single_play(self, north):
        trick = Trick()
        card = Card(Suit.SPADES, Rank.ACE)
        trick.add_play(north, card)
        assert len(trick) == 1
        assert trick.plays == [(north, card)]
        assert trick.is_empty() is False

    def test_add_four_plays_completes_trick(self, north, east, south, west):
        trick = Trick()
        for player, rank in [
            (north, Rank.ACE),
            (east, Rank.KING),
            (south, Rank.QUEEN),
            (west, Rank.JACK),
        ]:
            trick.add_play(player, Card(Suit.SPADES, rank))
        assert trick.is_complete() is True
        assert trick.size() == 4

    def test_add_play_raises_when_complete(self, north, east, south, west):
        trick = Trick()
        for player, rank in [
            (north, Rank.ACE),
            (east, Rank.KING),
            (south, Rank.QUEEN),
            (west, Rank.JACK),
        ]:
            trick.add_play(player, Card(Suit.SPADES, rank))
        with pytest.raises(ValueError, match="complete trick"):
            trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))


# ---------------------------------------------------------------------------
# get_plays / get_cards / get_led_suit
# ---------------------------------------------------------------------------


class TestTrickAccessors:
    def test_get_plays_returns_copy(self, north):
        trick = Trick()
        card = Card(Suit.HEARTS, Rank.SEVEN)
        trick.add_play(north, card)
        plays = trick.get_plays()
        plays.clear()
        # Mutating the returned list must not affect the trick.
        assert len(trick) == 1

    def test_get_cards_returns_only_cards(self, north, east):
        trick = Trick()
        c1 = Card(Suit.HEARTS, Rank.SEVEN)
        c2 = Card(Suit.HEARTS, Rank.KING)
        trick.add_play(north, c1)
        trick.add_play(east, c2)
        assert trick.get_cards() == [c1, c2]

    def test_get_led_suit_empty(self):
        assert Trick().get_led_suit() is None

    def test_get_led_suit_returns_first_card_suit(self, north, east):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))
        # Subsequent cards shouldn't change the lead.
        trick.add_play(east, Card(Suit.SPADES, Rank.ACE))
        assert trick.get_led_suit() is Suit.HEARTS


# ---------------------------------------------------------------------------
# get_winner — domain §6.4
# ---------------------------------------------------------------------------


class TestTrickWinnerNoTrump:
    def test_empty_trick_no_winner(self):
        assert Trick().get_winner() is None

    def test_highest_in_lead_suit_wins(self, north, east, south, west):
        trick = Trick()  # no trump
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))
        trick.add_play(east, Card(Suit.HEARTS, Rank.ACE))   # best
        trick.add_play(south, Card(Suit.HEARTS, Rank.KING))
        trick.add_play(west, Card(Suit.HEARTS, Rank.JACK))
        assert trick.get_winner() is east

    def test_off_suit_cards_cannot_win(self, north, east, south, west):
        """Cards not in lead suit (and not trump) never win — only the
        lead-suit cards compete."""
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))  # leads
        trick.add_play(east, Card(Suit.SPADES, Rank.ACE))     # off-suit, ignored
        trick.add_play(south, Card(Suit.DIAMONDS, Rank.ACE))  # off-suit, ignored
        trick.add_play(west, Card(Suit.CLUBS, Rank.ACE))      # off-suit, ignored
        assert trick.get_winner() is north


class TestTrickWinnerWithTrump:
    def test_trump_beats_non_trump(self, north, east, south, west):
        trick = Trick(trump_suit=Suit.CLUBS)
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))    # leads
        trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))    # weakest trump
        trick.add_play(south, Card(Suit.HEARTS, Rank.KING))   # follows lead
        trick.add_play(west, Card(Suit.HEARTS, Rank.JACK))    # follows lead
        # The seven of clubs is the only trump and wins despite being the
        # weakest physical card on the table.
        assert trick.get_winner() is east

    def test_higher_trump_beats_lower_trump(self, north, east, south, west):
        trick = Trick(trump_suit=Suit.SPADES)
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))    # leads, non-trump
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))   # weak trump
        trick.add_play(south, Card(Suit.SPADES, Rank.JACK))   # master trump
        trick.add_play(west, Card(Suit.SPADES, Rank.NINE))    # second-best trump
        # Trump order: Jack > 9 > Ace > 10 > King > Queen > 8 > 7.
        assert trick.get_winner() is south

    def test_trump_lead_highest_trump_wins(self, north, east, south, west):
        trick = Trick(trump_suit=Suit.SPADES)
        trick.add_play(north, Card(Suit.SPADES, Rank.SEVEN))  # leads trump
        trick.add_play(east, Card(Suit.SPADES, Rank.ACE))
        trick.add_play(south, Card(Suit.SPADES, Rank.JACK))   # winner
        trick.add_play(west, Card(Suit.SPADES, Rank.NINE))
        assert trick.get_winner() is south

    def test_first_card_wins_if_no_one_else_follows_or_trumps(
        self, north, east, south, west
    ):
        trick = Trick(trump_suit=Suit.SPADES)
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))  # leads, low
        trick.add_play(east, Card(Suit.DIAMONDS, Rank.ACE))   # off-suit, no trump
        trick.add_play(south, Card(Suit.CLUBS, Rank.ACE))     # off-suit, no trump
        trick.add_play(west, Card(Suit.DIAMONDS, Rank.KING))  # off-suit, no trump
        assert trick.get_winner() is north

    def test_winner_with_partial_trick(self, north, east):
        # get_winner doesn't require a complete trick — leading card wins
        # so far in this two-card snapshot.
        trick = Trick(trump_suit=Suit.SPADES)
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.HEARTS, Rank.SEVEN))
        assert trick.get_winner() is north
