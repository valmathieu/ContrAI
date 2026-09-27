"""Reading the score sheet: which row is which round, and what each one carried.

Every snapshot hands over the site's score sheet as it stands — one row per
round scored so far, the running totals after the newest — and nothing else
about the score. Two facts about that sheet decide how it has to be read.

**A row is a position, not a round number.** A passed-out round writes no row
yet spends a round number, so below it every row sits one number lower than
a one-row-per-round reading would put it. Nor does a snapshot ever name a
passed-out round as its newest: after an all-pass round 9 the next read still
says 8. So the sheet is read by position. Each snapshot is an **anchor** —
"after R rows the totals were T" — and every game adds one of its own, 0 / 0
after no row at all. A round's row is found by walking back from a
snapshot's newest scored round, stepping over passed-out rounds, and the
walk stops at the first round number the visit saw nothing of: the rows
below belong to rounds played before the join, or across a gap in the
watching, and no reading of this visit can say which.

**A carry is a step between two anchors.** A held dispute's pot (§7.5) is
paid into the next contract winner's running total and written in no row.
So what a round carried is what the totals moved by across its row, less
the row's made, announced and credited belote points. Keying that on the row
count rather than on the round number is what makes 0 / 0 usable before
round 1 of a game seen whole, and what keeps a join right after passed-out
rounds on the row it really joined at.

Both readings refuse rather than guess. Reads that contradict one another —
disagreeing rows, a round on two rows, a played round left without one —
place no row at all for the visit. Two reads stating different totals after
the same number of rows make that point a **barrier**: no carry is read
through it and no standing given at it. A carry that comes out negative
cannot be a payout, so it is unknown. Each refusal leaves one note.

Measured over the V5 corpus and the fleet's first ramp runs (2026-09-27): no
contradiction, no barrier, no negative step.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from contrai_core import TeamSide

from .snapshot import ScoreRow, Snapshot

#: The totals every game starts from, before any row is written.
_NOUGHT: Mapping[TeamSide, int] = dict.fromkeys(TeamSide, 0)


@dataclass(frozen=True, slots=True)
class SheetLine:
    """One played round's row, where the sheet puts it."""

    index: int
    """The row's position on the sheet, the game's first row being 0."""

    row: ScoreRow
    totals: Mapping[TeamSide, int] | None
    """The running totals after the round, when a read stated them."""

    carried_over: Mapping[TeamSide, int] | None
    """What each side was paid beyond its own marks, or ``None`` if unknown."""


@dataclass(frozen=True, slots=True)
class ScoreSheet:
    """What a visit's snapshots say about the score, round by round."""

    lines: Mapping[int, SheetLine]
    """Round number to its row, for every played round a row was placed on."""

    standings: Mapping[int, Mapping[TeamSide, int]]
    """Passed-out round number to the totals standing through it, when read."""

    notes: tuple[str, ...]
    """Every reading the sheet refused, one note each."""


class _Contradiction(Exception):
    """Two reads of one sheet that cannot both be true."""


def read_sheet(
    snapshots: Sequence[Snapshot],
    *,
    seen: frozenset[int],
    passed: frozenset[int],
) -> ScoreSheet:
    """Place every row on its round and read each round's carry.

    Args:
        snapshots: Every snapshot of the visit, in any order.
        seen: The round numbers the visit has events for.
        passed: The passed-out round numbers, all of them among ``seen``.

    Returns:
        The rows by round, the standings of passed-out rounds and the notes.
    """

    try:
        rows = _rows(snapshots)
        placed = _walk(snapshots, seen, passed)
    except _Contradiction as error:
        return ScoreSheet(
            lines={}, standings={},
            notes=(f"the score sheet contradicts itself — {error} — so no "
                   "score row was placed",),
        )
    notes: list[str] = []
    anchors, barriers = _anchors(snapshots, notes)
    carries = _carries(rows, anchors, barriers, placed, notes)
    lines = {
        number: SheetLine(
            index=index,
            row=rows[index],
            totals=anchors.get(index + 1),
            carried_over=carries.get(number),
        )
        for number, index in sorted(placed.items())
    }
    return ScoreSheet(
        lines=lines,
        standings=_standings(passed, placed, anchors),
        notes=tuple(notes),
    )


def _rows(snapshots: Sequence[Snapshot]) -> tuple[ScoreRow, ...]:
    """The sheet's rows: the longest list read, of which every other is a prefix.

    Raises:
        _Contradiction: If two reads disagree about a row both carry.
    """

    longest = max((snapshot.score_rows for snapshot in snapshots), key=len,
                  default=())
    for snapshot in snapshots:
        if longest[: len(snapshot.score_rows)] != snapshot.score_rows:
            raise _Contradiction("two reads disagree about a row both carry")
    return longest


