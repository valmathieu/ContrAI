"""What a session is doing, as one JSON object per line.

A recorder runs for hours on a machine nobody is watching, and the question
asked of it afterwards is never "did it crash" — that much is obvious — but
"was it *working*". A run that seats no table, a run whose second socket
dropped, a run rejecting every table it is offered: all three look like a
healthy process from the outside and are distinguishable only from counters.

So every transition writes a line and every line is a document: ``journalctl``
renders them as-is, ``jq`` filters them, and a shift's worth is a table
without a parser being written for it. The stream is **stderr**, because the
subcommands print their results on stdout and a caller redirecting one must
not get the other.

Nothing here opens a file or touches a clock it was not handed: the writer,
the wall clock and the monotonic clock are all injected, which is what lets
the cadence be tested without a test that sleeps.

A fleet writes one stream for all its workers, so every line a worker writes
carries its label — an opaque name such as ``bot01``, never the account's
address — and each worker counts for itself. A worker that has stopped seating
tables is then visible as *one* worker's flat counters rather than as a dip in
a total, and the fleet's own heartbeat adds the workers up.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class Counters:
    """What one session has done so far. Monotonic; never reset."""

    tables_seated: int = 0
    tables_rejected: int = 0
    games_recorded: int = 0
    rounds_recorded: int = 0
    frames_received: int = 0
    frames_deduped: int = 0
    frames_skipped: int = 0
    snapshots_seen: int = 0
    score_reads_wire: int = 0
    score_reads_panel: int = 0
    score_reads_failed: int = 0
    sockets_opened: int = 0
    sockets_closed: int = 0

    def as_dict(self) -> dict[str, int]:
        """Every counter, by name.

        Returns:
            The counters, in declaration order.
        """

        return asdict(self)

    def __add__(self, other: Counters) -> Counters:
        """Two sets of counts, added name by name."""

        return Counters(**{
            name: value + getattr(other, name)
            for name, value in self.as_dict().items()
        })


class HealthLog:
    """One JSON object per line, on its own stream."""

    __slots__ = ("_write", "_clock", "_monotonic", "_last", "_label", "_workers",
                 "counters")

    def __init__(
        self,
        *,
        write: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        label: str | None = None,
    ) -> None:
        """Build a log.

        Args:
            write: Where a line goes; defaults to stderr, flushed per line.
            clock: The wall clock stamping each line; defaults to UTC now.
            monotonic: The clock the heartbeat interval is measured on. A
                wall clock would make a heartbeat land twice, or not at all,
                the moment the machine's time is corrected.
            label: The worker every line is written for, or ``None`` for a
                process with only one. :meth:`worker` is the usual way to
                get a labelled log.
        """

        self._write = write if write is not None else _to_stderr
        self._clock = clock if clock is not None else _utc_now
        self._monotonic = monotonic
        self._last = monotonic()
        self._label = label
        self._workers: list[HealthLog] = []
        self.counters = Counters()
        """The session's running counts, shared with whoever bumps them."""

    def worker(self, label: str) -> HealthLog:
        """A log for one worker, writing to this one's stream.

        Its lines carry ``worker`` right after the event name, its counters
        are its own, and so is its heartbeat interval: each worker beats on
        its own cadence, while :meth:`fleet_heartbeat` adds them all up.

        Args:
            label: The worker's opaque name, such as ``bot01``. Never the
                account's address: this is what every one of its lines says.

        Returns:
            The worker's log.
        """

        log = HealthLog(
            write=self._write, clock=self._clock, monotonic=self._monotonic,
            label=label,
        )
        self._workers.append(log)
        return log

    def event(self, name: str, **fields: Any) -> None:
        """Write one line: ``{"at", "event", "worker"?, **fields}``.

        Args:
            name: What happened, in the recorder's own vocabulary.
            **fields: Whatever makes the line diagnosable — a table id, a
                rejection reason, a parse note.
        """

        self._line(self._head(name) | fields)

    def heartbeat(self, **fields: Any) -> None:
        """Write a line carrying every counter, and reset the interval.

        Args:
            **fields: Context for the beat, such as the game being watched.
        """

        self._line(self._head("heartbeat") | self.counters.as_dict() | fields)
        self._last = self._monotonic()

    def fleet_heartbeat(self, **fields: Any) -> None:
        """Write the whole fleet's counters as one line, and reset the interval.

        A separate event from ``heartbeat``, so a reader summing heartbeats
        per worker never counts a worker twice.

        Args:
            **fields: Context for the beat.
        """

        total = self.counters
        for worker in self._workers:
            total = total + worker.counters
        self._line(
            self._head("fleet_heartbeat")
            | {"workers": len(self._workers)}
            | total.as_dict()
            | fields
        )
        self._last = self._monotonic()

    def due(self, interval_s: float) -> bool:
        """Whether ``interval_s`` has passed since the last heartbeat.

        Args:
            interval_s: The cadence, in seconds.

        Returns:
            Whether a heartbeat is owed.
        """

        return self._monotonic() - self._last >= interval_s

    def _head(self, name: str) -> dict[str, Any]:
        """The fields every line starts with: when, what, and for which worker."""

        head: dict[str, Any] = {"at": self._stamp(), "event": name}
        if self._label is not None:
            head["worker"] = self._label
        return head

    def _stamp(self) -> str:
        """The current instant, spelled the way a record's stamps are."""

        return (
            self._clock().isoformat(timespec="seconds").replace("+00:00", "Z")
        )

    def _line(self, payload: dict[str, Any]) -> None:
        """Serialise one payload.

        ``sort_keys=False`` on purpose: the insertion order puts ``at`` and
        ``event`` first, which is what makes a tail of the log readable
        without a tool.
        """

        self._write(json.dumps(payload, ensure_ascii=False, sort_keys=False))


def _utc_now() -> datetime:
    """The current instant, in UTC."""

    return datetime.now(UTC)


def _to_stderr(line: str) -> None:
    """Write one line to stderr, flushed.

    Flushed per line for the same reason the raw log is: a session that is
    killed must leave behind everything it had already said.
    """

    print(line, file=sys.stderr, flush=True)
