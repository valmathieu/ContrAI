"""Tests for the debug screen in :mod:`contrai_engine.view.screens.debug`.

Covers the all-hands debug panel — title/seed suffix, the N/W/S/E seat
ordering, the per-seat hand row and its ``(empty)`` placeholder, and the
in-play summary line with its rank-only entries and exhausted-suit
marker — the AI-rationale panel, and the autoplay pause text.
"""

from __future__ import annotations

from contrai_core import Card, PassBid, Rank, Suit
from contrai_engine.model.player import (
    BidDecision,
    CardDecision,
    Rationale,
    RuleCitation,
)
from contrai_engine.view.screens.debug import (
    _autoplay_pause_text,
    _panel_ai_rationale,
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


class TestPanelAiRationale:
    """The debug strip's AI-rationale panel.

    Its data comes from ``debug_state.last_decisions``, which has its own
    tests; what is pinned here is the panel — the title, the placeholder
    while no AI seat has decided, and that each part of a rationale
    reaches the rendered text.
    """

    class _Round:
        """Just the two lists ``last_decisions`` reads."""

        def __init__(self, card_decisions=(), bid_decisions=()):
            self.card_decisions = list(card_decisions)
            self.bid_decisions = list(bid_decisions)

    def _decision(self, **kwargs):
        return CardDecision(
            Card(Suit.SPADES, Rank.JACK),
            Rationale(
                kwargs.pop("rule", "pull trump"),
                kwargs.pop("detail", "led the strongest trump."),
                **kwargs,
            ),
        )

    def test_title(self):
        panel = _panel_ai_rationale(self._Round())
        assert panel.title.plain == "Debug — AI rationale"

    def test_placeholder_before_any_ai_has_decided(self):
        panel = _panel_ai_rationale(self._Round())
        assert "(no AI decision yet this round)" in panel.renderable.plain

    def test_placeholder_uses_dim_style(self):
        text = _panel_ai_rationale(self._Round()).renderable
        assert any(span.style == DIM for span in text.spans)

    def test_a_missing_round_still_renders(self):
        """The strip draws on frames where no round exists yet."""
        assert "(no AI decision" in _panel_ai_rationale(None).renderable.plain

    def test_the_action_and_rule_reach_the_text(self):
        panel = _panel_ai_rationale(self._Round([self._decision()]))
        body = panel.renderable.plain
        assert "J♠" in body
        assert "pull trump" in body

    def test_the_detail_reaches_the_text(self):
        panel = _panel_ai_rationale(self._Round([self._decision()]))
        assert "led the strongest trump." in panel.renderable.plain

    def test_the_alternatives_are_listed_under_over(self):
        panel = _panel_ai_rationale(
            self._Round([self._decision(considered=("9 ♠", "King ♠"))])
        )
        body = panel.renderable.plain
        assert "over: 9 ♠ · King ♠" in body

    def test_a_decision_with_no_alternatives_omits_the_over_line(self):
        panel = _panel_ai_rationale(self._Round([self._decision()]))
        assert "over:" not in panel.renderable.plain

    def test_a_citation_renders_knob_value_and_effect(self):
        panel = _panel_ai_rationale(
            self._Round([
                self._decision(
                    citations=(
                        RuleCitation(
                            "under_trump_exemption",
                            "True",
                            "discarded instead of under-trumping",
                        ),
                    )
                )
            ])
        )
        body = panel.renderable.plain
        assert "under_trump_exemption = True" in body
        assert "discarded instead of under-trumping" in body

    def test_several_decisions_render_newest_first(self):
        panel = _panel_ai_rationale(
            self._Round([
                self._decision(rule="first", detail="a."),
                self._decision(rule="second", detail="b."),
            ])
        )
        body = panel.renderable.plain
        assert body.index("second") < body.index("first")

    def test_a_bid_decision_renders_too(self):
        panel = _panel_ai_rationale(
            self._Round(
                bid_decisions=[
                    BidDecision(
                        PassBid(None),
                        Rationale("no contract in hand", "nothing to bid."),
                    )
                ]
            )
        )
        assert "no contract in hand" in panel.renderable.plain
