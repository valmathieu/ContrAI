"""Tests for the in-game table in :mod:`contrai_engine.view.screens.trick`.

Covers the logic hiding inside the renderers: the running-points sum,
the trick-index clamping in the Round / Current-trick panels, the
diamond's winner highlight / pending seat / led marker / belote badge,
the hand panel's interactive vs neutral modes and constraint hints, the
playable-pill styling, and the per-play prompt texts.
"""

from __future__ import annotations

from rich.console import Console

from contrai_core import (
    Card,
    Play,
    Position,
    Rank,
    Suit,
    TeamSide,
    TrumpVariant,
    rules_for,
)
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
    fixture — a mapping of side to the tricks it took, each trick a
    sequence of :class:`~contrai_core.Play` records.
    """

    def __init__(self, team_tricks, trump):
        rules = rules_for(trump)
        self.card_points_by_side = {
            side: sum(
                rules.points(play.card)
                for trick in team_tricks.get(side, [])
                for play in trick
            )
            for side in TeamSide
        }
        self.trick_counts_by_side = {
            side: len(team_tricks.get(side, [])) for side in TeamSide
        }
        self.trick_winners: tuple = ()


class _StubRound:
    def __init__(self, *, contract=None, dealer=None,
                 round_number=1, team_tricks=None, announced_belotes=()):
        self.contract = contract
        self.dealer = dealer
        self.round_number = round_number
        self.team_tricks = team_tricks or {}
        self.announced_belotes = announced_belotes
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
        ns_trick = (
            Play(north, Card(Suit.HEARTS, Rank.JACK)),
            Play(east, Card(Suit.SPADES, Rank.ACE)),
        )
        # E-W captured a pointless 7♣.
        ew_trick = (Play(east, Card(Suit.CLUBS, Rank.SEVEN)),)
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

    def test_playing_phase_shows_the_trick_index(self, four_players):
        """The panel renders the index it is handed, verbatim.

        Working out *which* trick is on the table — and clamping it to
        the eight of a round — is ``_trick_index``'s job; the panel
        never sees the trick itself.
        """
        north, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract)
        text = _panel_round(
            round_, phase="playing", trick_index=3
        ).renderable.plain
        assert "3 of 8" in text

    def test_playing_phase_shows_the_running_round_points(self, four_players):
        """The live score is on by default — the §9.7 aid's default state."""
        north, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        text = _panel_round(
            _StubRound(contract=contract), phase="playing", trick_index=3
        ).renderable.plain
        assert "Round pts:" in text

    def test_live_score_off_omits_the_round_points_row(self, four_players):
        """Switched off, the aid hides the running pile — and nothing else."""
        north, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        text = _panel_round(
            _StubRound(contract=contract),
            phase="playing",
            trick_index=3,
            live_score=False,
        ).renderable.plain
        assert "Round pts:" not in text
        # The trick counter is not part of the aid: it stays either way.
        assert "3 of 8" in text

    def test_live_score_is_inert_during_bidding(self, four_players):
        """Bidding has no running pile to hide, so the flag changes nothing."""
        *_, west = four_players
        on = _panel_round(_StubRound(dealer=west), phase="bidding").renderable.plain
        off = _panel_round(
            _StubRound(dealer=west), phase="bidding", live_score=False
        ).renderable.plain
        assert on == off


