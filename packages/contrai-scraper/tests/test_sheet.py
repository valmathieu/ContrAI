"""Pins the score sheet's reading: rows by position, carries between anchors."""

import pytest
from contrai_core import Suit, TeamSide

from contrai_scraper import ScoreSheet, read_sheet
from contrai_scraper.parse.snapshot import RowContract, ScoreRow, Snapshot

NS, EW = TeamSide.NS, TeamSide.EW


def _row(ns=0, ew=0, *, announced=(0, 0), belote=(0, 0)):
    """A row marking each side ``ns`` / ``ew`` made points.

    Rows are compared by value, so tests that follow a row to its round give
    each one marks of its own.
    """

    return ScoreRow(
        made=True,
        contract=RowContract(value=80, suit=Suit.HEARTS, multiplier=1),
        taken={NS: 81, EW: 81},
        belote={NS: 0, EW: 0},
        marked={NS: (ns, announced[0]), EW: (ew, announced[1])},
        marked_belote={NS: belote[0], EW: belote[1]},
    )


def _totals(ns, ew):
    return {NS: ns, EW: ew}


def _snap(round_index, rows, totals=None):
    """A snapshot holding nothing but its newest round, rows and totals."""

    return Snapshot(table_id="t1", is_tournament=True, round_index=round_index,
                    seats={}, players={}, score_rows=tuple(rows),
                    totals=None if totals is None else _totals(*totals),
                    at=None)


def _sheet(*snapshots, seen, passed=()):
    return read_sheet(snapshots, seen=frozenset(seen), passed=frozenset(passed))


def _placed(sheet):
    """Round number to row index, which is what placement is about."""

    return {number: line.index for number, line in sheet.lines.items()}


#: Ten rows, each told apart by North-South's marks: 10, 20, 30 and so on.
_ROWS = tuple(_row(ns=10 * (index + 1)) for index in range(10))


class TestPlacement:
    def test_each_row_lands_on_its_round(self):
        sheet = _sheet(_snap(1, _ROWS[:1]), _snap(2, _ROWS[:2]),
                       _snap(3, _ROWS[:3]), seen={1, 2, 3})
        assert {n: line.row for n, line in sheet.lines.items()} == {
            1: _ROWS[0], 2: _ROWS[1], 3: _ROWS[2]}

    def test_passed_out_rounds_take_no_row(self):
        # obs-3af6e404: the read at round 8 carries six rows, because rounds
        # 2 and 7 were passed out. One row per round number put round 5's
        # row on round 6.
        sheet = _sheet(_snap(8, _ROWS[:6]), seen=range(1, 9), passed={2, 7})
        assert _placed(sheet) == {1: 0, 3: 1, 4: 2, 5: 3, 6: 4, 8: 5}

    def test_a_read_naming_a_passed_out_round_is_stepped_over(self):
        # Never measured — a read names the newest *scored* round — but a
        # read that did would still put its rows on the played rounds.
        sheet = _sheet(_snap(3, _ROWS[:2]), seen={1, 2, 3}, passed={3})
        assert _placed(sheet) == {1: 0, 2: 1}

    def test_rows_before_the_join_land_nowhere(self):
        # Joined during round 5: the four rows before it are rounds this
        # visit never saw.
        sheet = _sheet(_snap(4, _ROWS[:4]), _snap(6, _ROWS[:6]), seen={5, 6})
        assert _placed(sheet) == {5: 4, 6: 5}

    def test_the_walk_stops_at_a_round_the_visit_never_saw(self):
        # Round 3 left no event, so which rows below it are rounds 1 and 2
        # is not this read's to say: a passed-out round 3 would shift them.
        sheet = _sheet(_snap(5, _ROWS[:5]), seen={1, 2, 4, 5})
        assert _placed(sheet) == {4: 3, 5: 4}

    def test_an_earlier_read_still_places_the_rows_below_a_gap(self):
        sheet = _sheet(_snap(2, _ROWS[:2]), _snap(5, _ROWS[:5]),
                       seen={1, 2, 4, 5})
        assert _placed(sheet) == {1: 0, 2: 1, 4: 3, 5: 4}

    def test_placement_does_not_depend_on_the_order_of_reads(self):
        reads = [_snap(2, _ROWS[:2], (30, 0)), _snap(3, _ROWS[:3], (60, 0)),
                 _snap(5, _ROWS[:4], (100, 0))]
        forward = _sheet(*reads, seen=range(1, 6), passed={4})
        backward = _sheet(*reversed(reads), seen=range(1, 6), passed={4})
        assert forward == backward


