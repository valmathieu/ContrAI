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

**A backup is one zip with a manifest.** :func:`backup_corpus` packs the
raw logs, the games and the build report beside a ``MANIFEST.json`` of
per-file SHA-256 and size, and :func:`check_archive` re-hashes an archive
against it, so a copy on another drive can be proven whole on its own.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
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

#: How much of a file is compared or hashed at a time.
_CHUNK: Final[int] = 1 << 20

#: The manifest's name inside a backup archive.
MANIFEST_FILE: Final[str] = "MANIFEST.json"

#: The manifest's format, checked before anything else is read.
MANIFEST_FORMAT: Final[str] = "contrai-corpus-backup/1"


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


# ----------------------------------------------------------------------
# Backups: the raw logs and the games, with a manifest to check them by
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupSummary:
    """What :func:`backup_corpus` wrote.

    Attributes:
        path: The archive.
        files: How many corpus files it holds, the manifest not counted.
        size: Their total size in bytes, before compression.
        raw_logs: How many of them are raw logs.
        games: How many of them are game records.
    """

    path: Path
    files: int
    size: int
    raw_logs: int
    games: int


@dataclass(frozen=True, slots=True)
class ArchiveCheck:
    """What :func:`check_archive` found.

    Attributes:
        files: How many files the manifest lists.
        problems: One line per file that is missing, altered, unreadable
            or not listed. Empty when the archive is whole.
    """

    files: int
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Whether every listed file is present and unaltered, and no other is."""

        return not self.problems


def file_sha256(path: Path | str) -> str:
    """A file's SHA-256, read in chunks so a large log never sits in memory.

    Args:
        path: The file.

    Returns:
        The hex digest.
    """

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _generator() -> str:
    """This build's name and version, stamped into a backup's manifest."""

    try:
        return f"contrai-data {version('contrai-data')}"
    except PackageNotFoundError:  # pragma: no cover - installed in the workspace
        return "contrai-data"


def _backed_up(root: Path) -> list[str]:
    """The corpus files a backup holds, as sorted POSIX paths under ``root``.

    The raw logs because nothing else can rebuild them, the games because
    rebuilding them needs this exact parser, and the build report because
    it says how they were chosen. Verdicts and the catalog are left out:
    ``contrai verify`` and ``contrai catalog`` rebuild them from the games.
    """

    files = [
        path
        for directory in (root / RAW_DIR, games_dir(root))
        if directory.is_dir()
        for path in directory.rglob("*")
        if path.is_file()
    ]
    if (root / BUILD_FILE).is_file():
        files.append(root / BUILD_FILE)
    return sorted(path.relative_to(root).as_posix() for path in files)


def backup_corpus(
    root: Path | str, destination: Path | str, *, now: datetime | None = None
) -> BackupSummary:
    """Write one zip holding a corpus's raw logs, games and build report.

    The archive carries a ``MANIFEST.json`` naming every file with its
    SHA-256 and size, so :func:`check_archive` can prove a copy on another
    drive is still whole without the corpus it came from. It is written
    under a temporary name and renamed once complete, so an interrupted
    backup never leaves a file that looks like a finished one.

    Args:
        root: The corpus root.
        destination: The directory the archive goes in. Created if missing.
        now: The instant to stamp; defaults to now, in UTC.

    Returns:
        What was written.

    Raises:
        CorpusError: If the corpus holds nothing to back up, or an archive
            with this stamp already exists.
    """

    root, destination = Path(root), Path(destination)
    moment = now or datetime.now(UTC)
    names = _backed_up(root)
    if not names:
        raise CorpusError(f"{root} holds no raw log, game or build report to back up")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"contrai-corpus-{moment.strftime('%Y%m%dT%H%M%SZ')}.zip"
    if archive.exists():
        raise CorpusError(f"{archive} already exists")
    listed = {
        name: {"sha256": file_sha256(root / name), "size": (root / name).stat().st_size}
        for name in names
    }
    raw_prefix, games_prefix = f"{RAW_DIR}/", f"{games_dir(root).name}/"
    counts = {
        "raw_logs": sum(1 for name in names if name.startswith(raw_prefix)),
        "games": sum(1 for name in names if name.startswith(games_prefix)),
    }
    manifest = {
        "format": MANIFEST_FORMAT,
        "created_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": _generator(),
        "counts": counts,
        "files": listed,
    }
    temporary = archive.with_name(f"{archive.name}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for name in names:
                zipped.write(root / name, arcname=name)
            zipped.writestr(MANIFEST_FILE, json.dumps(manifest, indent=2) + "\n")
        os.replace(temporary, archive)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return BackupSummary(
        path=archive,
        files=len(names),
        size=sum(entry["size"] for entry in listed.values()),
        raw_logs=counts["raw_logs"],
        games=counts["games"],
    )


def _member_sha256(zipped: zipfile.ZipFile, name: str) -> str:
    """A member's SHA-256, streamed out of the archive."""

    digest = hashlib.sha256()
    with zipped.open(name) as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def check_archive(path: Path | str) -> ArchiveCheck:
    """Re-hash every file of a backup against its manifest.

    Args:
        path: An archive :func:`backup_corpus` wrote.

    Returns:
        How many files the manifest lists, and every problem found — all
        of them, not only the first.

    Raises:
        CorpusError: If the file is not a zip, or holds no manifest this
            build can read.
    """

    path = Path(path)
    try:
        zipped = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise CorpusError(f"{path} is not a zip archive") from error
    with zipped:
        try:
            manifest = json.loads(zipped.read(MANIFEST_FILE))
        except KeyError:
            raise CorpusError(f"{path} holds no {MANIFEST_FILE}") from None
        if manifest.get("format") != MANIFEST_FORMAT:
            raise CorpusError(
                f"{path}'s manifest is {manifest.get('format')!r}, not {MANIFEST_FORMAT}"
            )
        listed: dict[str, dict[str, Any]] = manifest["files"]
        present = set(zipped.namelist()) - {MANIFEST_FILE}
        problems: list[str] = []
        for name, expected in sorted(listed.items()):
            if name not in present:
                problems.append(f"missing: {name}")
                continue
            try:
                digest = _member_sha256(zipped, name)
            except zipfile.BadZipFile as error:
                # The zip's own CRC failed before the hash could be taken.
                problems.append(f"unreadable: {name} ({error})")
                continue
            if digest != expected["sha256"] or zipped.getinfo(name).file_size != expected["size"]:
                problems.append(f"altered: {name}")
        problems += [f"unlisted: {name}" for name in sorted(present - listed.keys())]
    return ArchiveCheck(files=len(listed), problems=tuple(problems))
