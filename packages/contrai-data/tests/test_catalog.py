"""Pins the corpus catalog: its rules, its schema, its build and its reader.

The pure rules — who won, whether a verdict is stale, what rows a game
turns into — are tested directly, without SQLite. The build is tested
end to end on small corpora written into ``tmp_path``, each one the
fixture game with one thing changed, so a failing case names the one
change that broke it. File modification times are pinned with
``os.utime`` wherever staleness matters: two files written in the same
test would otherwise race the filesystem clock.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
from datetime import UTC, datetime

import pytest
from contrai_core import ContractBid, Position, SlamLevel, Suit, TeamSide

from contrai_data import (
    CATALOG_FILE,
    CATALOG_SCHEMA_VERSION,
    BidMade,
    CatalogError,
    EndReason,
    GameEnded,
    GameStarted,
    GameVerdict,
    Header,
    JoinPhase,
    Mismatch,
    MismatchKind,
    ObservedFrom,
    RecordSource,
    RoundScored,
    RoundVerdict,
    Seat,
    SeatKind,
    build_catalog,
    catalog_path,
    encode,
    player_games,
    project,
    write_verdict,
)
from contrai_data.catalog import (
    _GameRow,
    _MismatchRow,
    _RoundRow,
    _SeatRow,
    _effective_winner,
    _game_rows,
    _pick_canonical,
    _verdict_status,
)

ENGINE_ID = "engine-20260910T181815Z-a1b2c3"

#: Fixed modification times, in seconds: a record, then a verdict written
#: after it.
RECORD_TIME = 1_000_000
VERDICT_TIME = RECORD_TIME + 60


def _ns(seconds: int) -> int:
    return seconds * 1_000_000_000


def _touch(path, seconds: int) -> None:
    """Pin a file's modification time."""

    os.utime(path, ns=(_ns(seconds), _ns(seconds)))


def _observed_seats(**names: str) -> dict[Position, Seat]:
    """Four observed seats, ids ``p-n`` / ``p-w`` / …, names overridable."""

    return {
        position: Seat(
            id=f"p-{position.name[0].lower()}",
            name=names.get(position.name[0], f"player-{position.name[0]}"),
            account=f"acc-{position.name[0].lower()}",
            kind=SeatKind.OBSERVED,
            level="gold",
        )
        for position in Position
    }


def _game(
    events,
    *,
    game_id: str | None = None,
    source: RecordSource | None = None,
    created_at: str | None = None,
    preset: str | None = None,
    seats=None,
    observed_from=None,
    ended=...,
    drop=(),
    replace=(),
):
    """The fixture game with some parts changed.

    Args:
        events: The fixture's events.
        game_id: A new header id.
        source: A new header source.
        created_at: A new header timestamp.
        preset: A new preset name.
        seats: New seats.
        observed_from: A mid-game join.
        ended: A new closing event, or ``None`` to drop it.
        drop: Predicates; an event any of them accepts is removed.
        replace: ``(predicate, function)`` pairs applied to each event.

    Returns:
        The changed events.
    """

    header, started, *rest = events
    header_changes = {}
    if game_id is not None:
        header_changes["game_id"] = game_id
    if source is not None:
        header_changes["source"] = source
    if created_at is not None:
        header_changes["created_at"] = created_at
    header = dataclasses.replace(header, **header_changes)
    started_changes = {}
    if seats is not None:
        started_changes["seats"] = seats
    if observed_from is not None:
        started_changes["observed_from"] = observed_from
    if preset is not None:
        started_changes["ruleset"] = dataclasses.replace(started.ruleset, preset=preset)
    started = dataclasses.replace(started, **started_changes)
    body = [event for event in rest if not isinstance(event, GameEnded)]
    body = [event for event in body if not any(test(event) for test in drop)]
    for test, change in replace:
        body = [change(event) if test(event) else event for event in body]
    closing = [event for event in rest if isinstance(event, GameEnded)]
    if ended is not ...:
        closing = [] if ended is None else [ended]
    return [header, started, *body, *closing]


def _ended(ns: int, ew: int, reason=EndReason.TARGET_REACHED, winner=None):
    return GameEnded(
        totals={TeamSide.NS: ns, TeamSide.EW: ew},
        winner=winner,
        reason=reason,
        ts="2026-09-10T19:00:00Z",
    )


