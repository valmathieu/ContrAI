"""Pins the join snapshot: seats as placed, players, score rows."""

import pytest
from contrai_core import Position, Suit, TeamSide

from contrai_scraper import Translator, read_snapshot

AT = 1788855235490


def read(profile, payload, *, at=AT):
    return read_snapshot(payload, Translator(profile), at=at)


class TestSeats:
    def test_the_four_seats_map_as_placed(self, profile, builders):
        # The seat on the right of the screen is East. The translator has
        # already checked that the placement walks the table the preset's
        # way, so reading it literally is safe.
        snapshot = read(profile, builders.snapshot_payload())
        assert snapshot.seats == {
            "p1": Position.NORTH,
            "p2": Position.EAST,
            "p3": Position.SOUTH,
            "p4": Position.WEST,
        }

    def test_a_seat_without_an_id_is_skipped(self, profile, builders):
        payload = builders.snapshot_payload()
        payload["table"]["seats"].append({"spot": "top"})
        assert len(read(profile, payload).seats) == 4


class TestPlayers:
    def test_a_player_carries_its_account_and_level(self, profile, builders):
        players = read(profile, builders.snapshot_payload()).players
        assert (players["p1"].account, players["p1"].level) == ("1001", "7")

    def test_a_player_knows_the_seat_it_sits_in(self, profile, builders):
        players = read(profile, builders.snapshot_payload()).players
        assert players["p4"].position is Position.WEST

    def test_a_missing_level_reads_as_none(self, profile, builders):
        # Measured live: the level is nullable per snapshot, so a seat can
        # report one on one read and nothing on the next.
        players = read(profile, builders.snapshot_payload()).players
        assert players["p2"].level is None

    def test_a_player_block_without_an_id_is_skipped(self, profile, builders):
        payload = builders.snapshot_payload()
        payload["state"]["people"]["top"] = {"label": "Nobody"}
        assert set(read(profile, payload).players) == {"p2", "p3", "p4"}

    def test_the_team_letters_are_kept_as_stated(self, profile, builders):
        players = read(profile, builders.snapshot_payload()).players
        assert {players[p].team_letter for p in players} == {"X", "Y"}


class TestRound:
    def test_the_round_index_is_the_last_completed_round(self, profile, builders):
        # The snapshot describes the round that *finished*, not the one being
        # watched: the joining round is skipped rather than reconstructed.
        assert read(profile, builders.snapshot_payload(round_index=5)).round_index == 5

    def test_the_round_block_is_found_by_prefix(self, profile, builders):
        # It is keyed by the prefix plus the game id, so there is no name to
        # look up — only a prefix to scan for.
        payload = builders.snapshot_payload()
        payload["state"]["round.some-other-game"] = payload["state"].pop("round.g1")
        assert read(profile, payload).round_index == 2

    def test_a_snapshot_without_a_round_block_still_reads_its_seats(
        self, profile, builders
    ):
        payload = builders.snapshot_payload()
        del payload["state"]["round.g1"]
        snapshot = read(profile, payload)
        assert (len(snapshot.seats), snapshot.round_index) == (4, None)

    def test_the_table_is_identified(self, profile, builders):
        snapshot = read(profile, builders.snapshot_payload(table_id="t9"))
        assert (snapshot.table_id, snapshot.is_tournament) == ("t9", True)


