"""Tests for the in-game table in :mod:`contrai_engine.view.screens.trick`.

Covers the logic hiding inside the renderers: the running-points sum,
the trick-index clamping in the Round / Current-trick panels, the
diamond's winner highlight / pending seat / led marker / belote badge,
the hand panel's interactive vs neutral modes and constraint hints, the
playable-pill styling, and the per-play prompt texts.
"""

from __future__ import annotations

from contrai_core import Card, Position, Rank, Suit, TeamSide, Trick, rules_for
from contrai_engine.model.player import HumanPlayer
from contrai_engine.view.screens.trick import (
    _ai_card_announcement,
    _card_prompt_text,
    _panel_current_trick,
    _panel_hand,
    _panel_last_trick,
    _panel_round,
    _render_card_cell,
    _render_diamond,
    _round_running_points,
    _trick_won_prompt_text,
)
from contrai_engine.view.theme import GREEN_BG


class _StubContract:
    def __init__(self, value, suit, team_name=TeamSide.NS, player=None):
        self.value = value
        self.suit = suit
        class _T:
            pass
        self.team = _T()
        self.team.name = team_name
        self.player = player
        self.double = False
        self.redouble = False


class _StubPlayState:
    """Stand-in for the core play state's per-side derivations.

    The screens ask a play state only what each side has captured, so
    the stub derives those two mappings once from the ``team_tricks``
    shape the tests already write — the fixture stays a pile of tricks,
    the screen reads the derivations.
    """

    def __init__(self, team_tricks, trump):
        rules = rules_for(trump)
        self.card_points_by_side = {
            side: sum(
                rules.points(card)
                for tr in team_tricks.get(side, [])
                for _, card in tr.get_plays()
            )
            for side in TeamSide
        }
        self.trick_counts_by_side = {
            side: len(team_tricks.get(side, [])) for side in TeamSide
        }
        self.trick_winners: tuple = ()


class _StubRound:
    def __init__(self, *, contract=None, tricks=None, dealer=None,
                 round_number=1, team_tricks=None):
        self.contract = contract
        self.tricks = tricks if tricks is not None else []
        self.dealer = dealer
        self.round_number = round_number
        self.team_tricks = team_tricks or {}
        self.play_state = _StubPlayState(
            self.team_tricks, contract.suit if contract else None
        )


class TestRoundRunningPoints:
    """Trump-aware sum of each team's captured pile so far."""

    def test_no_round_or_contract_yields_zeros(self):
        assert _round_running_points(None) == (0, 0)
        assert _round_running_points(_StubRound(contract=None)) == (0, 0)

    def test_sums_trump_aware_points_per_team(self, four_players):
        north, east, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        # N-S captured J♥ (trump jack = 20) + A♠ (11) = 31.
        ns_trick = Trick()
        ns_trick.add_play(north, Card(Suit.HEARTS, Rank.JACK))
        ns_trick.add_play(east, Card(Suit.SPADES, Rank.ACE))
        # E-W captured a pointless 7♣.
        ew_trick = Trick()
        ew_trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))
        round_ = _StubRound(
            contract=contract,
            team_tricks={
                TeamSide.NS: [ns_trick],
                TeamSide.EW: [ew_trick],
            },
        )
        assert _round_running_points(round_) == (31, 0)


class TestPanelRound:
    """The Round info panel: phase line, trick index, clamping."""

    def test_bidding_phase_shows_dealer(self, four_players):
        *_, west = four_players
        round_ = _StubRound(dealer=west, round_number=2)
        panel = _panel_round(round_, phase="bidding")
        text = panel.renderable.plain
        assert "Bidding in progress" in text
        assert "West" in text
        assert "Round #2" in panel.title.plain

    def test_no_contract_renders_trump_dash(self):
        text = _panel_round(_StubRound(), phase="bidding").renderable.plain
        assert "Trump:" in text
        assert "—" in text

    def test_playing_phase_counts_the_in_flight_trick(self, four_players):
        north, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract, tricks=[Trick(), Trick()])
        text = _panel_round(round_, phase="playing").renderable.plain
        # Two done + the one in flight = trick 3 of 8.
        assert "3 of 8" in text

    def test_trick_index_clamps_at_eight(self, four_players):
        north, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract, tricks=[Trick() for _ in range(8)])
        text = _panel_round(round_, phase="playing").renderable.plain
        assert "8 of 8" in text
        assert "9 of 8" not in text