def _write_record(root, events, name: str | None = None, *, at: int = RECORD_TIME):
    """Write events as a record file under ``root/games``."""

    path = root / "games" / f"{name or events[0].game_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(encode(e) + "\n" for e in events).encode("utf-8"))
    _touch(path, at)
    return path


def _verdict(
    game_id: str = ENGINE_ID,
    numbers=(1, 2, 3),
    *,
    source: str = "engine",
    preset: str = "classic",
    rounds=None,
    notes=(),
) -> GameVerdict:
    """A verdict, every round ``partial`` on an unchecked score by default."""

    return GameVerdict(
        game_id=game_id,
        source=source,
        preset=preset,
        rounds=rounds
        if rounds is not None
        else tuple(RoundVerdict.decide(n, unchecked=("score",)) for n in numbers),
        notes=notes,
    )


def _write_verdict(root, verdict: GameVerdict, *, at: int = VERDICT_TIME):
    path = write_verdict(root, verdict)
    _touch(path, at)
    return path


def _query(root, sql: str, *args):
    connection = sqlite3.connect(catalog_path(root))
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


@pytest.fixture
def root(tmp_path):
    """An empty records root."""

    path = tmp_path / "records"
    path.mkdir()
    return path


@pytest.fixture
def observed(three_round_game):
    """The fixture game as an observed record NS won on totals."""

    return _game(
        three_round_game,
        game_id="obs-0001",
        source=RecordSource.OBSERVED,
        created_at="2026-09-20T10:00:00Z",
        preset="tournament",
        seats=_observed_seats(),
        ended=_ended(2010, 1500),
    )


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


class TestCatalogPath:
    def test_it_sits_directly_under_the_root(self, tmp_path):
        assert catalog_path(tmp_path) == tmp_path / CATALOG_FILE == (
            tmp_path / "catalog.sqlite"
        )


class TestEffectiveWinner:
    def test_a_recorded_winner_is_used(self, three_round_game):
        events = _game(
            three_round_game, ended=_ended(100, 2000, winner=TeamSide.NS)
        )

        assert _effective_winner(project(events)) == (TeamSide.NS, "recorded")

    def test_one_side_over_the_target_wins_on_totals(self, three_round_game):
        events = _game(three_round_game, ended=_ended(1500, 2010))

        assert _effective_winner(project(events)) == (TeamSide.EW, "totals")

    def test_exactly_the_target_counts_as_reaching_it(self, three_round_game):
        events = _game(three_round_game, ended=_ended(2000, 1990))

        assert _effective_winner(project(events)) == (TeamSide.NS, "totals")

    def test_both_sides_over_the_target_is_nobody(self, three_round_game):
        # The belote gate or sudden death decided it; data cannot say which.
        events = _game(three_round_game, ended=_ended(2010, 2100))

        assert _effective_winner(project(events)) == (None, None)

    def test_an_interrupted_game_is_nobody(self, three_round_game):
        events = _game(
            three_round_game, ended=_ended(2010, 100, reason=EndReason.INTERRUPTED)
        )

        assert _effective_winner(project(events)) == (None, None)

    def test_missing_totals_is_nobody(self, three_round_game):
        ended = GameEnded(
            totals=None, winner=None, reason=EndReason.TARGET_REACHED, ts=None
        )
        events = _game(three_round_game, ended=ended)

        assert _effective_winner(project(events)) == (None, None)

    def test_a_record_that_never_ends_is_nobody(self, three_round_game):
        events = _game(three_round_game, ended=None)

        assert _effective_winner(project(events)) == (None, None)


