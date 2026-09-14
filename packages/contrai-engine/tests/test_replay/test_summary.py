"""Tests for the replay picker's rows.

The rows are built from hand-written record pieces rather than from a
played game: what is under test is the join between a record's rounds and
a verdict's, and a real record would only make the two numberings harder
to pull apart.
"""

from __future__ import annotations

from contrai_core.position import Position
from contrai_core.team_side import TeamSide
from contrai_data.events import RoundOutcome
from contrai_engine.replay.summary import ReplayRow, replay_rows
from contrai_engine.replay.verdict import (
    GameVerdict,
    RoundVerdict,
    Verdict,
)


class _Contract:
    """The one ``ObservedContract`` attribute a row reads."""

    def __init__(self, declarer=Position.NORTH):
        self.declarer = declarer


class _Score:
    """The one ``RoundScored`` attribute a row reads."""

    def __init__(self, totals):
        self.totals = totals


class _Round:
    """The five ``RoundRecord`` attributes a row reads."""

    def __init__(self, number, *, complete=True, contract=None,
                 outcome=None, score=None):
        self.number = number
        self.complete = complete
        self.contract = contract
        self.outcome = outcome
        self.score = score


class _Record:
    """The one ``GameRecord`` attribute ``replay_rows`` reads."""

    def __init__(self, rounds):
        self.rounds = tuple(rounds)


def _verdict(*pairs):
    return GameVerdict(
        game_id="g",
        source="engine",
        preset="classic",
        rounds=tuple(
            RoundVerdict.decide(number, ()) if v is Verdict.VERIFIED
            else RoundVerdict(number=number, verdict=v)
            for number, v in pairs
        ),
    )


class TestReplayRows:
    def test_every_round_is_listed_complete_or_not(self):
        record = _Record([_Round(7), _Round(8, complete=False), _Round(9)])

        rows = replay_rows(record)

        assert [row.number for row in rows] == [7, 8, 9]

    def test_an_incomplete_round_is_not_steppable(self):
        record = _Record([_Round(8, complete=False)])

        (row,) = replay_rows(record)

        assert row.steppable is False

    def test_a_complete_round_is_steppable(self):
        record = _Record([_Round(8)])

        (row,) = replay_rows(record)

        assert row.steppable is True

    def test_verdicts_join_on_the_records_own_numbering(self):
        # The record starts at 7 — a mid-game join. A positional join
        # would put every verdict on the wrong round.
        record = _Record([_Round(7), _Round(8), _Round(9)])
        verdict = _verdict(
            (7, Verdict.PARTIAL),
            (8, Verdict.SUSPECT),
            (9, Verdict.VERIFIED),
        )

        rows = replay_rows(record, verdict)

        assert [row.verdict for row in rows] == [
            Verdict.PARTIAL,
            Verdict.SUSPECT,
            Verdict.VERIFIED,
        ]

    def test_a_round_with_no_verdict_carries_none(self):
        record = _Record([_Round(7)])

        (row,) = replay_rows(record, _verdict((9, Verdict.VERIFIED)))

        assert row.verdict is None

    def test_no_verdict_at_all_leaves_every_row_unjudged(self):
        record = _Record([_Round(7), _Round(8)])

        rows = replay_rows(record)

        assert all(row.verdict is None for row in rows)

    def test_totals_come_off_the_score_line_when_there_is_one(self):
        totals = {TeamSide.NS: 120, TeamSide.EW: 40}
        record = _Record([_Round(7, score=_Score(totals))])

        (row,) = replay_rows(record)

        assert row.totals == totals

    def test_a_scoreless_round_has_no_totals(self):
        record = _Record([_Round(7)])

        (row,) = replay_rows(record)

        assert row.totals is None

    def test_a_score_line_that_reported_no_totals_has_none_either(self):
        # ``RoundScored.totals`` is itself optional: a source can state
        # an outcome without a running score.
        record = _Record([_Round(7, score=_Score(None))])

        (row,) = replay_rows(record)

        assert row.totals is None

    def test_the_outcome_is_carried_through(self):
        record = _Record([_Round(7, outcome=RoundOutcome.ALL_PASS)])

        (row,) = replay_rows(record)

        assert row.outcome is RoundOutcome.ALL_PASS

    def test_the_contract_is_carried_through(self):
        contract = _Contract()
        record = _Record([_Round(7, contract=contract)])

        (row,) = replay_rows(record)

        assert row.contract is contract

    def test_a_record_with_no_rounds_yields_no_rows(self):
        assert replay_rows(_Record([])) == ()


class TestDeclarerSide:
    def test_it_is_the_declarers_side(self):
        row = ReplayRow(
            number=1,
            contract=_Contract(Position.EAST),
            outcome=None,
            totals=None,
            verdict=None,
            steppable=True,
        )

        assert row.declarer_side is TeamSide.EW

    def test_a_passed_out_round_has_no_declarer_side(self):
        row = ReplayRow(
            number=1,
            contract=None,
            outcome=RoundOutcome.ALL_PASS,
            totals=None,
            verdict=None,
            steppable=True,
        )

        assert row.declarer_side is None
