"""A SQLite index over a records root — derived, and rebuilt whole.

A corpus is thousands of JSONL records plus their verdict files, and the
questions asked of it are joins: *which games did this player play*,
*which rounds are clean enough to train on*, *how many rounds a day are
landing*, *which suspect rounds share a mismatch class*. None of that is
answerable without reading every file, so :func:`build_catalog` reads
them once and folds them into ``<root>/catalog.sqlite``.

**The catalog is an index, never a second copy.** Every row is derived
from a record or a verdict file, and the records stay the source of
truth: a catalog can be deleted at any time and rebuilt, and nothing
ever writes to it except a rebuild. That is also why there are no schema
migrations — ``PRAGMA user_version`` names the schema, and a catalog of
another version is simply rebuilt (or refused by a reader).

**Rebuilt whole, every run.** ``load_game`` costs about 2 ms a game, so
ten thousand games rebuild in about twenty seconds. An incremental index
would need change detection, deletion handling and a migration story,
for a file that is by construction disposable.

**Swapped in atomically.** The new catalog is built in a temporary file
beside the old one and moved over it with :func:`os.replace`, so a
reader sees the old catalog or the new one, never half of either. A
failed build leaves the previous catalog untouched.

**Nothing aborts the build.** An unreadable record, a duplicate game id,
an orphan verdict — each lands in the ``skipped`` table with its reason,
and the rest of the corpus is indexed regardless. A corpus of thousands
of files will always hold one bad one, and a build that stops on it is
a build nobody runs.

**It holds personal data.** Seats carry the table's player ids and
display names, so ``catalog.sqlite`` stays local and is git-ignored.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from contrai_core import Position, SlamLevel, TeamSide

from .events import EndReason
from .exceptions import CatalogError
from .projection import GameRecord, RoundRecord, load_game
from .store import games_dir
from .tokens import (
    contract_suit_token,
    contract_value_token,
    position_token,
    side_token,
)
from .verdict import (
    GameVerdict,
    RoundVerdict,
    read_verdict,
    verdict_path,
    verdicts_dir,
)

#: The catalog's file name, directly under a records root.
CATALOG_FILE = "catalog.sqlite"

#: The schema this build writes and reads, stored as ``PRAGMA
#: user_version``. Bumped on any change to a table or a view.
CATALOG_SCHEMA_VERSION = 1


def catalog_path(root: Path | str) -> Path:
    """The catalog file of a records root.

    Args:
        root: A records root — the directory holding ``games/``.

    Returns:
        ``<root>/catalog.sqlite``. Not created.
    """

    return Path(root) / CATALOG_FILE


def _generator() -> str:
    """This build's name and version, stamped into the catalog's ``meta``.

    Returns:
        ``contrai-data <version>``, or the bare package name when the
        distribution metadata is not installed.
    """

    try:
        return f"contrai-data {version('contrai-data')}"
    except PackageNotFoundError:  # pragma: no cover - installed in the workspace
        return "contrai-data"


# ----------------------------------------------------------------------
# What a build returns
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkippedFile:
    """A file the build could not index, and why.

    Attributes:
        path: The file, relative to the records root, with forward
            slashes.
        kind: ``record`` or ``verdict``.
        reason: One line saying what was wrong with it.
    """

    path: str
    kind: str
    reason: str


@dataclass(frozen=True, slots=True)
class CatalogSummary:
    """What a finished catalog holds, counted off the catalog itself.

    Every figure is computed by SQL on the finished database rather than
    tallied during the build, so the summary cannot disagree with what a
    query would find.

    Attributes:
        path: The catalog file.
        built_at: When the build ran, in UTC.
        games_by_source: Indexed games per record source.
        round_count: Indexed rounds.
        complete_round_count: Rounds whose structure finished.
        clean_round_count: Rows of the ``clean_rounds`` view.
        game_verdicts: Games per verdict, among games with a readable
            verdict.
        verdict_statuses: Games per verdict status — ``fresh``,
            ``stale``, ``missing``, ``unreadable``.
        round_verdicts: Rounds per verdict, among rounds a readable
            verdict covers.
        player_count: Distinct player ids.
        skipped: Every file the build could not index.
    """

    path: Path
    built_at: str
    games_by_source: Mapping[str, int]
    round_count: int
    complete_round_count: int
    clean_round_count: int
    game_verdicts: Mapping[str, int]
    verdict_statuses: Mapping[str, int]
    round_verdicts: Mapping[str, int]
    player_count: int
    skipped: tuple[SkippedFile, ...]

    @property
    def game_count(self) -> int:
        """Indexed games, all sources together."""

        return sum(self.games_by_source.values())


# ----------------------------------------------------------------------
# Rows — one frozen dataclass per table, fields in column order
# ----------------------------------------------------------------------
#
# The INSERT statements are generated from these fields, and a test pins
# them against ``PRAGMA table_info``, so a column added to one and not the
# other fails loudly instead of shifting every later value by one.


@dataclass(frozen=True, slots=True)
class _GameRow:
    game_id: str
    path: str
    source: str
    generator: str
    created_at: str
    ended_at: str | None
    preset: str
    joined_round: int | None
    joined_phase: str | None
    round_count: int
    complete_round_count: int
    complete: int
    truncated: int
    end_reason: str | None
    total_ns: int | None
    total_ew: int | None
    winner: str | None
    winner_basis: str | None
    verdict: str | None
    verdict_status: str
    verdict_notes: str | None


@dataclass(frozen=True, slots=True)
class _SeatRow:
    game_id: str
    position: str
    side: str
    player_id: str | None
    name: str
    account: str | None
    kind: str
    level: str | None
    result: str | None


@dataclass(frozen=True, slots=True)
class _RoundRow:
    game_id: str
    round: int
    dealer: str
    hands_derivation: str
    bid_count: int
    trick_count: int
    derived_trick_count: int
    belote_count: int
    complete: int
    declarer: str | None
    declarer_side: str | None
    contract_value: int | None
    contract_slam: str | None
    trump: str | None
    multiplier: int | None
    outcome: str | None
    slam: str | None
    score_source: str | None
    taken_ns: int | None
    taken_ew: int | None
    marked_ns: int | None
    marked_ew: int | None
    total_ns: int | None
    total_ew: int | None
    verdict: str | None
    replayed: int | None
    unchecked: str | None


@dataclass(frozen=True, slots=True)
class _MismatchRow:
    game_id: str
    round: int
    n: int
    kind: str
    detail: str
    position: str | None
    trick: int | None
    seq: int | None
    expected: str | None
    observed: str | None


@dataclass(frozen=True, slots=True)
class _GameRows:
    """Every row one game contributes, across the four tables."""

    game: _GameRow
    seats: tuple[_SeatRow, ...]
    rounds: tuple[_RoundRow, ...]
    mismatches: tuple[_MismatchRow, ...]


# ----------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------
#
# STRICT tables: SQLite otherwise stores whatever it is handed, and a
# string landing in an INTEGER column would sort and compare wrongly in
# every query without an error. Booleans are 0/1, seats and sides are
# the record's own tokens (N/W/S/E, NS/EW), trumps are S/H/D/C/NT/AT.

_TABLES = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value ANY
) STRICT;

CREATE TABLE games (
    game_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    source TEXT NOT NULL,
    generator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ended_at TEXT,
    preset TEXT NOT NULL,
    joined_round INTEGER,
    joined_phase TEXT,
    round_count INTEGER NOT NULL,
    complete_round_count INTEGER NOT NULL,
    complete INTEGER NOT NULL,
    truncated INTEGER NOT NULL,
    end_reason TEXT,
    total_ns INTEGER,
    total_ew INTEGER,
    winner TEXT,
    winner_basis TEXT,
    verdict TEXT,
    verdict_status TEXT NOT NULL,
    verdict_notes TEXT
) STRICT;

CREATE TABLE seats (
    game_id TEXT NOT NULL REFERENCES games (game_id),
    position TEXT NOT NULL,
    side TEXT NOT NULL,
    player_id TEXT,
    name TEXT NOT NULL,
    account TEXT,
    kind TEXT NOT NULL,
    level TEXT,
    result TEXT,
    PRIMARY KEY (game_id, position)
) STRICT;

CREATE TABLE rounds (
    game_id TEXT NOT NULL REFERENCES games (game_id),
    round INTEGER NOT NULL,
    dealer TEXT NOT NULL,
    hands_derivation TEXT NOT NULL,
    bid_count INTEGER NOT NULL,
    trick_count INTEGER NOT NULL,
    derived_trick_count INTEGER NOT NULL,
    belote_count INTEGER NOT NULL,
    complete INTEGER NOT NULL,
    declarer TEXT,
    declarer_side TEXT,
    contract_value INTEGER,
    contract_slam TEXT,
    trump TEXT,
    multiplier INTEGER,
    outcome TEXT,
    slam TEXT,
    score_source TEXT,
    taken_ns INTEGER,
    taken_ew INTEGER,
    marked_ns INTEGER,
    marked_ew INTEGER,
    total_ns INTEGER,
    total_ew INTEGER,
    verdict TEXT,
    replayed INTEGER,
    unchecked TEXT,
    PRIMARY KEY (game_id, round)
) STRICT;

CREATE TABLE mismatches (
    game_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    n INTEGER NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    position TEXT,
    trick INTEGER,
    seq INTEGER,
    expected TEXT,
    observed TEXT,
    PRIMARY KEY (game_id, round, n),
    FOREIGN KEY (game_id, round) REFERENCES rounds (game_id, round)
) STRICT;

CREATE TABLE skipped (
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL
) STRICT;
"""