class TestVerdictStatus:
    def _status(self, events, verdict, record_s=RECORD_TIME, verdict_s=VERDICT_TIME):
        return _verdict_status(
            project(events),
            verdict,
            record_mtime_ns=_ns(record_s),
            verdict_mtime_ns=_ns(verdict_s),
        )

    def test_a_newer_verdict_is_fresh(self, three_round_game):
        assert self._status(three_round_game, _verdict()) == "fresh"

    def test_an_equal_mtime_is_fresh(self, three_round_game):
        # Coarse filesystem clocks stamp a verify run right after a record
        # with the same time; strictly older is the stale test.
        assert (
            self._status(three_round_game, _verdict(), RECORD_TIME, RECORD_TIME)
            == "fresh"
        )

    def test_an_older_verdict_is_stale(self, three_round_game):
        assert (
            self._status(three_round_game, _verdict(), VERDICT_TIME, RECORD_TIME)
            == "stale"
        )

    def test_a_verdict_over_other_rounds_is_stale(self, three_round_game):
        assert self._status(three_round_game, _verdict(numbers=(1, 2))) == "stale"

    def test_a_verdict_under_another_preset_is_stale(self, three_round_game):
        assert (
            self._status(three_round_game, _verdict(preset="tournament")) == "stale"
        )

    def test_a_verdict_from_another_source_is_stale(self, three_round_game):
        assert self._status(three_round_game, _verdict(source="observed")) == "stale"


