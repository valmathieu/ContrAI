"""A corpus: every game ever scraped, one record each, rebuilt from raw logs.

One game can reach the corpus more than once. The box and a fleet worker
may watch the same table, and a single session may leave a table and be
seated back at it — each raw log that saw the game yields its own record,
and the copies differ by how much of the game each one saw. The corpus
keeps exactly one, and this module decides which.

**The ranking is fixed, and it is lexicographic.** A copy that holds more
*scored* rounds wins outright, because a scored round is what verification
and training both consume; only on a tie does the plain round count
matter, then whether the copy closes on a ``game_ended`` carrying totals,
then the earlier join (a lower first round saw more of the opening). What
is left is a true tie between copies worth the same, broken on the source
label and then the origin path so that two builds over the same raw logs
always keep the same file.

**Every loser says why it lost.** The reason names the first tier on
which it fell behind the winner, with both values, so a build report can
be audited without re-running the ranking.

**Raw logs are the only thing kept by hand.** A corpus root uses the
records-root layout (``games/``, ``verdicts/``, ``catalog.sqlite``) plus
``raw/<source>/`` for the wire logs every game was parsed from. The logs
are imported, never edited and never pruned; everything else is rebuilt
from them, which is what lets a parser fix reach every game already
watched. :func:`import_raw` is the one door into ``raw/``, and
:func:`write_games` swaps a whole new ``games/`` in at once.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

from .catalog import catalog_path
from .events import GameEvent
from .exceptions import CorpusError
from .projection import GameRecord, project
from .store import RecordWriter, game_path, games_dir
from .verdict import verdicts_dir

#: The corpus's raw-log directory, one subdirectory per source label.
RAW_DIR: Final[str] = "raw"

#: The last build's report, beside ``games/``.
BUILD_FILE: Final[str] = "build.json"

#: What a raw log is, inside a source's directory.
_RAW_GLOB: Final[str] = "*.jsonl"

#: A source label: one lowercase path segment, so it can name a directory
#: on every OS and never climb out of ``raw/``.
_LABEL: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9_-]*")

#: How much of a file is compared at a time.
_CHUNK: Final[int] = 1 << 20


@dataclass(frozen=True, slots=True)
class RecordCopy:
    """One record of a game, as one raw log produced it.

    Attributes:
        source: The label of the machine or run the raw log came from,
            e.g. ``box`` or ``laptop``.
        origin: Where the copy came from, for the report — typically the
            raw log's path relative to the corpus root.
        events: The record's events, header first.
        game: The same events folded into rounds.
    """

    source: str
    origin: str
    events: tuple[GameEvent, ...]
    game: GameRecord

    @classmethod
    def of(cls, source: str, origin: str, events: Sequence[GameEvent]) -> RecordCopy:
        """Build a copy from its events, folding them once.

        Args:
            source: The source label.
            origin: Where the copy came from.
            events: The record's events, header first.

        Returns:
            The copy.

        Raises:
            RecordFormatError: If the events cannot be folded into rounds.
        """

        events = tuple(events)
        return cls(source=source, origin=origin, events=events, game=project(events))

    @property
    def game_id(self) -> str:
        """The game this copy is a record of."""

        return self.game.header.game_id

    @property
    def scored_rounds(self) -> int:
        """How many rounds carry a score line."""

        return sum(1 for round_ in self.game.rounds if round_.score is not None)

    @property
    def rounds(self) -> int:
        """How many rounds were dealt."""

        return len(self.game.rounds)

    @property
    def ended_with_totals(self) -> bool:
        """Whether the copy closes on a ``game_ended`` that states totals."""

        ended = self.game.ended
        return ended is not None and ended.totals is not None

    @property
    def first_round(self) -> int | None:
        """The first round the copy holds, or ``None`` when it holds none."""

        return self.game.rounds[0].number if self.game.rounds else None


@dataclass(frozen=True, slots=True)
class Rejection:
    """A copy that lost, and the first count it lost on.

    Attributes:
        copy: The losing copy.
        reason: One line naming the tier and both values, e.g.
            ``fewer scored rounds (3 against 5)``.
    """

    copy: RecordCopy
    reason: str


@dataclass(frozen=True, slots=True)
class CopyChoice:
    """The copy a corpus keeps for one game, and the ones it does not.

    Attributes:
        chosen: The copy kept.
        rejected: Every other candidate, in ranking order.
    """

    chosen: RecordCopy
    rejected: tuple[Rejection, ...]

    @property
    def game_id(self) -> str:
        """The game chosen for."""

        return self.chosen.game_id

    def as_report(self) -> dict[str, Any]:
        """The choice as a JSON-ready mapping, for a build report.

        Returns:
            The game id, the chosen copy's counts, and each rejected copy's
            counts with its reason.
        """

        return {
            "game_id": self.game_id,
            "chosen": _copy_report(self.chosen),
            "rejected": [
                {**_copy_report(rejection.copy), "reason": rejection.reason}
                for rejection in self.rejected
            ],
        }


def _copy_report(copy: RecordCopy) -> dict[str, Any]:
    """One copy's origin and the counts it was ranked on."""

    return {
        "source": copy.source,
        "origin": copy.origin,
        "scored_rounds": copy.scored_rounds,
        "rounds": copy.rounds,
        "ended_with_totals": copy.ended_with_totals,
        "first_round": copy.first_round,
    }