# Created after the inserts: building an index once over finished tables
# is cheaper than maintaining it row by row.
_INDEXES_AND_VIEWS = """
CREATE INDEX seats_player_id ON seats (player_id);
CREATE INDEX seats_name ON seats (name);
CREATE INDEX rounds_verdict ON rounds (verdict);
CREATE INDEX mismatches_kind ON mismatches (kind);

-- One row per player id. Names and levels change over time, so the
-- latest ones are picked by game date, and every name ever seen is kept.
CREATE VIEW players AS
SELECT
    s.player_id,
    COUNT(DISTINCT s.game_id) AS games,
    COUNT(DISTINCT s.game_id) FILTER (WHERE s.result = 'won') AS wins,
    COUNT(DISTINCT s.game_id) FILTER (WHERE s.result = 'lost') AS losses,
    MIN(g.created_at) AS first_seen,
    MAX(g.created_at) AS last_seen,
    (SELECT s2.name FROM seats s2 JOIN games g2 USING (game_id)
      WHERE s2.player_id = s.player_id
      ORDER BY g2.created_at DESC, g2.game_id DESC LIMIT 1) AS last_name,
    (SELECT s2.level FROM seats s2 JOIN games g2 USING (game_id)
      WHERE s2.player_id = s.player_id
      ORDER BY g2.created_at DESC, g2.game_id DESC LIMIT 1) AS last_level,
    (SELECT json_group_array(name) FROM (
        SELECT DISTINCT s3.name AS name FROM seats s3
        WHERE s3.player_id = s.player_id ORDER BY s3.name)) AS names
FROM seats s JOIN games g USING (game_id)
WHERE s.player_id IS NOT NULL
GROUP BY s.player_id;

CREATE VIEW player_names AS
SELECT
    s.player_id,
    s.name,
    COUNT(DISTINCT s.game_id) AS games,
    MIN(g.created_at) AS first_seen,
    MAX(g.created_at) AS last_seen
FROM seats s JOIN games g USING (game_id)
WHERE s.player_id IS NOT NULL
GROUP BY s.player_id, s.name;

CREATE VIEW player_levels AS
SELECT
    s.player_id,
    s.level,
    COUNT(DISTINCT s.game_id) AS games,
    MIN(g.created_at) AS first_seen,
    MAX(g.created_at) AS last_seen
FROM seats s JOIN games g USING (game_id)
WHERE s.player_id IS NOT NULL
GROUP BY s.player_id, s.level;

-- A round fit to learn from: its structure finished, a verdict written
-- after the record covers it, it was replayed, and nothing disagreed.
-- ``partial`` counts as clean: for an observed record ``verified`` is
-- unreachable, because the table folds the last-trick bonus into the
-- card points and never says which side took it.
CREATE VIEW clean_rounds AS
SELECT r.*, g.source, g.preset, g.created_at, g.path
FROM rounds r JOIN games g USING (game_id)
WHERE r.complete = 1
  AND g.verdict_status = 'fresh'
  AND r.replayed = 1
  AND r.verdict IN ('verified', 'partial');
"""