class TestGameRows:
    def _rows(self, events, verdict=None, status="missing", path="games/x.jsonl"):
        return _game_rows(project(events), path, verdict, status)

    def test_the_game_row_carries_the_record_s_identity(self, observed):
        game = self._rows(observed, _verdict("obs-0001", source="observed",
                                             preset="tournament"), "fresh").game

        assert game.game_id == "obs-0001"
        assert game.path == "games/x.jsonl"
        assert game.source == "observed"
        assert game.generator == "contrai-engine 0.4.0"
        assert game.created_at == "2026-09-20T10:00:00Z"
        assert game.ended_at == "2026-09-10T19:00:00Z"
        assert game.preset == "tournament"
        assert (game.round_count, game.complete_round_count) == (3, 3)
        assert (game.complete, game.truncated) == (1, 0)
        assert game.end_reason == "target_reached"
        assert (game.total_ns, game.total_ew) == (2010, 1500)
        assert (game.winner, game.winner_basis) == ("NS", "totals")
        assert (game.verdict, game.verdict_status) == ("partial", "fresh")
        assert json.loads(game.verdict_notes) == []

    def test_a_game_joined_mid_way_says_where(self, observed):
        joined = ObservedFrom(
            round=4,
            phase=JoinPhase.PLAY,
            totals={TeamSide.NS: 100, TeamSide.EW: 50},
        )

        game = self._rows(_game(observed, observed_from=joined)).game

        assert (game.joined_round, game.joined_phase) == (4, "play")

    def test_a_game_with_no_verdict_has_no_verdict_columns(self, observed):
        game = self._rows(observed).game

        assert game.verdict is None
        assert game.verdict_notes is None
        assert game.verdict_status == "missing"

    def test_four_seats_with_side_and_result(self, observed):
        seats = {seat.position: seat for seat in self._rows(observed).seats}

        assert set(seats) == {"N", "W", "S", "E"}
        assert seats["N"].side == seats["S"].side == "NS"
        assert seats["N"].result == seats["S"].result == "won"
        assert seats["W"].result == seats["E"].result == "lost"
        assert seats["N"].player_id == "p-n"
        assert seats["N"].account == "acc-n"
        assert seats["N"].kind == "observed"
        assert seats["N"].level == "gold"

    def test_an_unknown_winner_leaves_the_result_null(self, three_round_game):
        seats = self._rows(three_round_game).seats

        assert {seat.result for seat in seats} == {None}

    def test_engine_seats_have_no_player_id(self, three_round_game):
        seats = self._rows(three_round_game).seats

        assert {seat.player_id for seat in seats} == {None}
        assert {seat.name for seat in seats} == {"ai:expert"}

    def test_contract_columns_are_record_tokens(self, three_round_game):
        first = self._rows(three_round_game).rounds[0]

        assert first.dealer == "E"
        assert first.hands_derivation == "self_play"
        assert (first.declarer, first.declarer_side) == ("N", "NS")
        assert (first.contract_value, first.contract_slam) == (80, None)
        assert first.trump == "S"
        assert first.multiplier == 1
        assert first.bid_count == 4
        assert first.trick_count == 8
        assert first.outcome == "made"
        assert first.slam == "unannounced"
        assert first.score_source == "engine"
        assert (first.taken_ns, first.taken_ew) == (162, 0)
        assert (first.marked_ns, first.marked_ew) == (242, 0)
        assert (first.total_ns, first.total_ew) == (242, 0)

    def test_a_slam_bid_fills_contract_slam(self, three_round_game):
        slam = ContractBid(player=Position.NORTH, value=SlamLevel.SLAM, suit=Suit.SPADES)
        events = _game(
            three_round_game,
            replace=[
                (
                    lambda e: isinstance(e, BidMade) and e.round == 1 and e.seq == 1,
                    lambda e: dataclasses.replace(e, bid=slam),
                )
            ],
        )

        first = self._rows(events).rounds[0]

        assert (first.contract_value, first.contract_slam) == (None, "slam")

    def test_a_passed_out_round_has_no_contract(self, three_round_game):
        second = self._rows(three_round_game).rounds[1]

        assert second.declarer is None
        assert second.declarer_side is None
        assert second.contract_value is None
        assert second.trump is None
        assert second.multiplier is None
        assert second.outcome == "all_pass"
        assert second.trick_count == 0

    def test_an_unscored_round_leaves_the_score_null(self, three_round_game):
        events = _game(
            three_round_game,
            drop=[lambda e: isinstance(e, RoundScored) and e.round == 3],
        )

        third = self._rows(events).rounds[2]

        assert third.outcome is None
        assert third.slam is third.score_source is None
        assert third.taken_ns is third.marked_ns is third.total_ns is None

    def test_derived_tricks_are_counted(self, three_round_game):
        # The fixture reconstructs the last trick of each played round.
        rounds = self._rows(three_round_game).rounds

        assert [r.derived_trick_count for r in rounds] == [1, 0, 1]

    def test_verdicts_join_by_round_number_not_position(self, three_round_game):
        verdict = _verdict(
            rounds=(
                RoundVerdict.decide(3, mismatches=(Mismatch(MismatchKind.SCORE, "d"),)),
                RoundVerdict.decide(1),
                RoundVerdict.decide(2, unchecked=("score",)),
            )
        )

        rounds = self._rows(three_round_game, verdict, "fresh").rounds

        assert [(r.round, r.verdict) for r in rounds] == [
            (1, "verified"),
            (2, "partial"),
            (3, "suspect"),
        ]
        assert json.loads(rounds[1].unchecked) == ["score"]
        assert rounds[0].replayed == 1

    def test_mismatches_are_numbered_from_one(self, three_round_game):
        mismatches = (
            Mismatch(MismatchKind.TRICK_WINNER, "a", position="North", trick=2,
                     expected="North", observed="West"),
            Mismatch(MismatchKind.SCORE, "b"),
        )
        verdict = _verdict(
            rounds=(
                RoundVerdict.decide(1),
                RoundVerdict.decide(2),
                RoundVerdict.decide(3, mismatches=mismatches),
            )
        )

        rows = self._rows(three_round_game, verdict, "fresh").mismatches

        assert [(m.round, m.n, m.kind) for m in rows] == [
            (3, 1, "trick_winner"),
            (3, 2, "score"),
        ]
        assert rows[0].trick == 2
        assert (rows[0].expected, rows[0].observed) == ("North", "West")

    @pytest.mark.parametrize(
        ("written", "stored"), [("North", "N"), ("East", "E"), ("nowhere", "nowhere")]
    )
    def test_a_mismatch_position_becomes_the_record_token(
        self, three_round_game, written, stored
    ):
        verdict = _verdict(
            rounds=(
                RoundVerdict.decide(
                    1, mismatches=(Mismatch(MismatchKind.BELOTE, "d", position=written),)
                ),
            )
        )

        (row,) = self._rows(three_round_game, verdict, "stale").mismatches

        assert row.position == stored

    def test_mismatches_of_absent_rounds_are_dropped(self, three_round_game):
        verdict = _verdict(
            rounds=(
                RoundVerdict.decide(9, mismatches=(Mismatch(MismatchKind.SCORE, "d"),)),
            )
        )

        assert self._rows(three_round_game, verdict, "stale").mismatches == ()


