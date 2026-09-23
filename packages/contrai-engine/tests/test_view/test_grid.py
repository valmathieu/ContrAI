"""Tests for the replay's trick grid, :mod:`contrai_engine.view.screens.grid`.

The builders take a round and return renderables; what is asserted is the
logic inside them — which trick lands in which cell, what each footer
says, how the header reads the round's state, and that the whole screen
holds its width. Panels are laid out through a real console, because a
``Group`` of ``Panel``\\ s only has text once something renders it.
"""

from __future__ import annotations

import io

from rich.console import Console

from contrai_core import Card, Play, Position, Rank, Suit, TeamSide
from contrai_core.bid import PassBid
from contrai_engine.view.screens.grid import (
    GRID_WIDTH,
    TRICK_WIDTH,
    _grid_result_text,
    _grid_tricks,
    _panel_grid_header,
    _panel_grid_trick,
    _render_trick_grid,
    _replay_grid_prompt_text,
)


def _rendered(renderable, width: int = 120) -> str:
    console = Console(file=io.StringIO(), width=width, no_color=True)
    console.print(renderable)
    return console.file.getvalue()


def _trick(*plays):
    return tuple(Play(player, Card(suit, rank)) for player, suit, rank in plays)


class _Contract:
    value = 100
    suit = Suit.HEARTS
    double = False
    redouble = False

    def __init__(self, player):
        self.player = player


class _PlayState:
    def __init__(self, completed=(), winners=(), current=(), points=None):
        self.completed_tricks = tuple(completed)
        self.trick_winners = tuple(winners)
        self.current_trick = tuple(current)
        self.card_points_by_side = points or {TeamSide.NS: 0, TeamSide.EW: 0}


class _Score:
    """A stand-in ``RoundScore``: only its presence is read here."""


class _Round:
    def __init__(self, *, contract=None, play_state=None, auction=None,
                 scored=False, made=True, scores=None, announced=()):
        self.round_number = 3
        self.contract = contract
        self.play_state = play_state
        self.auction = auction
        self.round_score = _Score() if scored else None
        self.contract_made = made if scored else None
        self.round_scores = scores or {}
        self.announced_belotes = announced


def _hearts_trick(four_players):
    north, east, south, west = four_players
    return _trick(
        (north, Suit.HEARTS, Rank.JACK), (east, Suit.HEARTS, Rank.SEVEN),
        (south, Suit.HEARTS, Rank.ACE), (west, Suit.HEARTS, Rank.EIGHT),
    )


class TestGridTricks:
    def test_no_play_state_means_no_tricks(self):
        assert _grid_tricks(None) == []
        assert _grid_tricks(_Round()) == []

    def test_completed_tricks_carry_their_winners(self, four_players):
        north, *_ = four_players
        trick = _hearts_trick(four_players)
        round_ = _Round(play_state=_PlayState([trick], [north]))

        assert _grid_tricks(round_) == [(trick, north)]

    def test_the_trick_in_progress_comes_last_with_no_winner(
        self, four_players
    ):
        north, *_ = four_players
        done = _hearts_trick(four_players)
        live = _trick((north, Suit.SPADES, Rank.ACE))
        round_ = _Round(play_state=_PlayState([done], [north], current=live))

        assert _grid_tricks(round_)[-1] == (live, None)


class TestPanelGridTrick:
    def test_a_trick_not_reached_says_so(self):
        panel = _panel_grid_trick(
            5, None, None, Suit.HEARTS, badges={}, height=8
        )

        assert "(not played)" in _rendered(panel)
        assert panel.width == TRICK_WIDTH
        assert panel.height == 8

    def test_a_won_trick_names_its_winner_and_points(self, four_players):
        north, *_ = four_players
        text = _rendered(_panel_grid_trick(
            2, _hearts_trick(four_players), north, Suit.HEARTS,
            badges={}, height=8,
        ))

        # J♥ 20 + 7♥ 0 + A♥ 11 + 8♥ 0 at a hearts contract.
        assert "Trick 2" in text
        assert "Won N · 31" in text
        assert "▸N" in text
        assert "+10" not in text

    def test_the_eighth_trick_names_the_last_trick_bonus(self, four_players):
        north, *_ = four_players
        text = _rendered(_panel_grid_trick(
            8, _hearts_trick(four_players), north, Suit.HEARTS,
            badges={}, height=8,
        ))

        assert "Won N · 31+10" in text

    def test_a_trick_in_progress_has_no_winner_yet(self, four_players):
        north, *_ = four_players
        live = _trick((north, Suit.SPADES, Rank.ACE))
        text = _rendered(_panel_grid_trick(
            3, live, None, Suit.HEARTS, badges={}, height=8,
        ))

        assert "in progress" in text
        assert "Won" not in text

    def test_a_badge_is_drawn_on_its_trick(self, four_players):
        north, *_ = four_players
        text = _rendered(_panel_grid_trick(
            1, _hearts_trick(four_players), north, Suit.HEARTS,
            badges={Position.SOUTH: (Suit.HEARTS,)}, height=9,
        ))

        assert "★ Belote" in text