class TestTotals:
    def test_the_newest_row_keeps_its_reads_totals_and_older_rows_have_none(self):
        # An older row's running total is not in the payload at all.
        sheet = _sheet(_snap(3, _ROWS[:3], (60, 0)), seen={1, 2, 3})
        assert [line.totals for line in sheet.lines.values()] == [
            None, None, _totals(60, 0)]

    def test_a_passed_out_round_stands_at_the_totals_before_it(self):
        sheet = _sheet(_snap(2, _ROWS[:2], (30, 0)), _snap(4, _ROWS[:3]),
                       seen={1, 2, 3, 4}, passed={3})
        assert sheet.standings == {3: _totals(30, 0)}

    def test_a_passed_out_round_whose_totals_were_never_read_has_none(self):
        sheet = _sheet(_snap(4, _ROWS[:3], (60, 0)), seen={1, 2, 3, 4},
                       passed={3})
        assert sheet.standings == {}

    def test_a_run_of_passed_out_rounds_stands_at_one_total(self):
        sheet = _sheet(_snap(1, _ROWS[:1], (10, 0)), _snap(4, _ROWS[:2]),
                       seen={1, 2, 3, 4}, passed={2, 3})
        assert sheet.standings == {2: _totals(10, 0), 3: _totals(10, 0)}

    def test_a_passed_out_first_round_stands_at_nought(self):
        sheet = _sheet(_snap(2, _ROWS[:1]), seen={1, 2}, passed={1})
        assert sheet.standings == {1: _totals(0, 0)}

    def test_a_passed_out_join_round_stands_at_the_joins_totals(self):
        # Nothing below round 5 was seen, so its count comes from above: the
        # row round 6 took is the next one written.
        sheet = _sheet(_snap(4, _ROWS[:4], (100, 0)), _snap(6, _ROWS[:5]),
                       seen={5, 6}, passed={5})
        assert sheet.standings == {5: _totals(100, 0)}

    def test_a_run_of_passed_out_join_rounds_counts_from_above(self):
        sheet = _sheet(_snap(4, _ROWS[:4], (100, 0)), _snap(7, _ROWS[:5]),
                       seen={5, 6, 7}, passed={5, 6})
        assert sheet.standings == {5: _totals(100, 0), 6: _totals(100, 0)}

    def test_a_passed_out_round_between_unplaced_rounds_has_none(self):
        sheet = _sheet(_snap(1, _ROWS[:1], (10, 0)), seen={2, 3}, passed={2})
        assert sheet.standings == {}


#: obs-f3c28d3b round 4, as the site states it.
_R4 = _row(ns=14, ew=148, announced=(0, 90))


def _round_4(before, after):
    """Joined during round 4, with the totals before and after its row.

    The three rows before the join are rounds this visit never saw, so their
    marks do not matter.
    """

    return _sheet(_snap(3, _ROWS[:3], before),
                  _snap(4, (*_ROWS[:3], _R4), after), seen={4})


