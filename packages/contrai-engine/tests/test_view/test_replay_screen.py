"""Tests for the replay screens.

Named ``test_replay_screen`` rather than ``test_replay`` because
``tests/test_replay/`` already owns that name — the same reason
``test_debug_screen.py`` is spelled the way it is.

The picker wraps a Rich ``Table``, so its cells are only reachable once a
console has laid it out; :func:`_rendered` does that.
"""

from __future__ import annotations

import io

from rich.console import Console

from contrai_core import Position, Suit, TeamSide
from contrai_data.events import RoundOutcome
from contrai_engine.replay.summary import ReplayRow
from contrai_data import Verdict
from contrai_engine.view.screens.replay import (
    _format_replay_contract,
    _format_replay_verdict,
    _panel_replay_summary,
    _replay_contract_text,
    _replay_deal_text,
    _replay_step_prompt_text,
    _replay_step_rejection_text,
    _replay_summary_prompt_text,
    _replay_summary_rejection_text,
)


def _rendered(renderable) -> str:
    console = Console(file=io.StringIO(), width=80, no_color=True)
    console.print(renderable)
    return console.file.getvalue()


class _Contract:
    def __init__(self, declarer=Position.NORTH, value=80, suit=Suit.HEARTS,
                 doubled_by=None, redoubled_by=None):
        self.declarer = declarer
        self.value = value
        self.suit = suit
        self.doubled_by = doubled_by
        self.redoubled_by = redoubled_by


def _row(number=8, **overrides):
    fields = {
        "contract": _Contract(),
        "outcome": RoundOutcome.MADE,
        "totals": {TeamSide.NS: 120, TeamSide.EW: 40},
        "verdict": Verdict.VERIFIED,
        "steppable": True,
    }
    fields.update(overrides)
    return ReplayRow(number=number, **fields)


class TestSummaryPanel:
    def test_it_names_the_game(self):
        out = _rendered(_panel_replay_summary([_row()], "engine-abc123"))

        assert "engine-abc123" in out

    def test_it_lists_a_round_with_its_verdict(self):
        out = _rendered(_panel_replay_summary([_row(8)], "g"))

        assert "8" in out
        assert "verified" in out

    def test_an_incomplete_round_is_listed_and_marked(self):
        out = _rendered(
            _panel_replay_summary([_row(9, steppable=False)], "g")
        )

        assert "9" in out
        # Listed but not steppable — the viewer must be able to tell.
        assert "not steppable" in out

    def test_a_scoreless_round_shows_no_totals(self):
        out = _rendered(_panel_replay_summary([_row(9, totals=None)], "g"))

        assert "·" in out

    def test_a_round_with_no_outcome_shows_a_dash(self):
        out = _rendered(_panel_replay_summary([_row(9, outcome=None)], "g"))

        assert "—" in out

    def test_an_empty_record_still_renders(self):
        out = _rendered(_panel_replay_summary([], "g"))

        assert "no rounds" in out.lower()


class TestVerdictCell:
    def test_a_suspect_round_says_so(self):
        text = _format_replay_verdict(Verdict.SUSPECT, steppable=True)

        assert text.plain == "suspect"

    def test_a_partial_round_says_so(self):
        text = _format_replay_verdict(Verdict.PARTIAL, steppable=True)

        assert text.plain == "partial"

    def test_an_unverified_round_shows_a_dash(self):
        text = _format_replay_verdict(None, steppable=True)

        assert text.plain == "—"

    def test_not_steppable_wins_over_whatever_the_verdict_was(self):
        text = _format_replay_verdict(Verdict.VERIFIED, steppable=False)

        assert text.plain == "not steppable"


class TestContractCell:
    def test_a_plain_contract_names_value_suit_and_side(self):
        text = _format_replay_contract(_row(contract=_Contract()))

        assert "80" in text.plain
        assert "N-S" in text.plain

    def test_a_doubled_contract_says_so(self):
        text = _format_replay_contract(
            _row(contract=_Contract(doubled_by=Position.EAST))
        )

        assert "doubled" in text.plain

    def test_a_redoubled_contract_says_so(self):
        text = _format_replay_contract(
            _row(
                contract=_Contract(
                    doubled_by=Position.EAST, redoubled_by=Position.NORTH
                )
            )
        )

        assert "redoubled" in text.plain

    def test_a_passed_out_round_says_all_passed(self):
        text = _format_replay_contract(
            _row(contract=None, outcome=RoundOutcome.ALL_PASS)
        )

        assert "all passed" in text.plain