def _walk(
    snapshots: Sequence[Snapshot], seen: frozenset[int], passed: frozenset[int]
) -> dict[int, int]:
    """Which row each played round is on.

    Each snapshot's rows are walked back from its newest scored round, one
    row per played round, stepping over passed-out rounds. The walk stops
    at the first round number the visit saw nothing of, since the rows
    below it cannot be told apart from rounds before the join.

    Args:
        snapshots: Every snapshot of the visit.
        seen: The round numbers the visit has events for.
        passed: The passed-out round numbers.

    Returns:
        Round number to its row's index.

    Raises:
        _Contradiction: If a round falls on two rows, a played round has no
            row left, or the rows do not rise with the rounds.
    """

    placed: dict[int, int] = {}
    for snapshot in snapshots:
        if snapshot.round_index is None:
            continue
        number = snapshot.round_index
        index = len(snapshot.score_rows) - 1
        while True:
            while number in passed:
                number -= 1
            if number < 1 or number not in seen:
                break
            if index < 0:
                raise _Contradiction(
                    f"round {number} was played but no row is left for it"
                )
            if placed.setdefault(number, index) != index:
                raise _Contradiction(f"round {number} falls on two rows")
            number -= 1
            index -= 1
    indices = [index for _, index in sorted(placed.items())]
    if any(lower >= upper for lower, upper in zip(indices, indices[1:])):
        raise _Contradiction("the rows do not follow the rounds' order")
    return placed


def _anchors(
    snapshots: Sequence[Snapshot], notes: list[str]
) -> tuple[dict[int, Mapping[TeamSide, int]], frozenset[int]]:
    """The totals known after each number of rows, and the counts in dispute.

    Every game starts at 0 / 0 before its first row, so that anchor is there
    whatever was read; a read with no row claiming anything else disputes it
    like any other.

    Args:
        snapshots: Every snapshot of the visit.
        notes: Where a barrier says why.

    Returns:
        Row count to the totals after it, and the barrier counts, which two
        reads gave different totals and which therefore anchor nothing.
    """

    anchors: dict[int, Mapping[TeamSide, int]] = {0: _NOUGHT}
    barriers: set[int] = set()
    for snapshot in snapshots:
        count = len(snapshot.score_rows)
        if snapshot.totals is None or count in barriers:
            continue
        known = anchors.setdefault(count, dict(snapshot.totals))
        if known != snapshot.totals:
            del anchors[count]
            barriers.add(count)
            notes.append(
                f"two reads state different totals after {count} score "
                "row(s), so no carry is read across that point"
            )
    return anchors, frozenset(barriers)


def _marks(row: ScoreRow, side: TeamSide) -> int:
    """Everything a row adds to one side's running total."""

    return sum(row.marked[side]) + row.marked_belote[side]


def _carries(
    rows: Sequence[ScoreRow],
    anchors: Mapping[int, Mapping[TeamSide, int]],
    barriers: frozenset[int],
    placed: Mapping[int, int],
    notes: list[str],
) -> dict[int, Mapping[TeamSide, int]]:
    """Each placed round's carry, where the anchors around its row decide it.

    A row is bracketed by the nearest anchors below and above it. With the
    row alone between them, the carry is the residual: what the totals moved
    by, less the row's marks. A negative residual cannot be a payout, so the
    carry is unknown and noted. A bracket holding several rows, or ending at
    a barrier, is left unknown.

    Args:
        rows: The sheet's rows.
        anchors: Row count to the totals after it.
        barriers: The row counts two reads disagreed about.
        placed: Round number to its row's index.
        notes: Where an impossible residual says so.

    Returns:
        Round number to its carry, for every round whose carry is known.
    """

    round_of = {index: number for number, index in placed.items()}
    points = sorted({*anchors, *barriers})
    carries: dict[int, Mapping[TeamSide, int]] = {}
    for lower, upper in zip(points, points[1:]):
        if lower in barriers or upper in barriers or upper - lower != 1:
            continue
        rounds = [round_of[index] for index in range(lower, upper)
                  if index in round_of]
        residual = {
            side: anchors[upper][side] - anchors[lower][side]
            - sum(_marks(rows[index], side) for index in range(lower, upper))
            for side in TeamSide
        }
        if any(points_ < 0 for points_ in residual.values()):
            if rounds:
                notes.append(
                    f"round {rounds[0]}: the running totals moved by less than "
                    "the marks, so the carry is unknown"
                )
            continue
        for number in rounds:
            carries[number] = residual
    return carries


def _standings(
    passed: frozenset[int],
    placed: Mapping[int, int],
    anchors: Mapping[int, Mapping[TeamSide, int]],
) -> dict[int, Mapping[TeamSide, int]]:
    """The totals standing through each passed-out round, where read.

    Passing out writes no row and moves no total, so a passed-out round
    stands at the anchor for the rows written before it. That count comes
    from the nearest played round below it — its row plus one, or none at
    all when every round below was passed out too — else from the nearest
    played round above, whose row is the next one written.

    Args:
        passed: The passed-out round numbers.
        placed: Round number to its row's index.
        anchors: Row count to the totals after it.

    Returns:
        Passed-out round number to its standing, where the anchor is known.
    """

    standings: dict[int, Mapping[TeamSide, int]] = {}
    for number in sorted(passed):
        count = _count_before(number, passed, placed)
        if count is not None and count in anchors:
            standings[number] = anchors[count]
    return standings


def _count_before(
    number: int, passed: frozenset[int], placed: Mapping[int, int]
) -> int | None:
    """How many rows the sheet held when a passed-out round was dealt."""

    below = number - 1
    while below in passed:
        below -= 1
    if below < 1:
        return 0
    if below in placed:
        return placed[below] + 1
    above = number + 1
    while above in passed:
        above += 1
    return placed.get(above)