def _insert(table: str, row_type: type) -> str:
    """The INSERT statement for one row dataclass.

    Args:
        table: The table name.
        row_type: The row dataclass, whose fields are the columns in
            order.

    Returns:
        A parameterised ``INSERT`` naming every column.
    """

    names = [field.name for field in dataclasses.fields(row_type)]
    return (
        f"INSERT INTO {table} ({', '.join(names)}) "
        f"VALUES ({', '.join('?' for _ in names)})"
    )


# ----------------------------------------------------------------------
# Pure rules — no SQLite, no filesystem
# ----------------------------------------------------------------------


def _effective_winner(record: GameRecord) -> tuple[TeamSide | None, str | None]:
    """The side that won a game, and how that is known.

    The recorded winner is used when there is one. Otherwise a game that
    ended on reaching the target, with known totals, is won by the side
    at or above the ruleset's target — but only when **exactly one** side
    is. Both sides over the target is a game the belote gate or sudden
    death decided, rules that live in the engine; guessing would be
    worse than saying nothing.

    Args:
        record: The game.

    Returns:
        The winning side and ``"recorded"`` or ``"totals"``, or
        ``(None, None)`` when the winner is unknown.
    """

    ended = record.ended
    if ended is None:
        return None, None
    if ended.winner is not None:
        return ended.winner, "recorded"
    if ended.reason is not EndReason.TARGET_REACHED or ended.totals is None:
        return None, None
    target = record.ruleset.target_score
    over = [side for side in TeamSide if ended.totals[side] >= target]
    if len(over) == 1:
        return over[0], "totals"
    return None, None