class TestRenderDiamond:
    """Winner star, pending ?, led marker, dots, belote badge."""

    def test_live_winner_is_the_trump_cutter(self, four_players):
        north, east, *_ = four_players
        trick = Trick()
        trick.add_play(north, Card(Suit.SPADES, Rank.ACE))
        trick.add_play(east, Card(Suit.HEARTS, Rank.SEVEN))  # trump cut
        text = _render_diamond(
            trick, Suit.HEARTS,
            pending_position=Position.SOUTH, winner_position=None,
            dimmed=False, width=42,
        ).plain
        assert "E 7♥ ★" in text  # the low trump beats the off-suit ace
        assert "N A♠ (led)" in text
        assert "S ?" in text
        assert "W ·" in text

    def test_explicit_winner_position_overrides_live_computation(
        self, four_players
    ):
        north, east, *_ = four_players
        trick = Trick()
        trick.add_play(north, Card(Suit.SPADES, Rank.ACE))
        trick.add_play(east, Card(Suit.HEARTS, Rank.SEVEN))
        text = _render_diamond(
            trick, Suit.HEARTS,
            pending_position=None, winner_position=Position.NORTH,
            dimmed=True, width=18,
        ).plain
        assert "N A♠ ★" in text
        assert "E 7♥ ★" not in text

    def test_dimmed_rendering_drops_the_led_marker(self, four_players):
        north, *_ = four_players
        trick = Trick()
        trick.add_play(north, Card(Suit.SPADES, Rank.ACE))
        text = _render_diamond(
            trick, Suit.HEARTS,
            pending_position=None, winner_position=Position.NORTH,
            dimmed=True, width=18,
        ).plain
        assert "(led)" not in text

    def test_belote_badge_renders_under_the_announcing_seat(
        self, four_players
    ):
        text = _render_diamond(
            Trick(), Suit.HEARTS,
            pending_position=Position.NORTH, winner_position=None,
            dimmed=False, width=42,
            belote_by_position={Position.SOUTH: "belote"},
        ).plain
        assert "★ Belote" in text

    def test_no_badge_without_announcements(self, four_players):
        text = _render_diamond(
            Trick(), Suit.HEARTS,
            pending_position=Position.NORTH, winner_position=None,
            dimmed=False, width=42,
        ).plain
        assert "Belote" not in text


class TestPanelCurrentTrick:
    """Phase routing and the trick-number suffix in the title."""

    def test_bidding_phase_flags_the_human_turn(self, four_players):
        human = HumanPlayer("You", Position.SOUTH)
        panel = _panel_current_trick(
            None, None, "bidding", human, None, bidding_history=[]
        )
        assert "→ Your bid" in panel.renderable.plain
        assert "Bidding" in panel.title.plain

    def test_bidding_phase_names_the_ai_turn(self, four_players):
        north, *_ = four_players
        panel = _panel_current_trick(
            None, None, "bidding", north, None, bidding_history=[]
        )
        assert "→ North to bid" in panel.renderable.plain

    def test_playing_without_a_trick_shows_none(self):
        panel = _panel_current_trick(None, None, "playing", None, None)
        # The placeholder Text is centered inside an Align wrapper.
        assert "(none)" in panel.renderable.renderable.plain

    def test_title_counts_the_in_flight_trick_while_playing(
        self, four_players
    ):
        north, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract, tricks=[Trick() for _ in range(3)])
        trick = Trick()
        trick.add_play(north, Card(Suit.SPADES, Rank.ACE))
        panel = _panel_current_trick(round_, trick, "playing", None, None)
        assert "(#4)" in panel.title.plain

    def test_title_keeps_the_finished_trick_number_when_won(
        self, four_players
    ):
        north, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract, tricks=[Trick() for _ in range(3)])
        trick = Trick()
        trick.add_play(north, Card(Suit.SPADES, Rank.ACE))
        panel = _panel_current_trick(round_, trick, "trick_won", None, north)
        assert "(#3)" in panel.title.plain
        assert "Won: N" in panel.renderable.plain


class TestPanelLastTrick:
    """The dimmed echo of the previous trick."""

    def test_placeholder_before_any_trick_completes(self):
        panel = _panel_last_trick(None, None)
        # The placeholder Text is centered inside an Align wrapper.
        assert "(none)" in panel.renderable.renderable.plain
        assert panel.title.plain == "Last trick"

    def test_shows_winner_and_trick_number(self, four_players):
        north, east, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        trick = Trick()
        trick.add_play(north, Card(Suit.SPADES, Rank.ACE))
        trick.add_play(east, Card(Suit.SPADES, Rank.SEVEN))
        round_ = _StubRound(contract=contract, tricks=[trick, Trick()])
        panel = _panel_last_trick(round_, (trick, north))
        assert "Won: N" in panel.renderable.plain
        assert "Last trick (#2)" in panel.title.plain