class TestRenderDiamond:
    """Winner star, pending ?, led marker, dots, belote badge."""

    def test_live_winner_is_the_trump_cutter(self, four_players):
        north, east, *_ = four_players
        plays = (
            Play(north, Card(Suit.SPADES, Rank.ACE)),
            Play(east, Card(Suit.HEARTS, Rank.SEVEN)),  # trump cut
        )
        text = _render_diamond(
            plays, Suit.HEARTS,
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
        plays = (
            Play(north, Card(Suit.SPADES, Rank.ACE)),
            Play(east, Card(Suit.HEARTS, Rank.SEVEN)),
        )
        text = _render_diamond(
            plays, Suit.HEARTS,
            pending_position=None, winner_position=Position.NORTH,
            dimmed=True, width=18,
        ).plain
        assert "N A♠ ★" in text
        assert "E 7♥ ★" not in text

    def test_dimmed_rendering_drops_the_led_marker(self, four_players):
        north, *_ = four_players
        plays = (Play(north, Card(Suit.SPADES, Rank.ACE)),)
        text = _render_diamond(
            plays, Suit.HEARTS,
            pending_position=None, winner_position=Position.NORTH,
            dimmed=True, width=18,
        ).plain
        assert "(led)" not in text

    def test_belote_badge_renders_under_the_announcing_seat(
        self, four_players
    ):
        text = _render_diamond(
            (), Suit.HEARTS,
            pending_position=Position.NORTH, winner_position=None,
            dimmed=False, width=42,
            belote_by_position={Position.SOUTH: (Suit.HEARTS,)},
        ).plain
        assert "★ Belote" in text

    def test_no_badge_without_announcements(self, four_players):
        text = _render_diamond(
            (), Suit.HEARTS,
            pending_position=Position.NORTH, winner_position=None,
            dimmed=False, width=42,
        ).plain
        assert "Belote" not in text


class TestBeloteBadgeSpelling:
    """The badge names its pair's suit exactly when more than one exists.

    A suit contract has one belote suit, so naming it would only repeat
    the trump line. All trump has four, and a bare ★ Belote under two
    seats is precisely what makes a ``single`` table look like it is
    paying four bonuses.
    """

    def _badges(self, belote_by_position, trump, **kwargs):
        return _render_diamond(
            (), trump,
            pending_position=None, winner_position=None,
            dimmed=False, width=42,
            belote_by_position=belote_by_position,
            **kwargs,
        ).plain

    def test_a_suit_contract_names_no_suit(self):
        text = self._badges(
            {Position.SOUTH: (Suit.HEARTS,)}, Suit.HEARTS
        )
        assert "★ Belote" in text
        assert "★ Belote ♥" not in text

    def test_all_trump_names_the_pair_suit(self):
        text = self._badges(
            {Position.SOUTH: (Suit.CLUBS,)}, TrumpVariant.ALL_TRUMP
        )
        assert "★ Belote ♣" in text

    def test_two_pairs_get_a_multiplier_and_both_suits(self):
        text = self._badges(
            {Position.NORTH: (Suit.CLUBS, Suit.DIAMONDS)},
            TrumpVariant.ALL_TRUMP,
        )
        assert "★ Belote ×2 (♣♦)" in text

    def test_each_seat_gets_its_own_badge(self):
        text = self._badges(
            {
                Position.NORTH: (Suit.CLUBS, Suit.DIAMONDS),
                Position.SOUTH: (Suit.HEARTS,),
            },
            TrumpVariant.ALL_TRUMP,
        )
        assert "★ Belote ×2 (♣♦)" in text
        assert "★ Belote ♥" in text

    def test_no_trump_never_badges(self):
        # No belote suit exists at no trump, so nothing can be announced;
        # a stray entry must not produce a suit-naming badge.
        text = self._badges(
            {Position.SOUTH: ()}, TrumpVariant.NO_TRUMP
        )
        assert "Belote" not in text


class TestBeloteBadgeCompaction:
    """The narrow ``Last trick`` panel spells crowded badges short.

    Its diamond is 18 cells wide and the W / E badges share one row, so
    two full badges cannot fit. Compaction is derived from the crowding,
    not configured: a lone badge is always spelled in full, and only the
    ``four`` regime can ever badge two seats.
    """

    def _row(self, belote_by_position, **kwargs):
        return _render_diamond(
            (), TrumpVariant.ALL_TRUMP,
            pending_position=None, winner_position=None,
            dimmed=True, width=18,
            belote_by_position=belote_by_position,
            **kwargs,
        ).plain

    def test_one_badge_is_spelled_in_full_even_when_narrow(self):
        text = self._row(
            {Position.NORTH: (Suit.CLUBS,)}, narrow_badges=True
        )
        assert "★ Belote ♣" in text

    def test_two_badges_compact_to_star_plus_glyph(self):
        text = self._row(
            {
                Position.WEST: (Suit.CLUBS,),
                Position.EAST: (Suit.HEARTS,),
            },
            narrow_badges=True,
        )
        assert "★♣" in text
        assert "★♥" in text
        assert "Belote" not in text

    def test_a_compacted_multi_pair_badge_keeps_its_multiplier(self):
        text = self._row(
            {
                Position.WEST: (Suit.CLUBS, Suit.DIAMONDS),
                Position.EAST: (Suit.HEARTS, Suit.SPADES),
            },
            narrow_badges=True,
        )
        assert "★×2 ♣♦" in text
        assert "★×2 ♥♠" in text

    def test_the_compacted_west_east_row_fits_the_panel(self):
        # The case the compaction exists for: the widest crowded row must
        # still measure at most the diamond's 18 cells.
        text = self._row(
            {
                Position.WEST: (Suit.CLUBS, Suit.DIAMONDS),
                Position.EAST: (Suit.HEARTS, Suit.SPADES),
            },
            narrow_badges=True,
        )
        assert max(len(line) for line in text.split("\n")) <= 18

    def test_a_lone_multi_pair_badge_also_fits(self):
        text = self._row(
            {Position.NORTH: (Suit.CLUBS, Suit.DIAMONDS)},
            narrow_badges=True,
        )
        assert "★ Belote ×2 (♣♦)" in text
        assert max(len(line) for line in text.split("\n")) <= 18

    def test_the_wide_panel_never_compacts(self):
        text = _render_diamond(
            (), TrumpVariant.ALL_TRUMP,
            pending_position=None, winner_position=None,
            dimmed=False, width=42,
            belote_by_position={
                Position.WEST: (Suit.CLUBS,),
                Position.EAST: (Suit.HEARTS,),
            },
        ).plain
        assert "★ Belote ♣" in text
        assert "★ Belote ♥" in text


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
        round_ = _StubRound(contract=contract)
        plays = (Play(north, Card(Suit.SPADES, Rank.ACE)),)
        panel = _panel_current_trick(
            round_, plays, "playing", None, None, trick_index=4
        )
        assert "(#4)" in panel.title.plain

    def test_title_keeps_the_finished_trick_number_when_won(
        self, four_players
    ):
        north, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract)
        plays = (Play(north, Card(Suit.SPADES, Rank.ACE)),)
        panel = _panel_current_trick(
            round_, plays, "trick_won", None, north, trick_index=3
        )
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
        """The echoed trick is numbered one below the one on the table."""
        north, east, *_ = four_players
        contract = _StubContract(100, Suit.HEARTS, player=north)
        plays = (
            Play(north, Card(Suit.SPADES, Rank.ACE)),
            Play(east, Card(Suit.SPADES, Rank.SEVEN)),
        )
        round_ = _StubRound(contract=contract)
        panel = _panel_last_trick(round_, (plays, north), trick_index=3)
        assert "Won: N" in panel.renderable.plain
        assert "Last trick (#2)" in panel.title.plain