def _verdict_status(
    record: GameRecord,
    verdict: GameVerdict,
    *,
    record_mtime_ns: int,
    verdict_mtime_ns: int,
) -> str:
    """Whether a readable verdict still describes its record.

    A verdict file carries no timestamp and no hash of the record it
    judged, so staleness is inferred. It is ``stale`` when it is older
    than the record, when it covers other round numbers, or when it
    names another source or preset. A false "stale" only keeps rounds
    out of ``clean_rounds`` until the next ``contrai verify``; a false
    "fresh" would let an unchecked round in, so every doubt goes the
    safe way.

    Args:
        record: The indexed record.
        verdict: Its verdict, already read.
        record_mtime_ns: The record file's modification time.
        verdict_mtime_ns: The verdict file's modification time.

    Returns:
        ``fresh`` or ``stale``.
    """

    if verdict_mtime_ns < record_mtime_ns:
        return "stale"
    if {r.number for r in verdict.rounds} != {r.number for r in record.rounds}:
        return "stale"
    if verdict.source != str(record.header.source) or verdict.preset != record.preset:
        return "stale"
    return "fresh"


#: ``Mismatch.position`` is written as ``str(Position)`` — ``"North"`` —
#: while every other seat in the catalog is a record token.
_POSITION_TOKENS: Mapping[str, str] = {
    str(position): position_token(position) for position in Position
}


def _mismatch_position(value: str | None) -> str | None:
    """A mismatch's seat, as the record token the rest of the catalog uses.

    Args:
        value: The position as the verdict file spells it.

    Returns:
        ``N`` / ``W`` / ``S`` / ``E``, or the text unchanged when it is no
        seat's name.
    """

    if value is None:
        return None
    return _POSITION_TOKENS.get(value, value)


