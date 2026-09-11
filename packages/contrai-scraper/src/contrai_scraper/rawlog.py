"""A verbatim, append-only account of one observation session.

The raw log is the scraper's evidence. Every parsing decision downstream is a
guess about what a string meant, and a guess is only correctable if the string
survived: when the parser turns out to have mis-read a payload, the fix is to
re-run it over the logs, not to go and watch the games again.

So the text is stored **exactly as it arrived** — no re-encoding, no
normalisation, no pretty-printing — and the file is append-only, one JSON
document per line, flushed per line. The one thing that is dropped is the
mirrored connection's copy of a frame already recorded, through the same
:func:`~contrai_scraper.wire.duplicate_key` the live stream de-duplicates on,
so the log and the stream can never disagree about what "the same frame" is.

Lines carry a ``kind`` discriminator. Only ``header`` and ``frame`` are
written by the wire half; ``panel`` and ``note`` exist for the browser half,
which reads DOM panels the socket does not carry and wants them in the same
timeline rather than in a file of their own.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from .exceptions import WireError
from .wire import duplicate_key

if TYPE_CHECKING:  # pragma: no cover - import cycle, types only
    # ``frames`` imports this module at runtime, for the replay source
    # that reads a log back. The edge the other way is types only, so
    # the two modules import in either order.
    from .frames import RawFrame

#: The log's own format tag. The major is what a reader checks; a minor bump
#: adds fields a reader may ignore.
FORMAT = "contrai-raw/1"

#: The line kinds a log may hold.
HEADER = "header"
FRAME = "frame"
PANEL = "panel"
NOTE = "note"

_KINDS = frozenset({HEADER, FRAME, PANEL, NOTE})

#: Reserved keys a note may not overwrite.
_RESERVED = frozenset({"kind", "at", "socket", "dir", "text", "name"})


@dataclass(frozen=True, slots=True)
class RawLine:
    """One line of a raw log, whatever its kind."""

    kind: str
    socket: int | None = None
    direction: str | None = None
    at: float | None = None
    text: str | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    """Everything the line carried beyond the common shape."""


def raw_dir(root: Path | str) -> Path:
    """The directory raw logs live under.

    Args:
        root: The profile's raw root.

    Returns:
        ``<root>/raw``. Not created.
    """

    return Path(root) / "raw"


def raw_path(root: Path | str, session_id: str) -> Path:
    """The file one session's raw log lives in.

    Args:
        root: The profile's raw root.
        session_id: The session's identity, which becomes the file stem.

    Returns:
        ``<root>/raw/<session_id>.jsonl``. Not created.

    Raises:
        WireError: If ``session_id`` is not exactly one path segment.
    """

    if (
        not session_id
        or session_id in {".", ".."}
        or "/" in session_id
        or "\\" in session_id
    ):
        raise WireError(f"A session id is one path segment, got {session_id!r}")
    return raw_dir(root) / f"{session_id}.jsonl"


def new_session_id(
    *, now: datetime | None = None, entropy: str | None = None
) -> str:
    """Mint an identity for a new session.

    Args:
        now: The instant to stamp; defaults to now, in UTC.
        entropy: The random suffix; defaults to six hex characters.

    Returns:
        A sortable id — the timestamp leads, so a directory listing is a
        chronology — with enough entropy that two sessions started in the
        same second do not collide.
    """

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{entropy or secrets.token_hex(3)}"


class RawLogWriter:
    """Appends lines to one session's raw log, flushing each.

    Usable as a context manager::

        with RawLogWriter(path) as log:
            log.write_frame(frame)

    The handle is opened in append mode, so re-opening an existing log
    continues it — and the header is written only when the file is new, since
    a second header mid-file would read as a second session.
    """

    __slots__ = ("_path", "_handle", "_dedup", "_seen")

    def __init__(self, path: Path | str, *, dedup: bool = True) -> None:
        """Open ``path`` for appending, creating its directory if needed.

        Args:
            path: The log file. Missing parent directories are created.
            dedup: Whether to drop the mirrored connection's copies. Switch
                it off to keep an exact account of both sockets, which is
                what a protocol investigation wants.
        """

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self._path.exists() or self._path.stat().st_size == 0
        # ``newline="\n"`` pins LF on every platform: Python's default
        # translates "\n" to "\r\n" on Windows, and a log written on the dev
        # machine would then carry a stray CR inside every line the Debian
        # box reads back.
        self._handle: IO[str] | None = self._path.open(
            "a", encoding="utf-8", newline="\n"
        )
        self._dedup = dedup
        self._seen: set[str] = set()
        if fresh:
            self._write(
                {
                    "kind": HEADER,
                    "format": FORMAT,
                    "session": self._path.stem,
                    "started_at": datetime.now(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                }
            )

    @property
    def path(self) -> Path:
        """The file being written."""

        return self._path

    @property
    def closed(self) -> bool:
        """Whether the writer has been closed."""

        return self._handle is None

    def write_frame(self, frame: RawFrame) -> bool:
        """Append one socket frame, verbatim.

        Args:
            frame: The frame as it came off the socket.

        Returns:
            Whether it was written; ``False`` means the other connection
            already carried it.

        Raises:
            ValueError: If the writer is closed.
        """

        if self._dedup:
            identity = duplicate_key(frame.text)
            # A frame with no identity — a keepalive, or anything that is not
            # a document — is always written: it has no copy to be a copy of,
            # and dropping repeats would erase the connection's heartbeat.
            if identity is not None:
                if identity in self._seen:
                    return False
                self._seen.add(identity)
        self._write(
            {
                "kind": FRAME,
                "socket": frame.socket,
                "dir": frame.direction,
                "at": frame.at,
                "text": frame.text,
            }
        )
        return True

    def write_panel(self, name: str, text: str, *, at: float | None = None) -> None:
        """Append the raw text of one panel read.

        Args:
            name: Which panel, in the profile's own words.
            text: Its text or markup, verbatim.
            at: Seconds since the session started, if the caller tracks it.

        Raises:
            ValueError: If the writer is closed.
        """

        self._write({"kind": PANEL, "at": at, "name": name, "text": text})

    def write_note(self, *, at: float | None = None, **fields: Any) -> None:
        """Append a free-form note about the session.

        Args:
            at: Seconds since the session started, if the caller tracks it.
            **fields: Whatever is worth recording — a table change, a
                watchdog firing, a health counter.

        Raises:
            ValueError: If the writer is closed, or a field name is reserved.
        """

        clashes = sorted(_RESERVED & set(fields))
        if clashes:
            raise ValueError(f"A note may not carry {', '.join(clashes)}")
        self._write({"kind": NOTE, "at": at, **fields})

    def close(self) -> None:
        """Close the file. Idempotent."""

        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> RawLogWriter:
        """Enter the context manager.

        Returns:
            This writer.
        """

        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the file, however the block was left."""

        self.close()

    def _write(self, line: Mapping[str, Any]) -> None:
        """Serialise and flush one line."""

        if self._handle is None:
            raise ValueError(f"Writer is closed: {self._path}")
        self._handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        # Flushed per line, not per session: a crash must lose at most the
        # frame being written, and a session runs for hours.
        self._handle.flush()


