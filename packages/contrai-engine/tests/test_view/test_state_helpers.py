"""Tests for the game-state readers in :mod:`contrai_engine.view.state_helpers`.

These read a slice of round/trick state: the display-order hand sort, the
live trick-winner highlight, the green "↑ playable …" constraint hint, the
belote-badge projection, and the env-tunable AI pacing delay.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from contrai_core import Card, Play, Position, Rank, Suit, TeamSide
from contrai_engine.model.round import RoundScore
from contrai_engine.view.state_helpers import (
    _belote_badges_by_trick,
    _belote_by_position,
    _current_winner,
    _dispute_pot_after,
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
    """`_belote_by_position` — projects announced belotes onto seat keys.

    The trick diamond renders its ★ badge per *seat*, but the round tracks
    belote per ``(player, suit)`` *pair* — a seat can hold two under the
    all-trump ``four`` regime. This is the one place that collapses that
    down, and it reads ``Round.announced_belotes`` rather than the raw
    ``belote_state``, so the regime's own answer about which pairs mark
    is what reaches the screen. Its three empty-result paths are what the
    badge relies on to stay silent.
    """

    class _StubRound:
        """A round exposing only ``announced_belotes``, or not even that."""

        def __init__(self, announced=None, *, has_attribute=True):
            if has_attribute:
                self.announced_belotes = announced

    def test_no_active_round_yields_an_empty_map(self):
        """Before the first deal there is no round to read."""

        assert _belote_by_position(None) == {}

    def test_a_round_without_the_attribute_yields_an_empty_map(self):
        """The attribute is read defensively, so its absence is not a crash."""

        assert _belote_by_position(self._StubRound(has_attribute=False)) == {}

    def test_a_none_value_yields_an_empty_map(self):
        assert _belote_by_position(self._StubRound(None)) == {}

    def test_an_empty_tuple_yields_an_empty_map(self):
        """Nothing announced yet — the badge stays off."""

        assert _belote_by_position(self._StubRound(())) == {}

    def test_each_announced_pair_is_rekeyed_by_seat(self, four_players):
        north, _east, south, _west = four_players
        round_ = self._StubRound((
            (north, Suit.HEARTS),
            (south, Suit.CLUBS),
        ))

        assert _belote_by_position(round_) == {
            Position.NORTH: (Suit.HEARTS,),
            Position.SOUTH: (Suit.CLUBS,),
        }

    def test_two_pairs_in_one_seat_collect_under_that_seat(self, four_players):
        """All trump can put two pairs in a hand; both suits reach the badge."""

        north, *_ = four_players
        round_ = self._StubRound((
            (north, Suit.HEARTS),
            (north, Suit.SPADES),
        ))

        assert _belote_by_position(round_) == {
            Position.NORTH: (Suit.HEARTS, Suit.SPADES),
        }

    def test_announcement_order_is_preserved_within_a_seat(self, four_players):
        """The badge names the suits in the order the table heard them."""

        north, *_ = four_players
        round_ = self._StubRound((
            (north, Suit.SPADES),
            (north, Suit.HEARTS),
        ))

        assert _belote_by_position(round_)[Position.NORTH] == (
            Suit.SPADES, Suit.HEARTS,
        )


class TestBeloteBadgesByTrick:
    """`_belote_badges_by_trick` — each announcement on its own trick.

    The trick grid shows all eight tricks at once, so a badge belongs on
    the trick whose K or Q carried the announcement — not, as in the live
    diamond, on every trick from then on.
    """

    class _PlayState:
        def __init__(self, completed, current=()):
            self.completed_tricks = tuple(completed)
            self.current_trick = tuple(current)

    class _Round:
        def __init__(self, play_state, announced):
            self.play_state = play_state
            self.announced_belotes = announced

    @staticmethod
    def _trick(*plays):
        return tuple(Play(player, Card(suit, rank)) for player, suit, rank in plays)

    def test_no_round_or_no_play_yet_places_nothing(self, four_players):
        north, *_ = four_players
        assert _belote_badges_by_trick(None) == {}
        assert _belote_badges_by_trick(
            self._Round(None, ((north, Suit.HEARTS),))
        ) == {}

    def test_nothing_announced_places_nothing(self, four_players):
        north, east, south, west = four_players
        trick = self._trick(
            (north, Suit.HEARTS, Rank.KING), (east, Suit.HEARTS, Rank.SEVEN),
            (south, Suit.HEARTS, Rank.EIGHT), (west, Suit.HEARTS, Rank.NINE),
        )
        round_ = self._Round(self._PlayState([trick]), ())

        assert _belote_badges_by_trick(round_) == {}

    def test_the_belote_and_rebelote_tricks_each_carry_the_badge(
        self, four_players
    ):
        north, east, south, west = four_players
        belote = self._trick(
            (north, Suit.HEARTS, Rank.KING), (east, Suit.HEARTS, Rank.SEVEN),
            (south, Suit.HEARTS, Rank.EIGHT), (west, Suit.HEARTS, Rank.NINE),
        )
        quiet = self._trick(
            (north, Suit.SPADES, Rank.ACE), (east, Suit.SPADES, Rank.SEVEN),
            (south, Suit.SPADES, Rank.EIGHT), (west, Suit.SPADES, Rank.NINE),
        )
        rebelote = self._trick(
            (north, Suit.HEARTS, Rank.QUEEN), (east, Suit.CLUBS, Rank.SEVEN),
            (south, Suit.CLUBS, Rank.EIGHT), (west, Suit.CLUBS, Rank.NINE),
        )
        round_ = self._Round(
            self._PlayState([belote, quiet, rebelote]),
            ((north, Suit.HEARTS),),
        )

        assert _belote_badges_by_trick(round_) == {
            0: {Position.NORTH: (Suit.HEARTS,)},
            2: {Position.NORTH: (Suit.HEARTS,)},
        }

    def test_a_pair_that_does_not_mark_is_not_placed(self, four_players):
        # Under ``single`` only the first-announced pair marks, and
        # ``announced_belotes`` already leaves the others out.
        north, east, south, west = four_players
        trick = self._trick(
            (north, Suit.HEARTS, Rank.KING), (east, Suit.CLUBS, Rank.KING),
            (south, Suit.HEARTS, Rank.EIGHT), (west, Suit.HEARTS, Rank.NINE),
        )
        round_ = self._Round(
            self._PlayState([trick]), ((north, Suit.HEARTS),)
        )

        assert _belote_badges_by_trick(round_) == {
            0: {Position.NORTH: (Suit.HEARTS,)},
        }

    def test_another_seats_king_of_the_suit_is_not_placed(self, four_players):
        north, east, *_ = four_players
        trick = self._trick(
            (east, Suit.HEARTS, Rank.KING), (north, Suit.HEARTS, Rank.QUEEN),
        )
        round_ = self._Round(
            self._PlayState([], current=trick), ((north, Suit.HEARTS),)
        )

        assert _belote_badges_by_trick(round_) == {
            0: {Position.NORTH: (Suit.HEARTS,)},
        }

    def test_the_trick_in_progress_is_indexed_after_the_completed_ones(
        self, four_players
    ):
        north, east, south, west = four_players
        done = self._trick(
            (north, Suit.SPADES, Rank.ACE), (east, Suit.SPADES, Rank.SEVEN),
            (south, Suit.SPADES, Rank.EIGHT), (west, Suit.SPADES, Rank.NINE),
        )
        live = self._trick((north, Suit.HEARTS, Rank.QUEEN))
        round_ = self._Round(
            self._PlayState([done], current=live), ((north, Suit.HEARTS),)
        )

        assert _belote_badges_by_trick(round_) == {
            1: {Position.NORTH: (Suit.HEARTS,)},
        }

    def test_a_non_honour_of_the_pairs_suit_is_not_placed(self, four_players):
        north, *_ = four_players
        live = self._trick((north, Suit.HEARTS, Rank.ACE))
        round_ = self._Round(
            self._PlayState([], current=live), ((north, Suit.HEARTS),)
        )

        assert _belote_badges_by_trick(round_) == {}


def _score(*, held=0, carried=None):
    return RoundScore(
        scores={}, contract_made=True, unannounced_slam=None, marks={},
        belote_points={}, card_points={}, last_trick_side=None, multiplier=1,
        held=held, carried_over=carried or {},
    )


class TestDisputePotAfter:
    def test_an_unscored_round_leaves_the_pot_it_was_handed(self):
        round_ = SimpleNamespace(dispute_pot=161, round_score=None)
        assert _dispute_pot_after(round_) == 161

    def test_a_held_round_adds_its_points(self):
        round_ = SimpleNamespace(dispute_pot=161, round_score=_score(held=161))
        assert _dispute_pot_after(round_) == 322

    def test_a_payout_empties_it(self):
        round_ = SimpleNamespace(
            dispute_pot=161, round_score=_score(carried={TeamSide.EW: 161})
        )
        assert _dispute_pot_after(round_) == 0

    def test_an_ordinary_round_leaves_no_pot(self):
        round_ = SimpleNamespace(dispute_pot=0, round_score=_score())
        assert _dispute_pot_after(round_) == 0