class TestScoreRows:
    def test_the_score_rows_come_back_newest_last(self, profile, builders):
        rows = (
            builders.score_row(value=80, marked=((80, 0), (0, 0))),
            builders.score_row(value=110, marked=((0, 0), (110, 0))),
        )
        read_rows = read(profile, builders.snapshot_payload(rows=rows)).score_rows
        assert [row.contract.value for row in read_rows] == [80, 110]

    def test_a_row_carries_its_contract_and_its_outcome(self, profile, builders):
        rows = (builders.score_row(value=100, suit="water", multiplier=2),)
        row = read(profile, builders.snapshot_payload(rows=rows)).score_rows[0]
        assert (row.contract.value, row.contract.suit, row.contract.multiplier) == (
            100, Suit.HEARTS, 2)
        assert row.made is True

    def test_a_failed_row_reads_as_failed(self, profile, builders):
        rows = (builders.score_row(made=False),)
        assert read(profile, builders.snapshot_payload(rows=rows)).score_rows[0].made is False

    def test_the_components_are_keyed_by_side_not_by_team_letter(
        self, profile, builders
    ):
        rows = (builders.score_row(taken=(90, 72), belote=(20, 0),
                                   marked=((90, 80), (0, 0))),)
        row = read(profile, builders.snapshot_payload(rows=rows)).score_rows[0]
        # p1 sits North and carries letter X, so X is the North-South side.
        assert row.taken == {TeamSide.NS: 90, TeamSide.EW: 72}
        assert row.belote == {TeamSide.NS: 20, TeamSide.EW: 0}
        assert row.marked[TeamSide.NS] == (90, 80)

    def test_a_sweep_is_recorded_as_stated(self, profile, builders):
        # A side taking all eight tricks has its card-point component recorded
        # as 250, not 162. The row is stored as the wire stated it and never
        # reconciled by arithmetic — the slam flag is what a reader consults.
        rows = (builders.score_row(taken=(250, 0)),)
        row = read(profile, builders.snapshot_payload(rows=rows)).score_rows[0]
        assert row.taken[TeamSide.NS] == 250


class TestTotals:
    def test_the_totals_are_keyed_by_side_not_by_team_letter(self, profile, builders):
        snapshot = read(profile, builders.snapshot_payload(totals=(40, 60)))
        assert snapshot.totals == {TeamSide.NS: 40, TeamSide.EW: 60}

    def test_a_snapshot_whose_letters_are_unseated_has_no_totals(
        self, profile, builders
    ):
        payload = builders.snapshot_payload()
        for block in payload["state"]["people"].values():
            block["side"] = {}
        assert read(profile, payload).totals is None


class TestMalformedBlocks:
    # A snapshot that is merely odd must not stop a session: the browser half
    # re-reads state at every round boundary, and one unusable read is a
    # missing round rather than a dead run.

    def test_a_seat_entry_that_is_not_a_block_is_skipped(self, profile, builders):
        payload = builders.snapshot_payload()
        payload["table"]["seats"].append("nonsense")
        assert len(read(profile, payload).seats) == 4

    def test_a_player_map_that_is_not_a_map_yields_no_players(self, profile, builders):
        payload = builders.snapshot_payload()
        payload["state"]["people"] = ["nonsense"]
        assert read(profile, payload).players == {}

    def test_a_player_entry_that_is_not_a_block_is_skipped(self, profile, builders):
        payload = builders.snapshot_payload()
        payload["state"]["people"]["top"] = "nonsense"
        assert set(read(profile, payload).players) == {"p2", "p3", "p4"}

    def test_a_state_that_is_not_a_map_reads_as_no_round(self, profile, builders):
        payload = builders.snapshot_payload()
        payload["state"] = "nonsense"
        assert read(profile, payload).round_index is None

    def test_totals_that_are_not_a_map_read_as_none(self, profile, builders):
        payload = builders.snapshot_payload()
        payload["state"]["round.g1"]["score"]["by_team"] = ["nonsense"]
        assert read(profile, payload).totals is None

    def test_a_score_row_that_is_not_a_block_is_skipped(self, profile, builders):
        payload = builders.snapshot_payload(rows=("nonsense", builders.score_row()))
        assert len(read(profile, payload).score_rows) == 1


class TestTiming:
    def test_the_snapshot_keeps_the_clock_it_arrived_on(self, profile, builders):
        assert read(profile, builders.snapshot_payload(), at=99).at == 99
