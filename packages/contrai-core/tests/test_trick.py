"""Tests for the Trick class.

Covers add_play (incl. completeness guard), get_plays copy semantics,
get_led_suit, the __len__/is_complete invariants, and get_current_winner
across the lead-suit, trump-beats-non-trump, and trump-over-trump
scenarios.
"""

import pytest

from contrai_core import BasePlayer, Card, Rank, Suit, TrickStateError, Trick


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
        assert trick.is_complete() is False
        assert len(trick) == 0

    def test_trick_does_not_own_trump(self):
        # Trump is round-level state on the Contract, never stored on a
        # Trick. It is passed to get_current_winner at call time instead.
        trick = Trick()
        assert not hasattr(trick, "trump_suit")


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
        assert len(trick) == 4

    def test_add_play_raises_when_complete(self, north, east, south, west):
        trick = Trick()
        for player, rank in [
            (north, Rank.ACE),
            (east, Rank.KING),
            (south, Rank.QUEEN),
            (west, Rank.JACK),
        ]:
            trick.add_play(player, Card(Suit.SPADES, rank))
        with pytest.raises(TrickStateError, match="complete trick"):
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
# get_current_winner — full-trick scenarios
#
# Trump is always passed explicitly at call time; the engine builds
# ``Trick()`` without binding a trump and the contract carries the
# authoritative suit.
# ---------------------------------------------------------------------------


class TestTrickWinnerNoTrump:
    def test_empty_trick_no_winner(self):
        assert Trick().get_current_winner(None) is None

    def test_highest_in_lead_suit_wins(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))
        trick.add_play(east, Card(Suit.HEARTS, Rank.ACE))   # best
        trick.add_play(south, Card(Suit.HEARTS, Rank.KING))
        trick.add_play(west, Card(Suit.HEARTS, Rank.JACK))
        assert trick.get_current_winner(None) is east

    def test_off_suit_cards_cannot_win(self, north, east, south, west):
        """Cards not in lead suit (and not trump) never win — only the
        lead-suit cards compete."""
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))  # leads
        trick.add_play(east, Card(Suit.SPADES, Rank.ACE))     # off-suit, ignored
        trick.add_play(south, Card(Suit.DIAMONDS, Rank.ACE))  # off-suit, ignored
        trick.add_play(west, Card(Suit.CLUBS, Rank.ACE))      # off-suit, ignored
        assert trick.get_current_winner(None) is north


class TestTrickWinnerWithTrump:
    def test_trump_beats_non_trump(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))    # leads
        trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))    # weakest trump
        trick.add_play(south, Card(Suit.HEARTS, Rank.KING))   # follows lead
        trick.add_play(west, Card(Suit.HEARTS, Rank.JACK))    # follows lead
        # The seven of clubs is the only trump and wins despite being the
        # weakest physical card on the table.
        assert trick.get_current_winner(Suit.CLUBS) is east

    def test_higher_trump_beats_lower_trump(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))    # leads, non-trump
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))   # weak trump
        trick.add_play(south, Card(Suit.SPADES, Rank.JACK))   # master trump
        trick.add_play(west, Card(Suit.SPADES, Rank.NINE))    # second-best trump
        # Trump order: Jack > 9 > Ace > 10 > King > Queen > 8 > 7.
        assert trick.get_current_winner(Suit.SPADES) is south

    def test_trump_lead_highest_trump_wins(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.SPADES, Rank.SEVEN))  # leads trump
        trick.add_play(east, Card(Suit.SPADES, Rank.ACE))
        trick.add_play(south, Card(Suit.SPADES, Rank.JACK))   # winner
        trick.add_play(west, Card(Suit.SPADES, Rank.NINE))
        assert trick.get_current_winner(Suit.SPADES) is south

    def test_first_card_wins_if_no_one_else_follows_or_trumps(
        self, north, east, south, west
    ):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))  # leads, low
        trick.add_play(east, Card(Suit.DIAMONDS, Rank.ACE))   # off-suit, no trump
        trick.add_play(south, Card(Suit.CLUBS, Rank.ACE))     # off-suit, no trump
        trick.add_play(west, Card(Suit.DIAMONDS, Rank.KING))  # off-suit, no trump
        assert trick.get_current_winner(Suit.SPADES) is north


# ---------------------------------------------------------------------------
# get_current_winner — partial tricks (winner mid-play, before completion)
# ---------------------------------------------------------------------------


class TestTrickCurrentWinner:
    def test_empty_returns_none(self):
        assert Trick().get_current_winner(Suit.HEARTS) is None

    def test_partial_trick_partner_still_master(self, north, east):
        """Two cards in: lead Ace still beats follow-suit seven."""
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.HEARTS, Rank.SEVEN))
        assert trick.get_current_winner(Suit.SPADES) is north

    def test_partial_trick_opponent_overtrumps_partner(
        self, north, east
    ):
        """Partner (N) led the Ace of hearts; an opponent (E) trumped low
        with the seven of spades. E is now master even though N's card
        outranks it absolutely."""
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))
        assert trick.get_current_winner(Suit.SPADES) is east

    def test_winner_governed_entirely_by_passed_trump(self, north, east):
        """Trump is decided solely by the call-time argument — the trick
        stores none. SPADES passed in makes the seven of spades the only
        trump, so it wins despite the hearts Ace outranking it absolutely."""
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))
        assert trick.get_current_winner(Suit.SPADES) is east

    def test_no_trump_argument_falls_back_to_lead_suit(self, north, east):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))
        # No trump: spade can't beat lead-suit ace.
        assert trick.get_current_winner(None) is north

    def test_no_trump_enum_treated_as_non_trump(self, north, east):
        """``Suit.NO_TRUMP`` is what the engine passes for a no-trump
        contract; no card carries that suit, so play reduces to the
        follow-suit rule exactly as passing ``None`` does."""
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))
        assert trick.get_current_winner(Suit.NO_TRUMP) is north

    def test_higher_trump_takes_over(self, north, east, south):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))   # lead
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))  # weak trump
        trick.add_play(south, Card(Suit.SPADES, Rank.JACK))  # master trump
        assert trick.get_current_winner(Suit.SPADES) is south