class TestPromptText:
    def test_the_summary_prompt_names_the_steppable_range(self):
        text = _replay_summary_prompt_text([_row(7), _row(8)])

        assert "[7-8]" in text.plain
        assert "[g 7-8]" in text.plain
        assert "[q]" in text.plain

    def test_the_summary_prompt_holds_up_with_nothing_steppable(self):
        text = _replay_summary_prompt_text([_row(7, steppable=False)])

        assert "[q]" in text.plain
        assert "[g" not in text.plain

    def test_the_step_prompt_lists_every_key(self):
        text = _replay_step_prompt_text(can_go_back=True)

        for key in ("[n]", "[t]", "[r]", "[p]", "[q]"):
            assert key in text.plain

    def test_the_step_prompt_hides_back_at_the_first_stop(self):
        text = _replay_step_prompt_text(can_go_back=False)

        assert "[p]" not in text.plain

    def test_the_step_prompt_offers_a_while_bidding(self):
        text = _replay_step_prompt_text(
            can_go_back=True, can_skip_auction=True
        )

        assert "[a] skip bids" in text.plain

    def test_the_step_prompt_hides_a_once_the_auction_is_over(self):
        text = _replay_step_prompt_text(can_go_back=True)

        assert "[a]" not in text.plain

    def test_the_step_prompt_always_offers_the_grid(self):
        text = _replay_step_prompt_text(can_go_back=False)

        assert "[g] grid" in text.plain

    def test_the_step_prompt_is_one_line_that_fits_80_columns(self):
        text = _replay_step_prompt_text(
            can_go_back=True, can_skip_auction=True
        )

        assert "\n" not in text.plain
        assert text.cell_len <= 80


class TestRejectionText:
    def test_the_summary_rejection_names_the_steppable_range(self):
        text = _replay_summary_rejection_text([_row(7), _row(9)])

        assert "7-9" in text.plain

    def test_the_summary_rejection_says_so_when_nothing_can_be_stepped(self):
        text = _replay_summary_rejection_text([_row(7, steppable=False)])

        assert "No round" in text.plain

    def test_the_step_rejection_lists_the_keys_on_offer(self):
        text = _replay_step_rejection_text(can_go_back=True)

        assert "[p]" in text.plain

    def test_the_step_rejection_drops_back_at_the_first_stop(self):
        text = _replay_step_rejection_text(can_go_back=False)

        assert "[p]" not in text.plain
        assert "[g]" in text.plain

    def test_the_step_rejection_names_a_only_while_bidding(self):
        assert "[a]" in _replay_step_rejection_text(
            can_go_back=True, can_skip_auction=True
        ).plain
        assert "[a]" not in _replay_step_rejection_text(
            can_go_back=True
        ).plain


class TestContractText:
    class _Player:
        position = Position.EAST

    class _Contract:
        value = 100
        suit = Suit.HEARTS
        double = False
        redouble = False

        def __init__(self, player):
            self.player = player

    class _Round:
        def __init__(self, contract):
            self.contract = contract

    def test_it_names_the_contract_and_what_comes_next(self):
        round_ = self._Round(self._Contract(self._Player()))

        text = _replay_contract_text(round_)

        assert text.plain.startswith("Contract set: ")
        assert "100" in text.plain
        assert "first card" in text.plain

    def test_a_contractless_round_still_renders(self):
        text = _replay_contract_text(object())

        assert "first card" in text.plain


class TestDealText:
    class _Dealer:
        position = Position.WEST

    class _Round:
        round_number = 4
        dealer = None

    def test_it_names_the_round_and_the_dealer(self):
        round_ = self._Round()
        round_.dealer = self._Dealer()

        text = _replay_deal_text(round_)

        assert "#4" in text.plain
        assert "W" in text.plain

    def test_a_round_with_no_dealer_still_renders(self):
        text = _replay_deal_text(self._Round())

        assert "—" in text.plain

    def test_an_attribute_less_round_still_renders(self):
        text = _replay_deal_text(object())

        assert "?" in text.plain
