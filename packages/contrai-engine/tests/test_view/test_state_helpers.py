"""Tests for the game-state readers in :mod:`contrai_engine.view.state_helpers`.

These read a slice of round/trick state: the display-order hand sort, the
live trick-winner highlight, the green "↑ playable …" constraint hint, the
belote-badge projection, and the env-tunable AI pacing delay.
"""

from __future__ import annotations

import pytest

from contrai_core import Card, Play, Position, Rank, Suit
from contrai_engine.view.state_helpers import (
    _belote_by_position,
    _current_winner,
    _explain_constraint,
    _resolve_delay,
    _sort_hand_for_display,
    _trick_index,
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
# _trick_index
# ======================================================================


class TestTrickIndex:
    """Which of the eight tricks is on the table."""

    class _StubPlayState:
        def __init__(self, trick_number):
            self.trick_number = trick_number

    class _StubRound:
        def __init__(self, trick_number=None):
            self.play_state = (
                TestTrickIndex._StubPlayState(trick_number)
                if trick_number is not None
                else None
            )

    @staticmethod
    def _plays(count):
        """``count`` placeholder plays — only the length is read."""
        return (None,) * count

    def test_no_round_falls_back_to_the_first_trick(self):
        assert _trick_index(None, ()) == 1

    def test_unseeded_play_state_falls_back_to_the_first_trick(self):
        """Bidding: the round exists but play has not been seeded."""
        assert _trick_index(self._StubRound(), ()) == 1

    @pytest.mark.parametrize("played", [0, 1, 2, 3])
    def test_an_in_progress_trick_is_the_next_one(self, played):
        """Two tricks completed → the one being played is the third."""
        round_ = self._StubRound(trick_number=2)
        assert _trick_index(round_, self._plays(played)) == 3

    def test_a_just_completed_trick_is_not_counted_twice(self):
        """The play state advances the instant the fourth card lands.

        At that moment the trick still on the table is the one the
        state has just folded into its completed history, so counting
        it again would number it one too high.
        """
        round_ = self._StubRound(trick_number=3)
        assert _trick_index(round_, self._plays(4)) == 3

    def test_clamps_to_the_eight_tricks_of_a_round(self):
        round_ = self._StubRound(trick_number=8)
        assert _trick_index(round_, self._plays(4)) == 8
        # Past the last trick there is no ninth to advance to.
        assert _trick_index(round_, ()) == 8


# ======================================================================
# _explain_constraint
# ======================================================================


class TestExplainConstraint:
    """Human-readable hint under the hand row."""

    def _plays(self, *plays):
        """The trick on the table, as the core ``Play`` records the hint reads."""
        return tuple(Play(player, card) for player, card in plays)

    def test_empty_trick_is_your_lead(self, four_players):
        _, _, south, _ = four_players
        south.hand.clear()
        south.hand.append(Card(Suit.SPADES, Rank.ACE))
        result = _explain_constraint(south, (), list(south.hand), Suit.HEARTS)
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
        plays = self._plays((west, Card(Suit.SPADES, Rank.KING)))
        playable = south.hand.cards_of_suit(Suit.SPADES)
        result = _explain_constraint(south, plays, playable, Suit.HEARTS)
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
        plays = self._plays((west, Card(Suit.CLUBS, Rank.KING)))
        playable = south.hand.cards_of_suit(Suit.HEARTS)  # only trumps legal
        result = _explain_constraint(south, plays, playable, Suit.HEARTS)
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
        plays = self._plays((west, Card(Suit.CLUBS, Rank.KING)))
        # Playable list includes non-trump (Round logic decides — when partner
        # leads, the engine returns the full hand). Here we simulate "free".
        playable = list(south.hand)
        result = _explain_constraint(south, plays, playable, Suit.HEARTS)
        assert "free discard" in result.plain


class TestBeloteByPosition:
    """`_belote_by_position` — projects belote state onto seat keys.

    The trick diamond renders its ★ badge per *seat*, but the round tracks
    belote per ``(player, suit)`` *pair* — a seat can hold two under the
    all-trump ``four`` regime. This is the one place that collapses that
    down, so its three empty-result paths are what the badge relies on to
    stay silent, and its precedence rule is what keeps one seat to one
    badge.
    """

    class _StubRound:
        """A round exposing only ``belote_state``, or not even that."""

        def __init__(self, belote_state=None, *, has_attribute=True):
            if has_attribute:
                self.belote_state = belote_state

    def test_no_active_round_yields_an_empty_map(self):
        """Before the first deal there is no round to read."""

        assert _belote_by_position(None) == {}

    def test_a_round_without_belote_state_yields_an_empty_map(self):
        """The attribute is read defensively, so its absence is not a crash."""

        assert _belote_by_position(self._StubRound(has_attribute=False)) == {}

    def test_a_none_belote_state_yields_an_empty_map(self):
        assert _belote_by_position(self._StubRound(None)) == {}

    def test_an_empty_belote_state_yields_an_empty_map(self):
        """Nothing declared yet — the badge stays off."""

        assert _belote_by_position(self._StubRound({})) == {}

    def test_a_populated_state_is_rekeyed_by_position(self, four_players):
        north, _east, south, _west = four_players
        round_ = self._StubRound({
            (north, Suit.HEARTS): "belote",
            (south, Suit.HEARTS): "rebelote",
        })

        assert _belote_by_position(round_) == {
            Position.NORTH: "belote",
            Position.SOUTH: "rebelote",
        }

    def test_two_pairs_in_one_seat_render_one_badge(self, four_players):
        """All trump can put two pairs in a hand; the seat still gets one."""

        north, *_ = four_players
        round_ = self._StubRound({
            (north, Suit.HEARTS): "belote",
            (north, Suit.SPADES): "belote",
        })

        assert _belote_by_position(round_) == {Position.NORTH: "belote"}

    @pytest.mark.parametrize("first, second", [
        (Suit.HEARTS, Suit.SPADES),
        (Suit.SPADES, Suit.HEARTS),
    ])
    def test_rebelote_outranks_belote_whichever_pair_reached_it(
        self, four_players, first, second
    ):
        """The strongest kind wins, independent of dict order."""

        north, *_ = four_players
        round_ = self._StubRound({
            (north, first): "rebelote",
            (north, second): "belote",
        })

        assert _belote_by_position(round_) == {Position.NORTH: "rebelote"}

    def test_values_are_preserved_verbatim(self, four_players):
        """The helper re-keys; it must not reinterpret the kind string."""

        _north, east, *_ = four_players
        round_ = self._StubRound({(east, Suit.CLUBS): "★ Belote"})

        assert _belote_by_position(round_)[Position.EAST] == "★ Belote"
