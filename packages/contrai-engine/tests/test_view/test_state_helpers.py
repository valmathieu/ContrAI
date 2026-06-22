"""Tests for the game-state readers in :mod:`contrai_engine.view.state_helpers`.

These read a slice of round/trick state: the display-order hand sort, the
live trick-winner highlight, the green "↑ playable …" constraint hint, and
the env-tunable AI pacing delay.
"""

from __future__ import annotations

import pytest

from contrai_core import Card, Rank, Suit, Trick
from contrai_engine.view.state_helpers import (
    _current_winner,
    _explain_constraint,
    _resolve_delay,
    _sort_hand_for_display,
)


class TestResolveDelay:
    """Env-var pacing resolver — used by the AI hooks."""

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("CONTRAI_AI_TEST", raising=False)
        assert _resolve_delay("CONTRAI_AI_TEST", default=0.7) == 0.7

    def test_reads_float_from_env(self, monkeypatch):
        monkeypatch.setenv("CONTRAI_AI_TEST", "0.25")
        assert _resolve_delay("CONTRAI_AI_TEST", default=0.7) == 0.25

    def test_garbage_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CONTRAI_AI_TEST", "fast")
        assert _resolve_delay("CONTRAI_AI_TEST", default=0.7) == 0.7

    def test_negative_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("CONTRAI_AI_TEST", "-2.0")
        assert _resolve_delay("CONTRAI_AI_TEST", default=0.7) == 0.0


# ======================================================================
# _sort_hand_for_display
# ======================================================================


class TestSortHandForDisplay:
    """Display-order sort: trump-first, then suit-by-suit, rank desc."""

    def test_no_trump_default_order(self):
        cards = [
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.QUEEN),
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.ACE),
        ]
        result = _sort_hand_for_display(cards, trump_suit=None)
        # Default suit order: S, H, D, C
        assert [c.suit for c in result] == [
            Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS,
        ]

    def test_trump_goes_first(self):
        cards = [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.KING),
            Card(Suit.CLUBS, Rank.NINE),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        assert result[0].suit == Suit.HEARTS
        # Non-trump suits keep S, D, C order with hearts removed.
        assert [c.suit for c in result[1:]] == [
            Suit.SPADES, Suit.DIAMONDS, Suit.CLUBS,
        ]

    def test_within_suit_rank_desc_no_trump(self):
        """Within a non-trump suit, highest rank first (normal order)."""
        cards = [
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.JACK),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        assert [c.rank for c in result] == [Rank.ACE, Rank.JACK, Rank.SEVEN]

    def test_within_trump_suit_uses_trump_order(self):
        """Inside the trump suit, the Jack out-ranks the Ace (trump order)."""
        cards = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.NINE),
            Card(Suit.HEARTS, Rank.SEVEN),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        # Trump order: 7, 8, Q, K, 10, A, 9, J — so J on top, then 9, then A.
        assert [c.rank for c in result] == [
            Rank.JACK, Rank.NINE, Rank.ACE, Rank.SEVEN,
        ]

    def test_empty_suit_skipped(self):
        cards = [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.KING),
        ]
        result = _sort_hand_for_display(cards, trump_suit=None)
        assert len(result) == 2
        assert {c.suit for c in result} == {Suit.SPADES, Suit.DIAMONDS}

    def test_empty_hand_returns_empty(self):
        assert _sort_hand_for_display([], trump_suit=None) == []
        assert _sort_hand_for_display([], trump_suit=Suit.SPADES) == []


# ======================================================================
# _current_winner
# ======================================================================


class TestCurrentWinner:
    """Live trick-winner computation for the diamond gold-pill highlight."""

    def test_empty_plays_returns_none(self):
        assert _current_winner([], trump_suit=Suit.HEARTS) is None
        assert _current_winner([], trump_suit=None) is None

    def test_single_play_wins(self, four_players):
        north, _, _, _ = four_players
        plays = [(north, Card(Suit.SPADES, Rank.SEVEN))]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is north

    def test_highest_of_led_suit_wins_no_trump_played(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),
            (north, Card(Suit.SPADES, Rank.TEN)),
            (east, Card(Suit.SPADES, Rank.ACE)),  # ace wins
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is east

    def test_off_suit_non_trump_cannot_win(self, four_players):
        """Discarding off-suit (no trump) doesn't take the trick."""
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.SEVEN)),
            (north, Card(Suit.DIAMONDS, Rank.ACE)),  # off suit, no trump
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is west

    def test_trump_beats_non_trump(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.ACE)),
            (north, Card(Suit.HEARTS, Rank.SEVEN)),  # weakest trump still wins
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is north

    def test_highest_trump_wins(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),     # led
            (north, Card(Suit.HEARTS, Rank.NINE)),    # trump
            (east, Card(Suit.HEARTS, Rank.JACK)),     # jack is top trump
            (south, Card(Suit.HEARTS, Rank.ACE)),     # ace below jack/9
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is east

    def test_no_trump_contract_uses_led_suit(self, four_players):
        """``trump_suit=None`` (or NoTrump) means highest led-suit card wins."""
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),
            (north, Card(Suit.SPADES, Rank.ACE)),
            (east, Card(Suit.HEARTS, Rank.JACK)),     # off suit, can't win
        ]
        assert _current_winner(plays, trump_suit=None) is north


# ======================================================================
# _explain_constraint
# ======================================================================


class TestExplainConstraint:
    """Human-readable hint under the hand row."""

    def _make_trick(self, *plays):
        t = Trick()
        for player, card in plays:
            t.add_play(player, card)
        return t

    def test_empty_trick_is_your_lead(self, four_players):
        _, _, south, _ = four_players
        south.hand.clear()
        south.hand.append(Card(Suit.SPADES, Rank.ACE))
        empty = Trick()
        result = _explain_constraint(south, empty, list(south.hand), Suit.HEARTS)
        assert "your lead" in result.plain.lower()

    def test_must_follow_led_suit(self, four_players):
        north, _, south, west = four_players
        # West led ♠K, South has ♠s in hand → must follow.
        south.hand.clear()
        south.hand.extend([
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
        ])
        trick = self._make_trick((west, Card(Suit.SPADES, Rank.KING)))
        playable = south.hand.cards_of_suit(Suit.SPADES)
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "must follow" in result.plain
        assert "♠" in result.plain

    def test_must_trump_when_partner_not_winning(self, four_players):
        north, east, south, west = four_players
        # West led ♣K, South has no clubs, has hearts (trump) → must trump.
        south.hand.clear()
        south.hand.extend([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.QUEEN),
        ])
        trick = self._make_trick((west, Card(Suit.CLUBS, Rank.KING)))
        playable = south.hand.cards_of_suit(Suit.HEARTS)  # only trumps legal
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "must trump" in result.plain
        # The leader's position label should appear in the hint.
        assert "W" in result.plain

    def test_free_discard_when_no_led_suit_no_trump_obligation(self, four_players):
        """No led-suit in hand, playable includes non-trump → free discard."""
        north, _, south, west = four_players
        south.hand.clear()
        south.hand.extend([
            Card(Suit.DIAMONDS, Rank.QUEEN),
            Card(Suit.DIAMONDS, Rank.TEN),
        ])
        trick = self._make_trick((west, Card(Suit.CLUBS, Rank.KING)))
        # Playable list includes non-trump (Round logic decides — when partner
        # leads, the engine returns the full hand). Here we simulate "free".
        playable = list(south.hand)
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "free discard" in result.plain
