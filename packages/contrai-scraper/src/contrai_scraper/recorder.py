"""One table at a time, from seat to game end to hop.

This is the loop the whole package exists for, and it is written as a state
machine over two injected objects — something spectator-shaped and a frame
source — so that none of it imports Playwright and all of it can be driven by
a scripted source in a test. The browser is behind one of those objects and
the site is behind the profile; what is spelled out here is only the order of
the decisions.

**The buffer is the design.** A seated table's wire events are collected and
handed to the batch parser at the game boundary, rather than fed to a second,
incremental parser. Two rules then exist once instead of twice — a round is
complete at twenty-eight plays, and the round in progress at seating is
skipped — and the snapshot a boundary read returns needs no special handling
at all, because it lands in the same buffer the parser already walks.

**The buffer is reset at every seat.** Table discovery joins each candidate
and emits one snapshot per visit, so a listener that kept the first snapshot
would seat four players who are at a table it left: the record would be
complete, legal, and about the wrong people. For the same reason the stream's
memory of what it has already seen is reset too — and, because both
connections carry every frame, a snapshot already used to accept or reject a
table is remembered across that reset, so its mirrored copy cannot be read as
the next table's.

The score for a round arrives one of two ways: the client can ask for table
state without leaving, which answers on the socket as a fresh snapshot, and
that is the fast path. The panel read behind it is evidence only — it goes to
the raw log, where a later re-parse can use it — because the parser's scores
come from snapshots and nothing else. A round nobody could score simply has
no ``round_scored``, which is the schema saying so rather than the recorder
inventing a number.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from contrai_core import Position
from contrai_data import EndReason, RecordWriter, RoundDealt, game_path

from .exceptions import ParseError, ScraperError
from .frames import FrameSource, RawFrame
from .health import HealthLog
from .parse.session import parse_session
from .parse.snapshot import ScoreRow, Snapshot, read_snapshot
from .parse.translate import Translator
from .profile import Profile
from .rawlog import RawLogWriter
from .wire import DEAL_VERB, WireEvent, WireStream, duplicate_key, order_events

#: The name a scoreboard read is filed under in the raw log.
SCOREBOARD_PANEL = "scoreboard"


class StopReason(StrEnum):
    """Why a recorder stopped seating tables."""

    MAX_GAMES = "max_games"
    TIME_LIMIT = "time_limit"
    WINDOW_CLOSED = "window_closed"
    EGRESS_BLOCKED = "egress_blocked"
    SOURCE_ENDED = "source_ended"


@dataclass(frozen=True, slots=True)
class RecorderLimits:
    """When to stop; all unset means "until interrupted"."""

    max_games: int | None = None
    max_seconds: float | None = None
    """Hard stop: past it, the game in hand is closed as ``observer_left``."""

    seat_until_s: float | None = None
    """Seconds after which no new table is taken; the game in hand runs on to max_seconds."""


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """What one ``run`` did."""

    games_recorded: int
    tables_seated: int
    tables_rejected: int
    records: tuple[Path, ...]
    stop_reason: StopReason
    """What ended the session: a limit, the window, the egress, or the frames."""


class Recorder:
    """One table at a time, from seat to game end to hop.

    The loop buffers the seated table's wire events and hands them to
    :func:`~contrai_scraper.parse.session.parse_session` at the game
    boundary, rather than maintaining a second incremental parser. Two rules
    then exist once instead of twice — a round is complete at twenty-eight
    plays, and the round in progress at seating is skipped — and the snapshot
    a boundary read returns needs no special handling, because it lands in
    the same buffer the parser already walks.

    The buffer is reset at every seat. Table discovery joins each candidate
    and emits one snapshot per visit, so a listener that kept the first
    snapshot would seat four players who are at a table it left.
    """

    __slots__ = (
        "_spectator", "_frames", "_profile", "_health", "_limits", "_raw",
        "_monotonic", "_translator", "_iterator", "_stream", "_buffer",
        "_seen_snapshots", "_records", "_deadline", "_stopped", "_active",
        "_last_activity", "_seated", "_rejected", "_base", "_egress",
        "_seat_deadline", "_stop_reason",
    )

    def __init__(
        self,
        spectator: Any,
        frames: FrameSource,
        profile: Profile,
        health: HealthLog,
        *,
        limits: RecorderLimits = RecorderLimits(),
        raw: RawLogWriter | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        egress: Any = None,
    ) -> None:
        """Wire the loop up.

        Args:
            spectator: Anything with the browser half's surface —
                ``read_options``, ``read_scoreboard``, ``request_state`` and
                ``next_table``. Taken by duck type on purpose: this module
                imports no Playwright and none of its tests open a browser.
            frames: Where the wire comes from, live or replayed.
            profile: The loaded profile.
            health: Where transitions and counters are written.
            limits: When to stop.
            raw: The session's raw log, if one is being kept. Frames are
                written to it as they arrive, which is what makes an
                interrupted session re-parsable.
            monotonic: The clock the watchdog and the run deadline are
                measured on.
            egress: Anything with ``async check() -> EgressReading``, asked
                before every hop and when a table goes quiet. ``None`` skips
                both checks, which is what a replay or a test wants.
        """

        self._spectator = spectator
        self._frames = frames
        self._profile = profile
        self._health = health
        self._limits = limits
        self._raw = raw
        self._monotonic = monotonic
        self._egress = egress
        self._translator = Translator(profile)
        self._iterator: Any = None
        self._stream: WireStream | None = None
        self._buffer: list[WireEvent] = []
        self._seen_snapshots: set[str] = set()
        self._records: list[Path] = []
        self._deadline: float | None = None
        self._seat_deadline: float | None = None
        self._stopped = False
        self._stop_reason: StopReason | None = None
        self._active = False
        self._last_activity = 0.0
        self._seated = 0
        self._rejected = 0
        self._base = [0, 0, 0]

    async def run(self) -> SessionSummary:
        """Watch tables until the limits are reached or the frames stop.

        Returns:
            What the session did, and why it stopped.

        Raises:
            BaseException: Whatever stopped the process. An interruption
                writes the game in hand before it propagates — a session
                killed after three rounds should leave three rounds behind,
                not nothing.
        """

        self._iterator = self._frames.__aiter__()
        limits = self._limits
        if limits.max_seconds is not None or limits.seat_until_s is not None:
            # One reading for both: a second call would shift every scripted
            # clock in the suite by a tick.
            started = self._monotonic()
            if limits.max_seconds is not None:
                self._deadline = started + limits.max_seconds
            if limits.seat_until_s is not None:
                self._seat_deadline = started + limits.seat_until_s
        try:
            while not self._finished():
                seated = await self._seat()
                if seated is None:
                    continue
                snapshot, event = seated
                if not await self._gate(snapshot, event):
                    await self._hop()
                    continue
                await self._watch(snapshot)
        except (asyncio.CancelledError, KeyboardInterrupt):
            self._write(EndReason.INTERRUPTED)
            raise
        self._health.heartbeat(closing=True)
        return SessionSummary(
            games_recorded=len(self._records),
            tables_seated=self._seated,
            tables_rejected=self._rejected,
            records=tuple(self._records),
            stop_reason=self._stop_reason or self._limit_reason(),
        )

    # -- 1. seat ---------------------------------------------------------

    async def _seat(self) -> tuple[Snapshot, WireEvent] | None:
        """Wait for the join snapshot that describes wherever we landed.

        Returns:
            The snapshot and the event that carried it, or ``None`` when the
            wait ran out — in which case the table has already been left —
            or when the shift stopped taking tables while it waited.
        """

        self._reset_buffer()
        started = self._monotonic()
        timeout = self._profile.recorder.snapshot_timeout_s
        while True:
            if self._past_deadline():
                self._stop(StopReason.TIME_LIMIT)
                return None
            if self._past_seat_deadline():
                self._stop(StopReason.WINDOW_CLOSED)
                return None
            remaining = self._capped(
                timeout - (self._monotonic() - started), seating=True
            )
            if remaining <= 0:
                return await self._give_up_on_seat()
            pulled = await self._pull(remaining)
            if self._stopped:
                return None
            if pulled is None:
                return await self._give_up_on_seat()
            frame, event = pulled
            if event is None or event.kind != self._join_name:
                continue
            if not self._first_sight(frame):
                # The mirrored connection's copy of a snapshot this session
                # has already judged. Seating on it would re-seat the table
                # that was just left.
                continue
            try:
                snapshot = read_snapshot(
                    event.data, self._translator, at=event.received_ms
                )
            except ParseError as error:
                # A table this profile cannot read is a table to leave, not a
                # session to end. The site runs variants whose contracts the
                # tournament ruleset has no name for — all trump is the one
                # that was met — and the options gate that refuses such a
                # table sits *after* this read, so without this a table we
                # never wanted spends a slot of the shift's failure budget.
                # The reason and the message are logged and the count lands
                # in ``tables_rejected``, so a profile that has genuinely
                # drifted still shows itself rather than hiding as a hop.
                return await self._leave_unreadable(error)
            if self._past_seat_deadline():
                # The table described itself a moment too late: the window
                # closed while the snapshot was on its way, and a table seated
                # now would be watched on time the shift no longer has.
                self._stop(StopReason.WINDOW_CLOSED)
                return None
            return snapshot, event

    async def _leave_unreadable(self, error: ParseError) -> None:
        """Leave a table whose snapshot this profile cannot read.

        Args:
            error: What the reader objected to, logged verbatim so the
                operator sees the token rather than only the refusal.
        """

        self._rejected += 1
        self._health.counters.tables_rejected += 1
        self._health.event(
            "table_rejected", reason="unreadable_snapshot", error=str(error)
        )
        await self._hop()
        return None

    async def _give_up_on_seat(self) -> None:
        """Leave a table that never said what it was."""

        self._health.event("seat_timeout")
        await self._hop()
        return None

    # -- 2 to 6. the gates -----------------------------------------------

    async def _gate(self, snapshot: Snapshot, event: WireEvent) -> bool:
        """Decide whether this table is worth a game's worth of frames.

        Args:
            snapshot: What the table said when we sat down.
            event: The event that carried it, which becomes the buffer's
                first entry once the table is accepted.

        Returns:
            Whether the table was taken.
        """

        recorder = self._profile.recorder
        if not snapshot.is_tournament:
            return self._reject(snapshot, "not_tournament")
        if len(snapshot.score_rows) >= recorder.hop_after_rows:
            return self._reject(
                snapshot, "too_far_along", rows=len(snapshot.score_rows)
            )

        reading = await self._spectator.read_options(self._profile.rules.options)
        if not reading.matches:
            return self._reject(
                snapshot,
                "options_mismatch",
                missing=list(reading.missing),
                extra=list(reading.extra),
                differing=list(reading.differing),
            )

        board = await self._read_panel()
        if not _orientation_holds(snapshot, board.rows):
            # A record whose two sides are swapped is well formed and wrong
            # about who won every round, and nothing downstream can see it.
            self._health.event(
                "orientation_mismatch",
                table=snapshot.table_id,
                panel=list(board.rows[-1]),
                wire=list(_wire_pair(snapshot.score_rows[-1])),
            )
            self._rejected += 1
            self._health.counters.tables_rejected += 1
            return False

        self._seated += 1
        self._health.counters.tables_seated += 1
        self._health.event("table_seated", table=snapshot.table_id)
        self._buffer = [event]
        return True

    def _reject(self, snapshot: Snapshot, reason: str, **fields: Any) -> bool:
        """Count and log one refused table.

        Returns:
            ``False``, so a gate can ``return self._reject(...)``.
        """

        self._rejected += 1
        self._health.counters.tables_rejected += 1
        self._health.event(
            "table_rejected", reason=reason, table=snapshot.table_id, **fields
        )
        return False

    # -- 7 to 11. the live loop ------------------------------------------

    async def _watch(self, snapshot: Snapshot) -> None:
        """Follow one table until its game ends, or it goes quiet.

        Args:
            snapshot: The accepted snapshot, already in the buffer.
        """

        table_id = snapshot.table_id
        last_event_id: str | None = None
        last_deal_round: int | None = None
        self._last_activity = self._monotonic()
        recorder = self._profile.recorder

        while True:
            if self._health.due(recorder.health_interval_s):
                self._health.heartbeat(table=table_id)
            if self._past_deadline():
                # The shift is over. The game was not abandoned and did not
                # finish: we stopped watching it. Only this hard deadline stops
                # a game in hand; the seat deadline is never checked here.
                self._stop(StopReason.TIME_LIMIT)
                self._write(EndReason.OBSERVER_LEFT)
                return
            remaining = self._capped(
                recorder.stale_after_s - (self._monotonic() - self._last_activity)
            )
            if remaining <= 0:
                return await self._abandon()

            pulled = await self._pull(remaining)
            if self._stopped:
                self._write(EndReason.INTERRUPTED)
                return
            if pulled is None:
                return await self._abandon()

            frame, event = pulled
            if self._active:
                self._last_activity = self._monotonic()
            if event is None:
                continue

            self._buffer.append(event)
            last_event_id = event.frame_id or last_event_id
            if self._says(event, "ended"):
                return await self._close(table_id, last_event_id)
            key = event.key
            if key is None or key.verb != DEAL_VERB or key.round == last_deal_round:
                continue
            if last_deal_round is not None:
                # The deal that opens a round is the news that the previous
                # one is over and has a score to read. The first deal after
                # seating has none: the snapshot that seated us is it.
                await self._boundary(table_id, last_event_id)
            last_deal_round = key.round

    # -- 8. the boundary read --------------------------------------------

    async def _boundary(self, table_id: str | None, last_event_id: str | None) -> None:
        """Ask the table for a state snapshot, or fall back to the panel.

        The request answers on the socket, so the snapshot arrives in the
        live loop and lands in the buffer like any other event. The panel
        behind it cannot: the parser reads scores from snapshots only, so a
        panel read is evidence for the raw log and nothing more.
        """

        counters = self._health.counters
        if last_event_id is not None and await self._spectator.request_state(
            table_id, last_event_id
        ):
            counters.score_reads_wire += 1
            return
        board = await self._read_panel()
        if board.rows:
            counters.score_reads_panel += 1
        else:
            counters.score_reads_failed += 1

    # -- 9, 10, 12. the ways out -----------------------------------------

    async def _close(self, table_id: str | None, last_event_id: str | None) -> None:
        """Finish a game the table says is over, then leave.

        The last round has had no deal after it, so its score has never been
        asked for. One final request buys it, and the answer is waited for
        rather than fired and forgotten — otherwise the round the game was
        decided on is the one round with no score.
        """

        if last_event_id is not None and await self._spectator.request_state(
            table_id, last_event_id
        ):
            self._health.counters.score_reads_wire += 1
            await self._drain_for_snapshot()
        self._write(None)
        await self._hop()

    async def _drain_for_snapshot(self) -> None:
        """Keep reading until the answering snapshot arrives, or time runs out."""

        started = self._monotonic()
        timeout = self._profile.recorder.snapshot_timeout_s
        while timeout - (self._monotonic() - started) > 0:
            pulled = await self._pull(timeout - (self._monotonic() - started))
            if self._stopped or pulled is None:
                return
            _, event = pulled
            if event is None:
                continue
            self._buffer.append(event)
            if event.kind == self._join_name:
                return

    async def _abandon(self) -> None:
        """Give up on a table that has stopped playing — or on a tunnel that has."""

        self._health.event("table_stale")
        if self._egress is not None and not await self._egress_open():
            # Silence behind a dead tunnel is not a table breaking up. Writing
            # `abandoned` would blame the players for our network.
            self._write(EndReason.INTERRUPTED)
            return
        self._write(EndReason.ABANDONED)
        await self._hop(egress_checked=True)

    async def _hop(self, *, egress_checked: bool = False) -> None:
        """Ask the server for another table, if the shift still wants one.

        There is no leave: the site's exit control leaves spectating rather
        than the table, and nothing in-session recovers from that. The egress
        is re-checked first, because a hop is new traffic to the site.

        Args:
            egress_checked: Whether the caller has just checked the egress,
                so a stale table costs one probe rather than two.
        """

        if self._finished():
            return
        if (
            self._egress is not None
            and not egress_checked
            and not await self._egress_open()
        ):
            return
        await self._spectator.next_table()

    # -- the record ------------------------------------------------------

    def _write(self, end_reason: EndReason | None) -> None:
        """Parse the buffer into a record and write it.

        Args:
            end_reason: How the game ended, when the caller knows better
                than the wire does. ``None`` leaves it to the wire's flags.
        """

        if not self._buffer:
            return
        try:
            result = parse_session(
                order_events(self._buffer), self._profile, end_reason=end_reason
            )
        except ScraperError as error:
            # One table the parser cannot read costs a hop, not the shift.
            self._health.event("parse_failed", error=str(error))
            self._buffer = []
            return

        for note in result.notes:
            self._health.event("parse_note", note=note)
        header = result.events[0]
        path = game_path(self._profile.output.root, header.game_id)
        with RecordWriter(path) as writer:
            for event in result.events:
                writer.write(event)

        rounds = sum(1 for event in result.events if isinstance(event, RoundDealt))
        self._health.counters.games_recorded += 1
        self._health.counters.rounds_recorded += rounds
        self._records.append(path)
        self._health.event(
            "game_recorded", game=header.game_id, rounds=rounds, path=str(path)
        )
        # Emptied, not kept: the interrupt handler writes whatever is in the
        # buffer, and a record written twice is a record with two of every
        # round.
        self._buffer = []

    # -- the wire --------------------------------------------------------

    async def _pull(self, timeout: float) -> tuple[RawFrame, WireEvent | None] | None:
        """Take one frame off the source, log it, and ingest it.

        Args:
            timeout: How long to wait, in seconds.

        Returns:
            The frame and whatever event it carried, or ``None`` when the
            wait ran out. A source that has ended sets the stop flag instead.
        """

        try:
            frame = await asyncio.wait_for(anext(self._iterator), timeout)
        except TimeoutError:
            return None
        except StopAsyncIteration:
            self._stop(StopReason.SOURCE_ENDED)
            return None

        if self._raw is not None:
            self._raw.write_frame(frame)
        assert self._stream is not None
        before = self._stream.received
        event = self._stream.ingest(frame.text, frame.socket)
        # A frame the stream recognised means the table is alive, mirrored
        # copy included. A keepalive does not: it is the socket talking, not
        # the game.
        self._active = self._stream.received > before
        self._sync_counters()
        if event is not None and event.kind == self._join_name:
            self._health.counters.snapshots_seen += 1
        return frame, event

    def _reset_buffer(self) -> None:
        """Start a fresh table: empty buffer, fresh de-duplication."""

        if self._stream is not None:
            self._base[0] += self._stream.received
            self._base[1] += self._stream.deduped
            self._base[2] += self._stream.skipped
        self._stream = WireStream(self._profile.wire)
        self._buffer = []

    def _sync_counters(self) -> None:
        """Copy the stream's tallies onto the session's, which never reset."""

        assert self._stream is not None
        counters = self._health.counters
        counters.frames_received = self._base[0] + self._stream.received
        counters.frames_deduped = self._base[1] + self._stream.deduped
        counters.frames_skipped = self._base[2] + self._stream.skipped

    def _first_sight(self, frame: RawFrame) -> bool:
        """Whether this snapshot has not already decided a table's fate."""

        # The same identity the raw log and the live stream de-duplicate on,
        # falling back to the text itself so a frame without one is still
        # compared against something rather than against ``None``.
        identity = duplicate_key(frame.text) or frame.text
        if identity in self._seen_snapshots:
            return False
        self._seen_snapshots.add(identity)
        return True

    # -- small readings --------------------------------------------------

    async def _read_panel(self) -> Any:
        """Read the scoreboard panel and file its text as evidence."""

        board = await self._spectator.read_scoreboard()
        if self._raw is not None:
            self._raw.write_panel(SCOREBOARD_PANEL, board.text)
        return board

    def _says(self, event: WireEvent, field: str) -> bool:
        """Whether a table update carries one of the two lifecycle flags."""

        if event.kind != self._profile.wire.events.table_update:
            return False
        return bool(self._translator.field(event.data, field))

    @property
    def _join_name(self) -> str:
        """The event name a join snapshot arrives under."""

        return self._profile.wire.events.join_snapshot

    def _capped(self, remaining: float, *, seating: bool = False) -> float:
        """One wait, shortened so it cannot outlive the deadlines that bound it.

        Args:
            remaining: The wait the caller wants, in seconds.
            seating: Whether the wait is for a table to seat, which the seat
                deadline bounds too. A game already being watched is bounded
                by the hard deadline only: capping it by the seat deadline
                would abandon the game in hand the moment the window closed.

        Returns:
            The wait, cut to the earliest deadline that applies.
        """

        deadlines = [self._deadline]
        if seating:
            deadlines.append(self._seat_deadline)
        present = [deadline for deadline in deadlines if deadline is not None]
        if not present:
            return remaining
        return min(remaining, min(present) - self._monotonic())

    def _past_deadline(self) -> bool:
        """Whether the run's own time limit has passed."""

        return self._deadline is not None and self._monotonic() >= self._deadline

    def _past_seat_deadline(self) -> bool:
        """Whether the shift has stopped taking new tables."""

        return (
            self._seat_deadline is not None
            and self._monotonic() >= self._seat_deadline
        )

    def _finished(self) -> bool:
        """Whether the loop should stop seating tables."""

        if self._stopped or self._past_deadline() or self._past_seat_deadline():
            return True
        return (
            self._limits.max_games is not None
            and len(self._records) >= self._limits.max_games
        )

    def _stop(self, reason: StopReason) -> None:
        """Stop seating, keeping the first reason given."""

        self._stopped = True
        if self._stop_reason is None:
            self._stop_reason = reason

    def _limit_reason(self) -> StopReason:
        """Which limit ended a loop that stopped without saying why.

        Every stop that is not a limit goes through :meth:`_stop` and names
        itself, so a loop that ended with no reason ended on a limit — and one
        that was neither the game count nor the hard deadline was the seat
        deadline. Checked in that order, so a hard deadline that has also
        passed is the one reported.
        """

        limits = self._limits
        if limits.max_games is not None and len(self._records) >= limits.max_games:
            return StopReason.MAX_GAMES
        if self._past_deadline():
            return StopReason.TIME_LIMIT
        return StopReason.WINDOW_CLOSED

    async def _egress_open(self) -> bool:
        """Check the egress; on a refusal, say so and stop."""

        reading = await self._egress.check()
        if reading.ok:
            return True
        self._health.event("egress_blocked", **reading.fields())
        self._stop(StopReason.EGRESS_BLOCKED)
        return False