class TestCarries:
    def test_the_first_round_of_a_game_seen_whole_starts_from_nought(self):
        sheet = _sheet(_snap(1, [_row(ew=90, announced=(0, 80))], (0, 170)),
                       seen={1})
        assert sheet.lines[1].carried_over == _totals(0, 0)

    def test_the_observed_payout(self):
        # 129 / 496 before, 143 / 895 after: the 161 of a held round 3.
        sheet = _round_4((129, 496), (143, 895))
        assert sheet.lines[4].carried_over == _totals(0, 161)

    def test_an_ordinary_round_carries_nothing(self):
        sheet = _round_4((129, 496), (143, 734))
        assert sheet.lines[4].carried_over == _totals(0, 0)

    def test_credited_belote_is_not_a_carry(self):
        row = _row(ns=14, ew=148, announced=(0, 90), belote=(0, 20))
        sheet = _sheet(_snap(3, _ROWS[:3], (129, 496)),
                       _snap(4, (*_ROWS[:3], row), (143, 754)), seen={4})
        assert sheet.lines[4].carried_over == _totals(0, 0)

    @pytest.mark.parametrize("missing", ["before", "after"])
    def test_totals_on_no_side_leave_the_carry_unknown(self, missing):
        # A total whose label could not be placed is read as no total.
        sheet = _round_4(None if missing == "before" else (129, 496),
                         None if missing == "after" else (143, 895))
        assert sheet.lines[4].carried_over is None

    def test_a_negative_step_on_no_watched_round_is_not_noted(self):
        # Rows before the join: nothing this visit records is at stake.
        sheet = _sheet(_snap(1, _ROWS[:1], (5, 0)), seen={2})
        assert sheet.notes == ()

    def test_a_negative_residual_is_unknown_and_noted(self):
        # A carry is a payout, never negative: the rows must be wrong.
        sheet = _round_4((129, 496), (100, 734))
        assert (sheet.lines[4].carried_over, sheet.notes) == (None, (
            "round 4: the running totals moved by less than the marks, so "
            "the carry is unknown",))

    @pytest.mark.parametrize("passed_before", [1, 2])
    def test_a_join_after_passed_out_rounds_is_anchored_by_row_count(
        self, passed_before
    ):
        # Joined during round 10, with passed-out rounds just before it. The
        # join read names the newest *scored* round, so keying the join on
        # its number plus one lands on a passed-out round, never on 10.
        rows = 9 - passed_before
        before = sum(10 * (index + 1) for index in range(rows))
        sheet = _sheet(
            _snap(rows, _ROWS[:rows], (before, 0)),
            _snap(10, (*_ROWS[:rows], _row(ew=170)), (before, 170)),
            seen={10},
        )
        assert (sheet.lines[10].index, sheet.lines[10].carried_over) == (
            rows, _totals(0, 0))

    def test_a_row_between_distant_anchors_is_unknown(self):
        # Round 1's totals were never read, so no single step brackets either
        # row.
        sheet = _sheet(_snap(2, _ROWS[:2], (30, 0)), seen={1, 2})
        assert [line.carried_over for line in sheet.lines.values()] == [
            None, None]

    def test_a_row_above_the_last_stated_totals_is_unknown(self):
        sheet = _sheet(_snap(1, _ROWS[:1], (10, 0)), _snap(2, _ROWS[:2]),
                       seen={1, 2})
        assert [line.carried_over for line in sheet.lines.values()] == [
            _totals(0, 0), None]


class TestContradictions:
    @pytest.mark.parametrize(
        ("reads", "seen", "reason"),
        [
            pytest.param(
                [_snap(1, _ROWS[:1]), _snap(2, (_ROWS[5], _ROWS[1]))], {1, 2},
                "two reads disagree about a row both carry", id="prefix"),
            pytest.param(
                [_snap(2, _ROWS[:2]), _snap(3, _ROWS[:2])], {2, 3},
                "round 2 falls on two rows", id="two-rows"),
            pytest.param(
                [_snap(3, _ROWS[:2])], {1, 2, 3},
                "round 1 was played but no row is left for it", id="no-row"),
            pytest.param(
                [_snap(3, _ROWS[:3]), _snap(5, _ROWS[:1])], {2, 3, 5},
                "the rows do not follow the rounds' order", id="order"),
        ],
    )
    def test_a_contradiction_places_nothing_and_says_so(self, reads, seen, reason):
        assert _sheet(*reads, seen=seen) == ScoreSheet(
            lines={}, standings={},
            notes=(f"the score sheet contradicts itself — {reason} — so no "
                   "score row was placed",))


class TestBarriers:
    def test_two_totals_for_one_row_count_make_a_barrier(self):
        # Rounds 2 and 3 each have one side of their step at the disputed
        # count; round 1's step does not touch it.
        sheet = _sheet(
            _snap(1, _ROWS[:1], (10, 0)), _snap(2, _ROWS[:2], (30, 0)),
            _snap(2, _ROWS[:2], (35, 0)), _snap(3, _ROWS[:3], (60, 0)),
            seen={1, 2, 3},
        )
        assert [(line.totals, line.carried_over)
                for line in sheet.lines.values()] == [
            (_totals(10, 0), _totals(0, 0)), (None, None),
            (_totals(60, 0), None)]
        assert sheet.notes == (
            "two reads state different totals after 2 score row(s), so no "
            "carry is read across that point",)

    def test_a_read_with_no_row_claiming_other_totals_disputes_nought(self):
        sheet = _sheet(_snap(None, (), (40, 60)), _snap(1, _ROWS[:1], (10, 0)),
                       seen={1})
        assert (sheet.lines[1].carried_over, len(sheet.notes)) == (None, 1)

    def test_a_barrier_gives_no_standing(self):
        sheet = _sheet(
            _snap(1, _ROWS[:1], (10, 0)), _snap(1, _ROWS[:1], (15, 0)),
            _snap(3, _ROWS[:2], (30, 0)), seen={1, 2, 3}, passed={2},
        )
        assert sheet.standings == {}

    def test_a_third_read_at_a_barrier_adds_no_note(self):
        sheet = _sheet(*(_snap(1, _ROWS[:1], (total, 0))
                         for total in (10, 15, 20)), seen={1})
        assert len(sheet.notes) == 1
