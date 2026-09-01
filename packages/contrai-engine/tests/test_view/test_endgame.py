"""Tests for the end-game screen in :mod:`contrai_engine.view.screens.endgame`.

Covers the final scoreboard's three builders — the winner banner, the
round-by-round summary table, and the new-game / rematch / quit prompt —
plus the per-row contract cell, which must read in English vocabulary
exclusively (no French ``coinché`` / ``surcoinché`` leakage).

The summary table wraps a Rich ``Table``, so its cells are only reachable
once a console has laid it out; :func:`_rendered` does that.
"""

from __future__ import annotations

import pytest
from rich.console import Console
from rich.panel import Panel

from contrai_core import Suit, TeamSide
from contrai_engine.model.game import GameOverStatus
from contrai_engine.view.formatting import _team_abbr
from contrai_engine.view.rich_view import RoundSummary
from contrai_engine.view.screens.endgame import (
    _end_game_prompt_text,
    _format_summary_contract,
    _panel_game_over_banner,
    _panel_round_summary,
)
from contrai_engine.view.theme import GOLD


def _rendered(panel: Panel) -> str:
    """Plain text of a rendered panel, laid out wide enough not to wrap."""

    console = Console(width=100, record=True, force_terminal=False)
    console.print(panel)
    return console.export_text()


class _StubContract:
    """Minimal stand-in for ``Contract``: only what the cell builder reads."""

    def __init__(self, value, suit, *, double=False, redouble=False):
        self.value = value
        self.suit = suit
        self.double = double
        self.redouble = redouble


def _summary(
    *,
    round_number=1,
    contract=None,
    side=TeamSide.NS,
    made=True,
    ns_pts=100,
    ew_pts=0,
    running_ns=100,
    running_ew=0,
) -> RoundSummary:
    """A summary row with sensible defaults; override only what matters."""

    return RoundSummary(
        round_number=round_number,
        contract=contract,
        contract_side=side,
        contract_made=made,
        ns_pts=ns_pts,
        ew_pts=ew_pts,
        running_ns=running_ns,
        running_ew=running_ew,
    )


class TestFormatSummaryContract:
    """The end-game summary contract cell must use English vocabulary
    exclusively — no French ``coinché`` / ``surcoinché`` leakage."""

    @staticmethod
    def _row(contract, side=TeamSide.NS):
        return _summary(contract=contract, side=side)

    def test_doubled_contract_reads_english(self):
        row = self._row(_StubContract(100, Suit.HEARTS, double=True))
        text = _format_summary_contract(row).plain
        assert "doubled" in text
        assert "coinché" not in text

    def test_redoubled_contract_reads_english(self):
        row = self._row(_StubContract(100, Suit.HEARTS, redouble=True))
        text = _format_summary_contract(row).plain
        assert "redoubled" in text
        assert "surcoinché" not in text

    def test_plain_contract_has_no_double_marker(self):
        row = self._row(_StubContract(100, Suit.HEARTS))
        text = _format_summary_contract(row).plain
        assert "doubled" not in text
        assert "redoubled" not in text

    def test_passed_round_reads_all_passed(self):
        """A round nobody bid on has no contract to spell out."""

        assert _format_summary_contract(self._row(None)).plain == "all passed"

    def test_a_sideless_contract_omits_the_team_abbreviation(self):
        """Defensive: no declaring side means no label, not a stray one."""

        row = self._row(_StubContract(110, Suit.CLUBS), side=None)
        plain = _format_summary_contract(row).plain

        assert "110" in plain
        assert _team_abbr(TeamSide.NS) not in plain
        assert _team_abbr(TeamSide.EW) not in plain


