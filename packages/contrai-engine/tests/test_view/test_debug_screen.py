"""Tests for the debug screen in :mod:`contrai_engine.view.screens.debug`.

Covers the all-hands debug panel — title/seed suffix, the N/W/S/E seat
ordering, the per-seat hand row and its ``(empty)`` placeholder, and the
in-play summary line with its rank-only entries and exhausted-suit
marker — plus the autoplay pause text.
"""

from __future__ import annotations

from contrai_core import Card, Rank, Suit
from contrai_engine.view.screens.debug import (
    _autoplay_pause_text,
    _panel_debug_hands,
)
from contrai_engine.view.theme import DIM


class TestPanelDebugHands:
    """The debug strip's all-hands panel."""

    def test_title_shows_seed_when_given(self, four_players):
        panel = _panel_debug_hands(four_players, Suit.HEARTS, seed=42)
        assert panel.title.plain == "Debug — all hands · seed 42"

    def test_title_omits_seed_suffix_when_none(self, four_players):
        panel = _panel_debug_hands(four_players, Suit.HEARTS)
        assert panel.title.plain == "Debug — all hands"
        assert "seed" not in panel.title.plain

    def test_seat_rows_follow_canonical_n_w_s_e_order(self, four_players):
        """Rows render N/W/S/E top-to-bottom regardless of input order.

        This is the anticlockwise seating ``Position`` itself defines,
        and the order ``debug_state.deal_lines`` writes to the log — the
        panel matches it so the two can be read side by side.
        """
        north, east, south, west = four_players
        text = _panel_debug_hands(
            [west, south, east, north], Suit.HEARTS
        ).renderable.plain
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines[0].startswith("N:")
        assert lines[1].startswith("W:")
        assert lines[2].startswith("S:")
        assert lines[3].startswith("E:")

    def test_known_card_label_rendered(self, four_players):
        north, *_ = four_players
        north.hand.extend([Card(Suit.SPADES, Rank.ACE)])
        text = _panel_debug_hands(four_players, None).renderable.plain
        assert "A♠" in text

    def test_empty_hand_renders_dim_placeholder(self, four_players):
        north, *_ = four_players
        north.hand.extend([Card(Suit.SPADES, Rank.ACE)])
        # East/South/West are dealt nothing this round — their rows must
        # read "(empty)" rather than an empty cell.
        text = _panel_debug_hands(four_players, None).renderable.plain
        assert "(empty)" in text

    def test_empty_hand_placeholder_uses_dim_style(self, four_players):
        """The ``(empty)`` placeholder must actually carry the DIM style,
        not just appear as plain text."""
        north, *_ = four_players
        north.hand.extend([Card(Suit.SPADES, Rank.ACE)])
        renderable = _panel_debug_hands(four_players, None).renderable
        dim_spans = [s for s in renderable.spans if DIM in str(s.style)]
        assert any(
            renderable.plain[s.start:s.end] == "(empty)" for s in dim_spans
        )

    def test_in_play_line_present(self, four_players):
        north, *_ = four_players
        north.hand.extend([Card(Suit.SPADES, Rank.ACE)])
        text = _panel_debug_hands(four_players, None).renderable.plain
        assert "In play:" in text

    def test_in_play_entries_are_rank_only(self, four_players):
        """Cards in the in-play line carry no suit glyph of their own.

        Each group is already headed by its suit, so repeating the
        glyph per card would only widen the line.
        """
        north, *_ = four_players
        north.hand.extend(
            [Card(Suit.SPADES, Rank.ACE), Card(Suit.SPADES, Rank.TEN)]
        )
        text = _panel_debug_hands(four_players, None).renderable.plain
        in_play_line = next(
            ln for ln in text.splitlines() if "In play:" in ln
        )
        assert "♠ A 10" in in_play_line
        # The hand row above still spells cards out in full; only this
        # line drops the per-card glyph.
        assert "A♠" not in in_play_line

    def test_exhausted_suit_shows_dash_in_play_line(self, four_players):
        north, *_ = four_players
        # Only spades dealt — hearts/diamonds/clubs are exhausted.
        north.hand.extend([Card(Suit.SPADES, Rank.ACE)])
        text = _panel_debug_hands(four_players, None).renderable.plain
        in_play_line = next(
            ln for ln in text.splitlines() if "In play:" in ln
        )
        assert "—" in in_play_line

    def test_exhausted_suit_dash_uses_dim_style(self, four_players):
        """The ``—`` exhausted-suit marker must carry the DIM style."""
        north, *_ = four_players
        north.hand.extend([Card(Suit.SPADES, Rank.ACE)])
        renderable = _panel_debug_hands(four_players, None).renderable
        dim_spans = [s for s in renderable.spans if DIM in str(s.style)]
        assert any(
            renderable.plain[s.start:s.end] == "—" for s in dim_spans
        )

    def test_renders_without_raising_when_all_hands_empty(self, four_players):
        """Edge case: every hand empty, every suit exhausted, no trump."""
        panel = _panel_debug_hands(four_players, None)
        text = panel.renderable.plain
        assert "(empty)" in text
        assert "In play:" in text
        assert "—" in text


class TestAutoplayPauseText:
    """The dim autoplay notice that replaces the press-Enter prompt."""

    def test_contains_autoplay_marker_and_message(self):
        text = _autoplay_pause_text("North plays 7♣.").plain
        assert "(autoplay)" in text
        assert "North plays 7♣." in text