def _round_row(
    game_id: str, round_: RoundRecord, verdict: RoundVerdict | None
) -> _RoundRow:
    """One round's row.

    Contract columns come from the contract the projection derived off
    the auction, not from the score line's claim: the derivation is what
    the verifier checked.

    Args:
        game_id: The game.
        round_: The round.
        verdict: The round's verdict, or ``None`` when no readable
            verdict covers it.

    Returns:
        The row.
    """

    contract = round_.contract
    score = round_.score
    if contract is None:
        declarer = declarer_side = contract_slam = trump = None
        contract_value = multiplier = None
    else:
        declarer = position_token(contract.declarer)
        declarer_side = side_token(contract.declarer.team_side)
        if isinstance(contract.value, SlamLevel):
            contract_value = None
            contract_slam = str(contract_value_token(contract.value))
        else:
            contract_value = contract.value
            contract_slam = None
        trump = contract_suit_token(contract.suit)
        multiplier = 4 if contract.redoubled_by else 2 if contract.doubled_by else 1
    totals = score.totals if score is not None else None
    return _RoundRow(
        game_id=game_id,
        round=round_.number,
        dealer=position_token(round_.dealer),
        hands_derivation=str(round_.hands_derivation),
        bid_count=len(round_.auction),
        trick_count=len(round_.tricks),
        derived_trick_count=sum(round_.derived_tricks),
        belote_count=len(round_.belotes),
        complete=int(round_.complete),
        declarer=declarer,
        declarer_side=declarer_side,
        contract_value=contract_value,
        contract_slam=contract_slam,
        trump=trump,
        multiplier=multiplier,
        outcome=str(round_.outcome) if round_.outcome is not None else None,
        slam=str(score.slam) if score is not None else None,
        score_source=str(score.source) if score is not None else None,
        taken_ns=score.taken[TeamSide.NS] if score is not None else None,
        taken_ew=score.taken[TeamSide.EW] if score is not None else None,
        marked_ns=(
            score.marked[TeamSide.NS].made + score.marked[TeamSide.NS].announced
            if score is not None
            else None
        ),
        marked_ew=(
            score.marked[TeamSide.EW].made + score.marked[TeamSide.EW].announced
            if score is not None
            else None
        ),
        total_ns=totals[TeamSide.NS] if totals is not None else None,
        total_ew=totals[TeamSide.EW] if totals is not None else None,
        verdict=str(verdict.verdict) if verdict is not None else None,
        replayed=int(verdict.replayed) if verdict is not None else None,
        unchecked=json.dumps(list(verdict.unchecked)) if verdict is not None else None,
    )


def _game_rows(
    record: GameRecord,
    path: str,
    verdict: GameVerdict | None,
    status: str,
) -> _GameRows:
    """Every row one game contributes.

    Args:
        record: The game.
        path: Its file, relative to the root, with forward slashes.
        verdict: Its verdict when one was readable — fresh or stale —
            else ``None``.
        status: The verdict status: ``fresh``, ``stale``, ``missing`` or
            ``unreadable``.

    Returns:
        The game, seat, round and mismatch rows.
    """

    game_id = record.header.game_id
    winner, basis = _effective_winner(record)
    ended = record.ended
    joined = record.observed_from
    totals = ended.totals if ended is not None else None

    # Joined by the record's own round number, never by position: round
    # numbers are the source's deal count, may start above one and skip.
    by_number = (
        {round_.number: round_ for round_ in verdict.rounds} if verdict else {}
    )

    game = _GameRow(
        game_id=game_id,
        path=path,
        source=str(record.header.source),
        generator=record.header.generator,
        created_at=record.header.created_at,
        ended_at=ended.ts if ended is not None else None,
        preset=record.preset,
        joined_round=joined.round if joined is not None else None,
        joined_phase=str(joined.phase) if joined is not None else None,
        round_count=len(record.rounds),
        complete_round_count=sum(round_.complete for round_ in record.rounds),
        complete=int(record.complete),
        truncated=int(record.truncated),
        end_reason=str(ended.reason) if ended is not None else None,
        total_ns=totals[TeamSide.NS] if totals is not None else None,
        total_ew=totals[TeamSide.EW] if totals is not None else None,
        winner=side_token(winner) if winner is not None else None,
        winner_basis=basis,
        verdict=str(verdict.verdict) if verdict is not None else None,
        verdict_status=status,
        verdict_notes=json.dumps(list(verdict.notes)) if verdict is not None else None,
    )

    seats = tuple(
        _SeatRow(
            game_id=game_id,
            position=position_token(position),
            side=side_token(position.team_side),
            player_id=seat.id,
            name=seat.name,
            account=seat.account,
            kind=str(seat.kind),
            level=seat.level,
            result=(
                None
                if winner is None
                else "won" if position.team_side is winner else "lost"
            ),
        )
        for position, seat in record.seats.items()
    )

    rounds = tuple(
        _round_row(game_id, round_, by_number.get(round_.number))
        for round_ in record.rounds
    )

    # A verdict's mismatches for rounds the record no longer holds are
    # dropped: they would reference a round row that does not exist. Such
    # a verdict is already ``stale``.
    indexed = {round_.number for round_ in record.rounds}
    mismatches = tuple(
        _MismatchRow(
            game_id=game_id,
            round=number,
            n=n,
            kind=str(mismatch.kind),
            detail=mismatch.detail,
            position=_mismatch_position(mismatch.position),
            trick=mismatch.trick,
            seq=mismatch.seq,
            expected=mismatch.expected,
            observed=mismatch.observed,
        )
        for number, round_verdict in by_number.items()
        if number in indexed
        for n, mismatch in enumerate(round_verdict.mismatches, start=1)
    )
    return _GameRows(game=game, seats=seats, rounds=rounds, mismatches=mismatches)


