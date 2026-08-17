"""Tests for the Trick class.

Covers add_play (incl. completeness guard), get_plays copy semantics,
get_led_suit, the __len__/is_complete invariants, and get_current_winner
across the lead-suit, trump-beats-non-trump, and trump-over-trump
scenarios.
"""

import pytest

from contrai_core import (
    BasePlayer,
    Card,
    ObservedPlay,
    Play,
    Position,
    Rank,
    Suit,
    TrickRecord,
    TrickStateError,
    Trick,
    TrumpVariant,
)
from contrai_core.trick import current_winner


@pytest.fixture
def north():
    """North-seat player."""
    return BasePlayer("North", Position.NORTH)


@pytest.fixture
def east():
    """East-seat player."""
    return BasePlayer("East", Position.EAST)


@pytest.fixture
def south():
    """South-seat player."""
    return BasePlayer("South", Position.SOUTH)


@pytest.fixture
def west():
    """West-seat player."""
    return BasePlayer("West", Position.WEST)


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
        """``TrumpVariant.NO_TRUMP`` is what the engine passes for a no-trump
        contract; no card carries that suit, so play reduces to the
        follow-suit rule exactly as passing ``None`` does."""
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))
        assert trick.get_current_winner(TrumpVariant.NO_TRUMP) is north

    def test_higher_trump_takes_over(self, north, east, south):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))   # lead
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))  # weak trump
        trick.add_play(south, Card(Suit.SPADES, Rank.JACK))  # master trump
        assert trick.get_current_winner(Suit.SPADES) is south


# ---------------------------------------------------------------------------
# current_winner — parity with Trick.get_current_winner
#
# Trick.get_current_winner delegates to the module-level current_winner
# function so the winner rule can be reused without instantiating a Trick.
# Both must agree on every scenario the method itself is tested against.
# ---------------------------------------------------------------------------


class TestCurrentWinnerParity:
    def test_empty_plays(self):
        trick = Trick()
        assert current_winner(trick.plays, Suit.HEARTS) == trick.get_current_winner(Suit.HEARTS)
        assert current_winner(trick.plays, Suit.HEARTS) is None

    def test_partial_trick(self, north, east):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.HEARTS, Rank.SEVEN))
        assert current_winner(trick.plays, Suit.SPADES) == trick.get_current_winner(Suit.SPADES)
        assert current_winner(trick.plays, Suit.SPADES) is north

    def test_full_trick(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))
        trick.add_play(east, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(south, Card(Suit.HEARTS, Rank.KING))
        trick.add_play(west, Card(Suit.HEARTS, Rank.JACK))
        assert current_winner(trick.plays, None) == trick.get_current_winner(None)
        assert current_winner(trick.plays, None) is east

    def test_trump_beats_non_trump(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))
        trick.add_play(south, Card(Suit.HEARTS, Rank.KING))
        trick.add_play(west, Card(Suit.HEARTS, Rank.JACK))
        assert current_winner(trick.plays, Suit.CLUBS) == trick.get_current_winner(Suit.CLUBS)
        assert current_winner(trick.plays, Suit.CLUBS) is east

    def test_overtrump(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))
        trick.add_play(south, Card(Suit.SPADES, Rank.JACK))
        trick.add_play(west, Card(Suit.SPADES, Rank.NINE))
        assert current_winner(trick.plays, Suit.SPADES) == trick.get_current_winner(Suit.SPADES)
        assert current_winner(trick.plays, Suit.SPADES) is south

    def test_no_trump_led_suit_wins(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))
        trick.add_play(east, Card(Suit.SPADES, Rank.ACE))
        trick.add_play(south, Card(Suit.DIAMONDS, Rank.ACE))
        trick.add_play(west, Card(Suit.CLUBS, Rank.ACE))
        assert current_winner(trick.plays, None) == trick.get_current_winner(None)
        assert current_winner(trick.plays, None) is north

    def test_discard_does_not_win(self, north, east):
        """An off-suit, non-trump discard never overtakes the lead card."""
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))
        trick.add_play(east, Card(Suit.DIAMONDS, Rank.ACE))
        assert current_winner(trick.plays, Suit.SPADES) == trick.get_current_winner(Suit.SPADES)
        assert current_winner(trick.plays, Suit.SPADES) is north