class TestPanelBeloteBadges:
    """Which spelling each panel gets, end to end from the round.

    The two panels read the same ``announced_belotes``; only the narrow
    one is allowed to compact, and only when the badges would collide.
    """

    @staticmethod
    def _all_trump_round(north, east, announced):
        contract = _StubContract(100, TrumpVariant.ALL_TRUMP, player=north)
        return _StubRound(contract=contract, announced_belotes=announced)

    def test_last_trick_compacts_two_crowded_badges(self, four_players):
        north, east, _south, west = four_players
        round_ = self._all_trump_round(north, east, (
            (west, Suit.CLUBS),
            (east, Suit.HEARTS),
        ))
        plays = (Play(north, Card(Suit.SPADES, Rank.ACE)),)
        text = _panel_last_trick(round_, (plays, north), trick_index=3)
        text = text.renderable.plain
        assert "★♣" in text
        assert "★♥" in text

    def test_last_trick_spells_a_lone_badge_in_full(self, four_players):
        north, east, *_ = four_players
        round_ = self._all_trump_round(north, east, ((east, Suit.HEARTS),))
        plays = (Play(north, Card(Suit.SPADES, Rank.ACE)),)
        panel = _panel_last_trick(round_, (plays, north), trick_index=3)
        assert "★ Belote ♥" in panel.renderable.plain

    def test_current_trick_never_compacts(self, four_players):
        north, east, _south, west = four_players
        round_ = self._all_trump_round(north, east, (
            (west, Suit.CLUBS),
            (east, Suit.HEARTS),
        ))
        panel = _panel_current_trick(
            round_, (), "playing", north, None, trick_index=1
        )
        text = panel.renderable.plain
        assert "★ Belote ♣" in text
        assert "★ Belote ♥" in text


def _rendered(panel) -> str:
    """The panel as the terminal actually draws it, crop included.

    A panel's ``renderable`` always holds the full text; a fixed
    ``height`` discards the overflow only at print time. So the crop is
    only observable through a real ``Console``.
    """
    console = Console(width=60, force_terminal=False, no_color=True)
    with console.capture() as capture:
        console.print(panel)
    return capture.get()