def _pick_canonical(game_id: str, paths: Sequence[Path]) -> Path:
    """Which of several files claiming one game id is indexed.

    The file named after the id wins — it is the one the producers'
    ``game_path`` would have written. Failing that, the first in sorted
    order, so two builds over the same corpus always agree.

    Args:
        game_id: The id every file claims.
        paths: The files claiming it.

    Returns:
        The file to index.
    """

    for path in sorted(paths):
        if path.stem == game_id:
            return path
    return sorted(paths)[0]


# ----------------------------------------------------------------------
# I/O — reading the corpus, writing the catalog
# ----------------------------------------------------------------------


def _relative(root: Path, path: Path) -> str:
    """``path`` relative to ``root``, with forward slashes on every OS."""

    return path.relative_to(root).as_posix()


def _reason(exc: BaseException) -> str:
    """One line naming an exception and what it said."""

    return f"{type(exc).__name__}: {exc}"


def _scan(root: Path) -> tuple[list[_GameRows], list[SkippedFile]]:
    """Read every record and verdict under ``root`` into rows.

    Args:
        root: The records root.

    Returns:
        Each indexed game's rows, in game id order, and every file that
        could not be indexed.
    """

    skipped: list[SkippedFile] = []
    claims: dict[str, list[tuple[Path, GameRecord]]] = {}
    games = games_dir(root)
    record_files = sorted(games.glob("*.jsonl")) if games.is_dir() else []
    for path in record_files:
        # Caught narrowly, around the load only: everything malformed in a
        # record surfaces as a ValueError subclass (non-UTF-8 bytes and an
        # impossible ruleset included), and an unreadable file as OSError.
        try:
            record = load_game(path)
        except (OSError, ValueError) as exc:
            skipped.append(SkippedFile(_relative(root, path), "record", _reason(exc)))
            continue
        claims.setdefault(record.header.game_id, []).append((path, record))

    verdict_dir = verdicts_dir(root)
    verdict_files = (
        sorted(verdict_dir.glob("*.json")) if verdict_dir.is_dir() else []
    )
    claimed: set[Path] = set()
    rows: list[_GameRows] = []
    for game_id in sorted(claims):
        entries = claims[game_id]
        keep = _pick_canonical(game_id, [path for path, _ in entries])
        for path, _ in entries:
            if path != keep:
                skipped.append(
                    SkippedFile(
                        _relative(root, path),
                        "record",
                        f"duplicate game id {game_id}; indexed "
                        f"{_relative(root, keep)}",
                    )
                )
        record = next(rec for path, rec in entries if path == keep)
        verdict, status = _read_game_verdict(root, game_id, keep, record, skipped)
        if status != "missing":
            # Read — usable or not, it is this game's file and no orphan.
            claimed.add(verdict_path(root, game_id))
        rows.append(_game_rows(record, _relative(root, keep), verdict, status))

    for path in verdict_files:
        if path not in claimed:
            skipped.append(
                SkippedFile(
                    _relative(root, path), "verdict", "no indexed record has this game id"
                )
            )
    return rows, skipped


def _read_game_verdict(
    root: Path,
    game_id: str,
    record_path: Path,
    record: GameRecord,
    skipped: list[SkippedFile],
) -> tuple[GameVerdict | None, str]:
    """Find, read and date one game's verdict.

    Args:
        root: The records root.
        game_id: The game.
        record_path: The indexed record file, for its modification time.
        record: The indexed record.
        skipped: Appended to when the verdict cannot be used.

    Returns:
        The verdict, or ``None`` when there is none to use, and the
        verdict status.
    """

    try:
        path = verdict_path(root, game_id)
    except ValueError:
        # A record whose id is not one path segment cannot have a verdict
        # file, so none is looked for — and it is said, not silent.
        skipped.append(
            SkippedFile(
                _relative(root, record_path),
                "verdict",
                f"game id {game_id!r} is not one path segment; no verdict looked up",
            )
        )
        return None, "missing"
    if not path.is_file():
        return None, "missing"
    try:
        verdict = read_verdict(path)
        verdict_mtime_ns = path.stat().st_mtime_ns
    except (OSError, ValueError) as exc:
        skipped.append(SkippedFile(_relative(root, path), "verdict", _reason(exc)))
        return None, "unreadable"
    if verdict.game_id != game_id:
        skipped.append(
            SkippedFile(
                _relative(root, path),
                "verdict",
                f"names game {verdict.game_id!r}, not {game_id!r}",
            )
        )
        return None, "unreadable"
    status = _verdict_status(
        record,
        verdict,
        record_mtime_ns=record_path.stat().st_mtime_ns,
        verdict_mtime_ns=verdict_mtime_ns,
    )
    return verdict, status


