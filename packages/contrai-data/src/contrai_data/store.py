"""Records on disk: where they live, how they are appended, how they are read.

The only module in the package that touches the filesystem — everything
above it works on events in memory, which is what makes the rest of
``contrai-data`` trivially testable.

**One line, one flush.** :class:`RecordWriter` appends a single encoded
event and flushes it, so a producer that dies mid-game — a crashed
scraper, an interrupted autoplay — leaves a file that is complete up to
its last full line. ``fsync`` is deliberately not called: the failure
this guards against is a process ending, not a machine losing power, and
paying a disk sync per card would make the writer the slowest thing in
the loop.

**The reader is the other half of that bargain.** A final line that is
not JSON is what a crash mid-write looks like, so it is dropped and
reported through :attr:`ReadResult.truncated`. Anywhere else, a line that
does not parse is corruption and raises — swallowing it would silently
drop an event from the middle of a game, which is precisely the kind of
damage a record exists to rule out.

The layout is spec §3.3: ``<root>/games/<game_id>.jsonl`` for records and
``<root>/raw/`` for the scraper's verbatim wire frames, under
``$CONTRAI_HOME/records`` for the engine and the profile's output root
for the scraper.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from .codec import decode, encode
from .events import GameEvent
from .exceptions import RecordFormatError


@dataclass(frozen=True, slots=True)
class ReadResult:
    """Everything one record file yielded, and whether it was cut short.

    Attributes:
        events: The events, in file order.
        truncated: Whether the file's last line was a partial write that
            had to be dropped. A reader that cares about completeness
            must consult this — the events themselves look entirely
            healthy.
    """

    events: tuple[GameEvent, ...]
    truncated: bool


class RecordWriter:
    """Appends events to one record file, flushing each line.

    Usable as a context manager, which is how the engine and the scraper
    both drive it::

        with RecordWriter(path) as writer:
            writer.write(event)

    The handle is opened in append mode, so re-opening an existing record
    continues it rather than replacing it.
    """

    __slots__ = ("_path", "_handle")

    def __init__(self, path: Path | str) -> None:
        """Open ``path`` for appending, creating its directory if needed.

        Args:
            path: The record file. Missing parent directories are
                created.
        """

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ``newline="\n"`` pins LF on every platform: Python's default
        # translates "\n" to "\r\n" on Windows, and the Debian box that runs
        # the scraper would then read a stray CR inside the last token of
        # every line the dev machine wrote.
        self._handle: IO[str] | None = self._path.open(
            "a", encoding="utf-8", newline="\n"
        )

    @property
    def path(self) -> Path:
        """The file being written."""

        return self._path

    @property
    def closed(self) -> bool:
        """Whether the writer has been closed."""

        return self._handle is None

    def write(self, event: GameEvent) -> None:
        """Append one event and flush it.

        Args:
            event: The event to record.

        Raises:
            ValueError: If the writer is closed.
            RecordFormatError: If the event cannot be encoded.
        """

        if self._handle is None:
            raise ValueError(f"Writer is closed: {self._path}")
        self._handle.write(encode(event) + "\n")
        # Flushed per line, not per game: a crash must lose at most the
        # event being written.
        self._handle.flush()

    def close(self) -> None:
        """Close the file. Idempotent."""

        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> RecordWriter:
        """Enter the context manager.

        Returns:
            This writer.
        """

        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the file, however the block was left."""

        self.close()


def read_events(path: Path | str) -> ReadResult:
    """Read a whole record file.

    Tolerant of exactly one thing: an unparsable **final** line, which is
    what a crash mid-write leaves behind. It is dropped and reported. A
    line anywhere else that does not parse is corruption and raises, and
    so does a final line that *is* JSON but says something impossible —
    a crash cannot produce well-formed JSON with an invalid token, so
    that is a producer bug rather than a torn write.

    Args:
        path: The record file.

    Returns:
        The events and whether the file was cut short.

    Raises:
        FileNotFoundError: If the file does not exist.
        RecordFormatError: If any line but a torn final one fails to
            parse.
        UnsupportedFormatError: If the header names an unreadable format
            major.
    """

    text = Path(path).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ReadResult((), False)
    events: list[GameEvent] = [decode(line) for line in lines[:-1]]
    last = lines[-1]
    try:
        json.loads(last)
    except json.JSONDecodeError:
        # A crash mid-write leaves a partial line, and only ever the last
        # one. A line that *is* JSON but says something impossible is a
        # producer bug, so it falls through to ``decode`` and raises.
        return ReadResult(tuple(events), True)
    events.append(decode(last))
    return ReadResult(tuple(events), False)


def records_root() -> Path:
    """The directory records live under.

    ``$CONTRAI_HOME/records`` when the variable is set, else
    ``~/.contrai/records``. The environment variable is the escape hatch
    for a machine that keeps its corpus elsewhere, and it is also how the
    test suite stays out of the developer's real home directory.

    Returns:
        The records root. Not created.
    """

    home = os.environ.get("CONTRAI_HOME")
    base = Path(home) if home else Path.home() / ".contrai"
    return base / "records"


def games_dir(root: Path | str) -> Path:
    """The directory holding game records under ``root``.

    Args:
        root: A records root, e.g. from :func:`records_root`.

    Returns:
        ``<root>/games``. Not created.
    """

    return Path(root) / "games"


def game_path(root: Path | str, game_id: str) -> Path:
    """The file one game's record lives in.

    Args:
        root: A records root.
        game_id: The game's identity, which becomes the file name's stem.

    Returns:
        ``<root>/games/<game_id>.jsonl``.

    Raises:
        RecordFormatError: If ``game_id`` is not exactly one path
            segment.
    """

    # Both producers take the id from outside the process — the scraper
    # from the wire, the engine from a flag — so the file name it becomes
    # is checked here rather than trusted.
    if (
        not game_id
        or game_id in {".", ".."}
        or "/" in game_id
        or "\\" in game_id
    ):
        raise RecordFormatError(
            f"A game id is one path segment, got {game_id!r}"
        )
    return games_dir(root) / f"{game_id}.jsonl"


def new_game_id(
    prefix: str = "engine",
    *,
    now: datetime | None = None,
    entropy: str | None = None,
) -> str:
    """Mint an identity for a new record.

    Two prefixes are in use: ``engine`` for a game the engine played
    itself, ``obs`` for one it watched. An observed game normally takes
    the table's own opaque id instead, which is what lets a re-observed
    game be recognised rather than duplicated.

    Args:
        prefix: The id's leading word.
        now: The instant to stamp; defaults to now, in UTC.
        entropy: The random tail; defaults to six hex characters. It is
            what keeps two games started in the same second apart.

    Returns:
        An id of the shape ``<prefix>-<UTC stamp>-<tail>``.
    """

    moment = now or datetime.now(UTC)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    tail = entropy or secrets.token_hex(3)
    return f"{prefix}-{stamp}-{tail}"