def _rank(copy: RecordCopy) -> tuple[int, int, bool, float, str, str]:
    """A copy's sort key: smaller is better.

    Each "more is better" count is negated so a single ascending sort
    ranks every tier the right way round. A copy with no round at all
    joins "at infinity", behind any copy that holds one.
    """

    first = copy.first_round
    return (
        -copy.scored_rounds,
        -copy.rounds,
        not copy.ended_with_totals,
        math.inf if first is None else first,
        copy.source,
        copy.origin,
    )


def _reason(loser: RecordCopy, winner: RecordCopy) -> str:
    """The first tier on which ``loser`` fell behind ``winner``.

    Args:
        loser: A copy ranked after ``winner``.
        winner: The chosen copy.

    Returns:
        One line naming the tier and, where there is one, both values.
    """

    if loser.scored_rounds != winner.scored_rounds:
        return (
            f"fewer scored rounds ({loser.scored_rounds} against "
            f"{winner.scored_rounds})"
        )
    if loser.rounds != winner.rounds:
        return f"fewer rounds ({loser.rounds} against {winner.rounds})"
    if loser.ended_with_totals != winner.ended_with_totals:
        return "no game_ended with totals"
    if loser.first_round != winner.first_round:
        return (
            f"joined later (round {loser.first_round} against round "
            f"{winner.first_round})"
        )
    if loser.source != winner.source:
        return f"a tie, kept the first source ({winner.source} before {loser.source})"
    return "a tie, kept the first origin in sorted order"


def choose_copy(candidates: Sequence[RecordCopy]) -> CopyChoice:
    """Keep the most complete of several records of one game.

    Args:
        candidates: At least one copy, all of the same game. Their order
            does not matter: the ranking is total, so any order yields
            the same choice.

    Returns:
        The chosen copy and, for every other, why it lost.

    Raises:
        ValueError: If ``candidates`` is empty or mixes games.
    """

    if not candidates:
        raise ValueError("choose_copy needs at least one candidate")
    games = {copy.game_id for copy in candidates}
    if len(games) > 1:
        raise ValueError(f"The candidates are records of several games: {sorted(games)}")
    ranked = sorted(candidates, key=_rank)
    winner = ranked[0]
    return CopyChoice(
        chosen=winner,
        rejected=tuple(Rejection(copy, _reason(copy, winner)) for copy in ranked[1:]),
    )


# ----------------------------------------------------------------------
# Raw logs: the one thing a corpus keeps by hand
# ----------------------------------------------------------------------


class ImportStatus(Enum):
    """What importing one raw log did.

    A raw log is only ever appended to, so a log fetched while its session
    was still running is a *prefix* of the one fetched later. That is not a
    conflict: the longer file is the same log, further on.
    """

    COPIED = "copied"
    """The name was new to the source, so the log was copied in."""

    GROWN = "grown"
    """The kept log was a prefix of this one, which replaced it."""

    PRESENT = "present"
    """The source already held this exact log."""

    STALE = "stale"
    """This log is a prefix of the kept one, which stays."""


@dataclass(frozen=True, slots=True)
class RawImport:
    """What :func:`import_raw` did, one entry per log handed in.

    Attributes:
        entries: ``(source, file name, status)`` in the order the logs were
            handed in.
    """

    entries: tuple[tuple[str, str, ImportStatus], ...]

    def counts(self) -> dict[str, dict[str, int]]:
        """Per source, how many logs ended in each status.

        Returns:
            Source label to status value to count, every status present.
        """

        counts: dict[str, dict[str, int]] = {}
        for source, _, status in self.entries:
            tally = counts.setdefault(source, {s.value: 0 for s in ImportStatus})
            tally[status.value] += 1
        return counts


def _check_label(source: str) -> None:
    """Refuse a source label that is not one plain lowercase segment.

    Raises:
        CorpusError: If the label could not name a directory safely.
    """

    if not _LABEL.fullmatch(source):
        raise CorpusError(
            f"A source label is lowercase letters, digits, '-' and '_', got {source!r}"
        )


def _relation(kept: Path, incoming: Path) -> ImportStatus | None:
    """How an incoming log relates to the one already kept under its name.

    Args:
        kept: The log the corpus holds.
        incoming: The log being imported.

    Returns:
        ``PRESENT`` when both are identical, ``GROWN`` when the kept one is
        a prefix of the incoming one, ``STALE`` the other way round, and
        ``None`` when they differ within their common length.
    """

    kept_size, incoming_size = kept.stat().st_size, incoming.stat().st_size
    remaining = min(kept_size, incoming_size)
    with kept.open("rb") as left, incoming.open("rb") as right:
        while remaining:
            size = min(_CHUNK, remaining)
            if left.read(size) != right.read(size):
                return None
            remaining -= size
    if kept_size == incoming_size:
        return ImportStatus.PRESENT
    return ImportStatus.GROWN if incoming_size > kept_size else ImportStatus.STALE