class TestPickCanonical:
    def test_the_file_named_after_the_id_wins(self, tmp_path):
        paths = [tmp_path / "a-copy.jsonl", tmp_path / "obs-1.jsonl"]

        assert _pick_canonical("obs-1", paths) == tmp_path / "obs-1.jsonl"

    def test_otherwise_the_first_in_sorted_order(self, tmp_path):
        paths = [tmp_path / "b.jsonl", tmp_path / "a.jsonl"]

        assert _pick_canonical("obs-1", paths) == tmp_path / "a.jsonl"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    @pytest.mark.parametrize(
        ("table", "row_type"),
        [
            ("games", _GameRow),
            ("seats", _SeatRow),
            ("rounds", _RoundRow),
            ("mismatches", _MismatchRow),
        ],
    )
    def test_row_fields_are_the_columns_in_order(self, root, table, row_type):
        # The INSERTs are generated from the fields; a column added to one
        # and not the other must fail here, not shift every later value.
        build_catalog(root)

        columns = [row[1] for row in _query(root, f"PRAGMA table_info({table})")]

        assert columns == [field.name for field in dataclasses.fields(row_type)]

    def test_user_version_is_the_schema_version(self, root):
        build_catalog(root)

        assert _query(root, "PRAGMA user_version") == [(CATALOG_SCHEMA_VERSION,)]

    def test_meta_names_the_build(self, root):
        build_catalog(root, now=datetime(2026, 9, 24, 12, 0, tzinfo=UTC))

        meta = dict(_query(root, "SELECT key, value FROM meta"))

        assert meta["schema_version"] == CATALOG_SCHEMA_VERSION
        assert meta["built_at"] == "2026-09-24T12:00:00Z"
        assert meta["generator"].startswith("contrai-data")
        assert meta["root"] == root.resolve().as_posix()

    def test_tables_are_strict(self, root):
        build_catalog(root)
        connection = sqlite3.connect(catalog_path(root))

        # A non-strict table would store the text and sort it after every
        # number, wrongly and silently.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO mismatches (game_id, round, n, kind, detail) "
                "VALUES ('g', 'three', 1, 'score', 'd')"
            )
        connection.close()


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


