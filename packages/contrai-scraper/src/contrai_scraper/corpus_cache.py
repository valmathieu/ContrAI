"""A corpus build's memory of what each raw log parsed into.

Parsing is the one step of ``contrai-scrape corpus build`` whose cost grows
with every log ever kept, and a raw log only ever changes by growing. So the
build keeps, per log, the records its parse produced, under
``<corpus>/cache/<source>/<log>.json``, and a later build reads them back
instead of parsing the log again.

**A cache entry is trusted only when nothing it depends on has moved.** It
stores two keys and is used only when both still match:

* the log's SHA-256 — a log that grew, or was replaced, is parsed again;
* the *parser fingerprint* — a hash of the ``contrai-core``, ``contrai-data``
  and ``contrai-scraper`` source files and of the profile's ``[wire]`` and
  ``[rules]`` sections. Any edit to the code that could change a parse, or to
  the vocabulary it reads the wire with, turns every entry stale at once.

The fingerprint is deliberately coarse: an edit to a scraper module the parse
never calls invalidates the cache too. That costs one full parse, while a
fingerprint too narrow to see a real parser change would silently keep games
parsed by the old code — the one failure a rebuildable corpus must not have.

The cache is derived data. It is never backed up, a damaged entry reads as a
miss, and deleting the directory only makes the next build slower.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Final

import contrai_core
import contrai_data
from contrai_data import GameEvent, RecordError, decode, encode

import contrai_scraper

from .profile import Profile

#: The cache's directory under a corpus root.
CACHE_DIR: Final[str] = "cache"

#: The entry format; a reader skips any other.
CACHE_FORMAT: Final[str] = "contrai-parse-cache/1"

#: The packages whose source a parse depends on.
_PARSER_PACKAGES: Final[tuple[ModuleType, ...]] = (
    contrai_core,
    contrai_data,
    contrai_scraper,
)


@dataclass(frozen=True, slots=True)
class ParsedLog:
    """What one raw log parsed into.

    Attributes:
        visits: How many table visits the log held.
        not_tournament: How many of them were left out as not a tournament.
        records: One event sequence per visit that held a game, header first.
    """

    visits: int
    not_tournament: int
    records: tuple[tuple[GameEvent, ...], ...]


def parser_fingerprint(profile: Profile) -> str:
    """A hash of everything a parse's output depends on besides the log.

    Args:
        profile: The loaded profile.

    Returns:
        A hex SHA-256 over the parser packages' ``.py`` files — each named by
        its path inside its package, so the checkout's location does not
        matter — and the profile's wire vocabulary and ruleset.
    """

    digest = hashlib.sha256(CACHE_FORMAT.encode())
    for package in _PARSER_PACKAGES:
        base = Path(str(package.__file__)).parent
        for path in sorted(base.rglob("*.py")):
            name = f"{package.__name__}/{path.relative_to(base).as_posix()}"
            # NUL separators, so no file boundary can be shifted into a
            # different file with the same concatenated bytes.
            digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    # Both sections are frozen dataclasses of strings, enums, tuples and
    # mappings built in document order, so their repr is a stable spelling.
    digest.update(repr((profile.wire, profile.rules)).encode())
    return digest.hexdigest()


def cache_path(root: Path | str, source: str, log: Path) -> Path:
    """Where one raw log's entry lives.

    Args:
        root: The corpus root.
        source: The log's source label.
        log: The raw log.

    Returns:
        ``<root>/cache/<source>/<log name>.json``.
    """

    return Path(root) / CACHE_DIR / source / f"{log.name}.json"


def load_parsed(
    root: Path | str, source: str, log: Path, *, fingerprint: str, digest: str
) -> ParsedLog | None:
    """Read a log's entry back, if it still describes this log and this parser.

    Args:
        root: The corpus root.
        source: The log's source label.
        log: The raw log.
        fingerprint: The current :func:`parser_fingerprint`.
        digest: The log's current SHA-256.

    Returns:
        The cached parse, or ``None`` when there is no entry, it was made by
        another parser or from another version of the log, or it cannot be
        read. Every doubt is a miss: the price of a miss is one parse.
    """

    path = cache_path(root, source, log)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if (
            entry["format"] != CACHE_FORMAT
            or entry["fingerprint"] != fingerprint
            or entry["log_sha256"] != digest
        ):
            return None
        return ParsedLog(
            visits=entry["visits"],
            not_tournament=entry["not_tournament"],
            records=tuple(
                tuple(decode(line) for line in record) for record in entry["records"]
            ),
        )
    except (OSError, ValueError, KeyError, TypeError, RecordError):
        # Missing, not JSON, missing a key, or a line the codec refuses.
        return None


def save_parsed(
    root: Path | str,
    source: str,
    log: Path,
    parsed: ParsedLog,
    *,
    fingerprint: str,
    digest: str,
) -> None:
    """Store a log's parse, replacing any older entry whole.

    Written to a temporary file and renamed into place, so an interrupted
    build leaves either the old entry or the new one, never half of one.

    Args:
        root: The corpus root.
        source: The log's source label.
        log: The raw log.
        parsed: What the log parsed into.
        fingerprint: The :func:`parser_fingerprint` it was parsed under.
        digest: The log's SHA-256.
    """

    path = cache_path(root, source, log)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "format": CACHE_FORMAT,
        "fingerprint": fingerprint,
        "log_sha256": digest,
        "visits": parsed.visits,
        "not_tournament": parsed.not_tournament,
        # Each event through the record codec, so the cache can never spell
        # an event differently from the record it will become.
        "records": [[encode(event) for event in record] for record in parsed.records],
    }
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def prune_cache(root: Path | str, keep: Iterable[Path]) -> int:
    """Delete every entry that describes no current raw log.

    Args:
        root: The corpus root.
        keep: The entries still in use, as :func:`cache_path` spells them.

    Returns:
        How many entries were deleted.
    """

    cache = Path(root) / CACHE_DIR
    if not cache.is_dir():
        return 0
    wanted = {path.resolve() for path in keep}
    removed = 0
    for entry in cache.rglob("*.json"):
        if entry.resolve() not in wanted:
            entry.unlink()
            removed += 1
    return removed


def parse_or_load(
    root: Path | str,
    source: str,
    log: Path,
    *,
    fingerprint: str,
    parse: Callable[[Path], ParsedLog],
    force: bool = False,
) -> tuple[ParsedLog, bool]:
    """A log's parse, from the cache when it is still good, else fresh.

    Args:
        root: The corpus root.
        source: The log's source label.
        log: The raw log.
        fingerprint: The current :func:`parser_fingerprint`.
        parse: Parses a log into a :class:`ParsedLog`.
        force: Parse even when a good entry exists, and replace it.

    Returns:
        The parse, and whether it came from the cache.
    """

    digest = contrai_data.file_sha256(log)
    if not force:
        cached = load_parsed(root, source, log, fingerprint=fingerprint, digest=digest)
        if cached is not None:
            return cached, True
    parsed = parse(log)
    save_parsed(root, source, log, parsed, fingerprint=fingerprint, digest=digest)
    return parsed, False