def _copy_in(source: Path, destination: Path) -> None:
    """Copy a file into place so that a crash never leaves half of it.

    The bytes go to a temporary file in the destination's directory first
    and are renamed over the destination, which is atomic on one volume:
    a half-copied log would otherwise look like a different log with the
    same name, and the next import would refuse it.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=f"{destination.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def import_raw(root: Path | str, sources: Mapping[str, Sequence[Path]]) -> RawImport:
    """Copy raw logs into ``<root>/raw/<source>/``, never over different data.

    Every log is judged before any is copied, so a refusal leaves the
    corpus exactly as it was. A log is keyed by its source and file name;
    within one call, two logs handed in under the same key are compared
    with each other just as with the one on disk.

    Args:
        root: The corpus root. Created if missing.
        sources: Source label to the log files to import from it.

    Returns:
        What happened to each log.

    Raises:
        CorpusError: If a label is not a plain segment, or a log's name is
            taken, in its source, by a log it neither equals nor extends.
            Every conflict is named, not only the first.
    """

    root = Path(root)
    for source in sources:
        _check_label(source)
    # The content each key will end up holding, starting from the disk.
    kept: dict[tuple[str, str], Path] = {}
    entries: list[tuple[str, str, ImportStatus]] = []
    conflicts: list[str] = []
    for source, logs in sources.items():
        for log in logs:
            key = (source, log.name)
            current = kept.get(key)
            if current is None:
                on_disk = root / RAW_DIR / source / log.name
                current = on_disk if on_disk.is_file() else None
            status = ImportStatus.COPIED if current is None else _relation(current, log)
            if status is None:
                conflicts.append(f"{source}/{log.name} ({log})")
                continue
            if status in (ImportStatus.COPIED, ImportStatus.GROWN):
                kept[key] = log
            entries.append((source, log.name, status))
    if conflicts:
        raise CorpusError(
            "These logs differ from the ones already kept under their names: "
            + "; ".join(conflicts)
        )
    for (source, name), log in kept.items():
        _copy_in(log, root / RAW_DIR / source / name)
    return RawImport(tuple(entries))


def raw_logs(root: Path | str) -> tuple[tuple[str, Path], ...]:
    """Every raw log a corpus holds, with its source label.

    Args:
        root: The corpus root.

    Returns:
        ``(source, path)`` pairs, by source label and then file name, so a
        build always reads them in the same order.
    """

    raw = Path(root) / RAW_DIR
    if not raw.is_dir():
        return ()
    return tuple(
        (directory.name, log)
        for directory in sorted(raw.iterdir())
        if directory.is_dir()
        for log in sorted(directory.glob(_RAW_GLOB))
    )


# ----------------------------------------------------------------------
# Games: rebuilt whole, swapped in at once
# ----------------------------------------------------------------------


def write_games(root: Path | str, copies: Iterable[RecordCopy]) -> int:
    """Replace ``<root>/games/`` with exactly these records.

    The records are written into a temporary directory beside ``games/``
    and swapped in only once every one of them is on disk, so a build that
    fails leaves the previous games untouched. A new ``games/`` makes the
    old verdicts and catalog describe files that no longer exist, so both
    are removed before the swap — the catalog first, since on Windows it
    is the file another program is most likely to hold open, and failing
    there changes nothing.

    Args:
        root: The corpus root. Created if missing.
        copies: One copy per game, typically each :class:`CopyChoice`'s
            ``chosen``.

    Returns:
        How many records were written.

    Raises:
        ValueError: If two copies are records of the same game — the
            second would be appended to the first.
        PermissionError: If the catalog or ``games/`` is held open by
            another program. The previous games are left as they were.
    """

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=root, prefix="games.", suffix=".tmp"))
    written: set[str] = set()
    retired: Path | None = None
    try:
        for copy in copies:
            if copy.game_id in written:
                raise ValueError(f"Two copies of {copy.game_id} were handed in")
            written.add(copy.game_id)
            # ``game_path`` validates the id as one path segment.
            name = game_path(root, copy.game_id).name
            with RecordWriter(staging / name) as writer:
                for event in copy.events:
                    writer.write(event)
        catalog_path(root).unlink(missing_ok=True)
        if verdicts_dir(root).exists():
            shutil.rmtree(verdicts_dir(root))
        games = games_dir(root)
        if games.exists():
            retired = root / f"{staging.name}.old"
            games.rename(retired)
        staging.rename(games)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if retired is not None:
        shutil.rmtree(retired)
    return len(written)