def _tally(connection: sqlite3.Connection, sql: str) -> dict[str, int]:
    """A ``key, count`` query as a dict."""

    return {key: count for key, count in connection.execute(sql)}


def _summarise(
    connection: sqlite3.Connection, path: Path, built_at: str
) -> CatalogSummary:
    """Count a finished catalog.

    Args:
        connection: The catalog, fully written.
        path: Where it will live.
        built_at: When it was built.

    Returns:
        The summary.
    """

    def scalar(sql: str) -> int:
        return connection.execute(sql).fetchone()[0]

    return CatalogSummary(
        path=path,
        built_at=built_at,
        games_by_source=_tally(
            connection, "SELECT source, COUNT(*) FROM games GROUP BY source"
        ),
        round_count=scalar("SELECT COUNT(*) FROM rounds"),
        complete_round_count=scalar("SELECT COUNT(*) FROM rounds WHERE complete = 1"),
        clean_round_count=scalar("SELECT COUNT(*) FROM clean_rounds"),
        game_verdicts=_tally(
            connection,
            "SELECT verdict, COUNT(*) FROM games "
            "WHERE verdict IS NOT NULL GROUP BY verdict",
        ),
        verdict_statuses=_tally(
            connection,
            "SELECT verdict_status, COUNT(*) FROM games GROUP BY verdict_status",
        ),
        round_verdicts=_tally(
            connection,
            "SELECT verdict, COUNT(*) FROM rounds "
            "WHERE verdict IS NOT NULL GROUP BY verdict",
        ),
        player_count=scalar("SELECT COUNT(*) FROM players"),
        skipped=tuple(
            SkippedFile(*row)
            for row in connection.execute(
                "SELECT path, kind, reason FROM skipped ORDER BY kind, path"
            )
        ),
    )


def _write(
    connection: sqlite3.Connection,
    root: Path,
    built_at: str,
    games: list[_GameRows],
    skipped: list[SkippedFile],
) -> None:
    """Create the schema and fill it.

    Args:
        connection: An empty database.
        root: The records root, recorded in ``meta``.
        built_at: When the build ran.
        games: Every indexed game's rows.
        skipped: Every file that could not be indexed.
    """

    connection.executescript(_TABLES)
    connection.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [
            ("schema_version", CATALOG_SCHEMA_VERSION),
            ("built_at", built_at),
            ("generator", _generator()),
            ("root", root.resolve().as_posix()),
        ],
    )
    for table, row_type, rows in (
        ("games", _GameRow, [game.game for game in games]),
        ("seats", _SeatRow, [seat for game in games for seat in game.seats]),
        ("rounds", _RoundRow, [row for game in games for row in game.rounds]),
        (
            "mismatches",
            _MismatchRow,
            [row for game in games for row in game.mismatches],
        ),
    ):
        connection.executemany(
            _insert(table, row_type), [dataclasses.astuple(row) for row in rows]
        )
    connection.executemany(
        "INSERT INTO skipped (path, kind, reason) VALUES (?, ?, ?)",
        [(item.path, item.kind, item.reason) for item in skipped],
    )
    connection.commit()
    connection.executescript(_INDEXES_AND_VIEWS)
    connection.execute(f"PRAGMA user_version = {CATALOG_SCHEMA_VERSION}")
    connection.commit()