class TestPanelHand:
    """Interactive vs neutral modes, hints, and the empty-hand slot."""

    @staticmethod
    def _stock_hand(player):
        cards = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.KING),
            Card(Suit.SPADES, Rank.QUEEN),
        ]
        player.hand.extend(cards)
        return cards

    def test_leading_hand_hints_anything_goes(self, four_players):
        _n, _e, south, _w = four_players
        cards = self._stock_hand(south)
        panel = _panel_hand(
            south, Trick(), cards, "playing", None, interactive=True
        )
        assert "your lead — anything goes" in panel.renderable.plain

    def test_following_hand_hints_must_follow_led_suit(self, four_players):
        north, _e, south, _w = four_players
        cards = self._stock_hand(south)
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract)
        trick = Trick()
        trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))
        playable = [c for c in cards if c.suit == Suit.HEARTS]
        panel = _panel_hand(
            south, trick, playable, "playing", round_, interactive=True
        )
        assert "must follow ♥" in panel.renderable.plain

    def test_void_in_led_suit_hints_must_trump(self, four_players):
        north, _e, south, _w = four_players
        # South holds only trump (hearts) — void in the led spades.
        cards = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.SEVEN),
        ]
        south.hand.extend(cards)
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract)
        trick = Trick()
        trick.add_play(north, Card(Suit.SPADES, Rank.KING))
        panel = _panel_hand(
            south, trick, cards, "playing", round_, interactive=True
        )
        text = panel.renderable.plain
        assert "must trump" in text
        assert "N led K♠" in text

    def test_neutral_frame_reads_cards_remaining(self, four_players):
        _n, _e, south, _w = four_players
        self._stock_hand(south)
        panel = _panel_hand(
            south, None, None, "playing", None, interactive=False
        )
        text = panel.renderable.plain
        assert "3 cards remaining" in text
        assert "playable" not in text

    def test_bidding_phase_hints_no_obligation(self, four_players):
        _n, _e, south, _w = four_players
        self._stock_hand(south)
        panel = _panel_hand(
            south, None, None, "bidding", None, interactive=True
        )
        assert "no card-play obligation yet" in panel.renderable.plain

    def test_empty_hand_keeps_the_slot_with_a_placeholder(
        self, four_players
    ):
        _n, _e, south, _w = four_players
        panel = _panel_hand(
            south, None, None, "trick_won", None, interactive=False
        )
        assert "(no cards left)" in panel.renderable.plain


class TestRenderCardCell:
    """Cell text plus the green pill on playable cards."""

    def test_cell_reads_index_rank_and_glyph(self):
        cell = _render_card_cell(3, Card(Suit.SPADES, Rank.KING), True, "playing")
        assert cell.plain == "[3] K♠"

    def test_playable_cell_carries_the_green_pill(self):
        cell = _render_card_cell(1, Card(Suit.SPADES, Rank.KING), True, "playing")
        assert any(GREEN_BG in str(span.style) for span in cell.spans)

    def test_unplayable_cell_has_no_green_pill(self):
        cell = _render_card_cell(1, Card(Suit.SPADES, Rank.KING), False, "playing")
        assert not any(GREEN_BG in str(span.style) for span in cell.spans)


class TestPromptTexts:
    """The per-play prompt and announcement lines."""

    def test_card_prompt_flags_a_forced_play(self):
        text = _card_prompt_text([Card(Suit.HEARTS, Rank.ACE)], 5).plain
        assert "Only one legal play." in text
        assert "[1-5]" in text

    def test_card_prompt_stays_quiet_with_choices(self):
        playable = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.KING),
        ]
        text = _card_prompt_text(playable, 8).plain
        assert "Only one legal play." not in text
        assert "[1-8]" in text

    def test_ai_card_announcement(self, four_players):
        north, *_ = four_players
        text = _ai_card_announcement(north, Card(Suit.SPADES, Rank.ACE)).plain
        assert text == "N plays A♠."

    def test_trick_won_prompt_congratulates_the_human(self):
        human = HumanPlayer("You", Position.SOUTH)
        text = _trick_won_prompt_text(human).plain
        assert "You won the trick." in text

    def test_trick_won_prompt_names_the_ai_winner(self, four_players):
        north, *_ = four_players
        text = _trick_won_prompt_text(north).plain
        assert "N won the trick." in text