class TestPanelGameOverBanner:
    """The gold winner banner and the final score line beneath it."""

    @staticmethod
    def _status(winner, ns=1620, ew=1420) -> GameOverStatus:
        return GameOverStatus(
            game_over=winner is not None,
            winner=winner,
            tied_teams=None,
            final_scores={TeamSide.NS: ns, TeamSide.EW: ew},
        )

    @pytest.mark.parametrize("winner", [TeamSide.NS, TeamSide.EW])
    def test_names_the_winning_team_by_abbreviation(self, winner):
        text = _rendered(_panel_game_over_banner(self._status(winner)))
        assert f"★   {_team_abbr(winner)}   WINS   ★" in text

    def test_a_winnerless_status_renders_a_dash(self):
        """Defensive: ``winner`` is ``None`` until the game is actually over."""

        text = _rendered(_panel_game_over_banner(self._status(None)))
        assert "★   —   WINS   ★" in text

    def test_shows_both_final_scores(self):
        text = _rendered(_panel_game_over_banner(self._status(TeamSide.NS)))
        assert "1620" in text
        assert "1420" in text
        assert "Final score" in text

    def test_labels_both_teams_under_their_scores(self):
        text = _rendered(_panel_game_over_banner(self._status(TeamSide.NS)))
        assert _team_abbr(TeamSide.NS) in text
        assert _team_abbr(TeamSide.EW) in text

    def test_the_banner_ignores_the_belote_gate_signal(self):
        """``belote_gated`` is a between-rounds signal; a finished game
        never carries one, and the banner would have nothing to do with
        it if it did."""

        status = GameOverStatus(
            game_over=True,
            winner=TeamSide.NS,
            tied_teams=None,
            final_scores={TeamSide.NS: 1620, TeamSide.EW: 1420},
            belote_gated=TeamSide.EW,
        )
        text = _rendered(_panel_game_over_banner(status))
        assert f"★   {_team_abbr(TeamSide.NS)}   WINS   ★" in text
        assert "Belote" not in text

    def test_missing_scores_default_to_zero(self):
        """An empty ``final_scores`` must render zeroes, not raise."""

        status = GameOverStatus(
            game_over=True,
            winner=TeamSide.NS,
            tied_teams=None,
            final_scores={},
        )
        assert "0" in _rendered(_panel_game_over_banner(status))

    @pytest.mark.parametrize(
        ("winner", "winning_score", "losing_score"),
        [(TeamSide.NS, "1620", "1420"), (TeamSide.EW, "1420", "1620")],
        ids=["ns-wins", "ew-wins"],
    )
    def test_the_gold_highlight_follows_the_winner(
        self, winner, winning_score, losing_score
    ):
        """The winner's total is gold; the loser keeps its own team colour."""

        body = _panel_game_over_banner(self._status(winner)).renderable
        # ``Text.spans`` is the styled-run record: (start, end, style). Slice
        # the plain text by each span to find the run covering a given score.
        styles_by_run: dict[str, set[str]] = {}
        for span in body.spans:
            run = body.plain[span.start:span.end]
            styles_by_run.setdefault(run, set()).add(str(span.style))

        assert any(GOLD in style for style in styles_by_run[winning_score])
        assert not any(GOLD in style for style in styles_by_run[losing_score])


class TestPanelRoundSummary:
    """The round-by-round table: one row per :class:`RoundSummary`."""

    def test_renders_one_row_per_round(self):
        history = [
            _summary(round_number=n, contract=_StubContract(80 + n, Suit.HEARTS))
            for n in range(1, 4)
        ]
        text = _rendered(_panel_round_summary(history))

        for n in range(1, 4):
            assert str(80 + n) in text

    def test_empty_history_renders_headers_only(self):
        """A game with no completed rounds still draws a readable table."""

        text = _rendered(_panel_round_summary([]))
        assert "Contract" in text
        assert "Made" in text
        assert "Round-by-round summary" in text

    def test_a_made_contract_is_checked(self):
        history = [_summary(contract=_StubContract(100, Suit.SPADES), made=True)]
        assert "✓" in _rendered(_panel_round_summary(history))

    def test_a_failed_contract_is_crossed(self):
        history = [_summary(contract=_StubContract(100, Suit.SPADES), made=False)]
        assert "✗" in _rendered(_panel_round_summary(history))

    def test_a_passed_round_renders_a_dash_not_a_cross(self):
        """No contract means nothing was made *or* failed — neither mark fits."""

        text = _rendered(_panel_round_summary([_summary(contract=None, made=False)]))
        assert "all passed" in text
        assert "—" in text
        assert "✗" not in text

    def test_a_zero_point_side_renders_a_dot(self):
        """Zeroes are noise in a scoreboard; the table dims them to ``·``."""

        history = [
            _summary(contract=_StubContract(100, Suit.SPADES), ns_pts=162, ew_pts=0)
        ]
        text = _rendered(_panel_round_summary(history))
        assert "162" in text
        assert "·" in text

    def test_both_sides_scoring_renders_neither_as_a_dot(self):
        history = [
            _summary(contract=_StubContract(100, Suit.SPADES), ns_pts=90, ew_pts=72)
        ]
        text = _rendered(_panel_round_summary(history))
        assert "90" in text
        assert "72" in text

    def test_running_totals_read_ns_then_ew(self):
        history = [
            _summary(
                contract=_StubContract(100, Suit.SPADES),
                running_ns=340,
                running_ew=180,
            )
        ]
        text = _rendered(_panel_round_summary(history))
        assert "340 / 180" in text

    def test_running_column_is_headed_by_both_team_abbreviations(self):
        ns, ew = _team_abbr(TeamSide.NS), _team_abbr(TeamSide.EW)
        text = _rendered(_panel_round_summary([]))
        assert f"Running {ns} / {ew}" in text


class TestEndGamePromptText:
    """The prompt must offer exactly the choices ``main`` dispatches on."""

    def test_offers_new_game_rematch_and_quit(self):
        plain = _end_game_prompt_text().plain
        assert "[n]" in plain
        assert "[r]" in plain
        assert "[q]" in plain

    def test_labels_each_choice(self):
        plain = _end_game_prompt_text().plain
        assert "new game" in plain
        assert "rematch" in plain
        assert "quit" in plain