class TestGridResultText:
    def test_while_bidding(self):
        assert "Bidding in progress" in _grid_result_text(_Round()).plain

    def test_a_passed_out_round(self):
        text = _grid_result_text(_Round(auction=object())).plain

        assert "All passed" in text

    def test_a_made_contract_shows_the_round_score(self, four_players):
        north, *_ = four_players
        text = _grid_result_text(_Round(
            contract=_Contract(north), scored=True, made=True,
            scores={TeamSide.NS: 262, TeamSide.EW: 0},
        )).plain

        assert "✓ Made" in text
        assert "Round score" in text
        assert "262" in text

    def test_a_failed_contract_says_so(self, four_players):
        north, *_ = four_players
        text = _grid_result_text(_Round(
            contract=_Contract(north), scored=True, made=False,
            scores={TeamSide.NS: 0, TeamSide.EW: 260},
        )).plain

        assert "✗ Failed" in text

    def test_a_round_in_play_shows_the_card_points_so_far(self, four_players):
        north, *_ = four_players
        text = _grid_result_text(_Round(
            contract=_Contract(north),
            play_state=_PlayState(points={TeamSide.NS: 31, TeamSide.EW: 4}),
        )).plain

        assert "In progress" in text
        assert "Card points" in text
        assert "31" in text

    def test_a_contract_before_the_first_card_reads_zero(self, four_players):
        # The auction is over but the play state is not seeded yet.
        north, *_ = four_players
        text = _grid_result_text(_Round(contract=_Contract(north))).plain

        assert "Card points" in text


class TestPanelGridHeader:
    def test_it_names_the_round_and_the_contract(self, four_players):
        north, *_ = four_players
        panel = _panel_grid_header(_Round(contract=_Contract(north)))
        text = _rendered(panel)

        assert "Round #3" in text
        assert "100 ♥ by N" in text
        assert panel.width == GRID_WIDTH


class TestRenderTrickGrid:
    def _round(self, four_players, *, announced=()):
        north, *_ = four_players
        trick = _hearts_trick(four_players)
        return _Round(
            contract=_Contract(north),
            play_state=_PlayState([trick, trick], [north, north]),
            auction=object(),
            scored=True,
            scores={TeamSide.NS: 162, TeamSide.EW: 0},
            announced=announced,
        )

    def test_eight_cells_whatever_was_played(self, four_players):
        north, *_ = four_players
        text = _rendered(_render_trick_grid(
            self._round(four_players), [PassBid(north)]
        ))

        for number in range(1, 9):
            assert f"Trick {number}" in text
        assert text.count("(not played)") == 6
        assert "Bidding so far" in text

    def test_the_screen_holds_the_grid_width(self, four_players):
        north, *_ = four_players
        text = _rendered(_render_trick_grid(
            self._round(four_players), [PassBid(north)]
        ))

        assert max(len(line) for line in text.splitlines()) == GRID_WIDTH

    def _badged_grid_lines(self, four_players, announced):
        north, east, south, west = four_players
        trick = _trick(
            (north, Suit.SPADES, Rank.KING), (east, Suit.HEARTS, Rank.SEVEN),
            (south, Suit.HEARTS, Rank.KING), (west, Suit.HEARTS, Rank.EIGHT),
        )
        round_ = _Round(
            contract=_Contract(north),
            play_state=_PlayState([trick], [north]),
            auction=object(),
            announced=announced,
        )
        return _rendered(_render_trick_grid(round_, [])).splitlines()

    def test_one_badge_row_fits_the_standard_cell(self, four_players):
        north, _east, south, _west = four_players

        lines = self._badged_grid_lines(
            four_players, ((south, Suit.HEARTS),)
        )

        # Header (3) + two rows of eight-high cells (16) + bidding (3).
        assert len(lines) == 3 + 2 * 8 + 3

    def test_a_second_badge_row_grows_every_cell_alike(self, four_players):
        # Two pairs announced in trick 1, under N and under S: that one
        # trick needs a ninth line, and all eight cells take it so the
        # two grid rows stay flush.
        north, _east, south, _west = four_players

        lines = self._badged_grid_lines(
            four_players, ((south, Suit.HEARTS), (north, Suit.SPADES))
        )

        assert len(lines) == 3 + 2 * 9 + 3


class TestGridPromptText:
    def test_it_explains_the_marks_and_the_way_out(self):
        text = _replay_grid_prompt_text().plain

        assert "▸ led" in text
        assert "★ won" in text
        assert "[Enter] back" in text