class TestBuildCatalog:
    def test_a_root_that_is_not_a_directory_is_refused(self, tmp_path):
        path = tmp_path / "file"
        path.write_text("x", encoding="utf-8")

        with pytest.raises(NotADirectoryError):
            build_catalog(path)

    def test_a_root_without_games_builds_an_empty_catalog(self, root):
        summary = build_catalog(root)

        assert summary.game_count == 0
        assert summary.path == catalog_path(root)
        assert catalog_path(root).is_file()

    def test_every_record_is_a_row(self, root, three_round_game, observed):
        _write_record(root, three_round_game)
        _write_record(root, observed)

        build_catalog(root)

        assert _query(root, "SELECT game_id FROM games ORDER BY game_id") == [
            (ENGINE_ID,),
            ("obs-0001",),
        ]

    def test_paths_are_relative_with_forward_slashes(self, root, observed):
        _write_record(root, observed)

        build_catalog(root)

        assert _query(root, "SELECT path FROM games") == [("games/obs-0001.jsonl",)]

    def test_a_rebuild_replaces_the_catalog(self, root, three_round_game, observed):
        _write_record(root, three_round_game)
        build_catalog(root)
        _write_record(root, observed)

        summary = build_catalog(root)

        assert summary.game_count == 2
        assert _query(root, "SELECT COUNT(*) FROM games") == [(2,)]

    def test_no_temporary_file_is_left(self, root, observed):
        _write_record(root, observed)

        build_catalog(root)

        assert sorted(p.name for p in root.iterdir()) == ["catalog.sqlite", "games"]

    def test_a_failed_build_leaves_the_previous_catalog(
        self, root, three_round_game, observed, monkeypatch
    ):
        _write_record(root, three_round_game)
        build_catalog(root)
        _write_record(root, observed)

        def explode(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr("contrai_data.catalog._write", explode)

        with pytest.raises(RuntimeError):
            build_catalog(root)

        assert _query(root, "SELECT game_id FROM games") == [(ENGINE_ID,)]
        assert not list(root.glob("*.tmp"))

    def test_an_unreplaceable_catalog_raises_and_removes_the_temporary(
        self, root, observed, monkeypatch
    ):
        # What Windows does while another program holds the catalog open.
        _write_record(root, observed)

        def refuse(source, target):
            raise PermissionError(13, "in use", str(target))

        monkeypatch.setattr("contrai_data.catalog.os.replace", refuse)

        with pytest.raises(PermissionError):
            build_catalog(root)

        assert not list(root.glob("*.tmp"))

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            (b"not json\n{}\n", "RecordFormatError"),
            (b'{"event": "header", "\xff": 1}\n', "UnicodeDecodeError"),
        ],
    )
    def test_an_unreadable_record_is_skipped(self, root, observed, content, reason):
        _write_record(root, observed)
        bad = root / "games" / "bad.jsonl"
        bad.write_bytes(content)

        summary = build_catalog(root)

        assert summary.game_count == 1
        (skipped,) = summary.skipped
        assert (skipped.path, skipped.kind) == ("games/bad.jsonl", "record")
        assert skipped.reason.startswith(reason)

    def test_a_record_with_an_impossible_ruleset_is_skipped(self, root, observed):
        path = _write_record(root, observed)
        text = path.read_text(encoding="utf-8")
        assert '"target_score": 2000' in text
        path.write_text(
            text.replace('"target_score": 2000', '"target_score": 1234'),
            encoding="utf-8",
        )

        summary = build_catalog(root)

        assert summary.game_count == 0
        assert [s.kind for s in summary.skipped] == ["record"]

    def test_a_truncated_record_is_indexed_as_truncated(self, root, observed):
        path = _write_record(root, observed)
        with path.open("ab") as handle:
            handle.write(b'{"event": "card_pl')

        build_catalog(root)

        assert _query(root, "SELECT truncated, complete FROM games") == [(1, 0)]

    def test_a_duplicate_id_keeps_the_file_named_after_it(self, root, observed):
        _write_record(root, observed)
        _write_record(root, observed, name="a-copy")

        summary = build_catalog(root)

        assert _query(root, "SELECT path FROM games") == [("games/obs-0001.jsonl",)]
        (skipped,) = summary.skipped
        assert skipped.path == "games/a-copy.jsonl"
        assert "duplicate game id obs-0001" in skipped.reason

    def test_a_record_named_unlike_its_id_finds_its_verdict_by_id(
        self, root, observed
    ):
        _write_record(root, observed, name="renamed")
        _write_verdict(root, _verdict("obs-0001", source="observed", preset="tournament"))

        summary = build_catalog(root)

        assert summary.verdict_statuses == {"fresh": 1}
        assert summary.skipped == ()

    def test_an_unreadable_verdict_keeps_its_game(self, root, observed):
        _write_record(root, observed)
        (root / "verdicts").mkdir()
        (root / "verdicts" / "obs-0001.json").write_text("{", encoding="utf-8")

        summary = build_catalog(root)

        assert _query(root, "SELECT verdict, verdict_status FROM games") == [
            (None, "unreadable")
        ]
        (skipped,) = summary.skipped
        assert (skipped.path, skipped.kind) == ("verdicts/obs-0001.json", "verdict")
        assert "VerdictFormatError" in skipped.reason

    def test_a_verdict_naming_another_game_is_unreadable(self, root, observed):
        _write_record(root, observed)
        other = _write_verdict(root, _verdict("obs-9999", source="observed"))
        other.rename(root / "verdicts" / "obs-0001.json")

        summary = build_catalog(root)

        assert summary.verdict_statuses == {"unreadable": 1}
        assert "obs-9999" in summary.skipped[0].reason

    def test_an_orphan_verdict_is_skipped(self, root, observed):
        _write_record(root, observed)
        _write_verdict(root, _verdict("obs-0404", source="observed"))

        summary = build_catalog(root)

        assert summary.verdict_statuses == {"missing": 1}
        (skipped,) = summary.skipped
        assert (skipped.path, skipped.kind) == ("verdicts/obs-0404.json", "verdict")

    def test_a_game_id_that_is_not_one_segment_has_no_verdict(self, root, observed):
        _write_record(root, _game(observed, game_id=".."), name="dots")

        summary = build_catalog(root)

        assert summary.verdict_statuses == {"missing": 1}
        (skipped,) = summary.skipped
        assert skipped.kind == "verdict"
        assert "not one path segment" in skipped.reason


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class TestViews:
    def _fresh(self, root, events, **verdict_changes):
        game_id = events[0].game_id
        _write_record(root, events)
        _write_verdict(
            root,
            _verdict(
                game_id,
                source=str(events[0].source),
                preset=events[1].ruleset.preset,
                **verdict_changes,
            ),
        )

    def test_clean_rounds_are_complete_fresh_replayed_and_not_suspect(
        self, root, observed
    ):
        self._fresh(
            root,
            observed,
            rounds=(
                RoundVerdict.decide(1),
                RoundVerdict.decide(2, unchecked=("score",)),
                RoundVerdict.decide(3, mismatches=(Mismatch(MismatchKind.SCORE, "d"),)),
            ),
        )

        summary = build_catalog(root)

        rows = _query(root, "SELECT round, source, preset, path FROM clean_rounds ORDER BY round")
        assert rows == [
            (1, "observed", "tournament", "games/obs-0001.jsonl"),
            (2, "observed", "tournament", "games/obs-0001.jsonl"),
        ]
        assert summary.clean_round_count == 2

    def test_a_stale_verdict_makes_no_round_clean(self, root, observed):
        self._fresh(root, observed)
        _touch(root / "games" / "obs-0001.jsonl", VERDICT_TIME + 1)

        summary = build_catalog(root)

        assert summary.verdict_statuses == {"stale": 1}
        assert summary.clean_round_count == 0

    def test_a_round_that_was_not_replayed_is_not_clean(self, root, observed):
        self._fresh(
            root,
            observed,
            rounds=(
                RoundVerdict.decide(1),
                RoundVerdict.decide(2),
                RoundVerdict.decide(3, replayed=False),
            ),
        )

        build_catalog(root)

        assert _query(root, "SELECT round FROM clean_rounds ORDER BY round") == [(1,), (2,)]

    def test_players_count_games_wins_and_names(self, root, observed):
        _write_record(root, observed)
        _write_record(
            root,
            _game(
                observed,
                game_id="obs-0002",
                created_at="2026-09-21T10:00:00Z",
                seats=_observed_seats(N="renamed-north"),
                ended=_ended(1500, 2010),
            ),
        )

        build_catalog(root)

        (row,) = _query(
            root,
            "SELECT games, wins, losses, first_seen, last_seen, last_name, "
            "last_level, names FROM players WHERE player_id = 'p-n'",
        )
        assert row[:6] == (
            2,
            1,
            1,
            "2026-09-20T10:00:00Z",
            "2026-09-21T10:00:00Z",
            "renamed-north",
        )
        assert row[6] == "gold"
        assert json.loads(row[7]) == ["player-N", "renamed-north"]

    def test_player_names_date_each_name(self, root, observed):
        _write_record(root, observed)
        _write_record(
            root,
            _game(
                observed,
                game_id="obs-0002",
                created_at="2026-09-21T10:00:00Z",
                seats=_observed_seats(N="renamed-north"),
            ),
        )

        build_catalog(root)

        assert _query(
            root,
            "SELECT name, games, first_seen, last_seen FROM player_names "
            "WHERE player_id = 'p-n' ORDER BY first_seen",
        ) == [
            ("player-N", 1, "2026-09-20T10:00:00Z", "2026-09-20T10:00:00Z"),
            ("renamed-north", 1, "2026-09-21T10:00:00Z", "2026-09-21T10:00:00Z"),
        ]

    def test_player_levels_track_a_change(self, root, observed):
        promoted = {
            position: dataclasses.replace(seat, level="platinum")
            for position, seat in _observed_seats().items()
        }
        _write_record(root, observed)
        _write_record(
            root,
            _game(
                observed,
                game_id="obs-0002",
                created_at="2026-09-21T10:00:00Z",
                seats=promoted,
            ),
        )

        build_catalog(root)

        assert _query(
            root,
            "SELECT level, first_seen FROM player_levels "
            "WHERE player_id = 'p-n' ORDER BY first_seen",
        ) == [("gold", "2026-09-20T10:00:00Z"), ("platinum", "2026-09-21T10:00:00Z")]
        assert _query(
            root, "SELECT last_level FROM players WHERE player_id = 'p-n'"
        ) == [("platinum",)]

    def test_seats_without_an_id_are_not_players(self, root, three_round_game):
        _write_record(root, three_round_game)

        summary = build_catalog(root)

        assert summary.player_count == 0


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_games_are_counted_by_source(self, root, three_round_game, observed):
        _write_record(root, three_round_game)
        _write_record(root, observed)

        summary = build_catalog(root)

        assert summary.games_by_source == {"engine": 1, "observed": 1}
        assert summary.game_count == 2
        assert (summary.round_count, summary.complete_round_count) == (6, 6)
        assert summary.player_count == 4

    def test_games_are_counted_by_verdict_and_status(
        self, root, three_round_game, observed
    ):
        _write_record(root, three_round_game)
        _write_verdict(root, _verdict())
        _write_record(root, observed)

        summary = build_catalog(root)

        assert summary.verdict_statuses == {"fresh": 1, "missing": 1}
        assert summary.game_verdicts == {"partial": 1}
        assert summary.round_verdicts == {"partial": 3}

    def test_skipped_files_are_listed(self, root, observed):
        _write_record(root, observed)
        (root / "games" / "bad.jsonl").write_bytes(b"x\n{}\n")
        _write_verdict(root, _verdict("obs-0404", source="observed"))

        summary = build_catalog(root)

        assert [(s.kind, s.path) for s in summary.skipped] == [
            ("record", "games/bad.jsonl"),
            ("verdict", "verdicts/obs-0404.json"),
        ]

    def test_the_clean_count_is_the_view_s(self, root, three_round_game):
        _write_record(root, three_round_game)
        _write_verdict(root, _verdict())

        summary = build_catalog(root)

        assert summary.clean_round_count == 3
        assert _query(root, "SELECT COUNT(*) FROM clean_rounds") == [(3,)]