def _orientation_holds(
    snapshot: Snapshot, panel: tuple[tuple[int, int], ...]
) -> bool:
    """Whether the panel's "us" is the side the snapshot calls the south seat's.

    The panel is rendered from where the spectator sits, so its left-hand
    column is the bottom seat's side; the snapshot's own team labels have
    already been resolved through the seat map. Comparing the newest row of
    each is what turns "us" from an assumption into a measurement.

    Args:
        snapshot: The table's own account of the rounds scored so far.
        panel: What the scoreboard showed, oldest first.

    Returns:
        Whether the two agree. With no scored round on either side there is
        nothing to compare, and that is not a failure — it is what a game
        that has just started looks like.
    """

    if not snapshot.score_rows or not panel:
        return True
    return tuple(panel[-1]) == _wire_pair(snapshot.score_rows[-1])


def _wire_pair(row: ScoreRow) -> tuple[int, int]:
    """One score row as the panel would print it: the south seat's side first.

    A scoreboard column is a round's whole marked total — the made points,
    the announced ones and the belote the sheet credited — rather than the
    card points behind them. Leaving the belote out refused perfectly good
    tables as though their sides were swapped, on every round where one was
    marked.
    """

    return tuple(
        sum(row.marked[seat.team_side]) + row.marked_belote[seat.team_side]
        for seat in (Position.SOUTH, Position.WEST)
    )