class TestAllTrumpWinner:
    """Every suit is trump, so only the led one competes (§6.4)."""

    def test_highest_led_suit_card_wins(self, north, east, south, west):
        plays = [
            (north, Card(Suit.SPADES, Rank.ACE)),
            (east, Card(Suit.SPADES, Rank.JACK)),
            (south, Card(Suit.SPADES, Rank.NINE)),
            (west, Card(Suit.SPADES, Rank.TEN)),
        ]
        assert current_winner(plays, TrumpVariant.ALL_TRUMP) is east

    def test_an_off_suit_jack_cannot_take_the_trick(self, north, east, south, west):
        # No cross-suit cutting at all trump (§6.4).
        plays = [
            (north, Card(Suit.SPADES, Rank.SEVEN)),
            (east, Card(Suit.HEARTS, Rank.JACK)),
            (south, Card(Suit.CLUBS, Rank.JACK)),
            (west, Card(Suit.DIAMONDS, Rank.JACK)),
        ]
        assert current_winner(plays, TrumpVariant.ALL_TRUMP) is north

    def test_trick_agrees_with_the_free_function(self, north, east, south, west):
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(east, Card(Suit.HEARTS, Rank.NINE))
        trick.add_play(south, Card(Suit.SPADES, Rank.JACK))
        trick.add_play(west, Card(Suit.HEARTS, Rank.TEN))
        assert trick.get_current_winner(TrumpVariant.ALL_TRUMP) is east
        assert current_winner(trick.plays, TrumpVariant.ALL_TRUMP) is east


# ---------------------------------------------------------------------------
# TrickRecord — the immutable completed-trick value
# ---------------------------------------------------------------------------


def _four_plays(north, east, south, west):
    """A completed heart trick as Play records; East's Ace wins plain."""
    return (
        Play(north, Card(Suit.HEARTS, Rank.SEVEN)),
        Play(east, Card(Suit.HEARTS, Rank.ACE)),
        Play(south, Card(Suit.HEARTS, Rank.KING)),
        Play(west, Card(Suit.CLUBS, Rank.SEVEN)),
    )


class TestTrickRecordConstruction:
    def test_takes_a_single_iterable_of_four(self, north, east, south, west):
        plays = _four_plays(north, east, south, west)
        record = TrickRecord(iter(plays))
        assert tuple(record) == plays

    @pytest.mark.parametrize("count", [0, 1, 3, 5])
    def test_rejects_anything_but_four_plays(
        self, count, north, east, south, west
    ):
        plays = (_four_plays(north, east, south, west) * 2)[:count]
        with pytest.raises(TrickStateError, match="exactly 4"):
            TrickRecord(plays)

    def test_is_a_tuple_and_compares_like_one(self, north, east, south, west):
        plays = _four_plays(north, east, south, west)
        record = TrickRecord(plays)
        assert isinstance(record, tuple)
        assert record == plays
        # Unpacks and slices exactly like the bare tuple it types.
        first, *_rest = record
        assert first is record[0]
        assert record[1:3] == plays[1:3]

    def test_is_immutable(self, north, east, south, west):
        record = TrickRecord(_four_plays(north, east, south, west))
        with pytest.raises(TypeError):
            record[0] = None
        # __slots__ = () — no attribute can be stashed on a record.
        with pytest.raises(AttributeError):
            record.cached_winner = None


class TestTrickRecordLedSuit:
    def test_led_suit_is_the_first_cards_suit(self, north, east, south, west):
        record = TrickRecord(_four_plays(north, east, south, west))
        assert record.led_suit is Suit.HEARTS


class TestTrickRecordWinner:
    def test_winner_returns_the_winning_play_record(
        self, north, east, south, west
    ):
        record = TrickRecord(_four_plays(north, east, south, west))
        best = record.winner(None)
        # The record itself comes back — not just the who-slot — and it is
        # the very object held in the trick.
        assert best is record[1]
        assert best.player is east

    def test_winner_is_trump_aware(self, north, east, south, west):
        record = TrickRecord(_four_plays(north, east, south, west))
        # West's lone club is the only trump under a clubs contract.
        assert record.winner(Suit.CLUBS) is record[3]

    def test_no_trump_variant_matches_none(self, north, east, south, west):
        record = TrickRecord(_four_plays(north, east, south, west))
        assert record.winner(TrumpVariant.NO_TRUMP) is record.winner(None)

    def test_winner_works_on_observed_play_records(self):
        # The record type is generic: sealed (position, card) observation
        # records rank exactly like live-player plays.
        record = TrickRecord(
            (
                ObservedPlay(Position.NORTH, Card(Suit.HEARTS, Rank.SEVEN)),
                ObservedPlay(Position.EAST, Card(Suit.HEARTS, Rank.ACE)),
                ObservedPlay(Position.SOUTH, Card(Suit.HEARTS, Rank.KING)),
                ObservedPlay(Position.WEST, Card(Suit.CLUBS, Rank.SEVEN)),
            )
        )
        assert record.winner(None).position is Position.EAST
        assert record.winner(Suit.CLUBS).position is Position.WEST
        assert record.led_suit is Suit.HEARTS

    def test_agrees_with_current_winner(self, north, east, south, west):
        plays = _four_plays(north, east, south, west)
        record = TrickRecord(plays)
        for trump in (None, Suit.CLUBS, Suit.HEARTS, TrumpVariant.NO_TRUMP):
            assert record.winner(trump)[0] is current_winner(
                list(plays), trump
            )