def build_catalog(root: Path | str, *, now: datetime | None = None) -> CatalogSummary:
    """Rebuild ``<root>/catalog.sqlite`` from every record and verdict.

    Args:
        root: The records root — the directory holding ``games/`` and
            ``verdicts/``. A root with no ``games/`` builds an empty
            catalog.
        now: The build instant to stamp; defaults to now, in UTC.

    Returns:
        What the new catalog holds.

    Raises:
        NotADirectoryError: If ``root`` is not a directory.
        PermissionError: If the old catalog cannot be replaced — on
            Windows, while another program holds it open. The old
            catalog is left as it was.
    """

    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"Not a records root: {root}")
    built_at = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    games, skipped = _scan(root)

    # The descriptor is closed before SQLite opens the file: Windows will
    # not let a second handle delete or replace a file this process still
    # holds open.
    descriptor, name = tempfile.mkstemp(
        dir=root, prefix=f"{CATALOG_FILE}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            # No journal and no sync: a build that fails is thrown away
            # whole, so crash safety inside the temporary file buys nothing.
            connection.execute("PRAGMA journal_mode = OFF")
            connection.execute("PRAGMA synchronous = OFF")
            _write(connection, root, built_at, games, skipped)
            summary = _summarise(connection, catalog_path(root), built_at)
        finally:
            connection.close()
        os.replace(temporary, catalog_path(root))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return summary


# ----------------------------------------------------------------------
# Reading a catalog
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlayerGame:
    """One seat a player held in one game.

    Attributes:
        created_at: When the record was opened, in UTC.
        game_id: The game.
        player_id: The seat's player id, or ``None`` for an engine seat.
        name: The display name the seat carried in that game.
        position: The seat, as a record token.
        side: ``NS`` or ``EW``.
        partner: The partner's display name.
        result: ``won``, ``lost``, or ``None`` when the winner is unknown.
        end_reason: Why the record stops, or ``None`` when it does not.
        total_ns: The final North–South score, when known.
        total_ew: The final East–West score, when known.
        verdict: The game's verdict, when a readable one exists.
        verdict_status: ``fresh``, ``stale``, ``missing`` or
            ``unreadable``.
    """

    created_at: str
    game_id: str
    player_id: str | None
    name: str
    position: str
    side: str
    partner: str | None
    result: str | None
    end_reason: str | None
    total_ns: int | None
    total_ew: int | None
    verdict: str | None
    verdict_status: str


@dataclass(frozen=True, slots=True)
class PlayerReport:
    """Every game one player sat in, as a catalog knows them.

    Attributes:
        player: What was looked up — a player id or a display name.
        built_at: When the catalog was built; games recorded since are
            not in it.
        games: One entry per seat held, oldest game first.
    """

    player: str
    built_at: str
    games: tuple[PlayerGame, ...]


_PLAYER_GAMES = """
SELECT g.created_at, g.game_id, s.player_id, s.name, s.position, s.side,
       p.name, s.result, g.end_reason, g.total_ns, g.total_ew,
       g.verdict, g.verdict_status
FROM seats s
JOIN games g USING (game_id)
LEFT JOIN seats p
       ON p.game_id = s.game_id AND p.side = s.side AND p.position != s.position
WHERE s.player_id = ? OR s.name = ?
ORDER BY g.created_at, g.game_id, s.position
"""


def player_games(root: Path | str, player: str) -> PlayerReport:
    """List one player's games from an existing catalog, never rebuilding it.

    The player is matched on its id or on any display name it carried:
    ids are stable, but a name is what a person remembers.

    Args:
        root: The records root holding the catalog.
        player: A player id or a display name.

    Returns:
        The player's games; empty when the catalog knows no such player.

    Raises:
        FileNotFoundError: If the root has no catalog. None is created.
        CatalogError: If the file is not a catalog, or one of another
            schema version.
    """

    path = catalog_path(root)
    if not path.is_file():
        raise FileNotFoundError(f"No catalog at {path}")
    # Read-only through a URI: a plain ``connect`` would create an empty
    # database if the file vanished in between, and a lookup must never
    # write.
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        try:
            schema = connection.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise CatalogError(f"{path} is not a catalog: {exc}") from exc
        if schema != CATALOG_SCHEMA_VERSION:
            raise CatalogError(
                f"{path} has schema version {schema}, this build reads "
                f"{CATALOG_SCHEMA_VERSION}; rebuild it"
            )
        (built_at,) = connection.execute(
            "SELECT value FROM meta WHERE key = 'built_at'"
        ).fetchone()
        rows: list[Any] = connection.execute(_PLAYER_GAMES, (player, player)).fetchall()
    finally:
        connection.close()
    return PlayerReport(
        player=player,
        built_at=built_at,
        games=tuple(PlayerGame(*row) for row in rows),
    )
