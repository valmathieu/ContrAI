"""Tests for the end-game screen in :mod:`contrai_engine.view.screens.endgame`.

The round-by-round summary contract cell must read in English
vocabulary exclusively — no French ``coinché`` / ``surcoinché`` leakage.
"""

from __future__ import annotations

from contrai_core import Suit
from contrai_engine.view.rich_view import RoundSummary
from contrai_engine.view.screens.endgame import _format_summary_contract


class TestFormatSummaryContract:
    """The end-game summary contract cell must use English vocabulary
    exclusively — no French ``coinché`` / ``surcoinché`` leakage."""

    class _StubContract:
        def __init__(self, value, suit, *, double=False, redouble=False):
            self.value = value
            self.suit = suit
            self.double = double
            self.redouble = redouble

    @staticmethod
    def _row(contract, team_name="North-South"):
        return RoundSummary(
            round_number=1,
            contract=contract,
            contract_team_name=team_name,
            contract_made=True,
            ns_pts=100,
            ew_pts=0,
            running_ns=100,
            running_ew=0,
        )

    def test_doubled_contract_reads_english(self):
        row = self._row(self._StubContract(100, Suit.HEARTS, double=True))
        text = _format_summary_contract(row).plain
        assert "doubled" in text
        assert "coinché" not in text

    def test_redoubled_contract_reads_english(self):
        row = self._row(self._StubContract(100, Suit.HEARTS, redouble=True))
        text = _format_summary_contract(row).plain
        assert "redoubled" in text
        assert "surcoinché" not in text

    def test_plain_contract_has_no_double_marker(self):
        row = self._row(self._StubContract(100, Suit.HEARTS))
        text = _format_summary_contract(row).plain
        assert "doubled" not in text
        assert "redoubled" not in text