class TestDiamondPanelHeight:
    """The panels grow a row per belote badge row instead of cropping.

    Both trick panels used to be pinned at ``height=8``, which fits the
    diamond plus exactly *one* badge row. The all-trump ``four`` regime
    can badge three rows (N, the shared W/E row, and S), and every row
    past the first silently pushed the ``Won: …`` / ``→ …`` footer out of
    the panel.
    """

    @staticmethod
    def _round(four_players, announced):
        north, *_ = four_players
        contract = _StubContract(100, TrumpVariant.ALL_TRUMP, player=north)
        return _StubRound(contract=contract, announced_belotes=announced)

    @staticmethod
    def _three_badge_rows(four_players):
        north, east, south, west = four_players
        return (
            (north, Suit.CLUBS), (north, Suit.DIAMONDS),
            (west, Suit.HEARTS), (east, Suit.SPADES),
            (south, Suit.HEARTS),
        )

    def test_no_badges_keeps_the_established_height(self, four_players):
        north, *_ = four_players
        round_ = self._round(four_players, ())
        panel = _panel_current_trick(
            round_, (), "playing", north, None, trick_index=1
        )
        assert panel.height == 8

    def test_current_trick_footer_survives_three_badge_rows(
        self, four_players
    ):
        north, _east, south, _west = four_players
        round_ = self._round(four_players, self._three_badge_rows(four_players))
        panel = _panel_current_trick(
            round_, (), "playing", south, None, trick_index=1
        )
        assert "→ South's turn" in _rendered(panel)

    def test_current_trick_keeps_every_badge_row(self, four_players):
        north, _east, south, _west = four_players
        round_ = self._round(four_players, self._three_badge_rows(four_players))
        panel = _panel_current_trick(
            round_, (), "playing", south, None, trick_index=1
        )
        text = _rendered(panel)
        assert "★ Belote ×2 (♣♦)" in text     # N row
        assert "★ Belote ♥" in text           # W on the shared row
        assert "★ Belote ♠" in text           # E on the shared row

    def test_last_trick_footer_survives_three_badge_rows(self, four_players):
        north, *_ = four_players
        round_ = self._round(four_players, self._three_badge_rows(four_players))
        plays = (Play(north, Card(Suit.SPADES, Rank.ACE)),)
        panel = _panel_last_trick(round_, (plays, north), trick_index=4)
        assert "Won: N" in _rendered(panel)

    def test_the_two_panels_stay_flush(self, four_players):
        """Side by side in the grid, so they must agree on height."""

        north, _east, south, _west = four_players
        round_ = self._round(four_players, self._three_badge_rows(four_players))
        plays = (Play(north, Card(Suit.SPADES, Rank.ACE)),)
        current = _panel_current_trick(
            round_, (), "playing", south, None, trick_index=4
        )
        last = _panel_last_trick(round_, (plays, north), trick_index=4)
        assert current.height == last.height

    def test_the_placeholder_matches_the_grown_panel(self, four_players):
        """Trick 1 badges before any trick has completed to echo."""

        north, _east, south, _west = four_players
        round_ = self._round(four_players, self._three_badge_rows(four_players))
        current = _panel_current_trick(
            round_, (), "playing", south, None, trick_index=1
        )
        assert _panel_last_trick(round_, None, trick_index=1).height == (
            current.height
        )

    def test_bidding_phase_is_unaffected(self, four_players):
        """No trick, no badges — the auction diamond keeps its height."""

        north, *_ = four_players
        round_ = self._round(four_players, self._three_badge_rows(four_players))
        panel = _panel_current_trick(round_, None, "bidding", north, None)
        assert panel.height == 8


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
            south, (), cards, "playing", None, interactive=True
        )
        assert "your lead — anything goes" in panel.renderable.plain

    def test_following_hand_hints_must_follow_led_suit(self, four_players):
        north, _e, south, _w = four_players
        cards = self._stock_hand(south)
        contract = _StubContract(100, Suit.HEARTS, player=north)
        round_ = _StubRound(contract=contract)
        plays = (Play(north, Card(Suit.HEARTS, Rank.SEVEN)),)
        playable = [c for c in cards if c.suit == Suit.HEARTS]
        panel = _panel_hand(
            south, plays, playable, "playing", round_, interactive=True
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
        plays = (Play(north, Card(Suit.SPADES, Rank.KING)),)
        panel = _panel_hand(
            south, plays, cards, "playing", round_, interactive=True
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