# ---------------------------------------------------------------------------
# Reading a player back
# ---------------------------------------------------------------------------


class TestPlayerGames:
    @pytest.fixture
    def corpus(self, root, observed):
        _write_record(
            root,
            _game(
                observed,
                game_id="obs-0002",
                created_at="2026-09-21T10:00:00Z",
                seats=_observed_seats(N="renamed-north"),
                ended=_ended(1500, 2010),
            ),
        )
        _write_record(root, observed)
        _write_verdict(root, _verdict("obs-0001", source="observed", preset="tournament"))
        build_catalog(root, now=datetime(2026, 9, 24, 12, 0, tzinfo=UTC))
        return root

    def test_a_player_is_found_by_id(self, corpus):
        report = player_games(corpus, "p-n")

        assert [game.game_id for game in report.games] == ["obs-0001", "obs-0002"]
        assert report.player == "p-n"

    def test_a_player_is_found_by_any_name(self, corpus):
        report = player_games(corpus, "renamed-north")

        assert [game.game_id for game in report.games] == ["obs-0002"]

    def test_each_game_names_seat_partner_and_result(self, corpus):
        first, second = player_games(corpus, "p-n").games

        assert (first.position, first.side, first.partner) == ("N", "NS", "player-S")
        assert (first.result, second.result) == ("won", "lost")
        assert (first.total_ns, first.total_ew) == (2010, 1500)
        assert first.end_reason == "target_reached"
        assert (first.verdict, first.verdict_status) == ("partial", "fresh")
        assert (second.verdict, second.verdict_status) == (None, "missing")

    def test_games_come_oldest_first(self, corpus):
        created = [game.created_at for game in player_games(corpus, "p-n").games]

        assert created == sorted(created)

    def test_an_unknown_player_has_no_games(self, corpus):
        assert player_games(corpus, "nobody").games == ()

    def test_the_report_carries_the_build_time(self, corpus):
        assert player_games(corpus, "p-n").built_at == "2026-09-24T12:00:00Z"

    def test_a_missing_catalog_raises_and_creates_nothing(self, root):
        with pytest.raises(FileNotFoundError):
            player_games(root, "p-n")

        assert not catalog_path(root).exists()

    def test_another_schema_version_is_refused(self, corpus):
        connection = sqlite3.connect(catalog_path(corpus))
        connection.execute("PRAGMA user_version = 99")
        connection.close()

        with pytest.raises(CatalogError, match="schema version 99"):
            player_games(corpus, "p-n")

    def test_a_file_that_is_not_a_catalog_is_refused(self, root):
        catalog_path(root).write_text("not a database, just text\n" * 20, encoding="utf-8")

        with pytest.raises(CatalogError, match="not a catalog"):
            player_games(root, "p-n")

    def test_a_catalog_error_is_a_value_error(self):
        assert issubclass(CatalogError, ValueError)