def read_raw_log(path: Path | str) -> tuple[RawLine, ...]:
    """Read a whole raw log.

    Tolerant of exactly one thing: an unparsable **final** line, which is what
    a crash mid-write leaves behind. A line anywhere else that does not parse
    is corruption and raises — swallowing it would silently drop a frame from
    the middle of a game, which is the one failure a raw log exists to rule
    out.

    Args:
        path: The log file.

    Returns:
        Every line but the header, in file order.

    Raises:
        FileNotFoundError: If the file does not exist.
        WireError: If the header names an unreadable format, or any line but
            a torn final one fails to parse.
    """

    text = Path(path).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ()

    parsed: list[RawLine] = [_parse_line(line, path) for line in lines[:-1]]
    last = lines[-1]
    try:
        json.loads(last)
    except json.JSONDecodeError:
        # A crash mid-write leaves a partial line, and only ever the last one.
        pass
    else:
        parsed.append(_parse_line(last, path))
    # The header is the reader's business, not its caller's: it has been
    # checked by now, and leaving it in would make every consumer skip it.
    return tuple(line for line in parsed if line.kind != HEADER)


def _parse_line(line: str, path: Path | str) -> RawLine:
    """Read one log line.

    Raises:
        WireError: If the line is not a JSON object, names an unknown kind,
            or is a header for a format this reader does not understand.
    """

    try:
        document = json.loads(line)
    except json.JSONDecodeError as error:
        raise WireError(f"{path} holds a line that is not JSON: {error}") from error
    if not isinstance(document, dict):
        raise WireError(f"{path} holds a line that is not an object")

    kind = document.pop("kind", None)
    if kind not in _KINDS:
        raise WireError(f"{path} holds a line of unknown kind {kind!r}")
    if kind == HEADER:
        found = document.get("format")
        if found != FORMAT:
            raise WireError(
                f"{path} is in format {found!r}, and this reader knows {FORMAT!r}"
            )

    return RawLine(
        kind=kind,
        socket=document.pop("socket", None),
        direction=document.pop("dir", None),
        at=document.pop("at", None),
        text=document.pop("text", None),
        fields=document,
    )
